import logging
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from database.db_manager import get_db
import bleach

logger = logging.getLogger(__name__)
auth_bp = Blueprint('auth', __name__)

def sanitize(text):
    """ينظف المدخلات من XSS"""
    return bleach.clean(text.strip(), tags=[], strip=True)[:255]

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = sanitize(request.form.get('name', ''))
        email = sanitize(request.form.get('email', '')).lower()
        password = request.form.get('password', '')

        # تحقق من المدخلات
        if not name or not email or not password:
            flash('كل الخانات مطلوبة', 'error')
            return render_template('register.html')

        if len(password) < 8:
            flash('الباسورد لازم 8 أحرف على الأقل', 'error')
            return render_template('register.html')

        # شفّر الباسورد - هذا الآمن مش SHA256
        password_hash = generate_password_hash(password, method='pbkdf2:sha256:600000')

        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    # تأكد إن الإيميل مش مسجل
                    cur.execute("SELECT id FROM users WHERE email = %s", (email,))
                    if cur.fetchone():
                        flash('الإيميل هذا مسجل من قبل', 'error')
                        return render_template('register.html')

                    # سجل المستخدم الجديد
                    cur.execute("""
                        INSERT INTO users (email, password_hash, name)
                        VALUES (%s, %s, %s)
                    """, (email, password_hash, name))
                    conn.commit()

                    logger.info(f"New user registered: {email}")
                    flash('تم إنشاء الحساب بنجاح. سجل دخولك', 'success')
                    return redirect(url_for('auth.login'))

        except Exception as e:
            logger.error(f"Register error: {e}")
            flash('صار خطأ في السيرفر', 'error')

    return render_template('register.html')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = sanitize(request.form.get('email', '')).lower()
        password = request.form.get('password', '')

        if not email or not password:
            flash('اكتب الإيميل والباسورد', 'error')
            return render_template('login.html')

        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, email, password_hash, name
                        FROM users WHERE email = %s
                    """, (email,))
                    user = cur.fetchone()

                    if user and check_password_hash(user[2], password):
                        # تسجيل دخول ناجح
                        session['user_id'] = user[0]
                        session['email'] = user[1]
                        session['name'] = user[3]
                        session.permanent = True

                        logger.info(f"User logged in: {email}")
                        return redirect(url_for('pages.home'))
                    else:
                        flash('الإيميل أو الباسورد غلط', 'error')

        except Exception as e:
            logger.error(f"Login error: {e}")
            flash('صار خطأ في السيرفر', 'error')

    return render_template('login.html')

@auth_bp.route('/logout')
def logout():
    email = session.get('email', 'unknown')
    session.clear()
    logger.info(f"User logged out: {email}")
    flash('تم تسجيل الخروج', 'success')
    return redirect(url_for('auth.login'))
