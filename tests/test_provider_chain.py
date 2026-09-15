"""
اختبار تكامل لسلسلة مزوّدي الذكاء الاصطناعي (app/ai_service.py +
app/provider_health.py) — يشغّل الملفين الحقيقيين فعلياً (لا نسخة
مبسَّطة) مع تزييف requests.post فقط، للتأكد أن سلوك السلسلة صحيح فعلياً
لا نظرياً بالتوثيق فقط. شغّله مباشرة: `python3 tests/test_provider_chain.py`
(سكربت مستقل، ليس جزءاً من التطبيق نفسه ولا يُستورَد من مكان آخر — لا
يحتاج pytest، غير مضاف بـrequirements.txt عمداً).

ملاحظة تصميم: نستبدل app.config/app.extensions بنسخة خفيفة مزيَّفة
(بدل استيراد التطبيق الحقيقي كاملاً) حتى يعمل الاختبار بدون قاعدة
بيانات/Redis/متغيرات بيئة فعلية — هذا يعني الاختبار *لا* يتحقق من
تكامل باقي التطبيق (routes/api.py مثلاً)، فقط منطق سلسلة المزوّدين
نفسها بمعزل. لو nh3/PyPDF2 غير مثبَّتين فعلياً بالبيئة اللي تشغّل فيها
هذا (نادر لأنهما بـrequirements.txt)، نزيّفهما مؤقتاً فقط — لا نلمس
النسخة الحقيقية لو كانت مثبَّتة أصلاً.
"""
import sys, types, importlib.util, json, time
from unittest.mock import patch, MagicMock, call

try:
    import nh3  # noqa: F401 — نتحقق فقط أنه قابل للاستيراد فعلاً
except ImportError:
    sys.modules['nh3'] = types.ModuleType('nh3')
    sys.modules['nh3'].clean = lambda *a, **k: a[0] if a else ''

try:
    import PyPDF2  # noqa: F401
except ImportError:
    pypdf2_mod = types.ModuleType('PyPDF2')
    pypdf2_mod.PdfReader = MagicMock
    sys.modules['PyPDF2'] = pypdf2_mod


class Config:
    GROQ_API_KEY = "fake-groq-key"
    GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
    ENABLE_CROSS_PROVIDER_FALLBACK = True
    # ⚠️ تحديث 13 سبتمبر 2026: عائلة meta الاحتياطية صارت عبر OpenRouter
    # (SambaNova/Cerebras القديمان هنا صارا يطلبان بطاقة دفع ولم يعودا
    # مستخدَمين فعلياً بـ_build_provider_chain — راجع app/config.py).
    # كانت هذي القيم القديمة غائبة تماماً عن هذا الملف الوهمي رغم أن
    # الكود الحقيقي يقرأها؛ كان هذا سيُسقِط الاختبار بـAttributeError
    # فوراً عند أي محاولة فعلية لبناء سلسلة meta — أُصلح هنا.
    OPENROUTER_API_KEY = "fake-openrouter-key"
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
    OPENROUTER_META_MODEL = "dots-studio/dots-3-note-preview:free"
    OPENROUTER_META_FALLBACK_MODEL = "nvidia/nemotron-3.5-lightning:free"
    # ⚠️ إصلاح 15 سبتمبر 2026: طبقتا NVIDIA/Gemini (راجع app/config.py،
    # أُضيفتا 14 سبتمبر) كانتا غائبتين تماماً عن هذا الـConfig الوهمي رغم
    # أن _build_provider_chain الحقيقي يقرأهما مباشرة (`if Config.NVIDIA_API_KEY:`)
    # بمجرد أي محاولة فعلية على عائلة meta — هذا كان يُسقِط كل الاختبار
    # فوراً بـAttributeError عند أول سيناريو (تأكَّد فعلياً: شغّلت الملف
    # قبل هذا الإصلاح وطلع بالضبط هذا الخطأ). None = "غير مضبوط" هنا
    # يطابق سلوك Config الحقيقي بلا هذين المفتاحين — نفس ما كانت تختبره
    # السيناريوهات أصلاً (فقط OpenRouter مفعّل لعائلة meta).
    NVIDIA_API_KEY = None
    GEMINI_API_KEY = None
    # نفس المبدأ لخط الدفاع الأخير الجديد (Cloudflare Workers AI، راجع
    # نهاية _build_provider_chain) — معطَّل افتراضياً هنا حتى لا يُغيّر
    # سلوك السيناريوهات 1-6 الحالية (كلها تفترض عدم وجوده)، ويُفعَّل
    # صراحة فقط بسيناريو 7 أسفل عبر CloudflareEnabledConfig.
    CLOUDFLARE_API_TOKEN = None
    CLOUDFLARE_API_URL = None
    CLOUDFLARE_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    CLOUDFLARE_MAX_TOKENS = 4096
    PROVIDER_COOLDOWN_BASE_SECONDS = 20
    PROVIDER_COOLDOWN_MAX_SECONDS = 600
    PROVIDER_AUTH_ERROR_COOLDOWN_SECONDS = 600
    REDIS_URL = None
    MODEL_FAMILIES = {
        'groq': {'label': 'Wadi 5.4', 'supports_tools': True},
        'meta': {'label': 'Wadi 3.3', 'supports_tools': False},
    }
    DEFAULT_MODEL_FAMILY = 'groq'


