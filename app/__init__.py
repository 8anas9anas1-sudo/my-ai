"""
App factory. كل شيء يُجمَّع هنا: الإعدادات، الإضافات (DB pool،
Flask-Limiter)، الـ Blueprints، وحارس تسجيل الدخول العام.
"""
from flask import Flask, request, session, redirect, url_for, render_template

from app.config import Config, validate_config


def create_app():
    validate_config()  # يوقف التشغيل فوراً لو FLASK_SECRET_KEY ناقص

    app = Flask(__name__)
    app.config.from_object(Config)

    # الإضافات المشتركة (db_pool يُنشأ عند استيراد extensions أصلاً)
    from app.extensions import limiter, csrf, db_pool, log
    limiter.init_app(app)

    # ─── إجبار تسجيل الدخول قبل أي شيء (بما فيه فحص CSRF نفسه) ──
    # لازم يُسجَّل قبل csrf.init_app أدناه: Flask ينفّذ دوال
    # before_request بترتيب تسجيلها بالضبط. لو عكسنا الترتيب، طلب من
    # مستخدم غير مسجِّل دخول (بلا جلسة، وبالتالي بلا رمز CSRF أصلاً)
    # كان سيتلقى رسالة "انتهت صلاحية الجلسة" المضلِّلة بدل التحويل
    # المنطقي لصفحة الدخول — اكتُشف هذا فعلياً بالاختبار، لا نظرياً.
    @app.before_request
    def require_login():
        allowed_routes = ['auth.login', 'auth.register', 'auth.reset_password', 'static']
        if 'user' not in session and request.endpoint not in allowed_routes:
            return redirect(url_for('auth.login'))

    csrf.init_app(app)

    # ─── رؤوس أمان على كل رد (defense in depth) ─────────────────
    # CSP هذي مضبوطة على الموارد الخارجية الفعلية المستخدَمة بالمشروع
    # (Google Fonts، Font Awesome + JSZip من cdnjs، Supabase Storage،
    # صور pollinations.ai) — لا شيء أوسع مما هو مستخدَم فعلاً.
    #
    # ملاحظة مهمة: 'unsafe-inline' مطلوب بـscript-src أيضاً (لا style-src
    # فقط) — التطبيق يعتمد على onclick="..." خام بعشرات الأماكن (القوالب
    # + HTML مولَّد ديناميكياً بـapp.js)، وCSP تحجب أي inline event handler
    # بلا استثناء بدون هذا الاستثناء، بصرف النظر عن محتواه. الأثر: CSP
    # هنا لا توقف onerror= خاماً لو انحقن يوماً (نفس سيناريو 2.2/2.3) —
    # الحماية الفعلية من ذاك السيناريو موجودة أصلاً بإصلاحات app.js
    # (escHtml + حذف حيلة innerHTML). CSP تبقى مفيدة كطبقة إضافية حقيقية:
    # تمنع تحميل أي <script src> من نطاق غير مُصرَّح، وeval، وتغيير
    # <base>، والتضمين بإطار خارجي (clickjacking عبر X-Frame-Options).
    # إزالة unsafe-inline لاحقاً تحتاج ترحيل كل onclick= لـaddEventListener
    # — تغيير بنيوي أكبر، خارج نطاق هذا الإصلاح السريع.
    @app.after_request
    def set_security_headers(resp):
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['X-Frame-Options'] = 'DENY'
        resp.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        resp.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
            "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
            "img-src 'self' data: blob: https://image.pollinations.ai https://*.supabase.co; "
            "connect-src 'self'; "
            "media-src 'self' blob:; "
            "frame-src 'self' blob:; "
            "object-src 'none'; base-uri 'none'"
        )
        return resp

    from app.db import init_db
    init_db()

    from app.storage import storage_configured
    if not storage_configured():
        log.warning("SUPABASE_URL/SUPABASE_SERVICE_KEY غير مضبوطين — الصور المرفوعة للتحليل لن "
                    "تُخزَّن بشكل دائم (ميزة اختيارية، التطبيق يعمل بدونها)")

    if not Config.ADMIN_EMAILS:
        log.warning("ADMIN_EMAILS غير مضبوط — لوحة التحكم (/admin) غير متاحة لأي أحد حالياً "
                    "(هذا هو السلوك الآمن الافتراضي، وليس خطأً)")

    import os
    if not os.environ.get("PASSWORD_SALT"):
        log.warning(
            "PASSWORD_SALT غير مضبوط — أي حساب لا يزال بهاش SHA-256 قديم (قبل "
            "الترقية التلقائية لـbcrypt عند أول دخول ناجح) محمي حالياً بملح "
            "افتراضي مكتوب بكود التطبيق نفسه، لا بسرّ حقيقي (انظر شرح كامل "
            "بـsecurity.py). لتتأكد إن كان هذا يخصّك فعلياً أو لا، شغّل بمحرر "
            "SQL بلوحة Supabase: "
            "SELECT COUNT(*) FROM users WHERE password_hash ~ '^[0-9a-f]{64}$' — "
            "لو النتيجة صفر، لا خطر فعلي حالياً (كل الحسابات مُرقّاة لـbcrypt "
            "بالفعل) ويمكنك حذف مسار SHA-256 القديم بالكامل من الكود."
        )

    # ─── Blueprints ──────────────────────────────────────────────
    from app.routes.auth import bp as auth_bp
    from app.routes.pages import bp as pages_bp
    from app.routes.api import bp as api_bp
    from app.routes.admin import bp as admin_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(admin_bp)

    # ─── رد خطأ موحَّد حسب القناة اللي كل مسار /api/ يتوقعها فعلياً ────
    # /api/chat و/api/speak كلاهما SSE فعلياً، لكن بشكلي حدث مختلفين
    # تماماً بالواجهة (app.js): الأول يتوقع حدث 'done' بشكل رسالة دردشة
    # كاملة (response/rawResponse/id)، والثاني يتوقع حدث 'error' بسيط
    # (error فقط — انظر speakMessage بـapp.js). نسخ منطق /api/chat
    # حرفياً لـ/api/speak (كما كان مقترحاً أول الأمر) كان سيعطي شكل حدث
    # غلط ما تفهمه دالة speakMessage إطلاقاً. تُستخدم بثلاث معالجات تحت
    # (429، CSRFError، الاستثناء العام) بدل تكرار نفس التفريع 3 مرات.
    def _api_channel_error(path, friendly, json_status):
        from flask import jsonify, Response
        from app.routes.api import _sse
        if path == '/api/chat':
            def _gen():
                yield _sse('done', response=friendly, rawResponse="", id=None)
            return Response(_gen(), mimetype='text/event-stream')
        if path == '/api/speak':
            def _gen():
                yield _sse('error', error=friendly)
            return Response(_gen(), mimetype='text/event-stream')
        if path.startswith('/api/'):
            return jsonify({"error": friendly}), json_status
        return None  # المسار خارج /api/ — يقرر المستدعي السلوك المناسب

    # ─── معالج تجاوز حد المعدل (429) ────────────────────────────
    @app.errorhandler(429)
    def ratelimit_handler(e):
        from flask import jsonify
        api_resp = _api_channel_error(
            request.path, "⏱️ أرسلت طلبات كثيرة. انتظر قليلاً ثم حاول مجدداً.", 429
        )
        if api_resp is not None:
            return api_resp
        if request.path.startswith('/admin'):
            return jsonify({"error": "⏱️ طلبات كثيرة جداً على لوحة التحكم. انتظر قليلاً."}), 429
        if request.path.startswith('/reset-password/'):
            token = request.view_args.get('token', '') if request.view_args else ''
            return render_template(
                'reset_password.html', token=token, invalid=False,
                error='محاولات كثيرة جداً. انتظر بضع دقائق ثم حاول مجدداً.'
            ), 429
        if request.path == '/register':
            return render_template(
                'auth.html', mode='register', title='حساب جديد',
                error='محاولات كثيرة جداً. انتظر بضع دقائق ثم حاول مجدداً.',
                prefill_name=request.form.get('name', ''), prefill_email=request.form.get('email', '')
            ), 429
        return render_template(
            'auth.html', mode='login', title='تسجيل الدخول',
            error='محاولات كثيرة جداً. انتظر بضع دقائق ثم حاول مجدداً.'
        ), 429

    # ─── معالج رفض CSRF ──────────────────────────────────────────
    # يصير لما رمز الحماية مفقود/منتهي — عادة صفحة قديمة فُتحت من زمان
    # وما تحدَّثت، أو الجلسة انتهت. نفس فلسفة معالج 429 أعلاه بالضبط:
    # كل مسار يرجع بنفس الصيغة اللي الواجهة تتوقعها منه.
    from flask_wtf.csrf import CSRFError

    @app.errorhandler(CSRFError)
    def csrf_error_handler(e):
        from flask import jsonify
        friendly = "⚠️ انتهت صلاحية الجلسة أو الصفحة قديمة. أعد تحميل الصفحة وحاول مجدداً."
        api_resp = _api_channel_error(request.path, friendly, 400)
        if api_resp is not None:
            return api_resp
        if request.path.startswith('/admin'):
            return jsonify({"error": friendly}), 400
        if request.path.startswith('/reset-password/'):
            token = request.view_args.get('token', '') if request.view_args else ''
            return render_template('reset_password.html', token=token, invalid=False, error=friendly), 400
        if request.path == '/register':
            return render_template(
                'auth.html', mode='register', title='حساب جديد', error=friendly,
                prefill_name=request.form.get('name', ''), prefill_email=request.form.get('email', '')
            ), 400
        return render_template(
            'auth.html', mode='login', title='تسجيل الدخول', error=friendly
        ), 400

    # ─── معالج شامل لأي خطأ غير متوقع (500) ────────────────────────
    # بدون هذا، أي استثناء غير متوقّع بأي مكان بالكود (DB، Groq، أي
    # سطر ناسينا نلفّه بـ try/except) يوصل لصفحة خطأ HTML افتراضية من
    # Flask — الواجهة (app.js) ما تقدر تفهم منها شيء فترجع رسالة عامة
    # "خطأ في الخادم" بلا أي تفاصيل، لا للمستخدم ولا لنا بالسجلات.
    # هذا المعالج: (1) يسجّل الخطأ الحقيقي كاملاً بالسجلات (exc_info)
    # حتى نقدر نشخّصه فعلياً من سجلات Render، و(2) يرجّع رسالة واضحة
    # بنفس القناة اللي الواجهة تتوقعها (SSE لـ/api/chat، JSON للباقي
    # تحت /api/) — نفس فلسفة معالجي 429 وCSRFError أعلاه بالضبط.
    # نستثني HTTPException (404، 405، إلخ) ونخليها تاخذ سلوكها
    # الافتراضي الطبيعي — هذا المعالج لأخطائنا نحن فقط.
    from werkzeug.exceptions import HTTPException

    @app.errorhandler(Exception)
    def unhandled_error_handler(e):
        if isinstance(e, HTTPException):
            return e
        log.error(f"خطأ غير متوقع لم يُعالَج ({request.path}): {e}", exc_info=True)
        friendly = "⚠️ صار خطأ غير متوقع من جهتنا. حاول مرة ثانية، ولو تكرر معك خبّرنا."
        api_resp = _api_channel_error(request.path, friendly, 500)
        if api_resp is not None:
            return api_resp
        return friendly, 500

    log.info("التطبيق جاهز")
    return app
