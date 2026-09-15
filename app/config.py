"""
إعدادات التطبيق — كل قيمة قابلة للتعديل من مكان واحد.
"""
import os


class Config:
    # ─── مفتاح الجلسة — بدون قيمة افتراضية عمداً ───────────────
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")

    SESSION_PERMANENT = True
    PERMANENT_SESSION_LIFETIME = 86400
    # كان 25MB (يكفي بالكاد لمرفق واحد: PDF حتى 15MB + هامش عام). بعد
    # دعم حتى 5 صور برسالة واحدة (MAX_IMAGES_PER_MESSAGE × MAX_IMAGE_SIZE
    # أدناه = حتى 50MB بأسوأ حالة)، السقف القديم كان سيرفض الطلب كاملاً
    # بخطأ 413 خام من Flask نفسه — قبل ما يوصل حتى لفحص حجم كل صورة
    # بالتفصيل بـroutes/api.py، وبصيغة غير SSE أصلاً لا تتعامل معها
    # streamChat بالواجهة بشكل صحيح. رفعناه ليتسع لأسوأ حالة فعلياً
    # ممكنة + هامش معقول لبقية الحقول.
    MAX_CONTENT_LENGTH = 55 * 1024 * 1024  # 55MB لكل طلب

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

    # ─── عائلتا الموديل الظاهرتان للمستخدم — اختيار صريح لا تلقائي ────
    # المستخدم يختار العائلة عند بدء محادثة جديدة (مثل اختيار موديل
    # بكلود)، وتبقى مثبَّتة لعمر تلك المحادثة (راجع get_chat_locked_settings
    # بـdb.py). label هو الاسم الودود المعروض بالواجهة فقط — لا علاقة
    # له بأرقام إصدار Groq/Meta الحقيقية. 'groq' هي نفس سلسلة الاحتياط
    # التلقائي المعتادة (Groq ← Groq احتياطي ← SambaNova ← Cerebras)؛
    # 'meta' تروح *مباشرة* لعائلة SambaNova/Cerebras بلا أي محاولة على
    # Groq إطلاقاً — احترام صريح لاختيار المستخدم، لا "احتياطي عن
    # احتياطي". supports_tools=False لـ'meta' لأن البحث الحي وتنفيذ
    # الكود المدمجين يعملان فقط مع نماذج gpt-oss عند Groq (راجع
    # supports_builtin_tools بـai_service.py).
    # 'oss' ("Wadi 2.1") — عائلة ثالثة مضافة سبتمبر 2026: أفضل موديلين
    # مجانيين مفتوحي المصدر متاحين حالياً عبر OpenRouter بلا أي بطاقة
    # دفع (راجع OPENROUTER_* أسفل لتفاصيل الاختيار والمصدر). نفس مبدأ
    # 'meta' تماماً — لا علاقة لها بـGroq إطلاقاً، اختيار المستخدم صريح.
    MODEL_FAMILIES = {
        'groq': {'label': 'Wadi 5.4', 'supports_tools': True},
        'meta': {'label': 'Wadi 3.3', 'supports_tools': False},
        'oss': {'label': 'Wadi 2.1', 'supports_tools': False},
    }
    DEFAULT_MODEL_FAMILY = 'groq'
    GROQ_FALLBACK_MODEL = {
        'openai/gpt-oss-120b': 'openai/gpt-oss-20b',
    }

    # ─── مزوّد احتياطي مجاني حقيقي من خارج Groq (Meta Llama) ──────────
    # ⚠️ معطَّل فعلياً بالسلسلة الآن (سبتمبر 2026): SambaNova صار يطلب
    # بطاقة دفع فعلاً ("A payment method is required")، وCerebras أسفل
    # كذلك منذ 16 يوليو 2026 — القسمان أدناه ما عادا يُستخدَمان بـ
    # _build_provider_chain (استُبدلا بـOPENROUTER_META_MODEL/
    # OPENROUTER_META_FALLBACK_MODEL فوق). أبقيتهما هنا فقط كخيار جاهز
    # لو أُضيفت بطاقة دفع لأحدهما مستقبلاً — لا ضرر من بقائهما بلا استخدام.
    # يُستخدم فقط لو Groq فشل بكل نسخه (الموديل الأساسي + GROQ_FALLBACK_MODEL
    # أعلاه معاً) — انظر stream_chat_completion بـai_service.py. الهدف:
    # صورة "429 / الرسالة طويلة جداً" اللي تظهر بلوحة Groq توقف تماماً،
    # بدل ما ينكسر رد المستخدم، وبموديل Meta حقيقي فعلاً لا مجرد بديل.
    #
    # لماذا SambaNova تحديداً — راجعت البدائل التالية كلها (سبتمبر 2026)
    # وطلعت غير صالحة فعلياً لهدف "Llama مجاني بدون دفع":
    #   • Groq نفسه: ألغى llama-3.1-8b-instant وllama-3.3-70b-versatile
    #     نهائياً من التير المجاني/المطورين (16 أغسطس 2026) — صارت
    #     "Enterprise" فقط (عقد دفع، Contact Sales). لهذا أصلاً السبب
    #     الحقيقي وراء استخدامنا gpt-oss لا Llama بـGROQ_MODELS أعلاه.
    #   • Cerebras: أوقف تيره المجاني بلا بطاقة (أغسطس 2026) — صار رصيد
    #     تجربة $5 يتطلب بطاقة دفع مُفعَّلة لتفعيله، وحصر Llama بمسار
    #     "Dedicated Endpoints" المدفوع حتى على خطته المدفوعة العادية.
    #   • OpenRouter: أزال كل موديلات Llama من قائمته المجانية خلال
    #     الأسابيع الأخيرة (قائمته المجانية الحالية الآن Nemotron/Gemma/
    #     إلخ — بدون أي Llama إطلاقاً).
    # SambaNova Cloud حالياً أفضل خيار متبقٍ فعلياً: Llama 3.3 70B حقيقي
    # عبر واجهة متوافقة مع OpenAI، بدون بطاقة ائتمان عند التسجيل
    # (cloud.sambanova.ai). تحقق بنفسك عند التسجيل من نوع الحد بالضبط —
    # بعض المصادر تصفه كتير مجاني دائم بحد يومي لكل موديل (~200K توكن)،
    # ومصادر أخرى كرصيد تجربة $5 لمدة 30 يوم؛ التوثيق الرسمي لا يوضح
    # هذا صراحة، والمشهد كامله (الثلاث نقاط أعلاه) تغيّر خلال أسابيع
    # قليلة فقط. لو توقف هذا المزوّد أيضاً مستقبلاً، بدّل القيم هنا فقط
    # (+ ضبط SAMBANOVA_API_KEY الجديد بمتغيرات البيئة) — لا حاجة لأي
    # تعديل بـai_service.py.
    ENABLE_CROSS_PROVIDER_FALLBACK = os.environ.get("ENABLE_CROSS_PROVIDER_FALLBACK", "True") == "True"
    SAMBANOVA_API_KEY = os.environ.get("SAMBANOVA_API_KEY")
    SAMBANOVA_API_URL = "https://api.sambanova.ai/v1/chat/completions"
    SAMBANOVA_FALLBACK_MODEL = "Meta-Llama-3.3-70B-Instruct"
    # سقف متحفظ عمداً — الحد الأقصى الفعلي لمخرجات هذا الموديل عند
    # SambaNova لم يُتحقق رسمياً بتوثيقهم وقت كتابة هذا. أهم شيء هنا
    # نجاح الطلب لا استغلال كامل الطاقة الاستيعابية لموديل احتياطي
    # نادر الاستخدام أصلاً — رد مبتور أفضل بكثير من فشل الطلب بالكامل.
    SAMBANOVA_FALLBACK_MAX_TOKENS = 4096

    # ─── مزوّد احتياطي ثانٍ (Cerebras) — سبتمبر 2026 ──────────────────
    # لماذا مزوّد ثالث لا مجرد "اثنين كافيين": راجعنا حالة موثّقة فعلياً
    # (dev.to/eleata) عن راوترات LLM بمزوّد احتياطي واحد فقط تنهار كلياً
    # لو كلا المزوّدين ازدحما بنفس اللحظة (بالضبط ما حصل هنا فعلياً —
    # Groq وSambaNova فشلا معاً لرسالة واحدة). خط ثالث يقلّل احتمال هذا
    # التزامن كثيراً. معطّل تماماً افتراضياً (سلوك صفري لو ما ضبطت
    # المفتاح) — فعّله بإضافة CEREBRAS_API_KEY فقط بمتغيرات بيئة Render،
    # بدون أي تعديل كود إضافي (نفس نمط SAMBANOVA_API_KEY أعلاه تماماً).
    #
    # ⚠️ ملاحظة صدق مهمة: بحثت حالة Cerebras الحالية (سبتمبر 2026) ولقيت
    # مصادر متضاربة فعلياً — أغلبها (يونيو-أغسطس 2026) يصفه كتير مجاني
    # دائم بلا بطاقة (1M توكن/يوم على Llama 3.3 70B)، لكن هذا يعاكس
    # ملاحظة سابقة بهذا الملف نفسه (أغسطس 2026) قالت إنه صار يتطلب بطاقة
    # لتفعيل رصيد تجربة $5. المشهد يتغيّر بأسابيع — تحقق بنفسك عند
    # cloud.cerebras.ai وقت التفعيل الفعلي قبل الاعتماد عليه بالكامل.
    # الموديل ونقطة النهاية موثّقان رسمياً وواضحان بلا تضارب (inference-docs.cerebras.ai):
    CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY")
    CEREBRAS_API_URL = "https://api.cerebras.ai/v1/chat/completions"
    CEREBRAS_FALLBACK_MODEL = "llama-3.3-70b"
    CEREBRAS_FALLBACK_MAX_TOKENS = 4096  # نفس منطق السقف المتحفظ لـSAMBANOVA_FALLBACK_MAX_TOKENS أعلاه

    # ─── عائلة "Wadi 2.1" — أفضل اثنين مفتوحي مصدر مجاني عبر OpenRouter (سبتمبر 2026) ──
    # ⚠️ ملاحظة صدق مهمة: القائمة المجانية بـOpenRouter تتغيّر بشكل شبه
    # أسبوعي (نفس مشكلة Cerebras/SambaNova أعلاه بالضبط) — الاختيارين
    # هنا مبنيان على قائمة "Top Free Models" الحيّة فعلياً وقت الكتابة
    # (openrouter.ai/collections/free-models، سبتمبر 2026)، مو على مقال
    # قديم. راجع القائمة بنفسك دورياً وبدّل القيم هنا لو انسحب أحدهما —
    # لا حاجة لتعديل ai_service.py (نفس مبدأ SAMBANOVA_FALLBACK_MODEL).
    #   • النموذج الأساسي (Nemotron 3 Ultra من NVIDIA): الأعلى استخداماً
    #     فعلياً بين كل الموديلات المجانية حالياً (٣.٧٦ تريليون توكن
    #     معالَجة) — أقوى إشارة ثقة/استقرار متاحة، سياق يصل 1M توكن.
    #   • الاحتياطي (Inkling من Thinking Machines): من القلائل بالقائمة
    #     المجانية المذكور بوصفها الرسمي صراحة أنها مناسبة لـ"multilingual
    #     conversational applications" — أقرب توصيفاً لطبيعة Wadi
    #     كتطبيق محادثة عربي من نماذج البرمجة/الوكلاء المهيمنة على باقي
    #     القائمة المجانية حالياً.
    # التير المجاني: 50 طلب/يوم لكل مفتاح OpenRouter بلا بطاقة إطلاقاً
    # (يرتفع لـ1000/يوم فقط لو أُضيف رصيد $10 اختياري لمرة وحدة — غير
    # مطلوب للتفعيل). بسبب هذا السقف اليومي المنخفض نسبياً، هذي العائلة
    # معرّضة لاستنفاد حصتها بسرعة لو استُخدمت بكثافة — بلا Redis مشترك
    # لعدّاد الطلبات لن يمنع هذا مسبقاً، فقط دائرة القطع الحالية
    # (provider_health) ستتكفّل بتبريد مؤقت بعد أول 429 فعلي.
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
    OPENROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
    OPENROUTER_FALLBACK_MODEL = "thinkingmachines/inkling:free"

    # ─── عائلة "Wadi 3.3" — تحديث 13 سبتمبر 2026: موديلان أقوى ────────
    # الموديلان القديمان هنا (Nemotron 3 Super + Ling 3.0 Flash VL) كانا
    # مناسبين وقت اختيارهما، لكن راجعت قائمة "Top Free Models" الحيّة
    # بـOpenRouter اليوم (openrouter.ai/collections/free-models) ولقيت
    # بديلين أقوى فعلياً — بنفس OPENROUTER_API_KEY الموجود أصلاً، صفر
    # حساب جديد وصفر بطاقة دفع كالسابق تماماً (مجرد تغيير قيمتين هنا):
    #   • Dots3-Note Preview (Dots Studio) — أساسي جديد: 280B إجمالي/16B
    #     نشط (أكبر من 120B/12B القديم)، سياق 512K (ضِعف الـ262K القديم
    #     تقريباً)، مفتوح الأوزان (open-weight)، ومصنَّف رسمياً لاستخدام
    #     عام (تفكير + برمجة + فهم متعدد الوسائط + سياق طويل + وكلاء) لا
    #     تخصص ضيق ببرمجة فقط — يناسب تنوع أوضاع Wadi (سريع/مفكر/مبرمج/
    #     كاتب/إبداعي/مضحك) لا أحدها فقط. استخدام فعلي أعلى من الموديل
    #     القديم أيضاً (٥٥٦ مليار توكن معالَجة مقابل ٣٧٢ مليار سابقاً) —
    #     إشارة ثقة/استقرار أقوى، بنفس منهج اختيار Nemotron Ultra بعائلة
    #     "Wadi 2.1" فوق.
    #   • Nemotron 3.5 Lightning (NVIDIA) — احتياطي جديد: سياق 1M (الأكبر
    #     بين كل الخيارات المجانية المتاحة حالياً)، استخدام فعلي عالٍ جداً
    #     (٨٥١ مليار توكن — رابع أعلى موديل مجاني بكامل قائمة OpenRouter)،
    #     مفتوح الأوزان (NVIDIA Open License، نفس ترخيص Nemotron Ultra/
    #     Super). NVIDIA مستخدَمة أصلاً بـ"Wadi 2.1" (Nemotron Ultra)، لكن
    #     Lightning معمارية مختلفة كلياً (30B إجمالي/3B نشط فقط — خفيف
    #     وسريع فعلاً كما يوحي اسمه) لا مجرد نسخة أخرى من نفس الموديل —
    #     نفس مبدأ "مزوّد واحد (OpenRouter)، موديلان مختلفان فعلياً"
    #     المعتمد أصلاً بعائلتي oss وmeta كلتيهما، بلا أي تغيير بذلك المبدأ.
    # ⚠️ ملاحظة صدق: نفس تحذير "القائمة تتغيّر شبه أسبوعياً" أعلاه ينطبق
    # هنا حرفياً — تحقق من openrouter.ai/collections/free-models دورياً
    # قبل الاعتماد على هذين الاختيارين لفترة طويلة.
    OPENROUTER_META_MODEL = "dots-studio/dots-3-note-preview:free"
    OPENROUTER_META_FALLBACK_MODEL = "nvidia/nemotron-3.5-lightning:free"

    # ─── NVIDIA NIM وGoogle Gemini — طبقتان جديدتان بمفتاح مستقل تماماً ──
    # أُضيفتا 14 سبتمبر 2026 كحل لمشكلة حصة الـ50 طلب/يوم المشتركة أعلاه:
    # بدل الاعتماد كلياً على مفتاح OpenRouter الواحد لعائلتي meta وoss
    # معاً، هاتان طبقتان *قبل* خطوتي OpenRouter بكل سلسلة — بمفتاحين
    # منفصلين تماماً، فاستنفاد أحدهما لا يؤثر على البقية إطلاقاً.
    #
    # • NVIDIA NIM (build.nvidia.com) — بلا بطاقة دفع إطلاقاً (مؤكَّد من
    #   عدة مصادر مستقلة)، متوافق OpenAI بالكامل (نفس شكل طلب Groq/
    #   OpenRouter هنا حرفياً). ⚠️ ملاحظة صدق مهمة: المصادر تتضارب على
    #   طبيعة الحصة المجانية — بعضها يذكر "10,000 طلب/يوم" كرقم متجدد،
    #   وأخرى (تدوينات تقنية أحدث) تصفها كرصيد نقاط لمرة واحدة عند
    #   التسجيل (1000، يرتفع لـ5000 عند الطلب) لا يتجدد يومياً بالضرورة.
    #   التحقق اليقيني الوحيد: افحصي حسابكم الفعلي بـbuild.nvidia.com بعد
    #   التسجيل. بغض النظر عن أي التفسيرين صحيح، الكمية أكبر بكثير من
    #   الـ50/يوم الحالية بأي الحالتين. حد المعدّل اللحظي مؤكَّد من كل
    #   المصادر: 40 طلب/دقيقة.
    # • Google Gemini (مباشرة عبر نقطة توافق OpenAI الرسمية من Google، لا
    #   عبر OpenRouter) — بلا بطاقة دفع، حتى 1500 طلب/يوم لموديلات Flash
    #   حسب مصادر مجتمعية محدَّثة (جوجل نفسها توقفت عن نشر جدول أرقام
    #   رسمي ثابت للحصة المجانية وتوجّه للوحة تحكم AI Studio بدلاً من
    #   ذلك — تحققي من https://aistudio.google.com/rate-limit بعد
    #   التسجيل للرقم الفعلي الحالي لحسابكم).
    #
    # موديل مختلف لكل عائلة بكلا المزوّدين (نفس مبدأ "موديلان مختلفان
    # فعلياً" أعلاه) — meta تبقى أخف/أوسع استخداماً، oss تبقى الأثقل/الأكبر.
    NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")
    NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
    NVIDIA_META_MODEL = "nvidia/nemotron-3-super-120b-a12b"
    NVIDIA_OSS_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"

    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    GEMINI_META_MODEL = "gemini-2.5-flash"
    GEMINI_OSS_MODEL = "gemini-2.5-flash-lite"

    # ─── Cloudflare Workers AI — خط الدفاع الأخير العام (15 سبتمبر 2026) ──
    # الفرق الجوهري عن كل طبقة أعلاه: تلك كلها مربوطة بعائلة محدَّدة
    # (meta أو oss). هذي طبقة *عابرة للعائلات الثلاث* — تُضاف كخطوة
    # أخيرة بأي سلسلة (راجع نهاية _build_provider_chain بـai_service.py)
    # بلا أي علاقة بـENABLE_CROSS_PROVIDER_FALLBACK ولا بـmodel_family
    # المختار. الهدف: لو استُنفدت كل مفاتيح المزوّدين المجانيين أعلاه
    # معاً بنفس اللحظة تقريباً (نادر لكن وارد بحمل مرتفع — بالضبط
    # السيناريو اللي تشرحه تعليقات provider_health.py)، تبقى محاولة
    # حقيقية أخيرة قبل رسالة "كل المسارات مزدحمة" النهائية للمستخدم.
    #
    # • نقطة النهاية: REST API الرسمية من Cloudflare (api.cloudflare.com،
    #   لا gateway.ai.cloudflare.com القديمة) — /ai/v1/chat/completions
    #   موثّقة صراحة "OpenAI SDK compatible" (developers.cloudflare.com/
    #   changelog، تحديث REST API مايو 2026)، فتندمج بنفس
    #   _stream_openai_compatible المشترك هنا حرفياً بلا أي منطق خاص
    #   إضافي — نفس شكل طلب/بث Groq وNVIDIA وGemini وOpenRouter تماماً.
    # • الموديل (llama-3.3-70b-instruct-fp8-fast من كتالوج Workers AI):
    #   Llama 3.3 70B حقيقي (fp8، مُحسَّن للسرعة) — نفس وزن/فئة الموديلات
    #   المستخدمة بعائلتي meta/oss أعلاه، لا موديل صغير ضعيف. سياق محدود
    #   نسبياً (24K توكن فقط حسب توثيق Cloudflare الرسمي لهذا الموديل
    #   تحديداً) — لهذا CLOUDFLARE_MAX_TOKENS أسفل متحفظ، نفس فلسفة
    #   SAMBANOVA_FALLBACK_MAX_TOKENS فوق (رد مبتور أفضل من فشل الطلب).
    # • الحصة المجانية: 10,000 Neuron/يوم (وحدة قياس Cloudflare الموحَّدة
    #   لكل أنواع الاستدلال)، تتجدد يومياً 00:00 UTC، بلا أي بطاقة دفع
    #   مطلوبة للتفعيل — مؤكَّدة من توثيق Cloudflare الرسمي
    #   (developers.cloudflare.com/workers-ai/platform/pricing) ومصادر
    #   مستقلة متعددة (تحقق سبتمبر 2026). تجاوز الحصة يُفوتَر
    #   $0.011/1000 Neuron على خطة Workers Paid بدل رفض الطلب — لن يُحاسَب
    #   المستخدم شيئاً ما لم تُفعَّل خطة مدفوعة صراحة بحساب Cloudflare.
    # • بلا CLOUDFLARE_API_TOKEN/CLOUDFLARE_ACCOUNT_ID مضبوطين، هذي الخطوة
    #   تُستبعَد تلقائياً من كل سلسلة بلا أي خطأ (نفس مبدأ NVIDIA_API_KEY/
    #   GEMINI_API_KEY أعلاه) — التوكن يُنشأ من لوحة Cloudflare
    #   (Workers AI → API → إنشاء توكن بصلاحية "Workers AI: Read" فقط،
    #   لا صلاحيات أوسع)، ومعرّف الحساب من نفس رابط اللوحة
    #   (dash.cloudflare.com/<account_id>/ai/workers-ai).
    CLOUDFLARE_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN")
    CLOUDFLARE_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    CLOUDFLARE_API_URL = (
        f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1/chat/completions"
        if CLOUDFLARE_ACCOUNT_ID else None
    )
    CLOUDFLARE_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    CLOUDFLARE_MAX_TOKENS = 4096  # سقف متحفظ — سياق الموديل 24K توكن فقط عند Cloudflare (راجع الشرح أعلاه)

    # ─── دائرة القطع (Circuit Breaker) لكل مزوّد — راجع app/provider_health.py ──
    # بدل إعادة اكتشاف "هذا المزوّد مستنفد حالياً" من الصفر بكل رسالة
    # (round-trip كامل + مهلة اتصال ضائعة)، نتذكر الفشل لفترة تصاعدية:
    # أول فشل = PROVIDER_COOLDOWN_BASE_SECONDS، يتضاعف كل فشل متتالي حتى
    # PROVIDER_COOLDOWN_MAX_SECONDS. مصدر الأرقام: نفس نطاق cooldown_time
    # الافتراضي بأنظمة توجيه LLM إنتاجية حقيقية (LiteLLM/Bifrost) — راجع
    # docs.litellm.ai/docs/routing وdocs.getbifrost.ai/enterprise/circuit-breaker.
    PROVIDER_COOLDOWN_BASE_SECONDS = 20
    PROVIDER_COOLDOWN_MAX_SECONDS = 600      # 10 دقائق — سقف حتى لو تكرر الفشل كثيراً
    PROVIDER_AUTH_ERROR_COOLDOWN_SECONDS = 600  # 401/403 (مفتاح خاطئ) — تبريد ثابت طويل، لن يُصلَح نفسه بالانتظار

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

    # ─── بحث حي مستقل عن Groq لعائلتي meta/oss (Wadi 3.3 / Wadi 2.1) ──────
    # هاتان العائلتان (راجع MODEL_FAMILIES فوق) لا تدعمان أي أداة مدمجة
    # إطلاقاً. مصمَّمة كسلسلة طبقات (نفس فلسفة _build_provider_chain
    # بـai_service.py: أضعف اعتماداً أولاً):
    #
    #   الطبقة 1 (الأساسية، المقصودة فعلياً): بحث DuckDuckGo مباشر —
    #   صفحة النتائج العادية (html.duckduckgo.com) عبر HTTP، بلا أي
    #   مفتاح API، بلا حساب، وبلا أي علاقة بـGroq إطلاقاً. هذا عمداً:
    #   الهدف ألا تعتمد Wadi 3.3/2.1 على استمرار خطة Groq المجانية —
    #   بالضبط نفس نوع الانقطاع المفاجئ الموثّق فوق لـSambaNova/Cerebras،
    #   وحتى لبعض موديلات Groq نفسها (أُلغيت 16 أغسطس 2026). لو Groq
    #   توقف كلياً غداً، هذه الطبقة تستمر تعمل بلا أي تأثر.
    #   ⚠️ صدق كامل: لا اتفاقية استخدام رسمية ولا SLA من DuckDuckGo لهذه
    #   الصفحة — قد تُغيّر بنية HTML بلا إشعار فينكسر الاستخراج (خطر
    #   "صيانة" يُصلَح بتعديل fetch_ddg_search_snippets فقط، لا خطر
    #   "بطاقة دفع مفاجئة"). حجم استخدام Wadi الفعلي بعيد عن أي حجم
    #   يستدعي حجباً صارماً عملياً.
    #
    #   الطبقة 2 (احتياطي فقط — لا تُستدعى إطلاقاً لو نجحت الطبقة 1):
    #   "استعارة" بحث Groq نفسه (browser_search) بطلب داخلي منفصل تماماً
    #   عن رد المستخدم. هذا لا يُحسب "مفتاحاً جديداً" — GROQ_API_KEY
    #   مطلوب أصلاً لتشغيل كامل التطبيق بأي عائلة (بدونه لا يعمل شيء من
    #   الأساس)، فإعادة استخدامه هنا طبقة تأمين إضافية بلا أي تكلفة أو
    #   تبعية جديدة فعلية — فقط ممنوع أن تكون الطبقة الوحيدة أو الأولى.
    #
    # نص السياق المستخرَج من أي طبقة يُحقن كـ"سياق واقعي" قبل رد
    # Wadi 3.3/2.1 الفعلي، بنفس أسلوب حقن stored_memory بـrag.py تماماً
    # — لا كرد مباشر يصل المستخدم. فشل الطبقتين معاً يرجع None بهدوء؛
    # المستخدم يبقى محمياً بتحذير "لا تختلق" الصادق بـ_current_date_context
    # (ai_service.py) بغض النظر عن نتيجة أي طبقة هنا.
    #
    # يعمل فقط لو is_time_sensitive_question() اكتشفت نية زمنية بالرسالة
    # — لا نبحث بكل رسالة meta/oss، فقط عند الحاجة الفعلية.
    #
    # ⚠️ لم يُختبر فعلياً على بيئة إنتاج وقت كتابة هذا — راقب أول أيام
    # تفعيله (سجلات الأخطاء + هل DuckDuckGo يرجع نتائج فعلاً من بيئة
    # الاستضافة) قبل الاعتماد عليه كلياً. لو احتجت تعطيله كلياً: متغير
    # بيئة واحد (ENABLE_LIVE_GROUNDING_FOR_NON_GROQ=False) بلا أي كود.
    ENABLE_LIVE_GROUNDING_FOR_NON_GROQ = os.environ.get("ENABLE_LIVE_GROUNDING_FOR_NON_GROQ", "True") == "True"
    DDG_SEARCH_URL = "https://html.duckduckgo.com/html/"
    # user agent متصفح حقيقي — DuckDuckGo (ومحركات بحث أخرى كثيرة) تحجب
    # أو تتجاهل الـUser-Agent الافتراضي لمكتبة requests ("python-requests/x.x")
    GROUNDING_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
    GROUNDING_MAX_RESULTS = 4                         # عدد نتائج DuckDuckGo المُستخرَجة كحد أقصى
    GROUNDING_MODEL = GROQ_MODELS['fast']             # الطبقة 2 فقط — نفس موديل الترجمة السريع
    GROUNDING_TOOLS = [{"type": "browser_search"}]    # الطبقة 2 فقط — browser_search وحده، لا code_interpreter

    GROUNDING_TIMEOUT_SECONDS = 12                    # قصيرة عمداً — لا تؤخر رد Wadi 3.3/2.1 كثيراً لو تعثّر البحث
    GROUNDING_MAX_CONTEXT_CHARS = 1200                # حد أقصى لطول النص المحقون — يمنع تضخيم الطلب النهائي

    # ─── الذاكرة العائلية الدائمة (RAG) ────────────────────────────
    # تتطلب امتداد pgvector مُفعَّلاً بقاعدة البيانات (متوفر افتراضياً
    # بمشاريع Supabase). نموذج التضمين يعمل محلياً على السيرفر نفسه —
    # بدون أي مفتاح API إضافي، مجاني بالكامل — لكنه يحمّل ~500MB
    # بالذاكرة بأول استخدام فعلي. راجع التعليق بأعلى app/rag.py قبل
    # التفعيل على خطة Render محدودة الرام أو بأكثر من worker.
    # افتراضياً معطّلة — تحتاج رام أكبر من تير Render المجاني/Starter (512MB).
    # لتفعيلها لاحقاً (مثلاً بعد الترقية لـ Standard/2GB)، ضيف ENABLE_RAG=True
    # كمتغير بيئة بـRender بدون أي تعديل كود إضافي.
    ENABLE_RAG = os.environ.get("ENABLE_RAG", "False") == "True"
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
    # coder كان 4096 — قليل جداً لمشروع متعدد الملفات (كان يقطع الملفات
    # الأخيرة منتصفها فعلياً، وهذا يناقض قاعدة "اكتب الكود كاملاً دائماً"
    # بـMODE_PROMPTS['coder']). openai/gpt-oss-120b يدعم حتى 65,536 توكن
    # إخراج عند Groq — رفعناه لـ16000 كهامش حقيقي بدون تطرف على السقف.
    MAX_TOKENS_MAP = {
        'coder': 16000, 'thinker': 3000, 'writer': 2500,
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
    # حد احتياطي إضافي على /login بمفتاح البريد الإلكتروني وحده (بلا IP
    # إطلاقاً) — يبقى فعّالاً حتى لو get_client_ip() رجّعت عنوان IP قابلاً
    # للتزييف (انظر تعليق مطوَّل بـsecurity.py) لأن مهاجماً يبدّل عنوانه
    # المزيَّف بكل طلب يتفادى حد MAX_LOGIN_ATTEMPTS (IP+بريد) لكن ما
    # يقدر يتفادى هذا لأنه لا يعتمد على IP إطلاقاً. أعلى من الحد الأول
    # عمداً (يفسح مجال لعائلة/أجهزة متعددة تحاول بنفس البريد بأخطاء
    # كتابة طبيعية) لكنه سقف صلب لأي محاولة استنزاف حساب واحد.
    ACCOUNT_LOCKOUT_ATTEMPTS = 15
    CHAT_RATE_LIMIT = "20 per minute"
    MAX_PDF_SIZE = 15 * 1024 * 1024   # 15MB
    MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB

    # ─── مرفقات متعددة (تحليل رؤية) ─────────────────────────────────
    # حد أقصى لعدد الصور القابلة للرفع والتحليل برسالة واحدة (مسار
    # "حلل هذه الصور" عبر call_vision_model) — لا علاقة له بتوليد صورة
    # جديدة (مسار منفصل كلياً، صورة واحدة فقط بكل رد). ملاحظة: هذا الحد
    # مضروباً بـMAX_IMAGE_SIZE يحدد أسوأ حالة لحجم الطلب الكلي — انظر
    # MAX_CONTENT_LENGTH أعلاه، يجب أن يبقى أكبر من حاصل ضربهما.
    MAX_IMAGES_PER_MESSAGE = 5

    # ─── ملفات كود/نص بوضع المبرمج ───────────────────────────────────
    # يُقرأ الملف كاملاً كنص خام ضمن سياق الموديل (نفس فلسفة PDF) —
    # سقف أصغر من PDF عمداً لأنه لا استخراج/تلخيص، النص كله يدخل السياق.
    MAX_CODE_FILE_SIZE = 2 * 1024 * 1024  # 2MB
    # صيغ مسموح رفعها كملف "حقيقي" (لا صورة ولا PDF) بوضع المبرمج فقط
    # للتحليل كنص — نتحقق منها بامتداد اسم الملف (كما PDF)، لا content-type
    # الذي يختلف صدقه بين متصفح ونظام تشغيل لملفات الكود تحديداً.
    ALLOWED_CODE_EXTENSIONS = {
        'js', 'jsx', 'ts', 'tsx', 'py', 'java', 'c', 'h', 'cpp', 'hpp',
        'cs', 'go', 'rs', 'rb', 'php', 'swift', 'kt', 'kts', 'sql', 'sh',
        'bash', 'yaml', 'yml', 'json', 'xml', 'toml', 'ini', 'cfg', 'conf',
        'env', 'md', 'txt', 'css', 'scss', 'sass', 'less', 'html', 'htm',
        'vue', 'svelte', 'dart', 'lua', 'r', 'pl', 'gradle',
    }
    # أسماء ملفات شائعة بلا امتداد (تُطابَق كاملة بأحرف صغيرة)
    ALLOWED_CODE_FILENAMES = {'dockerfile', 'makefile'}

    # ❌ أُزيل فلتر حقن التعليمات (كان BANNED_PATTERNS + is_prompt_injection
    # بـsecurity.py) نهائياً من هنا — كان يُطبَّق على رسالة المستخدم
    # المباشرة نفسها، وهذا خطأ في تحديد حد الثقة (trust boundary):
    # المستخدم يخاطب مساعده هو، فما يكتبه بصندوق الشات مهما كانت صياغته
    # (نقد، سؤال عن آلية عملك، حتى محاولة كسر شخصية) هو "رسالة" لا
    # "حقن" بالتعريف — الحقن الحقيقي يحصل لما محتوى من مصدر ثالث غير
    # موثوق (ملف مرفوع، مقطع RAG) يدخل سياق النموذج ممزوجاً بكلام
    # المستخدم من غير علمه. الحماية المكافئة الآن منقولة لموضعين:
    #   1) تعليمة دائمة بالـsystem prompt نفسه (get_system_prompt
    #      بـai_service.py) تخلي النموذج يرفض بأسلوبه الطبيعي أي طلب
    #      لتجاهل تعليماته أو كشفها حرفياً — بفهم دلالي حقيقي متعدد
    #      اللغات، لا مطابقة نصية هشة بالإنجليزية فقط سهلة الالتفاف
    #      حولها (وسهلة الوقوع بإيجابيات خاطئة زي اللي صار هنا بالضبط).
    #   2) تطويق أي محتوى خارجي فعلي (نص PDF بـroutes/api.py، مقاطع
    #      rag.py) بوسوم واضحة + تنويه أنه بيانات لا تعليمات، عند نقطة
    #      إدخاله لسياق النموذج تحديداً — هنا فعلاً حد ثقة حقيقي بين
    #      كلام المستخدم ومحتوى لم يكتبه هو.

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
        # الدارجة الليبية بتسقط الهمزة: "رسم قطة..." بدل "ارسم قطة...".
        # نقيّدها بأول الرسالة فقط (^) عشان ما تخطف أسئلة زي "رسم
        # التسجيل شحال؟" (رسم = رسوم/fee) اللي ما تجي عادة بأول الجملة
        # بهذا الشكل بالضبط — نفس فلسفة تضييق \b الأصلية بالتعليق فوق.
        r'^رسمة?\b',
        r'^draw\b',
        r'\b(?:اعطيني|ابغى|ابي|اريد|ولّد|ولد|انشئ|اصنع|صمم|سوي|اعمل)\s+(?:لي\s+)?(?:صورة|رسمة)\b',
    ]

    # كلمات/عبارات مؤشّرة لسؤال قد يحتاج معلومة محدّثة بعد تدريب الموديل
    # ("نية زمنية") — تُستخدم فقط لتقرير هل نستدعي fetch_live_grounding_context
    # بـai_service.py (عائلتا meta/oss فقط، راجع ENABLE_LIVE_GROUNDING_FOR_NON_GROQ
    # أسفل لتفاصيل الفكرة). قائمة تقريبية عمداً لا شاملة 100% — إيجابية
    # خاطئة هنا تكلفتها طلب Groq داخلي إضافي واحد فقط (مقبولة)، وسلبية
    # خاطئة تعني رجوعاً للتحذير الصادق العادي بـ_current_date_context
    # (آمن أيضاً) — فلا داعٍ لدقة مثالية بهذه القائمة، فقط تغطية معقولة.
    TIME_SENSITIVE_TRIGGERS = [
        r'\bمتى\b', r'\bموعد\b', r'\bتاريخ\s+(?:إصدار|صدور|إطلاق)\b',
        r'\bآخر\s+(?:إصدار|تحديث|نسخة|أخبار|خبر)\b', r'\bأحدث\b',
        r'\bالحالي[ةه]?\b', r'\bحالياً\b', r'\bالآن\b', r'\bاليوم\b',
        r'\bهذا\s+(?:الأسبوع|الشهر|العام|السنة)\b',
        r'\bسعر\b', r'\bسعر\s+الصرف\b', r'\bالدولار\b',
        r'\bمن\s+هو\s+(?:رئيس|ملك|وزير|مدير)\b',
        r'\bنتيجة\b', r'\bنتائج\b', r'\bأخبار\b',
        r'\b20[2-9]\d\b',  # أي سنة صريحة من 2020 فما فوق بالرسالة
        r'\blatest\b', r'\bcurrent(?:ly)?\b', r'\btoday\b', r'\bright now\b',
        r'\brelease date\b', r'\bwhen is\b', r'\bwho is the current\b',
    ]


def validate_config():
    """يوقف تشغيل التطبيق فوراً لو أي متغير بيئة حرج ناقص، بدل تسريب
    قيمة افتراضية غير آمنة أو فشل صامت لاحقاً."""
    if not Config.SECRET_KEY:
        raise RuntimeError(
            "❌ FLASK_SECRET_KEY غير مضبوط في متغيرات البيئة. "
            "أضِفه في إعدادات Render قبل التشغيل (لا تستخدم قيمة افتراضية أبداً)."
        )
