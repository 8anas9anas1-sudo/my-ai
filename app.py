import os
import secrets
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import logging
import json

from services.ai_service import AIService
from database.db_manager import get_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
CORS(app)

ai_service = AIService()

# --- الواجهة الأصلية الكاملة ---
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>anas wadi - مساعدك الذكي</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #0f172a;
            color: #f1f5f9;
            min-height: 100vh;
            overflow-x: hidden;
        }
        
        /* صفحة تسجيل الدخول */
        .auth-container {
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            padding: 20px;
        }
        .auth-box {
            background: #1e293b;
            border-radius: 24px;
            box-shadow: 0 25px 80px rgba(0,0,0,0.6);
            padding: 45px;
            width: 100%;
            max-width: 480px;
            border: 1px solid #334155;
        }
        .logo-auth { text-align: center; margin-bottom: 35px; }
        .logo-auth h1 {
            color: #38bdf8;
            font-size: 36px;
            margin-bottom: 8px;
            font-weight: 700;
        }
        .logo-auth p { color: #94a3b8; font-size: 15px; }
        .form-group { margin-bottom: 22px; }
        label {
            display: block;
            color: #cbd5e1;
            margin-bottom: 9px;
            font-size: 14px;
            font-weight: 500;
        }
        input {
            width: 100%;
            padding: 15px;
            background: #0f172a;
            border: 2px solid #334155;
            border-radius: 14px;
            color: #f1f5f9;
            font-size: 15px;
            transition: all 0.3s;
        }
        input:focus {
            outline: none;
            border-color: #38bdf8;
            box-shadow: 0 0 0 4px rgba(56, 189, 248, 0.15);
        }
        .btn-auth {
            width: 100%;
            padding: 16px;
            background: linear-gradient(135deg, #38bdf8 0%, #0ea5e9 100%);
            color: white;
            border: none;
            border-radius: 14px;
            font-size: 17px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.3s;
            margin-top: 10px;
        }
        .btn-auth:hover { 
            transform: translateY(-3px);
            box-shadow: 0 10px 25px rgba(56, 189, 248, 0.4);
        }
        .toggle-auth {
            text-align: center;
            margin-top: 24px;
            color: #94a3b8;
            font-size: 15px;
        }
        .toggle-auth a {
            color: #38bdf8;
            text-decoration: none;
            font-weight: 700;
            cursor: pointer;
        }
        .toggle-auth a:hover { text-decoration: underline; }
        .error {
            background: #7f1d1d;
            color: #fecaca;
            padding: 14px;
            border-radius: 10px;
            margin-bottom: 22px;
            font-size: 14px;
            display: none;
            border: 1px solid #dc2626;
        }
        .hidden { display: none !important; }
        
        /* صفحة الشات */
        .chat-container {
            display: none;
            height: 100vh;
            flex-direction: column;
            background: #0f172a;
        }
        .chat-container.active { display: flex; }
        .chat-header {
            background: #1e293b;
            padding: 18px 24px;
            border-bottom: 1px solid #334155;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .chat-header h2 {
            color: #38bdf8;
            font-size: 22px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .user-info {
            display: flex;
            align-items: center;
            gap: 15px;
        }
        .user-info span { color: #cbd5e1; font-size: 15px; }
        .btn-logout {
            padding: 10px 18px;
            background: #7f1d1d;
            color: white;
            border: none;
            border-radius: 10px;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.2s;
        }
        .btn-logout:hover { background: #991b1b; }
        .chat-messages {
            flex: 1;
            overflow-y: auto;
            padding: 24px;
            display: flex;
            flex-direction: column;
            gap: 18px;
        }
        .message {
            max-width: 75%;
            padding: 14px 18px;
            border-radius: 18px;
            animation: fadeIn 0.3s;
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .message.user {
            background: linear-gradient(135deg, #38bdf8 0%, #0ea5e9 100%);
            color: white;
            align-self: flex-end;
            margin-right: auto;
        }
        .message.ai {
            background: #1e293b;
            color: #f1f5f9;
            align-self: flex-start;
            border: 1px solid #334155;
        }
        .message-time {
            font-size: 11px;
            opacity: 0.7;
            margin-top: 6px;
        }
        .chat-input-area {
            background: #1e293b;
            padding: 20px 24px;
            border-top: 1px solid #334155;
            display: flex;
            gap: 12px;
            align-items: center;
        }
        .chat-input {
            flex: 1;
            padding: 14px 18px;
            background: #0f172a;
            border: 2px solid #334155;
            border-radius: 14px;
            color: #f1f5f9;
            font-size: 15px;
            resize: none;
            max-height: 120px;
        }
        .chat-input:focus {
            outline: none;
            border-color: #38bdf8;
        }
        .btn-send {
            padding: 14px 24px;
            background: linear-gradient(135deg, #38bdf8 0%, #0ea5e9 100%);
            color: white;
            border: none;
            border-radius: 14px;
            cursor: pointer;
            font-size: 16px;
            transition: all 0.2s;
        }
        .btn-send:hover { transform: scale(1.05); }
        .btn-send:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        .typing-indicator {
            display: none;
            padding: 14px 18px;
            background: #1e293b;
            border-radius: 18px;
            align-self: flex-start;
            border: 1px solid #334155;
        }
        .typing-indicator.active { display: block; }
        .typing-indicator span {
            display: inline-block;
            width: 8px;
            height: 8px;
            background: #38bdf8;
            border-radius: 50%;
            margin: 0 2px;
            animation: typing 1.4s infinite;
        }
        .typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
        .typing-indicator span:nth-child(3) { animation-delay: 0.4s; }
        @keyframes typing {
            0%, 60%, 100% { transform: translateY(0); }
            30% { transform: translateY(-10px); }
        }
    </style>
</head>
<body>
    <!-- صفحة تسجيل الدخول -->
    <div id="authPage" class="auth-container">
        <div class="auth-box">
            <div class="logo-auth">
                <h1>✨ anas wadi ✨</h1>
                <p>مساعدك الذكي باللهجة الليبية</p>
            </div>
            
            <div id="error" class="error"></div>
            
            <!-- فورم تسجيل الدخول -->
            <form id="loginForm">
                <div class="form-group">
                    <label>الإيميل</label>
                    <input type="email" id="loginEmail" required placeholder="example@mail.com">
                </div>
                <div class="form-group">
                    <label>كلمة السر</label>
                    <input type="password" id="loginPassword" required placeholder="••••••••">
                </div>
                <button type="submit" class="btn-auth">دخول</button>
                <div class="toggle-auth">
                    ما عندكش حساب؟ <a onclick="showRegister()">سجل هنا</a>
                </div>
            </form>
            
            <!-- فورم إنشاء حساب -->
            <form id="registerForm" class="hidden">
                <div class="form-group">
                    <label>الاسم</label>
                    <input type="text" id="registerName" required placeholder="اسمك">
                </div>
                <div class="form-group">
                    <label>الإيميل</label>
                    <input type="email" id="registerEmail" required placeholder="example@mail.com">
                </div>
                <div class="form-group">
                    <label>كلمة السر</label>
                    <input type="password" id="registerPassword" required placeholder="••••••••">
                </div>
                <button type="submit" class="btn-auth">إنشاء حساب</button>
                <div class="toggle-auth">
                    عندك حساب؟ <a onclick="showLogin()">سجل دخول</a>
                </div>
            </form>
        </div>
    </div>
    
    <!-- صفحة الشات -->
    <div id="chatPage" class="chat-container">
        <div class="chat-header">
            <h2><i class="fas fa-robot"></i> ✨ anas wadi ✨</h2>
            <div class="user-info">
                <span id="userName"></span>
                <button class="btn-logout" onclick="logout()"><i class="fas fa-sign-out-alt"></i> خروج</button>
            </div>
        </div>
        
        <div class="chat-messages" id="chatMessages">
            <div class="message ai">
                <div>أهلاً بيك! أنا anas wadi، مساعدك الذكي. كيف نقدر نساعدك اليوم؟ 😊</div>
                <div class="message-time">الآن</div>
            </div>
        </div>
        
        <div class="typing-indicator" id="typingIndicator">
            <span></span><span></span>
        </div>
        
        <div class="chat-input-area">
            <textarea 
                id="chatInput" 
                class="chat-input" 
                placeholder="اكتب رسالتك هنا..."
                rows="1"
            ></textarea>
            <button class="btn-send" id="sendBtn" onclick="sendMessage()">
                <i class="fas fa-paper-plane"></i>
            </button>
        </div>
    </div>
    
    <script>
        let currentUser = null;
        
        // تبديل الفورمات
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
        
        // تسجيل الدخول
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
                currentUser = data.user;
                showChatPage();
            } else {
                showError(data.error || 'صار خطأ في تسجيل الدخول');
            }
        };
        
        // إنشاء حساب
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
                currentUser = data.user;
                showChatPage();
            } else {
                showError(data.error || 'صار خطأ في إنشاء الحساب');
            }
        };
        
        // عرض صفحة الشات
        function showChatPage() {
            document.getElementById('authPage').style.display = 'none';
            document.getElementById('chatPage').classList.add('active');
            document.getElementById('userName').textContent = currentUser.name;
        }
        
        // تسجيل خروج
        async function logout() {
            await fetch('/api/logout', { method: 'POST' });
            location.reload();
        }
        
        // إرسال رسالة
        async function sendMessage() {
            const input = document.getElementById('chatInput');
            const message = input.value.trim();
            if (!message) return;
            
            const sendBtn = document.getElementById('sendBtn');
            sendBtn.disabled = true;
            
            // عرض رسالة المستخدم
            addMessage(message, 'user');
            input.value = '';
            
            // عرض مؤشر الكتابة
            document.getElementById('typingIndicator').classList.add('active');
            
            try {
                const res = await fetch('/api/chat', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({message})
                });
                
                const data = await res.json();
                document.getElementById('typingIndicator').classList.remove('active');
                
                if (data.success) {
                    addMessage(data.response, 'ai');
                } else {
                    addMessage('عذراً، صار خطأ. حاول مرة ثانية', 'ai');
                }
            } catch (err) {
                document.getElementById('typingIndicator').classList.remove('active');
                addMessage('عذراً، صار خطأ في الاتصال', 'ai');
            }
            
            sendBtn.disabled = false;
            input.focus();
        }
        
        // إضافة رسالة للشات
        function addMessage(text, sender) {
            const messagesDiv = document.getElementById('chatMessages');
            const msgDiv = document.createElement('div');
            msgDiv.className = `message ${sender}`;
            
            const time = new Date().toLocaleTimeString('ar-LY', {hour: '2-digit', minute: '2-digit'});
            msgDiv.innerHTML = `
                <div>${text}</div>
                <div class="message-time">${time}</div>
            `;
            
            messagesDiv.appendChild(msgDiv);
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        }
        
        // إرسال بالانتر
        document.getElementById('chatInput').addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        
        // تكبير التكست اريا تلقائياً
        document.getElementById('chatInput').addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 120) + 'px';
        });
        
        // شيك لو المستخدم مسجل
        window.onload = async () => {
            const res = await fetch('/api/check_session');
            const data = await res.json();
            if (data.logged_in) {
                currentUser = data.user;
                showChatPage();
            }
        };
    </script>
