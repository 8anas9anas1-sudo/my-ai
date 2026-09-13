"""
دائرة قطع (Circuit Breaker) لكل مزوّد ذكاء اصطناعي خارجي + تصنيف أخطائهم.

المشكلة اللي يحلها هذا الملف: بدون هذا، أي فشل بمزوّد (Groq أو احتياطي)
كان يُعالَج بنفس الطريقة دائماً — "جرّب، فشل، انتقل للتالي" — بدون أي
ذاكرة بين الطلبات. يعني لو مزوّد معيّن مستنفد حصته اليومية كاملة، كل
طلب جديد بعده (من أي مستخدم بالعائلة، لأي رسالة) يعيد اكتشاف نفس الفشل
من الصفر، بإهدار round-trip كامل + مهلة اتصال، قبل ما ينتقل للاحتياطي.

هذا بالضبط الخلل الموثّق بأنظمة توجيه LLM ساذجة — راجع:
https://dev.to/eleata/how-multi-provider-llm-routers-silently-fail-5fdd
(مثال حقيقي: مزوّد استنفد حصته الشهرية عند t=0، والراوتر أعاد المحاولة
عليه كل 60 ثانية لمدة 24 ساعة كاملة — 1440 محاولة ضائعة/يوم — لأن
الكود لم يفرّق بين "عطل مؤقت" و"عطل طويل الأمد").

الحل هنا نفس الفكرة المستخدمة بأنظمة توجيه LLM إنتاجية حقيقية (LiteLLM
Router: cooldown_time + allowed_fails، Bifrost: circuit breaker بمهلة
تصعيدية) — دائرة قطع لكل مزوّد بـRedis (مشتركة بين كل gunicorn workers،
نفس مبدأ Flask-Limiter بـextensions.py)، بمهلة تبريد *تصاعدية* تكبر مع
كل فشل متتالي بدل مدة ثابتة، حتى لا نكرر خطأ "1440 محاولة/يوم" أعلاه.

مصادر التصميم:
- https://docs.litellm.ai/docs/routing (cooldowns/fallbacks/retries)
- https://docs.getbifrost.ai/enterprise/circuit-breaker
- https://docs.sambanova.ai/cloud/api-reference/using-the-api/api-error-codes
  (التفريق بين 429 queue_full [مؤقت، يستاهل إعادة محاولة فورية] و429
  insufficient_quota [حصة منتهية، لا فائدة من إعادة محاولة قريبة])
"""
import time
import random

from app.config import Config
from app.extensions import log

try:
    import redis as _redis_lib
except ImportError:
    _redis_lib = None

_redis_client = None
if Config.REDIS_URL and _redis_lib is not None:
    try:
        _redis_client = _redis_lib.from_url(
            Config.REDIS_URL, decode_responses=True,
            socket_timeout=1.5, socket_connect_timeout=1.5,
        )
    except Exception as e:
        log.error(f"تعذر إنشاء عميل Redis لدائرة قطع المزوّدين — سيُستخدم "
                  f"بديل بذاكرة العملية (أضعف مع أكثر من worker): {e}")
        _redis_client = None

# بديل بذاكرة العملية لو بدون Redis (REDIS_URL غير مضبوط، أو فشل
# الاتصال) — كل worker يرى نسخته الخاصة فقط، لكن أفضل من عدم وجود أي
# حماية إطلاقاً. الصيغة: {provider_id: (عدد الفشل المتتالي, وقت التوفر)}
_local_state = {}


def _now():
    return time.time()


def _cooldown_key(provider_id):
    return f"cb:until:{provider_id}"


def _fails_key(provider_id):
    return f"cb:fails:{provider_id}"


def _get_cooldown_until(provider_id):
    if _redis_client:
        try:
            val = _redis_client.get(_cooldown_key(provider_id))
            return float(val) if val else None
        except Exception as e:
            log.error(f"خطأ قراءة Redis بدائرة القطع (تم تجاهله، نعامل "
                      f"المزوّد '{provider_id}' كمتاح): {e}")
            return None
    entry = _local_state.get(provider_id)
    return entry[1] if entry else None


