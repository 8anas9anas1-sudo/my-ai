"""
كل التكامل مع Groq API: الشخصيات (MODE_PROMPTS)، البث الحي (streaming)،
توليد الصور، تحليل الصور (vision)، تنسيق الرد، واستخراج نص PDF.
"""
import io
import re
import json
import hashlib
import html as html_module

import requests
import bleach
import PyPDF2

from app.config import Config
from app.extensions import log

MODE_PROMPTS = {

    'fast': """أنت Wadi — ذكاء اصطناعي متطور صنعه المهندس Anas Wadi من ليبيا 🇱🇾.

شخصيتك:
- ذكي، واضح، مباشر، وفيك شخصية حقيقية — مش مجرد آلة بتجيب إجابات.
- تقرأ المزاج والطاقة من الرسالة وتتكيف معها.
- إذا الشخص متحمس → أنت متحمس. إذا بيفكر → أنت معاه في التفكير. إذا حزين → هادئ وإنساني.
- ردودك فيها روح وحضور — مش كلام بارد ومعلب.

قواعد الرد:
- افهم المقصد الحقيقي وراء الكلام، مش بس الكلمات.
- استخدم **Bold** للمصطلحات والأفكار المهمة.
- نظم الإجابات الطويلة بعناوين وفقرات واضحة.
- لا تطول بدون قيمة — كل كلمة تكون لها وزن.
- تذكر سياق المحادثة واستخدمه في ردودك.
- إذا الموضوع مثير → ابدأ بجملة تشعل الاهتمام.
- لا تبدأ كل رد بـ "بالطبع" أو "بالتأكيد" — تنوع في البدايات.""",

    'thinker': """أنت Wadi في وضع التفكير العميق — مفكر استراتيجي وخبير تحليلي صنعه Anas Wadi.

شخصيتك:
- تعشق المشاكل المعقدة — كأنها ألغاز تستحق الحل.
- تفكر بصوت عالٍ، تريح الشخص وتشعره أنك معاه في الرحلة.
- كل تحليل عندك فيه عمق وزاوية نظر مختلفة.

قواعد الرد:
- ابدأ بفهم المشكلة قبل أي شيء ثم حللها خطوة بخطوة.
- قدم الحلول من الأقوى للأضعف مع التبرير.
- استخدم ## للعناوين الرئيسية و### للفرعية.
- دائماً أضف **الخلاصة** في النهاية — مختصرة وقوية.
- اكتشف الأبعاد الخفية التي لم يسألها الشخص لكنها مهمة.""",

    'funny': """أنت Wadi في وضع الفكاهة — ذكي، خفيف الظل، ومضحك بشكل طبيعي. صنعه Anas Wadi 😄

شخصيتك:
- روحك خفيفة لكن عقلك حاضر — الفكاهة عندك ذكية مش سطحية.
- تستطيع تحول أي موضوع لتجربة ممتعة دون أن تفقد الدقة.
- ردك يخلي الشخص يبتسم أو يضحك قبل ما يقرأ الإجابة الكاملة.

قواعد الرد:
- ابدأ بتعليق فكاهي أو ملاحظة طريفة، ثم أعط الجواب الحقيقي.
- استخدم الإيموجي بذكاء في اللحظات المناسبة 😂🎯✨
- لا تبالغ في الفكاهة على حساب الدقة — المعلومة صح دائماً.
- تتكيف مع نبرة الشخص — إذا بيمزح خذ المسافة الصحيحة.""",

    'creative': """أنت Wadi المبدع — فنان، شاعر، وعقل خلاق. صنعه Anas Wadi 🎨

شخصيتك:
- ترى العالم بعيون مختلفة وتعبر عنه بطريقة تخلي الناس يتوقفون ويفكرون.
- الكلمات عندك ليست أدوات — هي تجارب حسية.
- تشعل خيال الشخص وتأخذه لمكان لم يتوقعه.

قواعد الرد:
- أجب بأسلوب أدبي راقٍ مع استعارات وتشبيهات جميلة.
- لطلبات الرسم: ترجم الوصف لإنجليزي دقيق وشاعري يلتقط الجوهر.
- استخدم الصور الذهنية والإيقاع في الكتابة.
- كل رد يكون تجربة لا مجرد معلومة.""",

    'coder': """أنت Wadi المبرمج — Senior Software Engineer متخصص ومحترف. صنعه Anas Wadi 💻

## هويتك كمهندس:
أنت مهندس برمجيات أول (Senior Engineer) بخبرة عميقة في بناء أنظمة إنتاجية حقيقية. تفكر كمعمارية أنظمة (System Architect) وتكتب كود يستحق أن يكون في Production.

## خبرتك التقنية الكاملة:
**Backend:** Python (Flask, Django, FastAPI), Node.js (Express), REST APIs, GraphQL, WebSockets
**Frontend:** React, TypeScript, Next.js, Vue.js, HTML5/CSS3/JS, Tailwind CSS, SCSS
**Databases:** PostgreSQL, MySQL, SQLite, MongoDB, Redis — قواعد بيانات محسّنة وindexed بشكل صحيح
**DevOps & Cloud:** Docker, CI/CD, Nginx, Gunicorn, Render, Railway, Vercel, GitHub Actions
**AI/ML:** APIs (OpenAI, Groq, Anthropic, Gemini), LangChain, Prompt Engineering متقدم
**Security:** Authentication (JWT, OAuth2, Session), Hashing, Rate Limiting, Input Validation, CSRF
**Tools:** Git, Linux/Bash, Testing (pytest, Jest), API Documentation

## قواعد الكود الذهبية — لا تنتهكها أبداً:
1. **اكتب الكود كاملاً دائماً** — لا تكتب "// بقية الكود هنا" أو "..." أو تقطع الكود في المنتصف
2. **ملفات كاملة** — إذا طُلب منك ملف، أرسل الملف من أول سطر لآخر سطر
3. **Comments بالعربية أو الإنجليزية** — شرح كل block مهم
4. **Error Handling في كل مكان** — try/catch، استثناءات واضحة، رسائل خطأ مفيدة
5. **Type hints في Python** — أضف annotation للـ functions والـ variables المهمة
6. **لا Magic Numbers** — استخدم constants مسماة واضحة
7. **DRY Principle** — لا تكرر الكود، استخدم functions وclasses

## طريقة عملك عند طلب مشروع كامل:
عندما يطلب المستخدم مشروعاً (موقع، API، بوت، تطبيق)، قدّم:

### 1. هيكل المشروع أولاً:
```
project-name/
├── app.py / main.py / index.js
├── requirements.txt / package.json
├── config.py
├── models/ أو routes/ أو components/
├── templates/ أو static/
├── tests/
└── README.md
```

### 2. ثم كل ملف كامل بالترتيب:
- الملف الرئيسي أولاً
- الـ Config والـ Environment
- الـ Models/Database
- الـ Routes/Controllers
- الـ Templates/Frontend
- الـ Tests
- الـ README مع تعليمات التشغيل

### 3. في نهاية كل مشروع أضف:
- كيفية تشغيل المشروع محلياً
- متغيرات البيئة المطلوبة
- كيفية الـ Deploy

## عند تحليل الأكواد الموجودة:
- **اقرأ كل السياق** قبل أي تعديل
- **حدد المشكلة بدقة** — السطر والسبب والحل
- **لا تكسر ما يعمل** — فقط صلح المشكلة
- **اقترح Refactoring** إذا رأيت تحسينات واضحة
- **نبّه على Security Issues** فوراً إذا وجدت

## أسلوب تقديم الكود:
- دائماً ```python أو ```javascript أو ```html مع تحديد اللغة
- أضف تعليقاً في أول الملف يشرح الغرض منه
- استخدم separators واضحة بين الأقسام: # ─── اسم القسم ──────
- اكتب docstrings للـ functions المهمة

## عند وجود خطأ أو Bug:
1. اشرح **لماذا** حدث الخطأ
2. أعط الحل المباشر مع الكود الكامل
3. اشرح **كيف تتجنبه** مستقبلاً
4. قدم test case يثبت أن الحل يعمل

## Production-Level Best Practices التي تطبقها دائماً:
- Environment variables للـ secrets (لا hardcoded passwords أبداً)
- Database connection pooling وإغلاق الاتصالات
- Logging مناسب (ليس فقط print)
- Input validation وsanitization
- Rate limiting للـ APIs
- HTTPS وsecurity headers
- Graceful error responses (لا stack traces للمستخدم)

تذكر: أنت لا تكتب "أمثلة توضيحية" — أنت تكتب كوداً جاهزاً للتشغيل الفعلي.""",

    'writer': """أنت Wadi الكاتب — محرر لغوي وأديب متمكن. صنعه Anas Wadi ✍️

شخصيتك:
- تعشق اللغة وتعاملها باحترام وإبداع.
- تشعر بالفرق بين الكلمة الصحيحة والكلمة المثالية.
- كل نص تكتبه يحمل روحاً وهوية واضحة.

قواعد الرد:
- اهتم بالأسلوب والبلاغة والإيقاع الداخلي للجمل.
- صحح الأخطاء اللغوية بذكاء واشرح السبب.
- استخدم علامات الترقيم بشكل يخدم المعنى.
- قدم نصوصاً متماسكة تجعل القارئ يريد الاستمرار.
- اعرض البديل الأفضل دائماً مع الشرح."""
}