fake_config_mod = types.ModuleType('app.config')
fake_config_mod.Config = Config


class FakeLog:
    def error(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass


fake_ext_mod = types.ModuleType('app.extensions')
fake_ext_mod.log = FakeLog()

app_mod = types.ModuleType('app')
app_mod.__path__ = ['app']
sys.modules['app'] = app_mod
sys.modules['app.config'] = fake_config_mod
sys.modules['app.extensions'] = fake_ext_mod

import os
_APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app')

spec_ph = importlib.util.spec_from_file_location(
    'app.provider_health', os.path.join(_APP_DIR, 'provider_health.py'))
ph = importlib.util.module_from_spec(spec_ph)
sys.modules['app.provider_health'] = ph
spec_ph.loader.exec_module(ph)

spec_ai = importlib.util.spec_from_file_location(
    'app.ai_service', os.path.join(_APP_DIR, 'ai_service.py'))
ai = importlib.util.module_from_spec(spec_ai)
sys.modules['app.ai_service'] = ai
spec_ai.loader.exec_module(ai)


def sse_lines(text_pieces):
    lines = []
    for piece in text_pieces:
        lines.append(f'data: {json.dumps({"choices":[{"delta":{"content": piece}}]})}'.encode())
    lines.append(b'data: [DONE]')
    return lines


def make_fail_response(status_code, message, retry_after=None):
    resp = MagicMock()
    resp.ok = False
    resp.status_code = status_code
    resp.json.return_value = {"error": {"message": message}}
    resp.headers = {"Retry-After": str(retry_after)} if retry_after else {}
    resp.close = MagicMock()
    return resp


def make_success_response(text_pieces):
    resp = MagicMock()
    resp.ok = True
    resp.iter_lines.return_value = sse_lines(text_pieces)
    return resp


def run_chain(mock_side_effects, model_family='groq'):
    events = []
    with patch('time.sleep') as mock_sleep, patch('requests.post', side_effect=mock_side_effects) as mock_post:
        for kind, data in ai.stream_chat_completion(
            "openai/gpt-oss-120b",
            [{"role": "user", "content": "hi"}],
            0.7, 2048, {}, fallback_model="openai/gpt-oss-20b", model_family=model_family
        ):
            events.append((kind, data))
    return events, mock_post, mock_sleep


print("=" * 70)
print("سيناريو 1: Groq (أساسي+احتياطي) يفشلان، عائلة meta الاحتياطية")
print("(OpenRouter): Dots3-Note يزدحم ويُعاد محاولته مرة ثم يفشل،")
print("Nemotron 3.5 Lightning ينجح")
print("=" * 70)
ph._local_state.clear()
events, mock_post, mock_sleep = run_chain([
    make_fail_response(429, "Rate limit reached, please try again in 20s"),   # groq primary
    make_fail_response(429, "Rate limit reached, please try again in 20s", retry_after=45),  # groq fallback (آخر فشل Groq فعلي)
    make_fail_response(429, "dots-studio/dots-3-note-preview:free is currently overloaded, please retry shortly"),  # openrouter meta أساسي — محاولة 1 (ازدحام مؤقت)
    make_fail_response(429, "dots-studio/dots-3-note-preview:free is currently overloaded, please retry shortly"),  # openrouter meta أساسي — محاولة 2 (فشلت أيضاً)
    make_success_response(["مرحباً", " بك"]),                                  # openrouter meta احتياطي (Nemotron 3.5 Lightning) ينجح
])
kinds = [k for k, d in events]
print("تسلسل الأحداث:", kinds)
assert kinds.count('quota_switch') == 1, \
    "يجب أن يظهر quota_switch *مرة واحدة بالضبط* (عند أول انتقال حقيقي لعائلة meta) لا لكل خطوة داخلها"
quota_switch_idx = kinds.index('quota_switch')
assert kinds[quota_switch_idx - 1] not in ('chunk',), "quota_switch يجب أن يسبق أي محتوى فعلي، لا يتبعه"
switch_payload = events[quota_switch_idx][1]
print("بيانات quota_switch:", switch_payload)
assert switch_payload['retry_after'] == 45, \
    f"retry_after يجب أن يُؤخَذ من آخر فشل Groq فعلي (45 من مثال الاختبار)، حصل {switch_payload['retry_after']!r}"
assert mock_post.call_count == 5, f"توقعنا 5 استدعاءات HTTP بالضبط (تضمّن إعادة محاولة Dots3-Note)، حصل {mock_post.call_count}"
assert mock_sleep.call_count == 1, "يجب إعادة محاولة Dots3-Note مرة واحدة فقط (ازدحام مؤقت 'overloaded')"
final_text = "".join(d for k, d in events if k == 'chunk')
assert final_text == "مرحباً بك", f"النص النهائي خطأ: {final_text!r}"
assert events[-1][0] == 'done'
print("✅ نجح: مرّ بكل السلسلة (Groq×2 فشل، Dots3-Note أعاد محاولة مرة وفشل، "
      "Nemotron 3.5 Lightning نجح)، تنبيه واحد فقط بـretry_after الحقيقي من Groq")
print()

print("=" * 70)
print("سيناريو 2: كل المزوّدين يفشلون نهائياً — رسالة عربية نهائية فقط،")
print("بدون أي نص خام من أي مزوّد يصل للمستخدم")
print("=" * 70)
ph._local_state.clear()
events, mock_post, mock_sleep = run_chain([
    make_fail_response(429, "quota exceeded"),
    make_fail_response(429, "quota exceeded"),
    make_fail_response(503, "Service unavailable"),
    make_fail_response(500, "Internal error"),
])
assert events[-1][0] == 'error'
err_msg = events[-1][1]
print("رسالة الخطأ النهائية:", err_msg)
assert 'quota' not in err_msg and 'Service unavailable' not in err_msg and 'Internal error' not in err_msg, \
    "🚨 نص خام من مزوّد تسرّب للمستخدم — بالضبط الخلل اللي أصلحناه!"
assert err_msg.startswith('⚠️'), "يجب أن تكون بنفس أسلوب بقية رسائل التطبيق"
print("✅ نجح: رسالة عربية نظيفة فقط، لا تسريب لأي نص تقني خام")
print()

print("=" * 70)
print("سيناريو 3: دائرة القطع — Dots3-Note (أساسي meta) بفترة تبريد من")
print("فشل سابق، يجب تخطيه فوراً بلا أي اتصال HTTP فعلي به")
print("=" * 70)
ph._local_state.clear()
ph.record_failure('openrouter:dots-studio/dots-3-note-preview:free')  # نحاكي فشلاً سابقاً قريباً لهذا الموديل تحديداً
events, mock_post, mock_sleep = run_chain([
    make_fail_response(429, "rate limited"),   # groq primary
    make_fail_response(429, "rate limited"),   # groq fallback
    # لا استجابة مزيَّفة لـ Dots3-Note إطلاقاً — لازم يُتخطى تماماً
    make_success_response(["أهلاً"]),           # Nemotron 3.5 Lightning
])
# كلا موديلَي meta يمران بنفس رابط OpenRouter، فالتمييز بينهما هنا عبر
# حقل "model" بجسم كل طلب فعلي لا عبر الرابط (خلافاً لـSambaNova/Cerebras
# القديمين اللي كان لكل منهما رابط مختلف).
models_called = [c.kwargs.get('json', {}).get('model') for c in mock_post.call_args_list]
print("الموديلات اللي فعلاً اتصلنا بيها بالترتيب:", models_called)
assert 'dots-studio/dots-3-note-preview:free' not in models_called, \
    "🚨 اتصلنا بـDots3-Note رغم إنه بفترة تبريد!"
assert 'nvidia/nemotron-3.5-lightning:free' in models_called, \
    "يجب الوصول لـNemotron 3.5 Lightning مباشرة بعد تخطي Dots3-Note"
assert [k for k, d in events].count('quota_switch') == 1, \
    "quota_switch يجب أن يظهر مرة واحدة رغم تخطي Dots3-Note (لأن Lightning بقي أول خطوة meta وصلناها فعلياً)"
print("✅ نجح: Dots3-Note اتُخطي فوراً بلا أي round-trip ضائع، انتقلنا مباشرة لـNemotron 3.5 Lightning")
print()

print("=" * 70)
print("سيناريو 4: محتوى وصل فعلياً ثم انقطع الاتصال — يجب عدم التبديل")
print("لمزوّد آخر (يمنع رداً مخلوطاً من مصدرين)")
print("=" * 70)


def broken_stream_response():
    resp = MagicMock()
    resp.ok = True

    def _iter():
        yield f'data: {json.dumps({"choices":[{"delta":{"content": "جزء "}}]})}'.encode()
        raise ConnectionError("connection reset mid-stream")
    resp.iter_lines.return_value = _iter()
    return resp


ph._local_state.clear()
events = []
with patch('time.sleep'), patch('requests.post', side_effect=[broken_stream_response()]):
    for kind, data in ai.stream_chat_completion(
        "openai/gpt-oss-120b", [{"role": "user", "content": "hi"}], 0.7, 2048, {}, fallback_model=None
    ):
        events.append((kind, data))
kinds = [k for k, d in events]
print("تسلسل الأحداث:", kinds)
assert 'quota_switch' not in kinds, "🚨 حاول التبديل لمزوّد آخر بعد محتوى جزئي فعلي — رد سيكون مخلوطاً!"
assert kinds == ['chunk', 'done'], f"توقعنا chunk واحد ثم done فقط، حصل {kinds}"
print("✅ نجح: توقف بمحتوى جزئي نظيف بدل التبديل لمصدر آخر")
print()

print("=" * 70)
print("سيناريو 5: model_family='meta' صريح — يجب عدم لمس Groq إطلاقاً")
print("ولا إطلاق quota_switch (لم يكن احتياطياً، كان الاختيار من البداية)")
print("=" * 70)
ph._local_state.clear()
events, mock_post, mock_sleep = run_chain([
    make_success_response(["أهلاً", " بالعائلة"]),   # Dots3-Note مباشرة (أول خطوة بعائلة meta)
], model_family='meta')
kinds = [k for k, d in events]
print("تسلسل الأحداث:", kinds)
urls_called = [c.args[0] if c.args else c.kwargs.get('url') for c in mock_post.call_args_list]
print("الروابط اللي اتصلنا بيها:", urls_called)
assert not any('groq' in u for u in urls_called), "🚨 اتصلنا بـGroq رغم اختيار المستخدم الصريح لعائلة Meta!"
assert 'quota_switch' not in kinds, "🚨 أطلقنا تنبيه 'تبديل' رغم إن المستخدم اختار Meta من البداية أصلاً"
assert mock_post.call_count == 1, f"توقعنا اتصالاً واحداً فقط (Dots3-Note مباشرة)، حصل {mock_post.call_count}"
final_text = "".join(d for k, d in events if k == 'chunk')
assert final_text == "أهلاً بالعائلة"
print("✅ نجح: عائلة meta الصريحة تروح مباشرة لـDots3-Note/Nemotron 3.5 Lightning بلا لمس Groq وبلا تنبيه زائف")
print()

print("=" * 70)
print("سيناريو 6: كل عائلة meta الصريحة تفشل — رسالة تذكر Wadi 3.3 تحديداً")
print("لا 'الأساسي والاحتياطية' (Groq لم يُجرَّب أصلاً بهذا السيناريو)")
print("=" * 70)
ph._local_state.clear()
events, mock_post, mock_sleep = run_chain([
    make_fail_response(503, "down"),
    make_fail_response(500, "down"),
], model_family='meta')
assert events[-1][0] == 'error'
err_msg = events[-1][1]
print("رسالة الخطأ النهائية:", err_msg)
assert 'Wadi 3.3' in err_msg, "الرسالة يجب تذكر اسم العائلة المختارة صراحة (Wadi 3.3)"
assert 'الأساسي والاحتياطية' not in err_msg, "🚨 رسالة مضلِّلة — Groq لم يُجرَّب إطلاقاً بهذا المسار"
print("✅ نجح: رسالة خطأ مخصَّصة وصحيحة لعائلة meta الصريحة")
print()
print("=" * 70)
print("سيناريو 7: Cloudflare Workers AI كخط دفاع أخير — Groq (أساسي+احتياطي)")
print("وعائلة meta الاحتياطية بالكامل يفشلون، Cloudflare ينقذ الرسالة")
print("=" * 70)


class CloudflareEnabledConfig(Config):
    """نفس الـConfig الأساسي + Cloudflare مفعَّل — سيناريو مستقل بدل تلويث
    الـ7 سيناريوهات السابقة (كلها تفترض عدم وجوده)."""
    CLOUDFLARE_API_TOKEN = "fake-cloudflare-token"
    CLOUDFLARE_API_URL = "https://api.cloudflare.com/client/v4/accounts/fake-account/ai/v1/chat/completions"


ai.Config = CloudflareEnabledConfig  # الوحدة تستورد Config وقت التحميل — نستبدل المرجع مباشرة لهذا السيناريو فقط
ph._local_state.clear()
events, mock_post, mock_sleep = run_chain([
    make_fail_response(429, "quota exceeded"),   # groq primary
    make_fail_response(429, "quota exceeded"),   # groq fallback
    make_fail_response(503, "down"),             # openrouter meta أساسي (Dots3-Note)
    make_fail_response(500, "down"),             # openrouter meta احتياطي (Nemotron 3.5 Lightning)
    make_success_response(["تم", " الإنقاذ"]),     # Cloudflare Workers AI — آخر خطوة بالسلسلة
])
kinds = [k for k, d in events]
print("تسلسل الأحداث:", kinds)
last_call = mock_post.call_args_list[-1]
print("آخر رابط اتُصل به:", last_call.args[0] if last_call.args else last_call.kwargs.get('url'))
assert mock_post.call_count == 5, f"توقعنا 5 استدعاءات (Groq×2 + meta×2 + Cloudflare)، حصل {mock_post.call_count}"
called_url = last_call.args[0] if last_call.args else last_call.kwargs.get('url')
assert called_url == CloudflareEnabledConfig.CLOUDFLARE_API_URL, \
    f"🚨 الخطوة الأخيرة يجب أن تكون Cloudflare تحديداً، اتصلنا بـ{called_url}"
called_model = last_call.kwargs.get('json', {}).get('model')
assert called_model == CloudflareEnabledConfig.CLOUDFLARE_MODEL, \
    f"🚨 موديل خاطئ بالطلب الأخير: {called_model!r}"
final_text = "".join(d for k, d in events if k == 'chunk')
assert final_text == "تم الإنقاذ", f"النص النهائي خطأ: {final_text!r}"
assert events[-1][0] == 'done'
assert kinds.count('quota_switch') == 1, \
    "quota_switch يجب أن يظهر مرة واحدة فقط (عند أول انتقال لعائلة meta) — Cloudflare خطوة صامتة لا تستاهل تنبيهاً ثانياً"
print("✅ نجح: كل شيء فشل (Groq×2 + meta×2)، Cloudflare أنقذ الرسالة كآخر محاولة حقيقية")
print()

print("=" * 70)
print("سيناريو 8: حتى Cloudflare يفشل — رسالة عربية نهائية نظيفة فقط،")
print("بدون تسريب أي نص خام من Cloudflare للمستخدم")
print("=" * 70)
ph._local_state.clear()
events, mock_post, mock_sleep = run_chain([
    make_fail_response(429, "quota exceeded"),
    make_fail_response(429, "quota exceeded"),
    make_fail_response(503, "down"),
    make_fail_response(500, "down"),
    make_fail_response(500, "Internal Server Error — Cloudflare raw message"),  # Cloudflare يفشل هو الآخر
])
assert events[-1][0] == 'error'
err_msg = events[-1][1]
print("رسالة الخطأ النهائية:", err_msg)
assert 'Cloudflare raw message' not in err_msg, \
    "🚨 نص خام من Cloudflare تسرّب للمستخدم!"
assert err_msg.startswith('⚠️')
print("✅ نجح: رسالة عربية نظيفة حتى بعد فشل كل الطبقات السبع (Groq×2 + meta×2 + Cloudflare)")
print()

ai.Config = Config  # نعيد المرجع الأصلي — لا يؤثر على سيناريوهات سابقة (كلها نُفِّذت أصلاً) لكن نظافة عامة

print("=" * 70)
print("كل السيناريوهات نجحت ✅")
print("=" * 70)