def _get_fail_count(provider_id):
    if _redis_client:
        try:
            val = _redis_client.get(_fails_key(provider_id))
            return int(val) if val else 0
        except Exception:
            return 0
    entry = _local_state.get(provider_id)
    return entry[0] if entry else 0


def is_available(provider_id):
    """True لو المزوّد ليس بفترة تبريد حالياً — نتحقق من هذا *قبل* أي
    اتصال فعلي، حتى نتفادى round-trip كامل لمزوّد شبه مؤكد إنه سيفشل."""
    until = _get_cooldown_until(provider_id)
    return until is None or _now() >= until


def seconds_until_available(provider_id):
    until = _get_cooldown_until(provider_id)
    if until is None:
        return 0
    return max(0, round(until - _now()))


def record_success(provider_id):
    """المزوّد رجع سليم — نصفّر عداد الفشل والتبريد فوراً (نفس مبدأ
    "closed state" بدائرة القطع التقليدية: نجاح واحد يكفي للثقة به مجدداً)."""
    if _redis_client:
        try:
            _redis_client.delete(_cooldown_key(provider_id), _fails_key(provider_id))
            return
        except Exception as e:
            log.error(f"خطأ كتابة Redis بدائرة القطع (تم تجاهله): {e}")
    _local_state.pop(provider_id, None)


def record_failure(provider_id, retry_after_seconds=None, long_cooldown=False):
    """
    فشل المزوّد — نحسب تبريداً *تصاعدياً*: يبدأ بـPROVIDER_COOLDOWN_BASE_SECONDS
    ويتضاعف مع كل فشل متتالي حتى سقف PROVIDER_COOLDOWN_MAX_SECONDS. لو
    المزوّد نفسه أرسل رأس Retry-After، نستخدمه كحد أدنى (هو أدرى بحالته
    الفعلية منا). long_cooldown=True لأخطاء الإعداد (401/403 — مفتاح
    خاطئ/منتهي) اللي لن تُصلَح نفسها بالانتظار — تبريد ثابت طويل مباشرة
    بدل تصعيد تدريجي عديم الفائدة (لا شيء سيتغيّر خلال 20 أو 40 ثانية).
    """
    fails = _get_fail_count(provider_id) + 1
    if long_cooldown:
        cooldown = Config.PROVIDER_AUTH_ERROR_COOLDOWN_SECONDS
    else:
        cooldown = min(
            Config.PROVIDER_COOLDOWN_BASE_SECONDS * (2 ** (fails - 1)),
            Config.PROVIDER_COOLDOWN_MAX_SECONDS
        )
    if retry_after_seconds:
        cooldown = max(cooldown, min(retry_after_seconds, Config.PROVIDER_COOLDOWN_MAX_SECONDS))
    until = _now() + cooldown

    if _redis_client:
        try:
            # TTL أطول شوي من التبريد نفسه — يمنع تراكم مفاتيح قديمة
            # بـRedis للأبد بدل انتهاء صلاحيتها تلقائياً.
            ttl = int(cooldown) + 60
            _redis_client.set(_cooldown_key(provider_id), until, ex=ttl)
            _redis_client.set(_fails_key(provider_id), fails, ex=ttl)
        except Exception as e:
            log.error(f"خطأ كتابة Redis بدائرة القطع (تم تجاهله): {e}")
            _local_state[provider_id] = (fails, until)
    else:
        _local_state[provider_id] = (fails, until)

    log.error(f"⚠️ المزوّد '{provider_id}' فشل (فشل رقم {fails} متتالي) — "
              f"تبريد لـ{round(cooldown)} ثانية تقريباً")