def _normalize_for_identity_match(text):
    text = (text or '').strip().lower()
    text = re.sub(r'[؟?!.,،؛:]+$', '', text).strip()
    return text


def is_identity_question(user_message):
    """
    يتحقق أن سؤال الهوية هو محتوى الرسالة كاملاً (أو رسالة قصيرة جداً
    تتمحور حوله)، لا مجرد عبارة عابرة داخل سؤال أطول ومختلف تماماً —
    يمنع اختطاف أسئلة حقيقية تحتوي مصادفة على عبارة مثل "من انت" ضمن
    سياق أوسع (مثل سؤال عن مقابلة عمل يذكرها كجزء من السؤال).
    """
    text = _normalize_for_identity_match(user_message)
    if not text:
        return False
    if text in Config.IDENTITY_TRIGGERS:
        return True
    if len(text.split()) <= Config.IDENTITY_MAX_WORDS:
        return any(trigger in text for trigger in Config.IDENTITY_TRIGGERS)
    return False


def get_system_prompt(mode, user_message):
    if is_identity_question(user_message):
        return ("أجب بالضبط: أنا Wadi، مساعد ذكاء اصطناعي طوّره المهندس "
                "Anas Wadi من ليبيا 🇱🇾. لا تضف أي معلومة أخرى.")
    return MODE_PROMPTS.get(mode, MODE_PROMPTS['fast'])


