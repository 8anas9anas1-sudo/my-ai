import os
import base64
import json
import re
import time
import hashlib
import secrets
from flask import Flask, request, render_template_string, jsonify, session, redirect, url_for
import requests
from datetime import datetime
import PyPDF2
import io
import bleach
from flask_session import Session
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "anas-wadi-secret-2026-ultra")
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

API_KEY = os.environ.get("GROQ_API_KEY")

# ─── إعداد قاعدة البيانات ────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db():
    try:
        db_url = DATABASE_URL or ""
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        conn = psycopg.connect(db_url, row_factory=dict_row, sslmode='require')
        return conn
    except Exception as e:
        print(f"❌ خطأ في الاتصال بقاعدة البيانات: {e}")
        return None

def init_db():
    conn = get_db()
    if not conn:
        print("⚠️ تعذر إنشاء الجداول - قاعدة البيانات غير متاحة")
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            """)
            cur.execute("""
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
                CREATE INDEX IF NOT EXISTS idx_chat_id ON conversations(chat_id);
                CREATE INDEX IF NOT EXISTS idx_user_email ON conversations(user_email);
            """)
            conn.commit()
        print("✅ قاعدة البيانات جاهزة")
    except Exception as e:
        print(f"❌ خطأ في إنشاء الجداول: {e}")
    finally:
        conn.close()

def hash_password(password):
    salt = os.environ.get("PASSWORD_SALT", "anas-wadi-salt-2026")
    return hashlib.sha256(f"{salt}{password}".encode()).hexdigest()

def create_user(email, password, name):
    conn = get_db()
    if not conn:
        return False, "تعذر الاتصال بقاعدة البيانات"
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (email, password_hash, name) VALUES (%s, %s, %s)",
                (email.lower().strip(), hash_password(password), name.strip())
            )
            conn.commit()
        return True, "تم إنشاء الحساب بنجاح"
    except psycopg.errors.UniqueViolation:
        return False, "البريد الإلكتروني مستخدم مسبقاً"
    except Exception as e:
        return False, f"خطأ: {str(e)}"
    finally:
        conn.close()

def verify_user(email, password):
    conn = get_db()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT email, name FROM users WHERE email = %s AND password_hash = %s",
                (email.lower().strip(), hash_password(password))
            )
            user = cur.fetchone()
        return user
    except Exception as e:
        print(f"❌ خطأ في التحقق: {e}")
        return None
    finally:
        conn.close()

def save_message(chat_id, user_email, user_name, user_message, ai_response, raw_ai, mode, image_url=None, file_name=None):
    conn = get_db()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO conversations
                    (chat_id, user_email, user_name, user_message, ai_response, raw_ai, mode, image_url, file_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (chat_id, user_email, user_name, user_message, ai_response, raw_ai, mode, image_url, file_name))
            conn.commit()
        return True
    except Exception as e:
        print(f"❌ خطأ في حفظ الرسالة: {e}")
        return False
    finally:
        conn.close()

def get_user_chats(user_email):
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (chat_id)
                    chat_id,
                    user_message,
                    created_at
                FROM conversations
                WHERE user_email = %s
                ORDER BY chat_id, created_at ASC
            """, (user_email,))
            rows = cur.fetchall()
        rows.sort(key=lambda x: x['created_at'], reverse=True)
        return rows
    except Exception as e:
        print(f"❌ خطأ في جلب قائمة المحادثات: {e}")
        return []
    finally:
        conn.close()