def classify_error(status_code, error_message):
    """
    يصنّف فشل HTTP لفئتين تقرران سلوك المستدعي (_try_provider بـ
    ai_service.py):
      category:
        'retry_once' — عطل مؤقت جداً يستاهل إعادة محاولة فورية واحدة
                       بنفس المزوّد (429 بصياغة "ازدحام/طابور" — مثال
                       فعلي: SambaNova queue_full أو رسالة "high demand"
                       — غالباً يزول خلال أجزاء الثانية، إعادة المحاولة
                       الفورية هنا منطقية، بعكس 429 حصة منتهية اللي لن
                       تتغيّر خلال ثوانٍ).
        'skip'        — انتقل للخطوة التالية بالسلسلة مباشرة بلا إعادة
                        محاولة محلية (429 حصة عادي، 5xx، مهلة اتصال).
        'terminal'    — على الأغلب مشكلة بالطلب نفسه لا بصحة المزوّد
                        (400 مدخلات، 404/410 موديل غير موجود) — ننتقل
                        للخطوة التالية احتياطاً برضه (موديل آخر قد يقبل
                        نفس المدخلات) لكن بدون افتراض إنه عطل مؤقت.
      is_auth_error: True لـ401/402/403 — يفعّل تبريد أطول ثابت بدل
                     التصعيد العادي (راجع record_failure أعلاه). 402
                     (Payment Required — مثال فعلي مؤكَّد: خطأ "A payment
                     method is required" اللي وصل فعلاً من SambaNova)
                     مشكلة إعداد/فوترة دائمة تماماً مثل مفتاح خاطئ —
                     لن تُصلَح نفسها بالانتظار، فتستاهل نفس المعاملة.
    """
    is_auth_error = status_code in (401, 402, 403)
    if status_code == 429:
        msg = (error_message or '').lower()
        # عمداً ضيّقة — لا نضمّن "try again"/"retry" هنا: كل رسالة 429
        # تقريباً تحتوي هذه العبارة كصياغة قياسية (حتى رسائل "حصتك
        # الشهرية انتهت" غير المؤقتة إطلاقاً)، فتضمينها كانت تصنّف كل
        # 429 كـ'retry_once' خطأً. هذه العلامات فعلياً مرتبطة بازدحام
        # لحظي حقيقي (مثال فعلي مؤكَّد: رسالة "high demand" اللي وصلت
        # فعلياً للمستخدم بالسكرين شوت — راجع أيضاً SambaNova queue_full
        # بتوثيقهم الرسمي).
        transient_markers = ('high demand', 'queue', 'overload', 'busy')
        if any(m in msg for m in transient_markers):
            return 'retry_once', is_auth_error
        return 'skip', is_auth_error
    if status_code in (500, 502, 503, 504):
        return 'skip', is_auth_error
    if is_auth_error:
        return 'skip', is_auth_error
    return 'terminal', is_auth_error


# أكواد الحالة اللي تعني فعلاً "المزوّد نفسه متعب" — الوحيدة اللي تستاهل
# تفعيل دائرة القطع (تبريد يمنع طلبات مستقبلية). كود مثل 400 (مدخلات
# غير صالحة لهذا الطلب تحديداً) لا يعني المزوّد نفسه عنده مشكلة — تفعيل
# تبريد بسببه قد يعطّل مزوّداً سليماً تماماً اعتماداً على طلب واحد غريب.
# المبدأ من: https://ranjankumar.in/fault-isolation-circuit-breaking-llm-agent-pipelines
# ("Trip the circuit breaker only on systemic failures... A breaker that
# opens on your 400s or your 429s [misclassified] takes itself down for
# the wrong reasons")
HEALTH_RELATED_STATUS_CODES = {429, 500, 502, 503, 504, 401, 402, 403}


def compute_retry_delay(retry_after_seconds):
    """مهلة إعادة المحاولة الفورية الواحدة (فئة 'retry_once') — نحترم
    Retry-After لو موجود، مع jitter عشوائي بسيط يمنع كل الطلبات المتزامنة
    (لو عدة أعضاء بالعائلة يرسلون رسائل بنفس اللحظة) من إعادة المحاولة
    بالضبط بنفس اللحظة، وسقف أقصى 3 ثوانٍ حتى لا نطيل انتظار المستخدم
    بمحادثة حية بانتظار إعادة محاولة قد تفشل برضه."""
    base = retry_after_seconds if retry_after_seconds else 0.6
    return min(base + random.uniform(0, 0.4), 3.0)