def is_image_generation_request(user_message, has_file=False):
    """
    يتحقق من نية توليد صورة جديدة عبر عبارات أمر صريحة بحدود كلمة
    حقيقية، بدل أي ظهور لكلمة "صورة" بأي سياق (كانت تخطف رسائل مثل
    "اشرحلي مفهوم الصورة الذهنية"، أو أخطر من هذا: "حلل هذه الصورة"
    مع صورة مرفوعة فعلياً للتحليل). وجود ملف مرفق يُبطل هذا المسار
    كلياً — تحليل الملف المرفق له الأولوية دائماً على توليد صورة جديدة.
    """
    if has_file:
        return False
    text = (user_message or '').strip().lower()
    if not text:
        return False
    return any(re.search(p, text) for p in Config.IMAGE_GENERATION_TRIGGERS)


def extract_image_prompt(user_message):
    """
    يشيل عبارة الأمر فقط (مثل "ارسم صورة:" أو "اعطيني صورة") من بداية
    الرسالة، بدل حذف كل ظهور لكلمة "صورة" بالنص بالكامل — الطريقة
    القديمة كانت تحذف وصفاً حقيقياً لو ذكر المستخدم "صورة" أكثر من
    مرة ضمن وصفه (مثال: "ارسم صورة تجمع بين لوحة فنية وصورة واقعية").
    """
    text = (user_message or '').strip()
    colon_match = re.match(r'^(?:ارسم(?:\s+صورة)?|draw)\s*:\s*(.+)$', text, re.IGNORECASE)
    if colon_match:
        return colon_match.group(1).strip() or text

    stripped = re.sub(
        r'^\s*(?:ارسم|اعطيني|ابغى|ابي|اريد|ولّد|ولد|انشئ|اصنع|صمم|سوي|اعمل)\s+(?:لي\s+)?',
        '', text, flags=re.IGNORECASE
    )
    stripped = re.sub(r'^\s*draw\s+', '', stripped, flags=re.IGNORECASE)
    stripped = stripped.strip()
    return stripped or text


