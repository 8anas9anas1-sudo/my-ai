"""
إعدادات التطبيق — كل قيمة قابلة للتعديل من مكان واحد.
"""
import os


class Config:
    # ─── مفتاح الجلسة — بدون قيمة افتراضية عمداً ───────────────
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")

    SESSION_PERMANENT = True
    PERMANENT_SESSION_LIFETIME = 86400
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024  # 25MB لكل طلب

    # ─── أمان كوكيز الجلسة ──────────────────────────────────────
    # Render يخدم HTTPS دائماً، فـ Secure=True آمن افتراضياً. غيّرها
    # لـ False محلياً فقط لو تختبر عبر http://localhost بدون HTTPS.
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() != "false"
    SESSION_COOKIE_HTTPONLY = True   # (افتراضي Flask أصلاً، صريح هنا للوضوح)
    SESSION_COOKIE_SAMESITE = "Lax"

    # ─── حماية CSRF (Flask-WTF) ────────────────────────────────────
    # None = الرمز يبقى صالحاً بقدر ما تبقى الجلسة نفسها صالحة، بدل
    # مؤقّت منفصل أقصر (الافتراضي بمكتبات كتيرة ساعة واحدة) — مهم هنا
    # لأن رمز CSRF يُحمَّل مرة وحدة بفتح الصفحة، فمحادثة طويلة كانت
    # ستنكسر فجأة بمنتصفها لو الرمز انتهت صلاحيته قبل الجلسة نفسها.
    WTF_CSRF_TIME_LIMIT = None

    # ─── قاعدة البيانات ──────────────────────────────────────────
    DATABASE_URL = os.environ.get("DATABASE_URL")
    DB_POOL_MIN_SIZE = 1
    DB_POOL_MAX_SIZE = 10

    # ─── Redis (تحديد المعدل المشترك بين workers) ────────────────
    REDIS_URL = os.environ.get("REDIS_URL")  # مثال: رابط Upstash Redis

    # ─── لوحة التحكم (اختيارية) ───────────────────────────────────
    # قائمة بيضاء صريحة بالبريد الإلكتروني — لا نظام أدوار كامل، هذا
    # يكفي لتطبيق شخصي. الافتراضي "فارغة" عمداً (fail-closed): بدون
    # ضبط هذا المتغير، لا أحد يصل للوحة التحكم إطلاقاً — ولو بمعرفة
    # الرابط. الأمان بالافتراضي هو "امنع"، ليس "اسمح".
    ADMIN_EMAILS = {
        e.strip().lower() for e in (os.environ.get("ADMIN_EMAILS") or "").split(',') if e.strip()
    }
    ADMIN_RATE_LIMIT = "30 per minute"  # حماية إضافية من تحديث متكرر يستنزف بركة اتصالات القاعدة

    # ─── Supabase Storage (تخزين دائم للصور) ──────────────────────
    # بدونها: الصور المرفوعة للتحليل تُفقد بعد الجلسة (اسم الملف فقط
    # يبقى)، والصور المولّدة تبقى معتمدة على استقرار pollinations.ai
    # على المدى الطويل. بوجودها: كلاهما يُخزَّن بشكل دائم.
    SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").rstrip('/')
    SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
    # الباكت الخاص بالصور المرفوعة من المستخدمين — لازم يكون Private
    # (روابط موقّعة مؤقتة فقط، لأنها صور شخصية محتملة الحساسية)
    SUPABASE_UPLOADS_BUCKET = "chat-uploads"
    # الباكت الخاص بالصور المولّدة بالذكاء الاصطناعي — يمكن يكون Public
    SUPABASE_GENERATED_BUCKET = "chat-generated"
    SIGNED_URL_EXPIRES_IN = 3600  # ثانية — رابط الصورة المرفوعة يُجدَّد تلقائياً بكل قراءة

    # ─── Groq API ────────────────────────────────────────────────
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
    GROQ_API_BASE = "https://api.groq.com/openai/v1"
    GROQ_API_URL = f"{GROQ_API_BASE}/chat/completions"
    GROQ_STT_URL = f"{GROQ_API_BASE}/audio/transcriptions"
    GROQ_TTS_URL = f"{GROQ_API_BASE}/audio/speech"

    # آخر تحديث: أغسطس 2026 — Groq ألغت llama-3.1-8b-instant و
    # llama-3.3-70b-versatile (إلغاء نهائي 16 أغسطس 2026)، وأزالت
    # qwen/qwen3-32b وmeta-llama/llama-4-scout-17b-16e-instruct من
    # الكتالوج فعلياً بتاريخ 21 يوليو 2026. عدّل هنا فقط عند أي تبديل.
    GROQ_MODELS = {
        'fast':     'openai/gpt-oss-20b',
        'funny':    'openai/gpt-oss-20b',
        'thinker':  'openai/gpt-oss-120b',
        'coder':    'openai/gpt-oss-120b',
        'writer':   'openai/gpt-oss-120b',
        'creative': 'openai/gpt-oss-120b',
    }
    GROQ_VISION_MODEL = 'qwen/qwen3.6-27b'  # preview عند Groq — راقب استقراره
    # موديل رؤية بديل عند 429 فقط — نفس فكرة GROQ_FALLBACK_MODEL تماماً،
    # بس لمسار الرؤية غير المبثوث (call_vision_model). راجعت توثيق Groq
    # الرسمي (console.groq.com/docs/vision): qwen3.8-27b موديل رؤية
    # حقيقي ومستقل تماماً عن qwen3.6-27b (لا نفس الموديل بس نعيد نفس
    # الطلب له — هذا ما كان سيفيد شيء لو المشكلة استقرار الموديل نفسه).
    GROQ_VISION_MODEL_FALLBACK = 'qwen/qwen3.8-27b'
    GROQ_FALLBACK_MODEL = {
        'openai/gpt-oss-120b': 'openai/gpt-oss-20b',
    }

    # ─── أدوات مدمجة عند Groq (بحث ويب + تنفيذ كود) ─────────────────
    # هذي أدوات Groq نفسها ينفذها على سيرفراته (server-side) — بدون
    # أي مفتاح API إضافي ولا سيرفر منا. متوفرة فقط لنماذج gpt-oss
    # (وكل نماذج المحادثة عندنا بـ GROQ_MODELS هي أصلاً gpt-oss، فما
    # فيه تعارض). الموديل نفسه يقرر إذا يحتاج يستخدم أداة أو لا —
    # ما نجبره بكل رسالة (tool_choice="auto").
    ENABLE_BUILTIN_TOOLS = True
    BUILTIN_TOOLS = [
        {"type": "browser_search"},   # بحث حي بالويب + استعراض صفحات فعلي
        {"type": "code_interpreter"},  # تنفيذ كود بايثون فعلي — يغطي أي حساب/معادلة
    ]

    # ─── الذاكرة العائلية الدائمة (RAG) ────────────────────────────
    # تتطلب امتداد pgvector مُفعَّلاً بقاعدة البيانات (متوفر افتراضياً
    # بمشاريع Supabase). نموذج التضمين يعمل محلياً على السيرفر نفسه —
    # بدون أي مفتاح API إضافي، مجاني بالكامل — لكنه يحمّل ~500MB
    # بالذاكرة بأول استخدام فعلي. راجع التعليق بأعلى app/rag.py قبل
    # التفعيل على خطة Render محدودة الرام أو بأكثر من worker.
    ENABLE_RAG = True
    RAG_EMBEDDING_MODEL = 'paraphrase-multilingual-MiniLM-L12-v2'  # يدعم العربي + 50 لغة، 384 بُعد
    RAG_EMBEDDING_DIM = 384
    RAG_CHUNK_MAX_CHARS = 800
    RAG_CHUNK_OVERLAP = 100
    RAG_TOP_K = 4               # أقصى عدد مقاطع نرسلها كسياق لكل رسالة
    RAG_MIN_SIMILARITY = 0.35   # نستبعد أي نتيجة أضعف من هذا — يمنع حقن سياق غير ذي علاقة

    # ─── حصة الاستخدام اليومية لكل مستخدم ──────────────────────────
    # حماية من استنزاف حد Groq المجاني المشترك بين كل حسابات العائلة.
    # الرقم تخمين بداية معقول لا قياس دقيق — Groq ما ينشر حداً يومياً
    # ثابتاً لكل الخطط، عدّله بناءً على استهلاكك الفعلي بلوحة
    # console.groq.com. الأدمن (ADMIN_EMAILS) مستثنى دائماً من هذا الحد.
    ENABLE_DAILY_QUOTA = True
    DAILY_MESSAGE_LIMIT = 200

    # ─── ذاكرة طويلة المدى (تلخيص تلقائي) ─────────────────────────
    # موديل خفيف مقصود — التلخيص استخراج/ضغط، لا يحتاج تفكيراً عميقاً،
    # وتقليل التكلفة والزمن هنا مهم لأنه يعمل بالخلفية لكل محادثة طويلة.
    SUMMARY_MODEL = 'openai/gpt-oss-20b'
    SUMMARY_MAX_TOKENS = 500
    SUMMARY_TRIGGER_THRESHOLD = 24   # أول تلخيص بعد ما تتجاوز المحادثة هالعدد من الرسائل
    SUMMARY_UPDATE_INTERVAL = 12     # إعادة تلخيص كل ما تراكم هالعدد من رسائل جديدة
    SUMMARY_MIN_VALID_LENGTH = 20    # أقل طول نص نقبله كملخص صالح (حماية من نتيجة فاضية/معطوبة)

    # ─── حدود جلب الرسائل/المحادثات من قاعدة البيانات ───────────────
    # بدون هذي الحدود، محادثة عائلية تراكمت لها مئات أو آلاف الرسائل
    # عبر شهور كانت تُجلب بالكامل من قاعدة البيانات بكل رسالة جديدة —
    # حتى لو الموديل نفسه يستخدم آخر 12-20 رسالة بس من السياق فعلياً.
    #
    # DB_CONTEXT_FETCH_LIMIT: تُستخدم داخلياً بـget_chat_history_for_context
    # (بناء سياق كل رسالة — المسار الحرج، يشتغل بكل رسالة) وبـ
    # app/memory.py (فحص التلخيص). سخية عمداً (أكبر بكثير من history_limit
    # الفعلي 12-20، وأكبر من SUMMARY_TRIGGER_THRESHOLD/UPDATE_INTERVAL)
    # حتى تبقى صحة حساب "الرسائل غير المُلخَّصة بعد" ومعالجة before_id
    # (إعادة التوليد) مضمونة بكل الحالات الواقعية، لا بس الحالة الشائعة.
    DB_CONTEXT_FETCH_LIMIT = 100
    # CHAT_DISPLAY_FETCH_LIMIT: تُستخدم فقط لما المستخدم يفتح محادثة
    # بالواجهة لعرضها — سخية جداً (300 رسالة) لأن هذي للعرض لا للسياق.
    CHAT_DISPLAY_FETCH_LIMIT = 300
    # CHAT_LIST_FETCH_LIMIT: أقصى عدد محادثات تظهر بالقائمة الجانبية.
    CHAT_LIST_FETCH_LIMIT = 200

    # ─── الصوت (تحويل صوت↔نص) ──────────────────────────────────────
    STT_MODEL = 'whisper-large-v3-turbo'  # سريع ورخيص — كافٍ لرسائل صوتية قصيرة
    MAX_AUDIO_SIZE = 15 * 1024 * 1024     # 15MB (حد Groq نفسه 25MB، نُبقي هامشاً)
    MAX_RECORDING_SECONDS = 60            # حد تسجيل من جهة المتصفح أيضاً

    # ⚠️ يتطلبان قبول شروط الموديل يدوياً من حساب Groq نفسه قبل ما
    # يشتغلا (console.groq.com → Playground → Text to Speech → قبول
    # الشروط لكل موديل) — بدون هالخطوة كل طلب TTS يفشل بخطأ صلاحيات،
    # بغض النظر عن صحة الكود. هذي موديلات Preview عند Groq.
    TTS_MODEL_AR = 'canopylabs/orpheus-arabic-saudi'
    TTS_VOICE_AR = 'fahad'  # الافتراضي لو المستخدم ما اختار — الأصوات الستة كلها بـTTS_VOICES_AR
    TTS_MODEL_EN = 'canopylabs/orpheus-v1-english'
    TTS_VOICE_EN = 'troy'
    # كل الأصوات المتاحة فعلياً عند Groq لكل موديل (راجعتها من توثيقهم
    # الرسمي) — نتحقق أي صوت يطلبه المستخدم مقابل هذي القائمة قبل
    # إرساله لـ Groq، حتى ما نمرر قيمة تعسفية من الواجهة بلا تدقيق.
    TTS_VOICES_AR = ['abdullah', 'fahad', 'sultan', 'lulwa', 'noura', 'aisha']
    TTS_VOICES_EN = ['autumn', 'diana', 'hannah', 'austin', 'daniel', 'troy']
    TTS_CHUNK_MAX_CHARS = 190   # حد Orpheus الفعلي 200 حرف/طلب — نُبقي هامش أمان
    TTS_MAX_INPUT_LENGTH = 2000  # حد إجمالي قبل التقسيم — يمنع رسالة ضخمة من توليد عشرات المقاطع بطلب واحد
    VOICE_RATE_LIMIT = "15 per minute"

    TEMP_MAP = {
        'funny': 0.92, 'creative': 0.88, 'writer': 0.82,
        'thinker': 0.45, 'coder': 0.25, 'fast': 0.72,
    }
    MAX_TOKENS_MAP = {
        'coder': 4096, 'thinker': 3000, 'writer': 2500,
        'creative': 2000, 'funny': 1500, 'fast': 2048,
    }
    # gpt-oss نماذج "تفكير" (reasoning) — نفعّلها بعمق متوسط لوضعي
    # المفكر والمبرمج حيث الدقة أهم، ونخفّضها للأوضاع السريعة/الإبداعية.
    REASONING_MAP = {
        'thinker': 'medium', 'coder': 'medium', 'writer': 'low',
        'creative': 'low', 'fast': 'low', 'funny': 'low',
    }

    # ─── حماية ────────────────────────────────────────────────────
    MAX_MSG_LENGTH = 4000
    MAX_LOGIN_ATTEMPTS = 5
    LOGIN_WINDOW_SECONDS = 300  # 5 دقائق
    CHAT_RATE_LIMIT = "20 per minute"
    MAX_PDF_SIZE = 15 * 1024 * 1024   # 15MB
    MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB

    BANNED_PATTERNS = [
        r'ignore (previous|all) instructions',
        r'you are now',
        r'jailbreak',
        r'DAN mode',
        r'pretend you',
        r'act as if',
        r'system prompt',
        r'forget your',
    ]

    IDENTITY_TRIGGERS = [
        'من انت', 'من أنت', 'عرف بنفسك', 'من تكون', 'ما اسمك',
        'شن اسمك', 'who are you', 'اسمك ايش', 'اسمك شن', 'عرفني عليك'
    ]
    # سؤال الهوية لازم يكون كل الرسالة أو رسالة قصيرة (≤ هالعدد من
    # الكلمات) تحتوي العبارة — لا أي رسالة أطول تذكرها بالمرور.
    IDENTITY_MAX_WORDS = 6

    # عبارات أمر صريحة بحدود كلمة حقيقية لتوليد صورة — وليس أي ظهور
    # لكلمة "صورة" بأي سياق (كانت تخطف رسائل مثل "اشرحلي الصورة الذهنية"
    # أو "حلل هذه الصورة" مع ملف مرفق).
    IMAGE_GENERATION_TRIGGERS = [
        r'\bارسم\b',
        r'^draw\b',
        r'\b(?:اعطيني|ابغى|ابي|اريد|ولّد|ولد|انشئ|اصنع|صمم|سوي|اعمل)\s+(?:لي\s+)?صورة\b',
    ]


def validate_config():
    """يوقف تشغيل التطبيق فوراً لو أي متغير بيئة حرج ناقص، بدل تسريب
    قيمة افتراضية غير آمنة أو فشل صامت لاحقاً."""
    if not Config.SECRET_KEY:
        raise RuntimeError(
            "❌ FLASK_SECRET_KEY غير مضبوط في متغيرات البيئة. "
            "أضِفه في إعدادات Render قبل التشغيل (لا تستخدم قيمة افتراضية أبداً)."
        )