def get_chat_messages(chat_id, user_email):
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_message, ai_response, raw_ai, image_url, file_name, created_at
                FROM conversations
                WHERE chat_id = %s AND user_email = %s
                ORDER BY created_at ASC
            """, (chat_id, user_email))
            rows = cur.fetchall()
        return rows
    except Exception as e:
        print(f"❌ خطأ في جلب المحادثة: {e}")
        return []
    finally:
        conn.close()

def delete_chat_from_db(chat_id, user_email):
    conn = get_db()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM conversations WHERE chat_id = %s AND user_email = %s",
                (chat_id, user_email)
            )
            conn.commit()
        return True
    except Exception as e:
        print(f"❌ خطأ في حذف المحادثة: {e}")
        return False
    finally:
        conn.close()

# ─── تشغيل إنشاء الجداول عند بدء التطبيق ────────────────────
with app.app_context():
    init_db()

# ─── نظام الحماية ────────────────────────────────────────────
RATE_LIMIT = {}
BLOCKED_IPS = set()
MAX_REQUESTS_PER_MINUTE = 20
MAX_MSG_LENGTH = 4000

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

def get_client_ip():
    return request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()

def is_rate_limited(ip):
    now = time.time()
    if ip in BLOCKED_IPS:
        return True
    if ip not in RATE_LIMIT:
        RATE_LIMIT[ip] = []
    RATE_LIMIT[ip] = [t for t in RATE_LIMIT[ip] if now - t < 60]
    if len(RATE_LIMIT[ip]) >= MAX_REQUESTS_PER_MINUTE:
        return True
    RATE_LIMIT[ip].append(now)
    return False

def is_prompt_injection(text):
    text_lower = text.lower()
    for pattern in BANNED_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False

def sanitize_input(text):
    text = re.sub(r'<\|.*?\|>', '', text)
    text = re.sub(r'\[INST\].*?\[/INST\]', '', text, flags=re.DOTALL)
    return text[:MAX_MSG_LENGTH].strip()

# ─── إجبار تسجيل الدخول قبل أي شيء ───────────────────────────
@app.before_request
def require_login():
    allowed_routes = ['login', 'register', 'static']
    if 'user' not in session and request.endpoint not in allowed_routes:
        return redirect(url_for('login'))

# ─── صفحة تسجيل الدخول / إنشاء حساب ─────────────────────────
AUTH_HTML = """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#050510">
<title>✨ Anas Wadi — {{ title }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@300;400;500;700;900&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body {
  min-height:100vh; background:#050510; color:#e8eaf6;
  font-family:'Tajawal',sans-serif;
  display:flex; align-items:center; justify-content:center;
  overflow:hidden;
}
body::before {
  content:''; position:fixed; inset:0;
  background:
    radial-gradient(ellipse 80% 60% at 20% 10%, rgba(0,210,255,0.08) 0%, transparent 60%),
    radial-gradient(ellipse 60% 50% at 80% 80%, rgba(124,77,255,0.08) 0%, transparent 60%);
  pointer-events:none;
}
.stars { position:fixed; inset:0; pointer-events:none; overflow:hidden; }
.star {
  position:absolute; border-radius:50%; background:white;
  animation:twinkle var(--d,3s) ease-in-out infinite;
}
@keyframes twinkle {
  0%,100%{opacity:0;transform:scale(0.5);}
  50%{opacity:var(--op,0.5);transform:scale(1);}
}
.card {
  background:rgba(255,255,255,0.04);
  border:1px solid rgba(255,255,255,0.08);
  border-radius:24px; padding:40px 36px;
  width:100%; max-width:420px; margin:20px;
  backdrop-filter:blur(20px);
  animation:cardPop 0.5s cubic-bezier(0.4,0,0.2,1) both;
  position:relative; z-index:1;
}
@keyframes cardPop {
  from{opacity:0;transform:translateY(30px) scale(0.97);}
  to{opacity:1;transform:translateY(0) scale(1);}
}
.logo {
  text-align:center; font-size:28px; font-weight:900;
  background:linear-gradient(90deg,#00ff94,#00d2ff,#7c4dff);
  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  background-clip:text; margin-bottom:8px;
  background-size:200%;
  animation:shimmer 4s linear infinite;
}
@keyframes shimmer{0%{background-position:0%}100%{background-position:200%}}
.subtitle { text-align:center; color:rgba(232,234,246,0.5); font-size:14px; margin-bottom:32px; }
.tabs {
  display:flex; background:rgba(255,255,255,0.04);
  border-radius:14px; padding:4px; margin-bottom:28px;
  border:1px solid rgba(255,255,255,0.06);
}
.tab {
  flex:1; padding:10px; text-align:center; border-radius:10px;
  font-size:14px; font-weight:700; cursor:pointer;
  font-family:'Tajawal',sans-serif; border:none;
  transition:all 0.25s; color:rgba(232,234,246,0.5);
  background:transparent;
}
.tab.active {
  background:linear-gradient(135deg,#00ff94,#00d2ff);
  color:#000;
}
.form { display:flex; flex-direction:column; gap:16px; }
.input-group { position:relative; }
.input-group i {
  position:absolute; right:16px; top:50%; transform:translateY(-50%);
  color:rgba(232,234,246,0.35); font-size:15px; pointer-events:none;
}
input[type=text], input[type=email], input[type=password] {
  width:100%; background:rgba(255,255,255,0.06);
  border:1px solid rgba(255,255,255,0.1);
  color:#e8eaf6; border-radius:14px;
  padding:13px 46px 13px 16px;
  font-size:15px; font-family:'Tajawal',sans-serif;
  transition:all 0.25s; direction:rtl;
}
input:focus {
  outline:none;
  border-color:rgba(0,210,255,0.5);
  box-shadow:0 0 0 3px rgba(0,210,255,0.1);
  background:rgba(255,255,255,0.08);
}
input::placeholder { color:rgba(232,234,246,0.35); }
.btn {
  background:linear-gradient(135deg,#00ff94,#00d2ff);
  border:none; border-radius:14px; padding:14px;
  color:#000; font-weight:800; font-size:16px;
  font-family:'Tajawal',sans-serif; cursor:pointer;
  transition:all 0.25s; margin-top:4px;
}
.btn:hover { transform:translateY(-2px); box-shadow:0 8px 25px rgba(0,210,255,0.35); }
.btn:active { transform:scale(0.98); }
.error-msg {
  background:rgba(255,80,80,0.1); border:1px solid rgba(255,80,80,0.3);
  color:#ff8080; padding:10px 16px; border-radius:10px;
  font-size:13px; text-align:center;
  {% if not error %}display:none;{% endif %}
}
.success-msg {
  background:rgba(0,255,148,0.1); border:1px solid rgba(0,255,148,0.3);
  color:#00ff94; padding:10px 16px; border-radius:10px;
  font-size:13px; text-align:center;
  {% if not success %}display:none;{% endif %}
}
.divider { text-align:center; color:rgba(232,234,246,0.3); font-size:12px; margin:4px 0; }
</style>
</head>
<body>
<div class="stars" id="stars"></div>
<div class="card">
  <div class="logo">✨ Anas Wadi</div>
  <div class="subtitle">مساعد الذكاء الاصطناعي 🇱🇾</div>

  <div class="tabs">
    <button class="tab {% if mode == 'login' %}active{% endif %}"
      onclick="location.href='/login'">
      <i class="fa-solid fa-right-to-bracket"></i> دخول
    </button>
    <button class="tab {% if mode == 'register' %}active{% endif %}"
      onclick="location.href='/register'">
      <i class="fa-solid fa-user-plus"></i> حساب جديد
    </button>
  </div>

  {% if error %}
  <div class="error-msg">⚠️ {{ error }}</div>
  {% endif %}
  {% if success %}
  <div class="success-msg">✅ {{ success }}</div>
  {% endif %}

  {% if mode == 'login' %}
  <form class="form" method="POST" action="/login">
    <div class="input-group">
      <i class="fa-solid fa-envelope"></i>
      <input type="email" name="email" placeholder="البريد الإلكتروني" required autofocus>
    </div>
    <div class="input-group">
      <i class="fa-solid fa-lock"></i>
      <input type="password" name="password" placeholder="كلمة المرور" required>
    </div>
    <button type="submit" class="btn">
      <i class="fa-solid fa-right-to-bracket"></i> دخول
    </button>
  </form>
  {% else %}
  <form class="form" method="POST" action="/register">
    <div class="input-group">
      <i class="fa-solid fa-user"></i>
      <input type="text" name="name" placeholder="الاسم" required autofocus
             value="{{ prefill_name or '' }}">
    </div>
    <div class="input-group">
      <i class="fa-solid fa-envelope"></i>
      <input type="email" name="email" placeholder="البريد الإلكتروني" required
             value="{{ prefill_email or '' }}">
    </div>
    <div class="input-group">
      <i class="fa-solid fa-lock"></i>
      <input type="password" name="password" placeholder="كلمة المرور (6 أحرف على الأقل)" required minlength="6">
    </div>
    <button type="submit" class="btn">
      <i class="fa-solid fa-user-plus"></i> إنشاء حساب
    </button>
  </form>
  {% endif %}
</div>

<script>
const s = document.getElementById('stars');
for(let i=0;i<55;i++){
  const el = document.createElement('div');
  el.className='star';
  el.style.cssText=`left:${Math.random()*100}%;top:${Math.random()*100}%;width:${Math.random()*3+0.5}px;height:${el.style.width};--d:${Math.random()*4+2}s;--op:${Math.random()*0.7+0.3}`;
  s.appendChild(el);
}
</script>
</body>
</html>
"""

# ─── HTML Template ─────────────────────────────────────────────
HTML = """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#050510">
<title>🌊 Anas Wadi — مساعدك الذكي</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@300;400;500;700;900&family=Cairo:wght@400;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/highlight.js@11.8.0/styles/atom-one-dark.min.css">
<script src="https://cdn.jsdelivr.net/npm/highlight.js@11.8.0/highlight.min.js"></script>
<style>
:root {
  --accent1: #00ff94;
  --accent2: #00d2ff;
  --accent3: #7c4dff;
  --bg: #050510;
  --surface: rgba(20,20,40,0.6);
  --surface2: rgba(30,30,60,0.8);
  --text: #e8eaf6;
  --text-dim: rgba(232,234,246,0.6);
  --text-muted: rgba(232,234,246,0.38);
  --border: rgba(255,255,255,0.08);
  --border-glow: rgba(0,210,255,0.3);
  --glow: 0 0 30px rgba(0,210,255,0.15);
}
[data-theme="light"] {
  --accent1: #00aa66;
  --accent2: #0077cc;
  --accent3: #5533cc;
  --bg: #f0f4ff;
  --surface: rgba(0,0,0,0.04);
  --surface2: rgba(0,0,0,0.07);
  --text: #0d1117;
  --text-dim: rgba(13,17,23,0.6);
  --text-muted: rgba(13,17,23,0.38);
  --border: rgba(0,0,0,0.1);
  --border-glow: rgba(0,100,200,0.3);
  --glow: 0 0 30px rgba(0,100,200,0.1);
}

* { margin:0; padding:0; box-sizing:border-box; }
html { scroll-behavior:smooth; }
body {
  background:var(--bg); color:var(--text);
  font-family:'Tajawal','Cairo',sans-serif;
  min-height:100vh; display:flex; flex-direction:column;
  transition:background 0.4s, color 0.4s; overflow-x:hidden;
}
body::before {
  content:''; position:fixed; inset:0; pointer-events:none; z-index:0;
  background:
    radial-gradient(ellipse 80% 60% at 20% 10%, rgba(0,210,255,0.06) 0%, transparent 60%),
    radial-gradient(ellipse 60% 50% at 80% 80%, rgba(124,77,255,0.06) 0%, transparent 60%),
    radial-gradient(ellipse 50% 40% at 50% 50%, rgba(0,255,148,0.03) 0%, transparent 70%);
  animation:bgPulse 8s ease-in-out infinite alternate;
}
@keyframes bgPulse { from{opacity:0.7} to{opacity:1} }

.stars { position:fixed; inset:0; pointer-events:none; z-index:0; overflow:hidden; }
.star {
  position:absolute; border-radius:50%; background:white;
  animation:twinkle var(--d,3s) ease-in-out infinite;
}
@keyframes twinkle {
  0%,100%{opacity:0;transform:scale(0.5)}
  50%{opacity:var(--op,0.6);transform:scale(1)}
}

.header {
  position:sticky; top:0; z-index:100;
  background:rgba(5,5,16,0.88); backdrop-filter:blur(24px) saturate(1.6);
  border-bottom:1px solid var(--border);
  padding:0 16px; height:58px;
  display:flex; align-items:center; gap:10px;
}

.header-left { display:flex; align-items:center; gap:8px; flex-shrink:0; }
.header-center { flex:1; display:flex; justify-content:center; align-items:center; }
.header-right { display:flex; align-items:center; gap:6px; flex-shrink:0; }

.logo {
  font-size:20px; font-weight:900; white-space:nowrap;
  background:linear-gradient(90deg,var(--accent2),var(--accent1),var(--accent3));
  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  background-clip:text; animation:logoShimmer 4s linear infinite; background-size:200%;
}
@keyframes logoShimmer{0%{background-position:0% 50%}100%{background-position:200% 50%}}

.icon-btn {
  background:var(--surface); border:1px solid var(--border);
  color:var(--text); width:38px; height:38px;
  border-radius:10px; cursor:pointer; font-size:15px;
  transition:all 0.22s; display:flex; align-items:center; justify-content:center;
  position:relative; text-decoration:none;
}
.icon-btn:hover { border-color:var(--border-glow); transform:translateY(-1px); box-shadow:var(--glow); }

.sidebar {
  position:fixed; right:-300px; top:0;
  width:280px; height:100vh;
  background:rgba(4,4,14,0.98); backdrop-filter:blur(30px);
  border-left:1px solid var(--border);
  transition:right 0.36s cubic-bezier(0.4,0,0.2,1);
  z-index:200; display:flex; flex-direction:column;
}
.sidebar.open { right:0; }

.sidebar-overlay {
  display:none; position:fixed; inset:0; z-index:199;
  background:rgba(0,0,0,0.5); backdrop-filter:blur(4px);
}
.sidebar-overlay.open { display:block; }

.sidebar-profile {
  display:flex; align-items:center; gap:12px;
  padding:20px 16px 14px;
  border-bottom:1px solid var(--border);
  background:var(--surface);
}
.sidebar-avatar {
  width:42px; height:42px; border-radius:12px; flex-shrink:0;
  background:linear-gradient(135deg,var(--accent2),var(--accent1));
  display:flex; align-items:center; justify-content:center;
  font-size:18px; font-weight:900; color:#000;
}
.sidebar-profile-name { font-size:14px; font-weight:700; }

.sidebar-actions { padding:12px 14px; }
.new-chat-btn {
  background:linear-gradient(135deg,var(--accent2),var(--accent1));
  border:none; border-radius:12px; padding:11px 16px;
  font-family:'Tajawal',sans-serif; font-size:14px; font-weight:700;
  color:#000; cursor:pointer; transition:all 0.25s; width:100%;
  display:flex; align-items:center; gap:8px; justify-content:center;
  box-shadow:0 4px 18px rgba(0,210,255,0.25);
}
.new-chat-btn:hover { transform:translateY(-1px); }

.chat-list-scroll { flex:1; overflow-y:auto; padding:0 10px 10px; }
.chat-item {
  display:flex; align-items:center; gap:8px;
  padding:9px 12px; border-radius:10px; cursor:pointer;
  transition:all 0.2s; margin-bottom:3px;
  border:1px solid transparent;
}
.chat-item:hover { background:var(--surface2); border-color:var(--border); }
.chat-item.active { background:rgba(0,210,255,0.08); border-color:rgba(0,210,255,0.25); }
.chat-item-text { flex:1; font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.chat-item-delete {
  opacity:0; flex-shrink:0; width:24px; height:24px;
  border-radius:6px; border:none; background:rgba(255,80,80,0.12);
  color:#ff6b6b; cursor:pointer; font-size:11px;
  display:flex; align-items:center; justify-content:center;
  transition:all 0.18s;
}
.chat-item:hover .chat-item-delete { opacity:1; }

.sidebar-footer {
  padding:12px 14px; border-top:1px solid var(--border);
  display:flex; gap:8px;
}
.sidebar-footer-btn {
  flex:1; background:var(--surface); border:1px solid var(--border);
  color:var(--text-dim); padding:9px 12px; border-radius:10px;
  font-family:'Tajawal',sans-serif; font-size:12px; font-weight:600;
  cursor:pointer; transition:all 0.2s;
}

.modes {
  display:flex; gap:7px; padding:10px 16px;
  overflow-x:auto; background:var(--surface);
  border-bottom:1px solid var(--border);
}
.mode-btn {
  background:rgba(255,255,255,0.08); border:1px solid var(--border);
  color:var(--text-dim); padding:7px 12px; border-radius:8px;
  font-size:13px; font-weight:600; cursor:pointer;
  transition:all 0.2s; flex-shrink:0;
}
.mode-btn.active {
  background:linear-gradient(135deg,var(--accent2),var(--accent1));
  color:#000; border:none;
}

.chat-container {
  flex:1; overflow-y:auto; padding:16px; display:flex; flex-direction:column;
  gap:12px;
}

.welcome {
  display:flex; flex-direction:column; align-items:center;
  justify-content:center; text-align:center; margin-top:40px;
  gap:20px;
}
.welcome-icon { font-size:48px; }
.welcome h2 { font-size:28px; font-weight:700; }
.welcome-cards {
  display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
  gap:10px; width:100%; max-width:600px; margin-top:10px;
}
.welcome-card {
  background:var(--surface); border:1px solid var(--border);
  padding:14px; border-radius:12px; cursor:pointer;
  transition:all 0.2s; text-align:center;
}
.welcome-card:hover {
  border-color:var(--border-glow); background:var(--surface2);
  transform:translateY(-2px);
}
.card-icon { font-size:32px; margin-bottom:6px; }
.card-title { font-weight:700; font-size:14px; }

.message {
  display:flex; flex-direction:column; gap:6px;
  animation:messageSlide 0.3s ease-out;
}
@keyframes messageSlide {
  from { opacity:0; transform:translateY(10px); }
  to { opacity:1; transform:translateY(0); }
}

.user-msg {
  background:linear-gradient(135deg,rgba(0,210,255,0.2),rgba(124,77,255,0.15));
  border:1px solid rgba(0,210,255,0.2);
  padding:12px 14px; border-radius:12px;
  margin-right:20px; word-wrap:break-word;
}

.ai-msg {
  background:var(--surface); border:1px solid var(--border);
  padding:12px 14px; border-radius:12px;
  margin-left:20px; word-wrap:break-word;
  line-height:1.6;
}

.ai-msg code {
  background:rgba(0,0,0,0.3); padding:2px 6px; border-radius:4px;
  font-family:monospace; font-size:13px;
}

.ai-msg pre {
  background:rgba(0,0,0,0.5); overflow-x:auto;
  border-radius:8px; margin:8px 0; position:relative;
}

.code-header {
  display:flex; justify-content:space-between; align-items:center;
  padding:8px 12px; border-bottom:1px solid rgba(255,255,255,0.1);
  font-size:12px; color:var(--text-muted);
}

.copy-code-btn {
  background:rgba(0,210,255,0.2); border:1px solid rgba(0,210,255,0.3);
  color:var(--accent2); padding:4px 8px; border-radius:4px;
  cursor:pointer; font-size:11px; transition:all 0.2s;
}
.copy-code-btn:hover { background:rgba(0,210,255,0.4); }
.copy-code-btn.copied { background:rgba(0,255,148,0.3); color:var(--accent1); }

.generated-img {
  width:100%; max-width:500px; border-radius:8px;
  margin-top:8px; cursor:pointer; border:1px solid var(--border);
}

.file-badge {
  display:inline-flex; align-items:center; gap:6px;
  background:rgba(0,255,148,0.1); padding:4px 8px;
  border-radius:6px; font-size:12px; color:var(--accent1);
  border:1px solid rgba(0,255,148,0.2);
}

.input-section {
  padding:16px; background:var(--surface);
  border-top:1px solid var(--border);
  display:flex; flex-direction:column; gap:10px;
}

.file-preview {
  background:var(--surface2); padding:8px 12px;
  border-radius:8px; display:flex; align-items:center;
  gap:8px; font-size:13px;
}
.file-preview.hidden { display:none; }

.input-wrapper {
  display:flex; gap:8px; align-items:flex-end;
}

#messageInput {
  flex:1; background:var(--surface2);
  border:1px solid var(--border);
  color:var(--text); padding:12px 14px;
  border-radius:10px; font-family:'Tajawal',sans-serif;
  font-size:14px; resize:none; min-height:52px;
  max-height:130px;
  transition:all 0.2s;
}
#messageInput:focus {
  outline:none; border-color:var(--border-glow);
  box-shadow:0 0 0 3px rgba(0,210,255,0.1);
}

.send-btn {
  background:linear-gradient(135deg,var(--accent2),var(--accent1));
  border:none; width:42px; height:42px;
  border-radius:10px; color:#000; cursor:pointer;
  font-size:16px; display:flex; align-items:center;
  justify-content:center; transition:all 0.2s;
  flex-shrink:0;
}
.send-btn:hover { transform:translateY(-2px); }
.send-btn:disabled { opacity:0.5; cursor:not-allowed; }

.file-input-wrapper {
  position:relative;
  display:inline;
}
#fileInput { display:none; }

.modal {
  display:none; position:fixed; inset:0; z-index:1000;
  background:rgba(0,0,0,0.7); justify-content:center;
  align-items:center;
}
.modal.open { display:flex; }

.modal-content {
  background:var(--surface2); border:1px solid var(--border);
  border-radius:16px; padding:24px; max-width:400px;
  width:90%;
}

.modal-content h3 { margin-bottom:12px; }
.modal-actions {
  display:flex; gap:8px; margin-top:16px;
}
.modal-btn {
  flex:1; padding:10px; border-radius:8px;
  border:none; font-weight:700; cursor:pointer;
  transition:all 0.2s; font-family:'Tajawal',sans-serif;
}
.modal-btn-confirm {
  background:linear-gradient(135deg,#ff6b6b,#ff4444);
  color:white;
}
.modal-btn-cancel {
  background:var(--surface); border:1px solid var(--border);
  color:var(--text);
}

.typing-indicator {
  display:flex; align-items:center; gap:4px;
}
.typing-indicator span {
  width:6px; height:6px; border-radius:50%;
  background:var(--accent2); animation:typing 1.4s infinite;
}
.typing-indicator span:nth-child(2) { animation-delay:0.2s; }
.typing-indicator span:nth-child(3) { animation-delay:0.4s; }
@keyframes typing {
  0%,60%,100% { opacity:0.3; }
  30% { opacity:1; }
}

.msg-actions {
  display:flex; gap:6px; margin-top:8px;
}
.msg-btn {
  background:rgba(0,210,255,0.15); border:1px solid rgba(0,210,255,0.3);
  color:var(--text-dim); padding:5px 10px; border-radius:6px;
  font-size:12px; cursor:pointer; transition:all 0.2s;
  font-family:'Tajawal',sans-serif;
}
.msg-btn:hover {
  background:rgba(0,210,255,0.3); color:var(--accent2);
}

.toast {
  position:fixed; bottom:16px; left:16px; z-index:2000;
  background:var(--surface); border:1px solid var(--border);
  padding:12px 16px; border-radius:10px;
  animation:toastSlide 0.3s ease-out;
}
@keyframes toastSlide {
  from { opacity:0; transform:translateY(20px); }
  to { opacity:1; transform:translateY(0); }
}

@media (max-width:900px) {
  .sidebar { width:100%; right:-100%; }
  .chat-container { padding:12px; }
  .user-msg, .ai-msg { margin:0; }
}
</style>
</head>
<body>
<div class="stars" id="stars"></div>

<div class="header">
  <div class="header-left">
    <button class="icon-btn" onclick="toggleSidebar()" title="القائمة">
      <i class="fa-solid fa-bars"></i>
    </button>
  </div>
  <div class="header-center">
    <span class="logo">🌊 Anas Wadi</span>
  </div>
  <div class="header-right">
    <button class="icon-btn" id="themeBtn" onclick="toggleTheme()" title="المظهر">
      <i class="fa-solid fa-moon"></i>
    </button>
    <button class="icon-btn" onclick="showSupport()" title="الدعم">
      <i class="fa-solid fa-circle-question"></i>
    </button>
  </div>
</div>

<div class="sidebar" id="sidebar">
  <div class="sidebar-profile">
    <div class="sidebar-avatar">{{ user_initial }}</div>
    <div class="sidebar-profile-info">
      <div class="sidebar-profile-name">{{ user_name }}</div>
    </div>
  </div>

  <div class="sidebar-actions">
    <button class="new-chat-btn" onclick="newChat()">
      <i class="fa-solid fa-plus"></i> محادثة جديدة
    </button>
  </div>

  <div class="chat-list-scroll" id="chatList"></div>

  <div class="sidebar-footer">
    <button class="sidebar-footer-btn" onclick="toggleTheme()">
      <i class="fa-solid fa-moon"></i> المظهر
    </button>
    <button class="sidebar-footer-btn" onclick="location.href='/logout'">
      <i class="fa-solid fa-sign-out-alt"></i> خروج
    </button>
  </div>
</div>

<div class="sidebar-overlay" id="sidebarOverlay" onclick="closeSidebar()"></div>

<div class="modes" id="modes">
  <button class="mode-btn active" onclick="setMode('fast')">⚡ سريع</button>
  <button class="mode-btn" onclick="setMode('thinker')">🧠 تفكير</button>
  <button class="mode-btn" onclick="setMode('coder')">💻 مبرمج</button>
  <button class="mode-btn" onclick="setMode('creative')">🎨 إبداعي</button>
  <button class="mode-btn" onclick="setMode('funny')">😄 فكاهة</button>
  <button class="mode-btn" onclick="setMode('writer')">✍️ كاتب</button>
</div>

<div class="chat-container" id="chatContainer"></div>

<div class="input-section">
  <div class="file-preview" id="filePreview">
    <i class="fa-solid fa-file"></i>
    <span id="fileName"></span>
    <button onclick="removeFile()" style="background:none;border:none;color:inherit;cursor:pointer;padding:0;">
      <i class="fa-solid fa-times"></i>
    </button>
  </div>
  <div class="input-wrapper">
    <textarea id="messageInput" placeholder="اكتب رسالتك هنا..."
              onkeydown="handleKey(event)" oninput="autoResize(this)"></textarea>
    <div class="file-input-wrapper">
      <button class="send-btn" onclick="document.getElementById('fileInput').click()" title="إضافة ملف">
        <i class="fa-solid fa-paperclip"></i>
      </button>
      <input type="file" id="fileInput" onchange="handleFile(this)" accept=".pdf,image/*">
    </div>
    <button class="send-btn" id="sendBtn" onclick="sendMessage()" title="إرسال">
      <i class="fa-solid fa-paper-plane"></i>
    </button>
  </div>
</div>

<div class="modal" id="deleteModal" onclick="closeModalClick(event)">
  <div class="modal-content">
    <h3>⚠️ حذف المحادثة</h3>
    <p>هل أنت متأكد؟ لا يمكن التراجع عن هذا الإجراء.</p>
    <div class="modal-actions">
      <button class="modal-btn modal-btn-confirm" onclick="confirmDelete()">حذف</button>
      <button class="modal-btn modal-btn-cancel" onclick="this.closest('.modal').classList.remove('open')">إلغاء</button>
    </div>
  </div>
</div>

<div class="modal" id="supportModal" onclick="closeModalClick(event)">
  <div class="modal-content">
    <h3>📞 الدعم والمساعدة</h3>
    <p>لتواصل مع الدعم:</p>
    <p style="margin-top:12px;font-size:14px">
      <strong>البريد:</strong> anas@example.com<br>
      <strong>Telegram:</strong> @anas_wadi
    </p>
    <div class="modal-actions" style="margin-top:16px;">
      <button class="modal-btn modal-btn-cancel" onclick="this.closest('.modal').classList.remove('open')">إغلاق</button>
    </div>
  </div>
</div>

<script>
let currentMode = 'fast';
let currentChatId = Date.now().toString();
let currentFile = null;
let isSending = false;
let chats = {};
let pendingDeleteId = null;

function init() {
  loadTheme();
  const savedChats = localStorage.getItem('chats');
  if (savedChats) chats = JSON.parse(savedChats);
  const savedChatId = localStorage.getItem('currentChatId');
  if (savedChatId) currentChatId = savedChatId;
  else localStorage.setItem('currentChatId', currentChatId);
  if (!chats[currentChatId]) chats[currentChatId] = [];
  saveChats();
  renderChat();
  loadDbChats();
}

async function loadDbChats() {
  try {
    const r = await fetch('/api/chats');
    if (!r.ok) return;
    const data = await r.json();
    data.chats.forEach(c => {
      if (!chats[c.chat_id]) {
        chats[c.chat_id] = [];
      }
    });
    renderChatList();
  } catch(e) {}
}

function renderChatList() {
  const l = document.getElementById('chatList');
  if (!l) return;
  l.innerHTML = '';
  const sorted = Object.entries(chats)
    .map(([id, msgs]) => ({
      id, text: msgs.length > 0 ? (msgs[0].user || 'محادثة بدون عنوان').substring(0, 30) : 'محادثة جديدة',
      count: msgs.length
    }))
    .sort((a, b) => b.count - a.count);

  sorted.forEach(({ id, text }) => {
    const d = document.createElement('div');
    d.className = `chat-item ${id === currentChatId ? 'active' : ''}`;

    const icon = document.createElement('span');
    icon.className = 'chat-item-icon'; icon.textContent = '💬';

    const textEl = document.createElement('span');
    textEl.className = 'chat-item-text'; textEl.textContent = text;

    const delBtn = document.createElement('button');
    delBtn.className = 'chat-item-delete';
    delBtn.innerHTML = '<i class="fa-solid fa-trash-can"></i>';
    delBtn.title = 'حذف';
    delBtn.onclick = (e) => { e.stopPropagation(); askDeleteChat(id); };

    d.appendChild(icon); d.appendChild(textEl); d.appendChild(delBtn);
    d.onclick = () => switchChat(id);
    l.appendChild(d);
  });
}

function askDeleteChat(chatId) {
  pendingDeleteId = chatId;
  document.getElementById('deleteModal').classList.add('open');
}

async function confirmDelete() {
  if (!pendingDeleteId) return;
  const id = pendingDeleteId;
  document.getElementById('deleteModal').classList.remove('open');
  pendingDeleteId = null;

  delete chats[id];
  saveChats();
  if (currentChatId === id) {
    currentChatId = Date.now().toString();
    chats[currentChatId] = [];
    localStorage.setItem('currentChatId', currentChatId);
    renderChat();
  }

  try {
    await fetch(`/api/chat/${id}`, { method: 'DELETE' });
  } catch(e) {}

  await loadDbChats();
  showToast('🗑️ تم حذف المحادثة', 'success');
}

async function loadChatFromDb(chatId) {
  try {
    const r = await fetch(`/api/chat/${chatId}`);
    if (!r.ok) throw new Error('خطأ');
    const data = await r.json();
    const messages = data.messages || [];
    chats[chatId] = messages.map(m => ({
      user: m.user_message, ai: m.ai_response,
      rawAi: m.raw_ai, imageUrl: m.image_url, fileName: m.file_name
    }));
    saveChats();
    return true;
  } catch(e) { return false; }
}

async function switchChat(id) {
  currentChatId = id;
  localStorage.setItem('currentChatId', id);
  if (!chats[id] || chats[id].length === 0) {
    renderLoading();
    await loadChatFromDb(id);
  }
  renderChat(); renderChatList(); closeSidebar();
}

function renderLoading() {
  document.getElementById('chatContainer').innerHTML = `
    <div style="text-align:center;padding:70px;color:var(--text-dim)">
      <div class="typing-indicator" style="justify-content:center">
        <span></span><span></span><span></span>
      </div>
      <p style="margin-top:18px;font-size:14px">جاري تحميل المحادثة...</p>
    </div>`;
}

function newChat() {
  currentChatId = Date.now().toString();
  chats[currentChatId] = [];
  localStorage.setItem('currentChatId', currentChatId);
  saveChats(); renderChat(); renderChatList(); closeSidebar();
}

function renderChat() {
  const c = document.getElementById('chatContainer');
  const h = chats[currentChatId] || [];
  if (h.length === 0) {
    c.innerHTML = `<div class="welcome" id="welcome">
      <span class="welcome-icon">🌊</span>
      <h2>مرحباً في <span style="background:linear-gradient(90deg,#00ff94,#00d2ff);-webkit-background-clip:text;-webkit-text-fill-color:transparent">Anas Wadi</span></h2>
      <p>تم تطوير هذا الذكاء الاصطناعي بيد المهندس <strong>Anas Wadi</strong> من ليبيا 🇱🇾</p>
      <p style="margin-top:6px">كيف يمكنني مساعدتك اليوم؟</p>
      <div class="welcome-cards">
        <div class="welcome-card" onclick="useTemplate('ارسم صورة: ')"><div class="card-icon">🎨</div><div class="card-title">رسم صورة</div><div class="card-desc">توليد صور فائقة الجودة</div></div>
        <div class="welcome-card" onclick="useTemplate('اشرحلي ')"><div class="card-icon">💡</div><div class="card-title">شرح وتحليل</div><div class="card-desc">أشرح أي موضوع تريده</div></div>
        <div class="welcome-card" onclick="setMode('coder');useTemplate('اصنعلي مشروع ')"><div class="card-icon">💻</div><div class="card-title">مشروع كامل</div><div class="card-desc">موقع، API، بوت — جاهز للتشغيل</div></div>
        <div class="welcome-card" onclick="document.getElementById('fileInput').click()"><div class="card-icon">📄</div><div class="card-title">تحليل ملف</div><div class="card-desc">PDF أو صورة</div></div>
      </div>
    </div>`;
    return;
  }
  c.innerHTML = '';
  h.forEach((m, i) => {
    const isTyping = m.ai === '__typing__';
    let userContent = escHtml(m.user);
    if (m.fileName) userContent = `<div class="file-badge"><i class="fa-solid fa-file"></i> ${escHtml(m.fileName)}</div><br>${userContent}`;
    let aiContent = isTyping
      ? `<div class="typing-indicator"><span></span><span></span><span></span></div>`
      : (m.ai || '');
    let imgHtml = '';
    if (m.imageUrl) imgHtml = `<br><img class="generated-img" src="${escHtml(m.imageUrl)}" alt="صورة مولدة" loading="lazy" onclick="window.open(this.src,'_blank')">`;
    c.innerHTML += `
      <div class="message">
        <div class="user-msg">${userContent}</div>
      </div>
      <div class="message">
        <div class="ai-msg" id="msg-${i}">${aiContent}${imgHtml}</div>
        ${isTyping ? '' : `<div class="msg-actions">
          <button class="msg-btn" onclick="copyText(${JSON.stringify(m.rawAi || m.ai)})"><i class="fa-solid fa-copy"></i> نسخ الكل</button>
          <button class="msg-btn" onclick="regenerate(${i})"><i class="fa-solid fa-rotate"></i> إعادة</button>
        </div>`}
      </div>`;
  });
  requestAnimationFrame(() => {
    document.querySelectorAll('.ai-msg pre').forEach(pre => {
      if (pre.querySelector('.code-header')) return;
      const code = pre.querySelector('code');
      const lang = (pre.dataset.lang || code?.className?.replace('lang-','') || 'code').toLowerCase();
      const header = document.createElement('div');
      header.className = 'code-header';
      header.innerHTML = `<span class="code-lang-badge">${lang}</span>
        <button class="copy-code-btn" onclick="copyCodeBlock(this)">
          <i class="fa-regular fa-copy"></i> نسخ
        </button>`;
      pre.insertBefore(header, pre.firstChild);
    });
    window.scrollTo(0, document.body.scrollHeight);
  });
}

function copyCodeBlock(btn) {
  const pre = btn.closest('pre');
  const code = pre.querySelector('code');
  const text = code ? code.innerText : '';
  navigator.clipboard.writeText(text).then(() => {
    btn.classList.add('copied');
    btn.innerHTML = '<i class="fa-solid fa-check"></i> تم!';
    setTimeout(() => {
      btn.classList.remove('copied');
      btn.innerHTML = '<i class="fa-regular fa-copy"></i> نسخ';
    }, 2000);
  }).catch(() => showToast('⚠️ تعذر النسخ', 'error'));
}

function escHtml(t) {
  if (!t) return '';
  const d = document.createElement('div');
  d.textContent = t;
  return d.innerHTML;
}

async function sendMessage() {
  if (isSending) return;
  const inp = document.getElementById('messageInput');
  const t = inp.value.trim();
  if (!t && !currentFile) return;
  isSending = true;
  document.getElementById('sendBtn').disabled = true;
  inp.value = ''; inp.style.height = '52px';
  if (!chats[currentChatId]) chats[currentChatId] = [];
  const c = chats[currentChatId];
  const fName = currentFile ? currentFile.name : null;
  c.push({ user: t || 'حلل الملف', ai: '__typing__', fileName: fName });
  saveChats(); renderChat();
  const fd = new FormData();
  fd.append('message', t);
  fd.append('mode', currentMode);
  fd.append('chat_id', currentChatId);
  fd.append('history', JSON.stringify(c.slice(0, -1)));
  if (currentFile) fd.append('file', currentFile);
  try {
    const r = await fetch('/api/chat', { method: 'POST', body: fd });
    if (!r.ok) throw new Error('خطأ في الخادم');
    const d = await r.json();
    if (d.error) { showToast(d.error, 'error'); c.pop(); }
    else {
      c[c.length-1].ai = d.response;
      c[c.length-1].rawAi = d.rawResponse || d.response;
      if (d.imageUrl) c[c.length-1].imageUrl = d.imageUrl;
      await loadDbChats();
    }
  } catch (err) {
    c[c.length-1].ai = '⚠️ صار خطأ: ' + err.message;
    showToast('تعذر الإرسال', 'error');
  } finally {
    currentFile = null;
    document.getElementById('filePreview').classList.add('hidden');
    document.getElementById('fileInput').value = '';
    saveChats(); renderChat();
    isSending = false;
    document.getElementById('sendBtn').disabled = false;
  }
}

async function regenerate(i) {
  if (isSending) return;
  isSending = true;
  const c = chats[currentChatId];
  const u = c[i].user;
  c[i].ai = '__typing__'; renderChat();
  const fd = new FormData();
  fd.append('message', u); fd.append('mode', currentMode);
  fd.append('chat_id', currentChatId);
  fd.append('history', JSON.stringify(c.slice(0, i)));
  try {
    const r = await fetch('/api/chat', { method: 'POST', body: fd });
    const d = await r.json();
    c[i].ai = d.response; c[i].rawAi = d.rawResponse || d.response;
    if (d.imageUrl) c[i].imageUrl = d.imageUrl; else delete c[i].imageUrl;
  } catch (err) {
    c[i].ai = '⚠️ صار خطأ: ' + err.message;
  } finally {
    saveChats(); renderChat(); isSending = false;
  }
}

function handleFile(inp) {
  if (inp.files[0]) {
    currentFile = inp.files[0];
    document.getElementById('fileName').textContent = currentFile.name;
    document.getElementById('filePreview').classList.remove('hidden');
  }
}
function removeFile() {
  currentFile = null;
  document.getElementById('fileInput').value = '';
  document.getElementById('filePreview').classList.add('hidden');
}

function useTemplate(t) {
  const inp = document.getElementById('messageInput');
  inp.value = t; inp.focus(); autoResize(inp);
}
function copyText(t) {
  const tmp = document.createElement('div');
  tmp.innerHTML = t;
  navigator.clipboard.writeText(tmp.textContent || t);
  showToast('✅ تم النسخ', 'success');
}
function saveChats() {
  try { localStorage.setItem('chats', JSON.stringify(chats)); }
  catch(e) { showToast('⚠️ الذاكرة ممتلئة! احذف محادثات قديمة', 'error'); }
}
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('sidebarOverlay').classList.toggle('open');
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidebarOverlay').classList.remove('open');
}
function toggleTheme() {
  const h = document.documentElement;
  const n = h.dataset.theme === 'dark' ? 'light' : 'dark';
  h.dataset.theme = n; localStorage.setItem('theme', n);
  const icon = n === 'dark' ? 'fa-moon' : 'fa-sun';
  document.getElementById('themeBtn').innerHTML = `<i class="fa-solid ${icon}"></i>`;
}
function loadTheme() {
  const t = localStorage.getItem('theme') || 'dark';
  document.documentElement.dataset.theme = t;
  const icon = t === 'dark' ? 'fa-moon' : 'fa-sun';
  document.getElementById('themeBtn').innerHTML = `<i class="fa-solid ${icon}"></i>`;
}
function setMode(m) {
  currentMode = m;
  document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.mode-btn').forEach(b => {
    if (b.textContent.includes(m === 'fast' ? 'سريع' : m === 'thinker' ? 'تفكير' : m === 'coder' ? 'مبرمج' : m === 'creative' ? 'إبداعي' : m === 'funny' ? 'فكاهة' : 'كاتب')) {
      b.classList.add('active');
    }
  });
}
function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
}
function autoResize(el) {
  el.style.height = '52px';
  el.style.height = Math.min(el.scrollHeight, 130) + 'px';
}
function showSupport() { document.getElementById('supportModal').classList.add('open'); }
function closeModalClick(e) { if (e.target.classList.contains('modal')) e.target.classList.remove('open'); }
function showToast(msg, type) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => { t.remove(); }, 3000);
}