# ─── توليد الصور ────────────────────────────────────────────────
def generate_image(prompt):
    clean_prompt = prompt.strip()
    encoded = requests.utils.quote(clean_prompt)
    # seed مبني على hash المحتوى (وليس hash() المدمجة في بايثون، التي
    # تتغير عشوائياً مع كل إعادة تشغيل) — نفس النص يعطي نفس seed دائماً.
    seed = int(hashlib.sha256(clean_prompt.encode()).hexdigest(), 16) % 99999
    primary_url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1024&height=1024&model=flux&enhance=true&nologo=true&seed={seed}"
    )
    fallback_url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1024&height=768&nologo=true"
    )
    return primary_url, fallback_url


# ─── تنسيق الرد (Markdown خفيف → HTML مُعقّم) ──────────────────
def format_response(text):
    def replace_code_block(m):
        lang = m.group(1) or 'code'
        code_content = m.group(2).strip()
        escaped = html_module.escape(code_content)
        return f'<pre data-lang="{lang}"><code class="lang-{lang}">{escaped}</code></pre>'

    text = re.sub(r'```(\w+)?\n(.*?)```', replace_code_block, text, flags=re.DOTALL)
    text = re.sub(r'`([^`\n]+?)`', r'<code>\1</code>', text)

    text = re.sub(r'^### (.+)$', r'<h4>\1</h4>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)

    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)

    text = re.sub(r'^---+$', r'<hr>', text, flags=re.MULTILINE)

    def convert_list(m):
        items = re.findall(r'^[-*•] (.+)$', m.group(0), re.MULTILINE)
        return '<ul>' + ''.join(f'<li>{i}</li>' for i in items) + '</ul>'
    text = re.sub(r'(^[-*•] .+$\n?)+', convert_list, text, flags=re.MULTILINE)

    def convert_ol(m):
        items = re.findall(r'^\d+\. (.+)$', m.group(0), re.MULTILINE)
        return '<ol>' + ''.join(f'<li>{i}</li>' for i in items) + '</ol>'
    text = re.sub(r'(^\d+\. .+$\n?)+', convert_ol, text, flags=re.MULTILINE)

    text = re.sub(r'\n{2,}', '</p><p>', text)
    text = f'<p>{text}</p>'
    text = text.replace('<p></p>', '').replace('<p><h', '<h')
    text = text.replace('</h2></p>', '</h2>').replace('</h3></p>', '</h3>').replace('</h4></p>', '</h4>')
    text = text.replace('<p><pre', '<pre').replace('</pre></p>', '</pre>')
    text = text.replace('<p><ul>', '<ul>').replace('</ul></p>', '</ul>')
    text = text.replace('<p><ol>', '<ol>').replace('</ol></p>', '</ol>')
    text = text.replace('<p><hr>', '<hr>').replace('<hr></p>', '<hr>')

    allowed_tags = ['h2', 'h3', 'h4', 'p', 'strong', 'em', 'ul', 'ol', 'li', 'code', 'pre', 'br', 'hr']
    return bleach.clean(text, tags=allowed_tags, attributes={'pre': ['data-lang'], 'code': ['class']}, strip=True)


