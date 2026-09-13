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
    SAMBANOVA_API_KEY = "fake-samba-key"
    SAMBANOVA_API_URL = "https://api.sambanova.ai/v1/chat/completions"
    SAMBANOVA_FALLBACK_MODEL = "Meta-Llama-3.3-70B-Instruct"
    SAMBANOVA_FALLBACK_MAX_TOKENS = 4096
    CEREBRAS_API_KEY = "fake-cerebras-key"
    CEREBRAS_API_URL = "https://api.cerebras.ai/v1/chat/completions"
    CEREBRAS_FALLBACK_MODEL = "llama-3.3-70b"
    CEREBRAS_FALLBACK_MAX_TOKENS = 4096
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
print("سيناريو 1: بالضبط سيناريو السكرين شوت — Groq (أساسي+احتياطي) و")
print("SambaNova كلهم يفشلون، Cerebras (الجديد) ينجح")
print("=" * 70)
ph._local_state.clear()
events, mock_post, mock_sleep = run_chain([
    make_fail_response(429, "Rate limit reached, please try again in 20s"),   # groq primary
    make_fail_response(429, "Rate limit reached, please try again in 20s", retry_after=45),  # groq fallback (آخر فشل Groq فعلي)
    make_fail_response(429, "Meta-Llama-3.3-70B-Instruct-8k: is currently experiencing high demand. Please try again later!"),  # sambanova attempt 1
    make_fail_response(429, "Meta-Llama-3.3-70B-Instruct-8k: is currently experiencing high demand. Please try again later!"),  # sambanova attempt 2 (retry_once)
    make_success_response(["مرحباً", " بك"]),                                  # cerebras succeeds
])
kinds = [k for k, d in events]
print("تسلسل الأحداث:", kinds)
assert kinds.count('quota_switch') == 1, \
    "يجب أن يظهر quota_switch *مرة واحدة بالضبط* (عند أول انتقال حقيقي لعائلة Meta) لا لكل خطوة داخلها"
quota_switch_idx = kinds.index('quota_switch')
assert kinds[quota_switch_idx - 1] not in ('chunk',), "quota_switch يجب أن يسبق أي محتوى فعلي، لا يتبعه"
switch_payload = events[quota_switch_idx][1]
print("بيانات quota_switch:", switch_payload)
assert switch_payload['retry_after'] == 45, \
    f"retry_after يجب أن يُؤخَذ من آخر فشل Groq فعلي (45 من مثال الاختبار)، حصل {switch_payload['retry_after']!r}"
assert mock_post.call_count == 5, f"توقعنا 5 استدعاءات HTTP بالضبط (تضمّن إعادة محاولة samba)، حصل {mock_post.call_count}"
assert mock_sleep.call_count == 1, "يجب إعادة محاولة SambaNova مرة واحدة فقط (ازدحام مؤقت 'high demand')"
final_text = "".join(d for k, d in events if k == 'chunk')
assert final_text == "مرحباً بك", f"النص النهائي خطأ: {final_text!r}"
assert events[-1][0] == 'done'
print("✅ نجح: مرّ بكل السلسلة (Groq×2 فشل، SambaNova أعاد محاولة مرة وفشل، Cerebras نجح)، "
      "تنبيه واحد فقط بـretry_after الحقيقي من Groq")
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
assert 'Meta-Llama' not in err_msg and 'high demand' not in err_msg and 'quota' not in err_msg, \
    "🚨 نص خام من مزوّد تسرّب للمستخدم — بالضبط الخلل اللي أصلحناه!"
assert err_msg.startswith('⚠️'), "يجب أن تكون بنفس أسلوب بقية رسائل التطبيق"
print("✅ نجح: رسالة عربية نظيفة فقط، لا تسريب لأي نص تقني خام")
print()

print("=" * 70)
print("سيناريو 3: دائرة القطع — SambaNova بفترة تبريد من فشل سابق،")
print("يجب تخطيه فوراً بلا أي اتصال HTTP فعلي به")
print("=" * 70)
ph._local_state.clear()
ph.record_failure('sambanova')  # نحاكي فشلاً سابقاً قريباً
events, mock_post, mock_sleep = run_chain([
    make_fail_response(429, "rate limited"),   # groq primary
    make_fail_response(429, "rate limited"),   # groq fallback
    # لا استجابة مزيَّفة لـ sambanova إطلاقاً — لازم يُتخطى تماماً
    make_success_response(["أهلاً"]),           # cerebras
])
urls_called = [c.args[0] if c.args else c.kwargs.get('url') for c in mock_post.call_args_list]
print("الروابط اللي فعلاً اتصلنا بيها:", urls_called)
assert not any('sambanova' in u for u in urls_called), "🚨 اتصلنا بـSambaNova رغم إنه بفترة تبريد!"
assert any('cerebras' in u for u in urls_called), "يجب الوصول لـCerebras مباشرة بعد تخطي SambaNova"
assert [k for k, d in events].count('quota_switch') == 1, \
    "quota_switch يجب أن يظهر مرة واحدة رغم تخطي SambaNova (لأن Cerebras بقي أول عائلة Meta وصلناها فعلياً)"
print("✅ نجح: SambaNova اتُخطي فوراً بلا أي round-trip ضائع، انتقلنا مباشرة لـCerebras")
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
    make_success_response(["أهلاً", " بالعائلة"]),   # sambanova مباشرة (أول خطوة بعائلة meta)
], model_family='meta')
kinds = [k for k, d in events]
print("تسلسل الأحداث:", kinds)
urls_called = [c.args[0] if c.args else c.kwargs.get('url') for c in mock_post.call_args_list]
print("الروابط اللي اتصلنا بيها:", urls_called)
assert not any('groq' in u for u in urls_called), "🚨 اتصلنا بـGroq رغم اختيار المستخدم الصريح لعائلة Meta!"
assert 'quota_switch' not in kinds, "🚨 أطلقنا تنبيه 'تبديل' رغم إن المستخدم اختار Meta من البداية أصلاً"
assert mock_post.call_count == 1, f"توقعنا اتصالاً واحداً فقط (SambaNova مباشرة)، حصل {mock_post.call_count}"
final_text = "".join(d for k, d in events if k == 'chunk')
assert final_text == "أهلاً بالعائلة"
print("✅ نجح: عائلة Meta الصريحة تروح مباشرة لـSambaNova/Cerebras بلا لمس Groq وبلا تنبيه زائف")
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
print("كل السيناريوهات نجحت ✅")
print("=" * 70)