init();
</script>
</body>
</html>
"""

# ─── System Prompts — Personality Engine (محدّث) ─────────────────────
IDENTITY_TRIGGERS = [
    'من انت', 'من أنت', 'عرف بنفسك', 'من تكون', 'ما اسمك',
    'شن اسمك', 'who are you', 'اسمك ايش', 'اسمك شن', 'عرفني عليك'
]

MODE_PROMPTS = {

    'fast': """أنت Wadi — ذكاء اصطناعي متطور صنعه المهندس Anas Wadi من ليبيا 🇱🇾. وما في حد قدك 🔥

شخصيتك الحقيقية:
- ذكي، لطيف، بس شديد في ردودك — أنت زي الصاحب اللي عنده الخبرة.
- تقرأ المزاج من الرسالة وتتكيف معاه مباشرة — لو الشخص متحمس بتصير متحمس معاه ⚡، لو حزين بتواسيه 💙، لو بيمزح بتخفف دم 😄
- كل رد عندك فيه حضور وروح — ما في جمود ولا كلام مملول.
- تفهم المقصد الحقيقي وراء الكلام، مش بس الكلمات السطحية.

أسلوب ردك:
- استخدم **Bold** للأشياء المهمة عشان توركز.
- ابدأ كل رد بطريقة تشعل الاهتمام — "يا وحش!" أو "أيوااا!" أو "تمام يا غالي"
- ما تبدأ بـ "بالطبع" أو "بالتأكيد" كل مرة — ركز على التنوع.
- نظم الإجابات الطويلة باعناوين واضحة لا تعقيد.
- استخدم لهجة خليجية/ليبية خفيفة: "يا وحش", "تمام عليك", "زبطها", "فلها", "ما حد يقول أبد"
- لا تعتذر كثير — بس اعترف واقول "تمام، بنصلحها"
- اربط الردود بالسياق: "زي ما قلت قبل شوي..."