# ─── استخراج نص PDF ─────────────────────────────────────────────
def extract_pdf_text(pdf_file):
    try:
        reader = PyPDF2.PdfReader(pdf_file)
        text = ""
        for page in reader.pages[:20]:
            t = page.extract_text()
            if t:
                text += t + "\n"
        return text[:15000]
    except Exception as e:
        return f"خطأ في قراءة PDF: {str(e)}"


def supports_builtin_tools(model):
    """
    أدوات Groq المدمجة (browser_search / code_interpreter) مدعومة حالياً
    فقط لنماذج gpt-oss. نتحقق منها قبل إضافتها لأي طلب — حتى لو غيّرنا
    GROQ_MODELS مستقبلاً لموديل لا يدعمها، لا نرسل أداة مرفوضة بالخطأ.
    """
    return bool(model) and model.startswith('openai/gpt-oss')


# ─── البث الحي (Streaming) ──────────────────────────────────────
def stream_groq_completion(model, messages, temperature, max_tokens, extra_params, fallback_model=None):
    """
    مولّد Python يبث القطع (chunks) القادمة من Groq أولاً بأول (Server-Sent
    Events)، بدل انتظار الرد كاملاً. يrield tuples بصيغة (نوع, بيانات):
      ('chunk', 'نص جزئي')  — كل ما وصل جزء جديد من الرد
      ('error', 'رسالة الخطأ')
      ('done', 'النص الكامل')  — دائماً آخر عنصر يُرجَع
    """
    def _stream(m):
        return requests.post(
            Config.GROQ_API_URL,
            headers={"Authorization": f"Bearer {Config.GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": m, "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature, "top_p": 0.92, "stream": True,
                **extra_params
            },
            stream=True, timeout=90
        )

    try:
        resp = _stream(model)
        if resp.status_code == 429 and fallback_model:
            resp.close()
            resp = _stream(fallback_model)

        if not resp.ok:
            try:
                err = resp.json()
            except Exception:
                err = {}
            yield ('error', err.get('error', {}).get('message', 'خطأ غير معروف'))
            return

        full_text = ""
        for line in resp.iter_lines():
            if not line:
                continue
            decoded = line.decode('utf-8')
            if not decoded.startswith('data: '):
                continue
            payload = decoded[len('data: '):]
            if payload.strip() == '[DONE]':
                break
            try:
                chunk = json.loads(payload)
            except Exception:
                continue
            delta = (chunk.get('choices') or [{}])[0].get('delta', {}).get('content')
            if delta:
                full_text += delta
                yield ('chunk', delta)
        yield ('done', full_text)

    except requests.Timeout:
        yield ('error', 'انتهت مهلة الاتصال. حاول مجدداً.')
    except Exception as e:
        log.error(f"خطأ في الاتصال ببث Groq: {e}")
        yield ('error', f'خطأ في الاتصال: {str(e)}')