</body>
</html>
'''

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
                # ننشئ الجدول لو مش موجود
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
                    CREATE TABLE IF NOT EXISTS chat_history (
                        id SERIAL PRIMARY KEY,
                        user_email TEXT NOT NULL,
                        message TEXT NOT NULL,
                        response TEXT NOT NULL,
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
        logger.info(f"New user registered: {email}")
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
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        name TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT NOW()
                    );
                ''')
                cur.execute('SELECT * FROM users WHERE email = %s', (email,))
                user = cur.fetchone()
        
        if user and check_password_hash(user['password_hash'], password):
            session['user_email'] = user['email']
            session['user_name'] = user['name']
            logger.info(f"User logged in: {email}")
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
            'user': {
                'email': session['user_email'],
                'name': session.get('user_name', '')
            }
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
        
        # استدعاء الـ AI
        response = ai_service.get_response(message, session['user_email'])
        
        # حفظ في التاريخ
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS chat_history (
                        id SERIAL PRIMARY KEY,
                        user_email TEXT NOT NULL,
                        message TEXT NOT NULL,
                        response TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT NOW()
                    );
                ''')
                cur.execute(
                    'INSERT INTO chat_history (user_email, message, response) VALUES (%s, %s, %s)',
                    (session['user_email'], message, response)
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