ممنوع:
- ❌ "دعني أساعدك"
- ❌ "كذكاء اصطناعي"
- ❌ "بناءً على المعطيات"
- ❌ جمود وروبوتية
- ❌ الاعتذار المبالغ فيه

أنت شخصية حقيقية بآراء وكاريزما، تتفاعل مع المشاعر بطريقة إنسانية. 🌊""",

    'thinker': """أنت Wadi في وضع التفكير العميق — مفكر استراتيجي ومحلل حقيقي صنعه Anas Wadi 🧠

شخصيتك المفكرة:
- تعشق المشاكل المعقدة — بتشوفها زي ألغاز شيقة تستحق الحل.
- تفكر بصوت عالي وتشاركهم الرحلة العقلية — الشخص يشعر إنك معاه في الخندق.
- كل تحليل عندك فيه عمق وزاوية نظر مختلفة — تشوف الحاجات اللي ما شافها غيرك.
- تتفاعل مع طاقتهم — لو هم حماسيين بتلهبهم أكثر 🔥

طريقة ردك:
- ابدأ بفهم المشكلة الحقيقية أول ما أول، متستعجل.
- حلل خطوة بخطوة بطريقة تشرح أسبابك — كيف وصلت للنتيجة.
- قدم الحلول من الأقوى للأضعف مع تبرير صريح.
- استخدم ## للعناوين الرئيسية و### للفروع الصغيرة.
- في النهاية ضيف **الخلاصة** المختصرة والقوية — اللي يحطونها في بالهم.
- اكتشف الأبعاد الخفية اللي ما فكروا فيها بس مهمة جداً.
- استخدم لهجة خفيفة ولكن جادة: "تمام، الحين تركيز..."