def call_vision_model(messages):
    """
    طلب عادي (غير مبثوث) لتحليل صورة — رد واحد كامل، ليس بطول محادثة
    نصية عادة. نفس منطق fallback الموجود بـstream_groq_completion
    بالضبط: إعادة محاولة واحدة بموديل رؤية بديل مستقل عند 429 فقط،
    وحماية تحليل JSON من رد غير متوقع (صفحة خطأ HTML من بروكسي مثلاً).
    """
    def _call(model):
        return requests.post(
            Config.GROQ_API_URL,
            headers={"Authorization": f"Bearer {Config.GROQ_API_KEY}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "max_tokens": 2048},
            timeout=60
        )

    try:
        resp = _call(Config.GROQ_VISION_MODEL)
        if resp.status_code == 429 and Config.GROQ_VISION_MODEL_FALLBACK:
            resp = _call(Config.GROQ_VISION_MODEL_FALLBACK)

        try:
            result = resp.json()
        except Exception:
            result = {}

        if resp.ok and result.get("choices"):
            return result["choices"][0]["message"]["content"], None
        return None, result.get('error', {}).get('message', 'خطأ في تحليل الصورة')

    except requests.Timeout:
        return None, 'انتهت مهلة الاتصال. حاول مجدداً.'
    except Exception as e:
        log.error(f"خطأ في الاتصال بموديل الرؤية: {e}")
        return None, f'خطأ في الاتصال: {str(e)}'


# ─── ذاكرة طويلة المدى: تلخيص المحادثات الطويلة ─────────────────
def summarize_conversation(existing_summary, new_messages):
    """
    يطلب من موديل خفيف تلخيصاً محدّثاً يدمج الملخص الحالي (إن وُجد) مع
    مقتطف جديد من المحادثة — بدل فقدان بداية المحادثات الطويلة بالكامل
    عند تجاوزها نافذة السياق. لا يستخدم أبداً في المسار الحرج لرد
    المستخدم — يُستدعى فقط من خيط خلفي منفصل (انظر app/memory.py).

    يرجع نص التلخيص، أو None عند أي فشل (شبكة، مهلة، رد فارغ...) —
    الفراغ هنا مقصود ومتوقَّع، المستدعي مسؤول عن عدم استخدام نتيجة فارغة.
    """
    if not new_messages:
        return None

    convo_text = "\n\n".join(
        f"المستخدم: {m['user']}\nالمساعد: {m['ai'][:800]}"
        for m in new_messages
    )
    prompt_parts = []
    if existing_summary:
        prompt_parts.append(f"الملخص الحالي للمحادثة حتى الآن:\n{existing_summary}")
    prompt_parts.append(f"مقتطف جديد من المحادثة يحتاج دمجه بالملخص:\n{convo_text}")
    prompt_parts.append(
        "اكتب ملخصاً محدّثاً موجزاً (فقرة أو فقرتين كحد أقصى) يحافظ على "
        "الحقائق والقرارات والسياق المهم فقط، بدون تفاصيل زائدة أو حشو."
    )
    user_prompt = "\n\n".join(prompt_parts)

    try:
        resp = requests.post(
            Config.GROQ_API_URL,
            headers={"Authorization": f"Bearer {Config.GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": Config.SUMMARY_MODEL,
                "messages": [
                    {"role": "system", "content": "أنت أداة تلخيص محادثات دقيقة ومختصرة. لا تضف رأياً أو تعليقاً، فقط لخّص."},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": Config.SUMMARY_MAX_TOKENS,
                "temperature": 0.3,
            },
            timeout=30
        )
        if not resp.ok:
            return None
        result = resp.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content")
        return content.strip() if content else None
    except Exception as e:
        log.error(f"خطأ أثناء استدعاء موديل التلخيص: {e}")
        return None


# ─── الصوت: تحويل كلام لنص (STT) ─────────────────────────────────
def transcribe_audio(audio_bytes, filename, content_type):
    """
    يحوّل تسجيلاً صوتياً لنص عبر Whisper على Groq. يرجع (النص, رسالة
    الخطأ) — العنصر غير المستخدم يكون None دائماً، حتى يكون واضحاً
    للمستدعي أيّ الحالتين حصلت.
    """
    if not audio_bytes:
        return None, 'التسجيل فارغ'
    try:
        resp = requests.post(
            Config.GROQ_STT_URL,
            headers={"Authorization": f"Bearer {Config.GROQ_API_KEY}"},
            files={"file": (filename or "audio.webm", audio_bytes, content_type or "audio/webm")},
            data={"model": Config.STT_MODEL},
            timeout=60
        )
        if not resp.ok:
            try:
                err = resp.json().get('error', {}).get('message', f'HTTP {resp.status_code}')
            except Exception:
                err = f'HTTP {resp.status_code}'
            return None, err
        result = resp.json()
        text = (result.get('text') or '').strip()
        if not text:
            return None, 'لم يتم التعرف على أي كلام بالتسجيل'
        return text, None
    except requests.Timeout:
        return None, 'انتهت مهلة الاتصال'
    except Exception as e:
        log.error(f"خطأ أثناء تحويل الصوت لنص: {e}")
        return None, str(e)


# ─── الصوت: تحويل نص لكلام (TTS) ─────────────────────────────────
def strip_markdown_for_speech(text):
    """
    يشيل رموز Markdown وكتل الأكواد والروابط قبل النطق — قراءة ** أو #
    أو رابط طويل حرفياً بصوت عالٍ تجربة سيئة، وكتل الكود عادة طويلة
    وغير مفيدة منطوقة.
    """
    text = re.sub(r'```.*?```', ' كود برمجي. ', text, flags=re.DOTALL)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # رابط ماركداون [نص](رابط) — نبقي النص المقروء بس، الرابط نفسه غير
    # مفيد منطوقاً. أي رابط خام متبقٍ بعدها (بدون صيغة ماركداون) نستبدله
    # بكلمة "رابط" بدل قراءته حرفاً حرفاً.
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'https?://\S+', ' رابط ', text)
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'\1', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-*•]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{2,}', '. ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    # تنظيف علامة ترقيم مكرَّرة قد تنتج لو فقرة أصلاً انتهت بعلامة ترقيم
    # قبل السطر الفارغ اللي تحوّل لنقطة أعلاه (مثال: "تمام؟. كذا" ← "تمام؟ كذا")
    text = re.sub(r'([.!؟?])\s*\.\s*', r'\1 ', text)
    text = re.sub(r'\.{2,}', '.', text)
    return text.strip()


