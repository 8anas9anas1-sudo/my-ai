from flask import Blueprint, request, render_template, session, redirect, g

from app.config import Config
from app.extensions import limiter
from app.db import create_user, verify_user, get_valid_password_reset, consume_password_reset
from app.security import get_client_ip

bp = Blueprint('auth', __name__)


@bp.route('/login', methods=['GET', 'POST'])
@limiter.limit(
    f"{Config.MAX_LOGIN_ATTEMPTS} per {Config.LOGIN_WINDOW_SECONDS} seconds",
    key_func=lambda: f"{get_client_ip()}:{(request.form.get('email') or '').strip().lower()}",
    exempt_when=lambda: request.method != 'POST',
    deduct_when=lambda response: getattr(g, 'login_failed', False),
)
def login():
    if request.method == 'POST':
        g.login_failed = True  # القيمة الافتراضية؛ تُلغى فقط عند نجاح الدخول
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        if not email or not password:
            return render_template('auth.html', mode='login', title='تسجيل الدخول',
                                    error='يرجى ملء جميع الحقول')
        user = verify_user(email, password)
        if user:
            g.login_failed = False
            session.permanent = Config.SESSION_PERMANENT
            session['user'] = {'email': user['email'], 'name': user['name']}
            return redirect('/')
        return render_template('auth.html', mode='login', title='تسجيل الدخول',
                                error='البريد الإلكتروني أو كلمة المرور غير صحيحة')
    return render_template('auth.html', mode='login', title='تسجيل الدخول',
                            error=None, success=None)


@bp.route('/register', methods=['GET', 'POST'])
@limiter.limit(
    f"{Config.MAX_LOGIN_ATTEMPTS} per {Config.LOGIN_WINDOW_SECONDS} seconds",
    key_func=lambda: f"{get_client_ip()}:register",
    exempt_when=lambda: request.method != 'POST',
    deduct_when=lambda response: getattr(g, 'register_failed', False),
)
def register():
    if request.method == 'POST':
        g.register_failed = True  # القيمة الافتراضية؛ تُلغى فقط عند نجاح التسجيل
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        if not name or not email or not password:
            return render_template('auth.html', mode='register', title='حساب جديد',
                                    error='يرجى ملء جميع الحقول',
                                    prefill_name=name, prefill_email=email)
        if len(password) < 6:
            return render_template('auth.html', mode='register', title='حساب جديد',
                                    error='كلمة المرور يجب أن تكون 6 أحرف على الأقل',
                                    prefill_name=name, prefill_email=email)
        if '@' not in email or '.' not in email.split('@')[-1]:
            return render_template('auth.html', mode='register', title='حساب جديد',
                                    error='يرجى إدخال بريد إلكتروني صحيح',
                                    prefill_name=name, prefill_email=email)
        ok, msg = create_user(email, password, name)
        if ok:
            g.register_failed = False
            session.permanent = Config.SESSION_PERMANENT
            session['user'] = {'email': email.lower().strip(), 'name': name}
            return redirect('/')
        return render_template('auth.html', mode='register', title='حساب جديد',
                                error=msg, prefill_name=name, prefill_email=email)
    return render_template('auth.html', mode='register', title='حساب جديد',
                            error=None, success=None, prefill_name=None, prefill_email=None)


@bp.route('/logout')
def logout():
    session.pop('user', None)
    return redirect('/login')


@bp.route('/reset-password/<token>', methods=['GET', 'POST'])
@limiter.limit(
    f"{Config.MAX_LOGIN_ATTEMPTS} per {Config.LOGIN_WINDOW_SECONDS} seconds",
    key_func=get_client_ip,
)
def reset_password(token):
    """
    رابط استرداد يولّده الأدمن يدوياً من /admin (لا بريد إلكتروني
    بالمشروع). صالح 24 ساعة، استخدام واحد فقط — get_valid_password_reset
    تتحقق من الشرطين، consume_password_reset تحرقه ذرياً عند الاستخدام
    الفعلي حتى لا يُستخدم مرتين.
    """
    reset = get_valid_password_reset(token)
    if not reset:
        return render_template(
            'reset_password.html', token=token, invalid=True, error=None
        )

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        if len(password) < 6:
            return render_template('reset_password.html', token=token, invalid=False,
                                    error='كلمة المرور يجب أن تكون 6 أحرف على الأقل')
        if password != confirm:
            return render_template('reset_password.html', token=token, invalid=False,
                                    error='كلمتا المرور غير متطابقتين')
        ok = consume_password_reset(token, password)
        if ok:
            return render_template(
                'auth.html', mode='login', title='تسجيل الدخول', error=None,
                success='✅ تم تغيير كلمة المرور بنجاح — سجّل دخولك الآن'
            )
        return render_template('reset_password.html', token=token, invalid=True, error=None)

    return render_template('reset_password.html', token=token, invalid=False, error=None)
