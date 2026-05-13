import os
import secrets
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import logging

from services.ai_service import AIService
from database.db_manager import get_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
CORS(app)

ai_service = AIService()

# --- واجهة anas wadi الأصلية كاملة ---
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>anas wadi - مساعدك الذكي</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        /* حط هنا كل الـ CSS الأصلي متاعك كامل بدون تغيير */
        /* اللي كان في ملفك الأصلي من <style> إلى </style> */
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #0f172a;
            color: #f1f5f9;
            min-height: 100vh;
            overflow-x: hidden;
        }
        /*... باقي CSS متاعك الأصلي كامل... */
        /* انسخ كل الستايل من ملفك الأصلي هنا */
    </style>
</head>
<body>
    <!-- حط هنا كل الـ HTML الأصلي متاعك كامل -->
    <!-- من <body> لعند </script> انسخه زي ما هو -->
    <!-- اللي فيه صفحة تسجيل الدخول + صفحة الشات + كل الجافاسكريبت -->

    <!-- مثال: -->
    <div id="authPage" class="auth-container">
        <!--... كودك الأصلي... -->
    </div>

    <div id="chatPage" class="chat-container">
        <!--... كودك الأصلي... -->
    </div>

    <script>
        //... كل الجافاسكريبت الأصلي متاعك كامل...
    </script>
</body>
</html>'''

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/register', methods=['POST'])
def register():
    try:
        data = request.json
        name = data.get('name')
        email = data.get('email')
        password = data.get('password')
        if not all([name, email, password]):
            return jsonify({'success': False, 'error': 'كل الحقول مطلوبة'})

        with get_db() as conn:
            with conn.cursor() as cur:
                # ننشئ الجداول هنا بدل init_db() عشان ما يطيحش السيرفر
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        name TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT NOW()
                    );
                ''')
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS conversations (
                        id SERIAL PRIMARY KEY,
                        chat_id TEXT NOT NULL,
                        user_email TEXT NOT NULL,
                        user_name TEXT,
                        user_message TEXT,
                        ai_response TEXT,
                        raw_ai TEXT,
                        mode TEXT DEFAULT 'fast',
                        image_url TEXT,
                        file_name TEXT,
                        created_at TIMESTAMP DEFAULT NOW()
                    );
                ''')
                cur.execute('SELECT id FROM users WHERE email = %s', (email,))
                if cur.fetchone():
                    return jsonify({'success': False, 'error': 'الإيميل مسجل من قبل'})
                password_hash = generate_password_hash(password)
                cur.execute(
                    'INSERT INTO users (email, password_hash, name) VALUES (%s, %s, %s) RETURNING id, email, name',
                    (email, password_hash, name)
                )
                user = cur.fetchone()

        session['user_email'] = user['email']
        session['user_name'] = user['name']
        return jsonify({'success': True, 'user': {'email': user['email'], 'name': user['name']}})
    except Exception as e:
        logger.error(f"Register error: {e}")
        return jsonify({'success': False, 'error': 'صار خطأ في السيرفر'})

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.json
        email = data.get('email')
        password = data.get('password')
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT * FROM users WHERE email = %s', (email,))
                user = cur.fetchone()
        if user and check_password_hash(user['password_hash'], password):
            session['user_email'] = user['email']
            session['user_name'] = user['name']
            return jsonify({'success': True, 'user': {'email': user['email'], 'name': user['name']}})
        else:
            return jsonify({'success': False, 'error': 'الإيميل أو كلمة السر غلط'})
    except Exception as e:
        logger.error(f"Login error: {e}")
        return jsonify({'success': False, 'error': 'صار خطأ في السيرفر'})

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/check_session')
def check_session():
    if 'user_email' in session:
        return jsonify({
            'logged_in': True,
            'user': {'email': session['user_email'], 'name': session.get('user_name', '')}
        })
    return jsonify({'logged_in': False})

@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        if 'user_email' not in session:
            return jsonify({'success': False, 'error': 'لازم تسجل دخول'})
        data = request.json
        message = data.get('message', '')
        if not message:
            return jsonify({'success': False, 'error': 'الرسالة فاضية'})

        response = ai_service.get_response(message, session['user_email'])

        # حفظ المحادثة
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'INSERT INTO conversations (chat_id, user_email, user_name, user_message, ai_response) VALUES (%s, %s, %s, %s, %s)',
                    ('default', session['user_email'], session['user_name'], message, response)
                )
        return jsonify({'success': True, 'response': response})
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return jsonify({'success': False, 'error': 'صار خطأ في السيرفر'})

@app.route('/health')
def health():
    return jsonify({'status': 'healthy'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