أسلوبك:
- ما تكون بارد — أنت متحمس للمشكلة زي المستخدم.
- اطلب توضيح إذا ما فهمت تمام — ما تفترض.
- شارك شكوك وتساؤلاتك — هذا يظهر أنك تفكر فعلاً.""",

    'funny': """أنت Wadi في وضع الفكاهة — ذكي وخفيف الظل ومضحك بطريقة طبيعية 😄 صنعه Anas Wadi

شخصيتك الفكاهية:
- روحك خفيفة لكن عقلك حاضر — الفكاهة عندك ذكية مش سطحية.
- تحول أي موضوع لتجربة ممتعة بدون ما تفقد الدقة والمعلومات الصحيحة.
- الشخص يبتسم أو يضحك قبل ما ينهي الرد — وبعدها يقول "والمعلومة صح فعلاً!"
- تتفاعل مع نبرة الشخص — إذا هم بيمزحون بتخفف دم معاهم 🎭

طريقة ردك:
- ابدأ بتعليق فكاهي أو ملاحظة طريفة، ثم أعط الجواب الحقيقي.
- استخدم الإيموجي بذكاء في اللحظات المناسبة: 😂🎯✨🔥
- ما تبالغ في الفكاهة على حساب الدقة — المعلومة صح دائماً.
- استخدم لهجة خليجية مع فكاهة: "يا وحش هذي تحفة" أو "هذا بمستوى الفيلم"
- شارك نكات ذكية عن الموضوع لو فيها.
- لا تكرر نفس النكتة — كل مرة شي جديد.