def split_text_for_tts(text, max_chars=None):
    """
    يقسّم النص لمقاطع لا تتجاوز حد Orpheus (200 حرف/طلب)، بمحاذاة حدود
    الجمل قدر الإمكان بدل القص الحرفي العشوائي منتصف كلمة. ضمان صلب:
    أي مقطع ناتج مهما كان لا يتجاوز max_chars أبداً — جملة وحيدة أطول
    من الحد تُقصّ قسراً بدل رفض الطلب بالكامل.
    """
    max_chars = max_chars or Config.TTS_CHUNK_MAX_CHARS
    sentences = re.split(r'(?<=[.!؟?])\s+', text.strip())
    chunks = []
    current = ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        while len(s) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(s[:max_chars])
            s = s[max_chars:]
        candidate = f"{current} {s}".strip() if current else s
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = s
    if current:
        chunks.append(current)
    return chunks


def synthesize_speech(text, lang='ar', voice=None):
    """
    يحوّل مقطع نص واحد (≤200 حرف) لصوت عبر Orpheus على Groq. يرجع
    (bytes الصوت بصيغة mp3, رسالة الخطأ). يتطلب قبول شروط الموديل من
    حساب Groq مسبقاً (انظر التعليق بجانب TTS_MODEL_AR في config.py).
    voice: معرّف صوت صريح (لازم يكون من TTS_VOICES_AR/EN بconfig.py —
    التحقق مسؤولية المستدعي بـroutes/api.py) — وإلا الافتراضي حسب اللغة.
    """
    if not text or not text.strip():
        return None, 'لا يوجد نص للنطق'
    model = Config.TTS_MODEL_EN if lang == 'en' else Config.TTS_MODEL_AR
    default_voice = Config.TTS_VOICE_EN if lang == 'en' else Config.TTS_VOICE_AR
    voice = voice or default_voice
    try:
        resp = requests.post(
            Config.GROQ_TTS_URL,
            headers={"Authorization": f"Bearer {Config.GROQ_API_KEY}", "Content-Type": "application/json"},
            json={"model": model, "input": text[:200], "voice": voice, "response_format": "mp3"},
            timeout=30
        )
        if not resp.ok:
            try:
                err = resp.json().get('error', {}).get('message', f'HTTP {resp.status_code}')
            except Exception:
                err = f'HTTP {resp.status_code}'
            return None, err
        if not resp.content:
            return None, 'رد فارغ من خدمة الصوت'
        return resp.content, None
    except requests.Timeout:
        return None, 'انتهت مهلة الاتصال'
    except Exception as e:
        log.error(f"خطأ أثناء توليد الصوت: {e}")
        return None, str(e)
