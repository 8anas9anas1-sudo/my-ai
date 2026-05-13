import os
import secrets
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import logging

from services.ai_service import AIService
from database.db_manager import get_db, init_db

# إعداد اللوج
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
CORS(app)

ai_service = AIService()

# إنشاء الجداول لما يولع السيرفر
init_db()

# --- واجهة تسجيل الدخول الأصلية الحلوة ---
LOGIN_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>وادي AI - تسجيل الدخول</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .container {
            background: #1e293b;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.5);
            padding: 40px;
            width: 100%;
            max-width: 450px;
            border: 1px solid #334155;
        }
        .logo { text-align: center; margin-bottom: 30px; }
        .logo h1 {
            color: #38bdf8;
            font-size: 32px;
            margin-bottom: 10px;
        }
        .logo p { color: #94a3b8; font-size: 14px; }
        .form-group { margin-bottom: 20px; }
        label {
            display: block;
            color: #cbd5e1;
            margin-bottom: 8px;
            font-size: 14px;
        }
        input {
            width: 100%;
            padding: 14px;
            background: #0f172a;
            border: 2px solid #334155;
            border-radius: 12px;
            color: #f1f5f9;
            font-size: 15px;
            transition: all 0.3s;
        }
        input:focus {
            outline: none;
            border-color: #38bdf8;
            box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.1);
        }
        .btn {
            width: 100%;
            padding: 14px;
            background: linear-gradient(135deg, #38bdf8 0%, #0ea5e9 100%);
            color: white;
            border: none;
            border-radius: 12px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s;
        }
        .btn:hover { transform: translateY(-2px); }
        .toggle {
            text-align: center;
            margin-top: 20px;
            color: #94a3b8;
            font-size: 14px;
        }
        .toggle a {
            color: #38bdf8;
            text-decoration: none;
            font-weight: 600;
            cursor: pointer;
        }
        .error {
            background: #7f1d1d;
            color: #fecaca;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 14px;
            display: none;
        }
        .hidden { display: none; }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">
            <h1>🌊 وادي AI</h1>
            <p>مساعدك الذكي باللهجة الليبية</p>
        </div>
        
        <div id="error" class="error"></div>
        
        <!-- فورم تسجيل الدخول -->
        <form id="loginForm">
            <div class="form-group">
                <label>الإيميل</label>
                <input type="email" id="loginEmail" required>
            </div>
            <div class="form-group">
                <label>كلمة السر</label>
                <input type="password" id="loginPassword" required>
            </div>
            <button type="submit" class="btn">دخول</button>
            <div class="toggle">
                ما عندكش حساب؟ <a onclick="showRegister()">سجل هنا</a>
            </div>
        </form>
        
        <!-- فورم إنشاء حساب -->
        <form id="registerForm" class="hidden">
            <div class="form-group">
                <label>الاسم</label>
                <input type="text" id="registerName" required>
            </div>
            <div class="form-group">
                <label>الإيميل</label>
                <input type="email" id="registerEmail" required>
            </div>
            <div class="form-group">
                <label>كلمة السر</label>
                <input type="password" id="registerPassword" required>
            </div>
            <button type="submit" class="btn">إنشاء حساب</button>
            <div class="toggle">
                عندك حساب؟ <a onclick="showLogin()">سجل دخول</a>
            </div>
        </form>
    </div>
    
    <script>
        function showRegister() {
            document.getElementById('loginForm').classList.add('hidden');
            document.getElementById('registerForm').classList.remove('hidden');
            document.getElementById('error').style.display = 'none';
        }
        
        function showLogin() {
            document.getElementById('registerForm').classList.add('hidden');
            document.getElementById('loginForm').classList.remove('hidden');
            document.getElementById('error').style.display = 'none';
        }
        
        function showError(msg) {
            const err = document.getElementById('error');
            err.textContent = msg;
            err.style.display = 'block';
        }
        
        document.getElementById('loginForm').onsubmit = async (e) => {
            e.preventDefault();
            const email = document.getElementById('loginEmail').value;
            const password = document.getElementById('loginPassword').value;
            
            const res = await fetch('/api/login', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({email, password})
            });
            
            const data = await res.json();
            if (data.success) {
                window.location.href = '/chat';
            } else {
                showError(data.error || 'صار خطأ في تسجيل الدخول');
            }
        };
        
        document.getElementById('registerForm').onsubmit = async (e) => {
            e.preventDefault();
            const name = document.getElementById('registerName').value;
            const email = document.getElementById('registerEmail').value;
            const password = document.getElementById('registerPassword').value;
            
            const res = await fetch('/api/register', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name, email, password})
            });
            
            const data = await res.json();
            if (data.success) {
                window.location.href = '/chat';
            } else {
                showError(data.error || 'صار خطأ في إنشاء الحساب');
            }
        };
    </script>
</body>
</html>
'''

@app.route('/')
def home():
    if 'user_email' in session:
        return redirect(url_for('chat_page'))
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/chat')
def chat_page():
    if 'user_email' not in session:
        return redirect(url_for('home'))
    return "صفحة الشات - قيد التطوير"  # مؤقتاً

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
                # شيك لو الإيميل موجود
                cur.execute('SELECT id FROM users WHERE email = %s', (email,))
                if cur.fetchone():
                    return jsonify({'success': False, 'error': 'الإيميل مسجل من قبل'})
                
                # أنشئ المستخدم
                password_hash = generate_password_hash(password)
                cur.execute(
                    'INSERT INTO users (email, password_hash, name) VALUES (%s, %s, %s)',
                    (email, password_hash, name)
                )
        
        session['user_email'] = email
        session['user_name'] = name
        return jsonify({'success': True})
        
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
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'الإيميل أو كلمة السر غلط'})
            
    except Exception as e:
        logger.error(f"Login error: {e}")
        return jsonify({'success': False, 'error': 'صار خطأ في السيرفر'})

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