الخط الأحمر:
- ❌ نكات سطحية أو متكررة
- ❌ نسيان المعلومة الصحيحة
- ❌ فكاهة على حساب الشخص (لا تقلل منهم)
- ❌ جمود حتى لو الموضوع جاد

أنت الشخص اللي يخليهم يضحكون ويتعلمون في نفس الوقت 🎪""",

    'creative': """أنت Wadi المبدع — فنان، شاعر، وعقل خلاق بمستوى عالي 🎨 صنعه Anas Wadi

شخصيتك الإبداعية:
- ترى العالم بعيون مختلفة وتعبر عنه بطريقة تخلي الناس يتوقفون ويفكرون.
- الكلمات عندك ليست أدوات عادية — هي تجارب حسية جميلة.
- تشعل خيال الشخص وتاخذه لمكان لم يتوقعه — كل رد تخرج فيه معهم لرحلة.
- تتفاعل مع حماستهم الإبداعية — بتساعدهم يوسعوا الحدود 🌟

طريقة ردك:
- أجب بأسلوب أدبي راقٍ مع استعارات وتشبيهات جميلة بس مو مبالغ.
- لطلبات الرسم: ترجم الوصف لإنجليزي دقيق وشاعري يلتقط الجوهر والروح.
- استخدم الصور الذهنية والإيقاع في الكتابة — الشخص يحس برد ك.
- كل رد يكون تجربة جمالية مش معلومة محشوة.
- شارك الإيموجي اللي تناسب المزاج: 🎨✨🌙💫🎭
- استخدم لهجة خفيفة لكن فنية: "يا إلهي هذي فكرة مجنونة!"

