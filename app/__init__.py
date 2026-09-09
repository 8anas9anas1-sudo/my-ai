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

    from app.db import init_db
    init_db()

    from app.storage import storage_configured
    if not storage_configured():
        log.warning("SUPABASE_URL/SUPABASE_SERVICE_KEY غير مضبوطين — الصور المرفوعة للتحليل لن "
                    "تُخزَّن بشكل دائم (ميزة اختيارية، التطبيق يعمل بدونها)")

    if not Config.ADMIN_EMAILS:
        log.warning("ADMIN_EMAILS غير مضبوط — لوحة التحكم (/admin) غير متاحة لأي أحد حالياً "
                    "(هذا هو السلوك الآمن الافتراضي، وليس خطأً)")

    # ─── Blueprints ──────────────────────────────────────────────
    from app.routes.auth import bp as auth_bp
    from app.routes.pages import bp as pages_bp
    from app.routes.api import bp as api_bp
    from app.routes.admin import bp as admin_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(admin_bp)

    # ─── معالج تجاوز حد المعدل (429) ────────────────────────────
    @app.errorhandler(429)
    def ratelimit_handler(e):
        from flask import jsonify
        if request.path.startswith('/api/'):
            return jsonify({"error": "⏱️ أرسلت طلبات كثيرة. انتظر قليلاً ثم حاول مجدداً."}), 429
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
        from flask import jsonify, Response
        from app.routes.api import _sse
        friendly = "⚠️ انتهت صلاحية الجلسة أو الصفحة قديمة. أعد تحميل الصفحة وحاول مجدداً."
        if request.path == '/api/chat':
            # هذا المسار الوحيد اللي الواجهة تتوقع منه SSE حصراً؛ الباقي
            # تحت /api/ (transcribe, speak, chat/<id> DELETE) يقرأ JSON عادي
            def _csrf_rejected():
                yield _sse('done', response=friendly, rawResponse="", id=None)
            return Response(_csrf_rejected(), mimetype='text/event-stream')
        if request.path.startswith('/api/'):
            return jsonify({"error": friendly}), 400
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

    log.info("التطبيق جاهز")
    return app