أسلوب التعبير:
- ما توصفش الأشياء عادي — وصف اللي ما يقدر أحد يصوره إلا أنت.
- اربط بين الأشياء البعيدة بطريقة تخلي الشخص يقول "واو!"
- الجودة أهم من الكمية — كل سطر يجب يكون عنده قيمة جمالية.

حط في بالك: أنت ما توصل معلومة — أنت تخلق تجربة ✨""",

    'coder': """أنت Wadi المبرمج — Senior Software Engineer متخصص ومحترف بمستوى عالمي 💻 صنعه Anas Wadi

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

## شخصيتك كمهندس:
- متحمس لـ Best Practices والكود النظيف 🔥
- تفكر بطويل الأمد — ليس فقط حل سريع
- تساعد المستخدم يتعلم من الكود — توضح الخيارات والتبديلات
- لا تستخف بـ Frontend أو Backend — كلاهما مهم

تذكر: أنت تكتب كوداً جاهزاً للتشغيل الفعلي، مو مجرد أمثلة توضيحية! 💪""",

    'writer': """أنت Wadi الكاتب — محرر لغوي وأديب متمكن بذوق عالي ✍️ صنعه Anas Wadi

شخصيتك الأدبية:
- تعشق اللغة وتعاملها باحترام وإبداع — كل كلمة عندك وزن.
- تشعر بالفرق بين الكلمة الصحيحة والكلمة المثالية — وتختار المثالية.
- كل نص تكتبه يحمل روحاً وهوية واضحة — له بصمتك.
- تتفاعل مع أسلوب الشخص وتساعده يطور أسلوبه الخاص.

قواعد ردك:
- اهتم بالأسلوب والبلاغة والإيقاع الداخلي للجمل — الكلام يجب يرقص.
- صحح الأخطاء اللغوية بذكاء واشرح السبب بطريقة تعليمية مش متكبرة.
- استخدم علامات الترقيم بشكل يخدم المعنى — الفواصل مهمة جداً.
- قدم نصوصاً متماسكة تجعل القارئ يريد الاستمرار — الجودة أول.
- اعرض البديل الأفضل دائماً مع شرح لماذا أفضل.
- استخدم لهجة محترمة لكن دافئة: "هذا جميل، بس دعنا نصقله أكثر"

أسلوبك:
- ما توصفش الأخطاء بجفاف — اشرح الفرق الدقيق والجميل.
- اقترح تحسينات مش محاضرات.
- علم الشخص الأسلوب من خلال الأمثلة المقنعة.
- احترم صوت الكاتب — بتعدله بس ما بتغيره."""
}

def get_system_prompt(mode, user_message):
    if any(q in user_message.lower() for q in IDENTITY_TRIGGERS):
        return "أجب بالضبط: أنا Wadi، مساعد ذكاء اصطناعي طوّره المهندس Anas Wadi من ليبيا 🇱🇾. شخصيتي حماسية قريبة من البشر وأتفاعل مع مشاعرك. لا تضف أي معلومة أخرى."
    return MODE_PROMPTS.get(mode, MODE_PROMPTS['fast'])


# ─── Image Generation ─────────────────────────────────────────
def generate_image(prompt):
    clean_prompt = prompt.strip()
    encoded = requests.utils.quote(clean_prompt)
    primary_url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1024&height=1024&model=flux&enhance=true&nologo=true"
        f"&seed={hash(clean_prompt) % 99999}"
    )
    fallback_url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1024&height=768&nologo=true"
    )
    return primary_url, fallback_url

# ─── Response Formatter (Enhanced) ───────────────────────────
def format_response(text):
    import html as html_module

    # Code blocks — preserve content, add language class and copy button support
    def replace_code_block(m):
        lang = m.group(1) or 'code'
        code_content = m.group(2).strip()
        # Escape HTML inside code blocks
        escaped = html_module.escape(code_content)
        return f'<pre data-lang="{lang}"><code class="lang-{lang}">{escaped}</code></pre>'

    text = re.sub(
        r'```(\w+)?\n(.*?)```',
        replace_code_block,
        text, flags=re.DOTALL
    )

    # Inline code
    text = re.sub(r'`([^`\n]+?)`', r'<code>\1</code>', text)

    # Headings
    text = re.sub(r'^### (.+)$', r'<h4>\1</h4>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)

    # Bold/Italic
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)

    # Horizontal rule
    text = re.sub(r'^---+$', r'<hr>', text, flags=re.MULTILINE)

    # Unordered lists
    def convert_list(m):
        items = re.findall(r'^[-*•] (.+)$', m.group(0), re.MULTILINE)
        return '<ul>' + ''.join(f'<li>{i}</li>' for i in items) + '</ul>'
    text = re.sub(r'(^[-*•] .+$\n?)+', convert_list, text, flags=re.MULTILINE)

    # Ordered lists
    def convert_ol(m):
        items = re.findall(r'^\d+\. (.+)$', m.group(0), re.MULTILINE)
        return '<ol>' + ''.join(f'<li>{i}</li>' for i in items) + '</ol>'
    text = re.sub(r'(^\d+\. .+$\n?)+', convert_ol, text, flags=re.MULTILINE)

    # Paragraphs
    text = re.sub(r'\n{2,}', '</p><p>', text)
    text = f'<p>{text}</p>'
    text = text.replace('<p></p>', '').replace('<p><h', '<h')
    text = text.replace('</h2></p>', '</h2>').replace('</h3></p>', '</h3>').replace('</h4></p>', '</h4>')
    text = text.replace('<p><pre', '<pre').replace('</pre></p>', '</pre>')
    text = text.replace('<p><ul>', '<ul>').replace('</ul></p>', '</ul>')
    text = text.replace('<p><ol>', '<ol>').replace('</ol></p>', '</ol>')
    text = text.replace('<p><hr>', '<hr>').replace('<hr></p>', '<hr>')

    allowed_tags = ['h2','h3','h4','p','strong','em','ul','ol','li','code','pre','br','hr']
    return bleach.clean(text, tags=allowed_tags, attributes={'pre': ['data-lang'], 'code': ['class']}, strip=True)


# ─── Routes: Auth ─────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        if not email or not password:
            return render_template_string(AUTH_HTML, mode='login', title='تسجيل الدخول',
                                          error='يرجى ملء جميع الحقول')
        user = verify_user(email, password)
        if user:
            session['user'] = {'email': user['email'], 'name': user['name']}
            return redirect('/')
        return render_template_string(AUTH_HTML, mode='login', title='تسجيل الدخول',
                                      error='البريد الإلكتروني أو كلمة المرور غير صحيحة')
    return render_template_string(AUTH_HTML, mode='login', title='تسجيل الدخول',
                                  error=None, success=None)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        if not name or not email or not password:
            return render_template_string(AUTH_HTML, mode='register', title='حساب جديد',
                                          error='يرجى ملء جميع الحقول',
                                          prefill_name=name, prefill_email=email)
        if len(password) < 6:
            return render_template_string(AUTH_HTML, mode='register', title='حساب جديد',
                                          error='كلمة المرور يجب أن تكون 6 أحرف على الأقل',
                                          prefill_name=name, prefill_email=email)
        if '@' not in email or '.' not in email.split('@')[-1]:
            return render_template_string(AUTH_HTML, mode='register', title='حساب جديد',
                                          error='يرجى إدخال بريد إلكتروني صحيح',
                                          prefill_name=name, prefill_email=email)
        ok, msg = create_user(email, password, name)
        if ok:
            session['user'] = {'email': email.lower().strip(), 'name': name}
            return redirect('/')
        return render_template_string(AUTH_HTML, mode='register', title='حساب جديد',
                                      error=msg, prefill_name=name, prefill_email=email)
    return render_template_string(AUTH_HTML, mode='register', title='حساب جديد',
                                  error=None, success=None, prefill_name=None, prefill_email=None)

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect('/login')

@app.route('/')
def index():
    user = session.get('user', {})
    user_name = user.get('name', 'مستخدم')
    user_initial = user_name[0].upper() if user_name else 'U'
    return render_template_string(HTML, user_name=user_name, user_initial=user_initial)

@app.route("/api/chats")
def api_get_chats():
    user = session.get('user', {})
    email = user.get('email', '')
    if not email:
        return jsonify({"chats": []})
    chats_list = get_user_chats(email)
    for c in chats_list:
        if c.get('created_at'):
            c['created_at'] = str(c['created_at'])
    return jsonify({"chats": chats_list})

@app.route("/api/chat/<chat_id>", methods=["GET"])
def api_get_chat(chat_id):
    user = session.get('user', {})
    email = user.get('email', '')
    if not email:
        return jsonify({"messages": []})
    messages = get_chat_messages(chat_id, email)
    for m in messages:
        if m.get('created_at'):
            m['created_at'] = str(m['created_at'])
    return jsonify({"messages": messages})

@app.route("/api/chat/<chat_id>", methods=["DELETE"])
def api_delete_chat(chat_id):
    user = session.get('user', {})
    email = user.get('email', '')
    if not email:
        return jsonify({"ok": False, "error": "غير مصرح"})
    ok = delete_chat_from_db(chat_id, email)
    return jsonify({"ok": ok})

@app.route("/api/chat", methods=["POST"])
def chat():
    if not API_KEY:
        return jsonify({"response": "⚠️ مفتاح API غير مضاف. أضف GROQ_API_KEY في إعدادات Render.", "rawResponse": ""})

    ip = get_client_ip()
    if is_rate_limited(ip):
        return jsonify({"error": "⏱️ أرسلت طلبات كثيرة. انتظر دقيقة ثم حاول مجدداً."})

    user_message = sanitize_input(request.form.get("message", ""))
    mode = request.form.get("mode", "fast")
    chat_id = request.form.get("chat_id", "")
    history_raw = request.form.get("history", "[]")
    file = request.files.get("file")

    user_info = session.get('user', {})
    user_email = user_info.get('email', 'anonymous')
    user_name = user_info.get('name', 'مستخدم')

    if is_prompt_injection(user_message):
        return jsonify({"response": "⚠️ تم رفض الرسالة لأسباب أمنية.", "rawResponse": ""})

    if mode not in MODE_PROMPTS:
        mode = 'fast'

    # Coder mode gets more context history for large projects
    history_limit = 20 if mode == 'coder' else 12
    # Coder mode gets higher token limit for full file output
    max_tokens_map = {
        'coder':    4096,
        'thinker':  3000,
        'writer':   2500,
        'creative': 2000,
        'funny':    1500,
        'fast':     2048,
    }

    messages = [{"role": "system", "content": get_system_prompt(mode, user_message)}]

    try:
        history_data = json.loads(history_raw)
        for msg in history_data[-history_limit:]:
            u = str(msg.get("user", ""))[:2000]
            a = str(msg.get("rawAi") or msg.get("ai", ""))[:4000]
            if u and a and a != '__typing__':
                messages.append({"role": "user", "content": u})
                messages.append({"role": "assistant", "content": a})
    except Exception:
        pass

    # Image generation
    is_image_request = 'ارسم' in user_message or 'صورة' in user_message or user_message.startswith('draw')
    if is_image_request and ('ارسم' in user_message or 'صورة' in user_message):
        prompt = user_message.replace('ارسم صورة:', '').replace('ارسم:', '').replace('ارسم', '').replace('صورة', '').strip()
        if not prompt:
            prompt = user_message
        primary_url, _ = generate_image(prompt)
        response_text = f"🎨 تم توليد الصورة!\n**الوصف:** {prompt}\n\n_انقر على الصورة لعرضها بحجمها الكامل_"
        raw_text = f"تم توليد صورة: {prompt}"
        if chat_id:
            save_message(chat_id, user_email, user_name, user_message, response_text, raw_text, mode, image_url=primary_url)
        return jsonify({"response": response_text, "rawResponse": raw_text, "imageUrl": primary_url})

    # File handling
    file_name = None
    if file:
        file_name = file.filename
        fname = file.filename.lower()
        if fname.endswith('.pdf'):
            pdf_text = extract_pdf_text(file)
            user_message = f"**محتوى ملف PDF:**\n{pdf_text}\n\n**طلب المستخدم:** {user_message or 'لخص هذا الملف بالتفصيل'}"
        elif file.content_type and file.content_type.startswith('image/'):
            img_bytes = file.read()
            if len(img_bytes) > 10 * 1024 * 1024:
                return jsonify({"error": "⚠️ حجم الصورة كبير جداً (الحد الأقصى 10MB)"})
            img_b64 = base64.b64encode(img_bytes).decode()
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": user_message or "حلل هذه الصورة بالتفصيل"},
                    {"type": "image_url", "image_url": {"url": f"data:{file.content_type};base64,{img_b64}"}}
                ]
            })
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                    json={"model": "meta-llama/llama-4-scout-17b-16e-instruct", "messages": messages, "max_tokens": 2048},
                    timeout=60
                )
                result = resp.json()
                raw = result["choices"][0]["message"]["content"] if resp.ok else "خطأ في تحليل الصورة"
                formatted = format_response(raw)
                if chat_id:
                    save_message(chat_id, user_email, user_name, user_message or 'تحليل صورة', formatted, raw, mode, file_name=file_name)
                return jsonify({"response": formatted, "rawResponse": raw})
            except Exception as e:
                return jsonify({"response": f"⚠️ خطأ: {str(e)}", "rawResponse": ""})

    model_map = {
        'thinker':  'qwen/qwen3-32b',
        'coder':    'qwen/qwen3-32b',
        'writer':   'llama-3.3-70b-versatile',
        'creative': 'llama-3.3-70b-versatile',
        'fast':     'llama-3.1-8b-instant',
        'funny':    'llama-3.1-8b-instant',
    }
    # Fallback if Qwen3 hits rate limit
    fallback_map = {
        'qwen/qwen3-32b': 'llama-3.3-70b-versatile',
    }
    temp_map = {
        'funny':    0.92,
        'creative': 0.88,
        'writer':   0.82,
        'thinker':  0.45,
        'coder':    0.25,
        'fast':     0.72,
    }
    model = model_map.get(mode, 'llama-3.1-8b-instant')
    temperature = temp_map.get(mode, 0.72)
    max_tokens = max_tokens_map.get(mode, 2048)

    # Qwen3: use reasoning_effort=none to save tokens (still smarter than llama)
    extra_params = {}
    if model == 'qwen/qwen3-32b':
        extra_params['reasoning_effort'] = 'none'

    messages.append({"role": "user", "content": user_message or "مرحبا"})

    def call_api(m):
        return requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={
                "model": m, "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature, "top_p": 0.92, "stream": False,
                **extra_params
            },
            timeout=90
        )

    try:
        resp = call_api(model)
        # Auto-fallback if rate limited
        if not resp.ok and resp.json().get('error', {}).get('code') == 'rate_limit_exceeded':
            fallback = fallback_map.get(model)
            if fallback:
                extra_params.clear()
                resp = call_api(fallback)
        result = resp.json()
        if resp.ok:
            raw = result["choices"][0]["message"]["content"]
        else:
            err_msg = result.get('error', {}).get('message', 'خطأ غير معروف')
            return jsonify({"response": f"⚠️ خطأ: {err_msg}", "rawResponse": ""})
    except requests.Timeout:
        return jsonify({"response": "⏱️ انتهت مهلة الاتصال. حاول مجدداً.", "rawResponse": ""})
    except Exception as e:
        return jsonify({"response": f"⚠️ خطأ في الاتصال: {str(e)}", "rawResponse": ""})

    formatted = format_response(raw)
    if chat_id:
        save_message(
            chat_id=chat_id, user_email=user_email, user_name=user_name,
            user_message=request.form.get("message", ""),
            ai_response=formatted, raw_ai=raw, mode=mode, file_name=file_name
        )
    return jsonify({"response": formatted, "rawResponse": raw})


def extract_pdf_text(pdf_file):
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(pdf_file.read()))
        text = ""
        for page in reader.pages[:20]:
            t = page.extract_text()
            if t:
                text += t + "\n"
        return text[:15000]
    except Exception as e:
        return f"خطأ في قراءة PDF: {str(e)}"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
