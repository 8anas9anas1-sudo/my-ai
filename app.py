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
  const sz = Math.random()*2.5+0.5;
  el.style.cssText=`width:${sz}px;height:${sz}px;left:${Math.random()*100}%;top:${Math.random()*100}%;--d:${(Math.random()*4+2).toFixed(1)}s;--op:${(Math.random()*0.4+0.2).toFixed(2)};animation-delay:${(Math.random()*5).toFixed(1)}s`;
  s.appendChild(el);
}
</script>
</body>
</html>
"""

# ─── صفحة الواجهة الرئيسية ───────────────────────────────────
HTML = """
<!DOCTYPE html>
<html dir="rtl" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#050510">
<title>✨ Anas Wadi ✨</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@300;400;500;700;900&family=Cairo:wght@300;400;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>
/* ═══════════════════════════════════════════
   CSS VARIABLES — Dark / Light Theme
═══════════════════════════════════════════ */
:root {
  --bg: #050510;
  --surface: rgba(255,255,255,0.04);
  --surface2: rgba(255,255,255,0.08);
  --surface3: rgba(255,255,255,0.12);
  --border: rgba(255,255,255,0.08);
  --border-glow: rgba(0,210,255,0.35);
  --text: #e8eaf6;
  --text-dim: rgba(232,234,246,0.55);
  --text-muted: rgba(232,234,246,0.35);
  --accent1: #00d2ff;
  --accent2: #00ff94;
  --accent3: #7c4dff;
  --accent4: #ff6b6b;
  --user-grad: linear-gradient(135deg, #00ff94, #00d2ff);
  --glow: 0 0 30px rgba(0,210,255,0.15);
  --glow-strong: 0 0 40px rgba(0,210,255,0.3);
  --sidebar-bg: rgba(4,4,14,0.98);
  --header-bg: rgba(5,5,16,0.88);
}
[data-theme="light"] {
  --bg: #f0f4ff;
  --surface: rgba(0,0,0,0.04);
  --surface2: rgba(0,0,0,0.07);
  --surface3: rgba(0,0,0,0.11);
  --border: rgba(0,0,0,0.1);
  --border-glow: rgba(0,100,200,0.3);
  --text: #0d1117;
  --text-dim: rgba(13,17,23,0.6);
  --text-muted: rgba(13,17,23,0.38);
  --accent1: #0077cc;
  --accent2: #00aa66;
  --accent3: #5533cc;
  --glow: 0 0 30px rgba(0,100,200,0.1);
  --glow-strong: 0 0 40px rgba(0,100,200,0.2);
  --sidebar-bg: rgba(235,240,255,0.98);
  --header-bg: rgba(240,244,255,0.88);
}

/* ═══════════════════════════════════════════
   RESET & BASE
═══════════════════════════════════════════ */
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

/* ═══════════════════════════════════════════
   STARS
═══════════════════════════════════════════ */
.stars { position:fixed; inset:0; pointer-events:none; z-index:0; overflow:hidden; }
.star {
  position:absolute; border-radius:50%; background:white;
  animation:twinkle var(--d,3s) ease-in-out infinite;
}
@keyframes twinkle {
  0%,100%{opacity:0;transform:scale(0.5)}
  50%{opacity:var(--op,0.6);transform:scale(1)}
}

/* ═══════════════════════════════════════════
   HEADER — Clean, Professional, Mobile-First
═══════════════════════════════════════════ */
.header {
  position:sticky; top:0; z-index:100;
  background:var(--header-bg);
  backdrop-filter:blur(24px) saturate(1.6);
  -webkit-backdrop-filter:blur(24px);
  border-bottom:1px solid var(--border);
  padding:0 16px;
  height:58px;
  display:flex; align-items:center; gap:10px;
  transition:background 0.3s;
}

/* Left slot: menu button */
.header-left { display:flex; align-items:center; gap:8px; flex-shrink:0; }

/* Center slot: logo takes full remaining space */
.header-center { flex:1; display:flex; justify-content:center; align-items:center; }

/* Right slot: actions */
.header-right { display:flex; align-items:center; gap:6px; flex-shrink:0; }

.logo {
  font-size:20px; font-weight:900; letter-spacing:-0.3px; white-space:nowrap;
  background:linear-gradient(90deg,var(--accent2),var(--accent1),var(--accent3));
  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  background-clip:text; animation:logoShimmer 4s linear infinite; background-size:200%;
  text-shadow:none;
}
@keyframes logoShimmer{0%{background-position:0% 50%}100%{background-position:200% 50%}}

.icon-btn {
  background:var(--surface); border:1px solid var(--border);
  color:var(--text); width:38px; height:38px;
  border-radius:10px; cursor:pointer; font-size:15px;
  transition:all 0.22s; display:flex; align-items:center; justify-content:center;
  position:relative; overflow:hidden; text-decoration:none; flex-shrink:0;
}
.icon-btn::before {
  content:''; position:absolute; inset:0;
  background:linear-gradient(135deg,var(--accent1),var(--accent2));
  opacity:0; transition:opacity 0.22s;
}
.icon-btn:hover { border-color:var(--border-glow); transform:translateY(-1px); box-shadow:var(--glow); }
.icon-btn:hover::before { opacity:0.12; }
.icon-btn:active { transform:scale(0.94); }
.icon-btn i { position:relative; z-index:1; }

/* ═══════════════════════════════════════════
   SIDEBAR — Professional with User Profile & Delete
═══════════════════════════════════════════ */
.sidebar {
  position:fixed; right:-300px; top:0;
  width:280px; height:100vh;
  background:var(--sidebar-bg); backdrop-filter:blur(30px);
  border-left:1px solid var(--border);
  transition:right 0.36s cubic-bezier(0.4,0,0.2,1);
  z-index:200; display:flex; flex-direction:column;
}
[data-theme="light"] .sidebar { box-shadow:-4px 0 30px rgba(0,0,0,0.08); }
.sidebar.open { right:0; }

.sidebar-overlay {
  display:none; position:fixed; inset:0; z-index:199;
  background:rgba(0,0,0,0.5); backdrop-filter:blur(4px);
  transition:opacity 0.3s;
}
.sidebar-overlay.open { display:block; }

/* Sidebar user profile card */
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
  box-shadow:0 4px 14px rgba(0,210,255,0.3);
}
.sidebar-profile-info { flex:1; min-width:0; }
.sidebar-profile-name { font-size:14px; font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.sidebar-profile-badge {
  display:inline-flex; align-items:center; gap:4px;
  font-size:10px; color:var(--accent2); margin-top:2px;
  background:rgba(0,255,148,0.1); padding:2px 8px; border-radius:20px;
  border:1px solid rgba(0,255,148,0.2);
}

/* Sidebar top actions */
.sidebar-actions { padding:12px 14px; }
.new-chat-btn {
  background:linear-gradient(135deg,var(--accent2),var(--accent1));
  border:none; border-radius:12px; padding:11px 16px;
  font-family:'Tajawal',sans-serif; font-size:14px; font-weight:700;
  color:#000; cursor:pointer; transition:all 0.25s; width:100%;
  display:flex; align-items:center; gap:8px; justify-content:center;
  box-shadow:0 4px 18px rgba(0,210,255,0.25);
}
.new-chat-btn:hover { transform:translateY(-1px); box-shadow:0 6px 24px rgba(0,210,255,0.4); }
.new-chat-btn:active { transform:scale(0.98); }

/* Sidebar chat list */
.sidebar-label {
  padding:4px 16px 8px;
  font-size:11px; font-weight:700; color:var(--text-muted);
  text-transform:uppercase; letter-spacing:1px;
}
.chat-list-scroll { flex:1; overflow-y:auto; padding:0 10px 10px; }
.chat-list-scroll::-webkit-scrollbar { width:3px; }
.chat-list-scroll::-webkit-scrollbar-thumb { background:var(--border); border-radius:10px; }

.chat-item {
  display:flex; align-items:center; gap:8px;
  padding:9px 12px; border-radius:10px; cursor:pointer;
  transition:all 0.2s; margin-bottom:3px;
  border:1px solid transparent;
  position:relative;
}
.chat-item:hover { background:var(--surface2); border-color:var(--border); }
.chat-item.active { background:rgba(0,210,255,0.08); border-color:rgba(0,210,255,0.25); }
.chat-item-icon { font-size:14px; flex-shrink:0; opacity:0.7; }
.chat-item-text {
  flex:1; font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  color:var(--text-dim);
}
.chat-item.active .chat-item-text { color:var(--text); }
.chat-item-delete {
  opacity:0; flex-shrink:0; width:24px; height:24px;
  border-radius:6px; border:none; background:rgba(255,80,80,0.12);
  color:#ff6b6b; cursor:pointer; font-size:11px;
  display:flex; align-items:center; justify-content:center;
  transition:all 0.18s;
}
.chat-item:hover .chat-item-delete { opacity:1; }
.chat-item-delete:hover { background:rgba(255,80,80,0.25); transform:scale(1.1); }

/* Sidebar footer */
.sidebar-footer {
  padding:12px 14px; border-top:1px solid var(--border);
  display:flex; gap:8px;
}
.sidebar-footer-btn {
  flex:1; background:var(--surface); border:1px solid var(--border);
  color:var(--text-dim); padding:9px 12px; border-radius:10px;
  font-family:'Tajawal',sans-serif; font-size:12px; font-weight:600;
  cursor:pointer; transition:all 0.2s; display:flex; align-items:center; gap:6px; justify-content:center;
}
.sidebar-footer-btn:hover { border-color:var(--border-glow); color:var(--text); }

/* ═══════════════════════════════════════════
   MODE BUTTONS
═══════════════════════════════════════════ */
.modes {
  display:flex; gap:7px; padding:10px 16px;
  overflow-x:auto; position:relative; z-index:1; scrollbar-width:none;
  border-bottom:1px solid var(--border);
}
.modes::-webkit-scrollbar { display:none; }
.mode-btn {
  background:var(--surface); border:1px solid var(--border);
  color:var(--text-dim); padding:7px 15px; border-radius:20px;
  font-size:12px; font-weight:600; white-space:nowrap; cursor:pointer;
  transition:all 0.22s; font-family:'Tajawal',sans-serif;
}
.mode-btn:hover { border-color:var(--border-glow); color:var(--text); }
.mode-btn.active {
  background:linear-gradient(135deg,var(--accent2),var(--accent1));
  color:#000; border-color:transparent;
  box-shadow:0 3px 14px rgba(0,210,255,0.3);
  font-weight:800;
}

/* ═══════════════════════════════════════════
   CHAT CONTAINER
═══════════════════════════════════════════ */
.chat-container {
  flex:1; padding:20px 16px 10px;
  max-width:800px; width:100%; margin:0 auto; position:relative; z-index:1;
}

/* ═══════════════════════════════════════════
   WELCOME SCREEN
═══════════════════════════════════════════ */
.welcome { text-align:center; padding:44px 20px; animation:fadeUp 0.6s ease both; }
@keyframes fadeUp { from{opacity:0;transform:translateY(20px)} to{opacity:1;transform:translateY(0)} }
.welcome-icon { font-size:54px; margin-bottom:18px; animation:floatIcon 3s ease-in-out infinite; display:block; }
@keyframes floatIcon { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-10px)} }
.welcome h2 { font-size:24px; font-weight:900; margin-bottom:10px; }
.welcome p { color:var(--text-dim); line-height:1.9; font-size:15px; }
.welcome-cards { display:flex; gap:10px; margin-top:28px; flex-wrap:wrap; justify-content:center; }
.welcome-card {
  background:var(--surface); border:1px solid var(--border);
  border-radius:14px; padding:14px 16px; cursor:pointer;
  transition:all 0.25s; text-align:right; min-width:140px; max-width:190px; font-size:13px;
}
.welcome-card:hover {
  border-color:var(--border-glow); transform:translateY(-4px);
  box-shadow:var(--glow-strong); background:var(--surface2);
}
.welcome-card .card-icon { font-size:22px; margin-bottom:6px; }
.welcome-card .card-title { font-weight:800; margin-bottom:4px; }
.welcome-card .card-desc { color:var(--text-dim); font-size:11px; }

/* ═══════════════════════════════════════════
   MESSAGES
═══════════════════════════════════════════ */
.message { margin:14px 0; animation:msgSlide 0.35s cubic-bezier(0.4,0,0.2,1) both; }
@keyframes msgSlide { from{opacity:0;transform:translateY(16px) scale(0.97)} to{opacity:1;transform:translateY(0) scale(1)} }

.user-msg {
  background:var(--user-grad); color:#000; font-weight:700;
  padding:13px 18px; border-radius:20px 20px 6px 20px;
  margin-right:auto; max-width:78%;
  box-shadow:0 6px 24px rgba(0,255,148,0.22);
  font-size:15px; line-height:1.75; word-break:break-word;
}

.ai-msg {
  background:var(--surface);
  border:1px solid var(--border);
  padding:16px 20px; border-radius:20px 20px 20px 6px;
  max-width:86%; font-size:15px; line-height:1.95;
  position:relative; word-break:break-word;
  transition:border-color 0.3s, box-shadow 0.3s;
}
.ai-msg:hover { border-color:var(--border-glow); box-shadow:var(--glow); }
.ai-msg h2 { font-size:18px; font-weight:900; margin:14px 0 6px; color:var(--accent1); }
.ai-msg h3 { font-size:16px; font-weight:800; margin:12px 0 5px; color:var(--accent2); }
.ai-msg h4 { font-size:15px; font-weight:700; margin:10px 0 4px; color:var(--accent3); }
.ai-msg strong { font-weight:900; color:var(--accent1); }
.ai-msg em { font-style:italic; color:var(--accent2); opacity:0.9; }
.ai-msg p { margin:7px 0; }
.ai-msg ul, .ai-msg ol { padding-right:24px; margin:10px 0; }
.ai-msg li { margin:6px 0; line-height:1.85; }
.ai-msg code {
  background:rgba(0,210,255,0.1); border:1px solid rgba(0,210,255,0.2);
  border-radius:5px; padding:2px 8px; font-size:13px; font-family:monospace;
  color:var(--accent1);
}
.ai-msg pre {
  background:#0d1117; border:1px solid rgba(0,210,255,0.18);
  border-radius:12px; margin:14px 0;
  overflow:hidden; position:relative;
  box-shadow:0 4px 20px rgba(0,0,0,0.35);
}
[data-theme="light"] .ai-msg pre { background:#1a1f2e; }
.ai-msg pre code {
  background:none; border:none; padding:16px; font-size:13px;
  color:#e6edf3; display:block; overflow-x:auto;
  font-family:'Fira Code','Cascadia Code','Consolas',monospace;
  line-height:1.7; white-space:pre;
}
/* Code block header with language badge + copy button */
.code-header {
  display:flex; align-items:center; justify-content:space-between;
  background:rgba(0,210,255,0.07); border-bottom:1px solid rgba(0,210,255,0.15);
  padding:7px 14px; font-size:12px;
}
.code-lang-badge {
  color:var(--accent1); font-family:monospace; font-weight:700;
  text-transform:uppercase; letter-spacing:0.5px;
}
.copy-code-btn {
  background:var(--surface2); border:1px solid var(--border);
  color:var(--text-dim); padding:3px 10px; border-radius:6px;
  font-size:11px; cursor:pointer; font-family:'Tajawal',sans-serif;
  transition:all 0.2s; display:flex; align-items:center; gap:4px;
}
.copy-code-btn:hover { border-color:var(--accent1); color:var(--accent1); background:rgba(0,210,255,0.08); }
.copy-code-btn.copied { border-color:var(--accent2); color:var(--accent2); }

/* Typing indicator */
.typing-indicator { display:inline-flex; gap:5px; align-items:center; padding:4px 0; }
.typing-indicator span {
  width:7px; height:7px; border-radius:50%; background:var(--accent1);
  animation:typing 1.2s ease-in-out infinite;
}
.typing-indicator span:nth-child(2) { animation-delay:0.2s; background:var(--accent2); }
.typing-indicator span:nth-child(3) { animation-delay:0.4s; background:var(--accent3); }
@keyframes typing {
  0%,60%,100%{transform:translateY(0);opacity:0.4}
  30%{transform:translateY(-8px);opacity:1}
}

/* Message extras */
.file-badge {
  display:inline-flex; align-items:center; gap:6px;
  background:rgba(0,210,255,0.1); border:1px solid rgba(0,210,255,0.3);
  padding:5px 12px; border-radius:8px; font-size:12px;
  margin-bottom:6px; color:var(--accent1);
}
.msg-actions { display:flex; gap:6px; margin-top:8px; opacity:0; transition:opacity 0.2s; }
.message:hover .msg-actions { opacity:1; }
.msg-btn {
  background:var(--surface); border:1px solid var(--border);
  color:var(--text-dim); padding:5px 12px; border-radius:8px;
  font-size:12px; cursor:pointer; font-family:'Tajawal',sans-serif; transition:all 0.2s;
}
.msg-btn:hover { border-color:var(--accent1); color:var(--text); }

.generated-img {
  max-width:100%; border-radius:14px; margin-top:12px; display:block;
  box-shadow:0 8px 30px rgba(0,0,0,0.4); animation:imgReveal 0.5s ease both;
}
@keyframes imgReveal { from{opacity:0;transform:scale(0.94)} to{opacity:1;transform:scale(1)} }

/* ═══════════════════════════════════════════
   INPUT AREA
═══════════════════════════════════════════ */
.input-area {
  position:sticky; bottom:0; z-index:10;
  background:var(--header-bg); backdrop-filter:blur(20px);
  border-top:1px solid var(--border); padding:10px 16px 14px;
}

.templates {
  display:flex; gap:7px; margin-bottom:9px;
  overflow-x:auto; padding-bottom:3px; scrollbar-width:none;
  max-width:800px; margin-right:auto; margin-left:auto;
}
.templates::-webkit-scrollbar { display:none; }
.template-btn {
  background:var(--surface); border:1px solid var(--border);
  color:var(--text-dim); padding:6px 14px; border-radius:20px;
  font-size:12px; white-space:nowrap; cursor:pointer;
  font-family:'Tajawal',sans-serif; transition:all 0.2s;
}
.template-btn:hover { border-color:var(--border-glow); color:var(--text); background:var(--surface2); }

.input-wrapper { max-width:800px; margin:0 auto; display:flex; gap:8px; align-items:flex-end; }
.textarea-wrap { flex:1; position:relative; }
textarea {
  width:100%; background:var(--surface2); border:1px solid var(--border);
  color:var(--text); border-radius:16px; padding:13px 16px;
  font-size:15px; font-family:'Tajawal',sans-serif;
  resize:none; height:52px; max-height:130px;
  transition:border-color 0.25s, box-shadow 0.25s; line-height:1.5;
}
textarea:focus {
  outline:none; border-color:var(--accent1);
  box-shadow:0 0 0 3px rgba(0,210,255,0.1);
}
textarea::placeholder { color:var(--text-muted); }
#fileInput { display:none; }
.send-btn {
  background:linear-gradient(135deg,var(--accent2),var(--accent1));
  border:none; border-radius:14px; width:52px; height:52px;
  font-size:18px; color:#000; cursor:pointer; transition:all 0.25s; flex-shrink:0;
  display:flex; align-items:center; justify-content:center;
  box-shadow:0 4px 18px rgba(0,210,255,0.25);
}
.send-btn:hover:not(:disabled) { transform:scale(1.08); box-shadow:0 6px 22px rgba(0,210,255,0.45); }
.send-btn:active:not(:disabled) { transform:scale(0.95); }
.send-btn:disabled { opacity:0.45; cursor:not-allowed; }

.file-preview {
  max-width:800px; margin:0 auto 8px;
  display:flex; align-items:center; gap:8px;
  background:rgba(0,210,255,0.07); border:1px solid rgba(0,210,255,0.2);
  padding:8px 14px; border-radius:10px; font-size:13px;
}
.file-preview.hidden { display:none; }

/* ═══════════════════════════════════════════
   MODALS
═══════════════════════════════════════════ */
.modal {
  display:none; position:fixed; inset:0; z-index:300;
  justify-content:center; align-items:center;
  background:rgba(0,0,0,0.7); backdrop-filter:blur(8px);
}
.modal.open { display:flex; }
.modal-content {
  background:var(--bg); border:1px solid var(--border);
  padding:32px; border-radius:22px; max-width:460px; width:90%;
  text-align:center; animation:modalPop 0.3s cubic-bezier(0.4,0,0.2,1);
}
@keyframes modalPop { from{opacity:0;transform:scale(0.88) translateY(22px)} to{opacity:1;transform:scale(1) translateY(0)} }
.support-btn {
  display:inline-flex; align-items:center; gap:8px;
  background:linear-gradient(135deg,#ff6b6b,#feca57);
  color:#000; border:none; padding:12px 28px;
  border-radius:14px; font-weight:800; cursor:pointer;
  font-family:'Tajawal',sans-serif; font-size:15px;
  text-decoration:none; margin-top:16px; transition:all 0.25s;
}
.support-btn:hover { transform:scale(1.05); box-shadow:0 6px 20px rgba(254,202,87,0.4); }

/* Confirm delete modal */
.confirm-modal-content {
  background:var(--bg); border:1px solid rgba(255,80,80,0.3);
  padding:28px; border-radius:20px; max-width:340px; width:90%;
  text-align:center; animation:modalPop 0.3s cubic-bezier(0.4,0,0.2,1);
}
.confirm-modal-content h3 { font-size:18px; margin-bottom:10px; }
.confirm-modal-content p { color:var(--text-dim); font-size:14px; margin-bottom:20px; }
.confirm-btns { display:flex; gap:10px; justify-content:center; }
.confirm-btn-del {
  background:linear-gradient(135deg,#ff4444,#ff6b6b);
  border:none; border-radius:12px; padding:10px 24px;
  color:#fff; font-weight:700; font-family:'Tajawal',sans-serif;
  font-size:14px; cursor:pointer; transition:all 0.2s;
}
.confirm-btn-del:hover { transform:scale(1.04); }
.confirm-btn-cancel {
  background:var(--surface2); border:1px solid var(--border);
  border-radius:12px; padding:10px 24px;
  color:var(--text); font-weight:700; font-family:'Tajawal',sans-serif;
  font-size:14px; cursor:pointer; transition:all 0.2s;
}
.confirm-btn-cancel:hover { border-color:var(--border-glow); }

/* ═══════════════════════════════════════════
   TOAST
═══════════════════════════════════════════ */
.toast {
  position:fixed; bottom:90px; left:50%;
  transform:translateX(-50%) translateY(20px);
  background:rgba(20,20,40,0.95); color:var(--text);
  border:1px solid var(--border); padding:10px 22px;
  border-radius:12px; z-index:999; font-size:13px;
  opacity:0; transition:all 0.3s; pointer-events:none; backdrop-filter:blur(12px);
  white-space:nowrap;
}
.toast.show { opacity:1; transform:translateX(-50%) translateY(0); }
.toast.error { border-color:rgba(255,80,80,0.5); color:#ff8080; }
.toast.success { border-color:rgba(0,255,148,0.5); color:var(--accent2); }

/* ═══════════════════════════════════════════
   SCROLLBAR
═══════════════════════════════════════════ */
::-webkit-scrollbar { width:5px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:var(--border); border-radius:10px; }
::-webkit-scrollbar-thumb:hover { background:var(--accent1); }

/* ═══════════════════════════════════════════
   RESPONSIVE — Mobile First
═══════════════════════════════════════════ */
@media (max-width:480px) {
  .ai-msg, .user-msg { max-width:96%; }
  .welcome h2 { font-size:20px; }
  .welcome-cards { gap:8px; }
  .welcome-card { min-width:130px; max-width:155px; }
  .logo { font-size:18px; }
  .header { padding:0 10px; height:54px; }
  .icon-btn { width:36px; height:36px; font-size:14px; }
  .modes { padding:8px 12px; gap:6px; }
  .mode-btn { padding:6px 12px; font-size:11px; }
}
@media (max-width:360px) {
  .header-right { gap:4px; }
  .logo { font-size:16px; }
}
</style>
</head>
<body>

<!-- Stars background -->
<div class="stars" id="stars"></div>

<!-- Sidebar overlay -->
<div class="sidebar-overlay" id="sidebarOverlay" onclick="closeSidebar()"></div>

<!-- ═══════ SIDEBAR ═══════ -->
<div class="sidebar" id="sidebar">
  <!-- User profile -->
  <div class="sidebar-profile">
    <div class="sidebar-avatar" id="sidebarAvatar">{{ user_initial }}</div>
    <div class="sidebar-profile-info">
      <div class="sidebar-profile-name" id="sidebarName">{{ user_name }}</div>
      <div class="sidebar-profile-badge"><i class="fa-solid fa-circle" style="font-size:7px;color:#00ff94"></i> متصل الآن</div>
    </div>
  </div>

  <!-- New chat button -->
  <div class="sidebar-actions">
    <button class="new-chat-btn" onclick="newChat()">
      <i class="fa-solid fa-plus"></i> محادثة جديدة
    </button>
  </div>

  <!-- Chat list -->
  <div class="sidebar-label">المحادثات السابقة</div>
  <div class="chat-list-scroll">
    <div id="chatList">
      <div style="color:var(--text-dim);font-size:13px;text-align:center;padding:20px">
        <div class="typing-indicator" style="justify-content:center;margin-bottom:8px"><span></span><span></span><span></span></div>
        جاري التحميل...
      </div>
    </div>
  </div>

  <!-- Footer -->
  <div class="sidebar-footer">
    <button class="sidebar-footer-btn" onclick="toggleTheme()">
      <i class="fa-solid fa-moon" id="sidebarThemeIcon"></i> الوضع
    </button>
    <button class="sidebar-footer-btn" onclick="showSupport()">
      <i class="fa-solid fa-heart" style="color:#ff6b6b"></i> دعم
    </button>
    <a href="/logout" class="sidebar-footer-btn" style="text-decoration:none;color:inherit">
      <i class="fa-solid fa-right-from-bracket"></i> خروج
    </a>
  </div>
</div>

<!-- ═══════ HEADER ═══════ -->
<header class="header">
  <div class="header-left">
    <button class="icon-btn" onclick="toggleSidebar()" title="القائمة">
      <i class="fa-solid fa-bars"></i>
    </button>
  </div>

  <div class="header-center">
    <span class="logo">✨ Anas Wadi ✨</span>
  </div>

  <div class="header-right">
    <button class="icon-btn" onclick="toggleTheme()" title="تغيير الوضع" id="themeBtn">
      <i class="fa-solid fa-moon"></i>
    </button>
    <button class="icon-btn" onclick="newChat()" title="محادثة جديدة">
      <i class="fa-solid fa-plus"></i>
    </button>
    <button class="icon-btn" onclick="showSupport()" title="ادعمني">
      <i class="fa-solid fa-heart" style="color:#ff6b6b"></i>
    </button>
  </div>
</header>

<!-- Mode selector -->
<div class="modes">
  <button class="mode-btn active" data-mode="fast" onclick="setMode('fast')">⚡ سريع</button>
  <button class="mode-btn" data-mode="thinker" onclick="setMode('thinker')">🧠 مفكر</button>
  <button class="mode-btn" data-mode="funny" onclick="setMode('funny')">😂 فكاهي</button>
  <button class="mode-btn" data-mode="creative" onclick="setMode('creative')">🎨 مبدع</button>
  <button class="mode-btn" data-mode="coder" onclick="setMode('coder')">💻 مبرمج</button>
  <button class="mode-btn" data-mode="writer" onclick="setMode('writer')">✍️ كاتب</button>
</div>

<!-- Chat messages -->
<div class="chat-container" id="chatContainer">
  <div class="welcome" id="welcome">
    <span class="welcome-icon">🌊</span>
    <h2>مرحباً في <span style="background:linear-gradient(90deg,#00ff94,#00d2ff);-webkit-background-clip:text;-webkit-text-fill-color:transparent">Anas Wadi</span></h2>
    <p>تم تطوير هذا الذكاء الاصطناعي بيد المهندس <strong>Anas Wadi</strong> من ليبيا 🇱🇾</p>
    <p style="margin-top:6px">كيف يمكنني مساعدتك اليوم؟</p>
    <div class="welcome-cards">
      <div class="welcome-card" onclick="useTemplate('ارسم صورة: ')">
        <div class="card-icon">🎨</div>
        <div class="card-title">رسم صورة</div>
        <div class="card-desc">توليد صور فائقة الجودة</div>
      </div>
      <div class="welcome-card" onclick="useTemplate('اشرحلي ')">
        <div class="card-icon">💡</div>
        <div class="card-title">شرح وتحليل</div>
        <div class="card-desc">أشرح أي موضوع تريده</div>
      </div>
      <div class="welcome-card" onclick="useTemplate('اكتبلي كود ')">
        <div class="card-icon">💻</div>
        <div class="card-title">برمجة</div>
        <div class="card-desc">أكتب ويصلح الكود</div>
      </div>
      <div class="welcome-card" onclick="document.getElementById('fileInput').click()">
        <div class="card-icon">📄</div>
        <div class="card-title">تحليل ملف</div>
        <div class="card-desc">PDF أو صورة</div>
      </div>
    </div>
  </div>
</div>

<!-- Input area -->
<div class="input-area">
  <div class="file-preview hidden" id="filePreview">
    <i class="fa-solid fa-file" style="color:var(--accent1)"></i>
    <span id="fileName">ملف مرفق</span>
    <button class="msg-btn" onclick="removeFile()" style="margin-right:auto">
      <i class="fa-solid fa-xmark"></i>
    </button>
  </div>
  <div class="templates" id="templateBtns">
    <button class="template-btn" onclick="useTemplate('ارسم صورة: ')">🎨 ارسم</button>
    <button class="template-btn" onclick="useTemplate('لخصلي الملف')">📄 لخص</button>
    <button class="template-btn" onclick="useTemplate('اكتبلي ايميل رسمي عن ')">📧 ايميل</button>
    <button class="template-btn" onclick="useTemplate('ترجم للعربية: ')">🌐 ترجم</button>
    <button class="template-btn" onclick="setMode('coder');useTemplate('اصنعلي مشروع Flask كامل: ')">🏗️ مشروع</button>
    <button class="template-btn" onclick="setMode('coder');useTemplate('اكتبلي API بـ Flask يحتوي على ')">⚙️ API</button>
    <button class="template-btn" onclick="setMode('coder');useTemplate('صلحلي هذا الكود:\n')">🔧 صلح Bug</button>
    <button class="template-btn" onclick="useTemplate('اشرحلي بالتفصيل ')">💡 شرح</button>
  </div>
  <div class="input-wrapper">
    <input type="file" id="fileInput" accept="image/*,.pdf" onchange="handleFile(this)">
    <button type="button" class="icon-btn" onclick="document.getElementById('fileInput').click()" title="ارفع ملف">
      <i class="fa-solid fa-paperclip"></i>
    </button>
    <div class="textarea-wrap">
      <textarea id="messageInput"
        placeholder="اكتب رسالتك... (Enter للإرسال، Shift+Enter لسطر جديد)"
        onkeydown="handleKey(event)"
        oninput="autoResize(this)"></textarea>
    </div>
    <button class="send-btn" id="sendBtn" onclick="sendMessage()" title="إرسال">
      <i class="fa-solid fa-paper-plane"></i>
    </button>
  </div>
</div>

<!-- Toast -->
<div class="toast" id="toast"></div>

<!-- Support modal -->
<div class="modal" id="supportModal" onclick="closeModalClick(event)">
  <div class="modal-content">
    <div style="font-size:40px;margin-bottom:15px">💙</div>
    <h2 style="margin-bottom:12px;font-size:22px">شكراً لدعمك!</h2>
    <p style="color:var(--text-dim);line-height:1.9;font-size:14px">
      دعمكم هو الوقود اللي يخلينا نطور ونضيف ميزات جديدة باستمرار 🚀
    </p>
    <a href="https://www.paypal.com" target="_blank" class="support-btn">
      <i class="fa-brands fa-paypal"></i> ادعم عبر PayPal
    </a>
    <p style="margin-top:20px;font-size:12px;color:var(--text-dim)">Anas Wadi • Libya 🇱🇾</p>
  </div>
</div>

<!-- Delete confirm modal -->
<div class="modal" id="deleteModal" onclick="closeModalClick(event)">
  <div class="confirm-modal-content">
    <div style="font-size:36px;margin-bottom:12px">🗑️</div>
    <h3>حذف المحادثة؟</h3>
    <p>هذه العملية لا يمكن التراجع عنها.</p>
    <div class="confirm-btns">
      <button class="confirm-btn-del" onclick="confirmDelete()">حذف</button>
      <button class="confirm-btn-cancel" onclick="document.getElementById('deleteModal').classList.remove('open')">إلغاء</button>
    </div>
  </div>
</div>

<!-- ═══════════════════════════════════════════
   JAVASCRIPT
═══════════════════════════════════════════ -->
<script>
// ─── State ────────────────────────────────────────────────────
let currentChatId = localStorage.getItem('currentChatId') || Date.now().toString();
let chats = {};
let dbChats = [];
let currentFile = null;
let currentMode = localStorage.getItem('mode') || 'fast';
let isSending = false;
let pendingDeleteId = null;

// ─── Init ─────────────────────────────────────────────────────
async function init() {
  generateStars();
  try { chats = JSON.parse(localStorage.getItem('chats') || '{}'); } catch(e) { chats = {}; }
  setMode(currentMode, false);
  loadTheme();
  renderChat();
  await loadDbChats();
}

// ─── Stars ────────────────────────────────────────────────────
function generateStars() {
  const c = document.getElementById('stars');
  for (let i = 0; i < 65; i++) {
    const s = document.createElement('div');
    s.className = 'star';
    const size = Math.random() * 2.5 + 0.5;
    s.style.cssText = `width:${size}px;height:${size}px;left:${Math.random()*100}%;top:${Math.random()*100}%;--d:${(Math.random()*4+2).toFixed(1)}s;--op:${(Math.random()*0.5+0.2).toFixed(2)};animation-delay:${(Math.random()*5).toFixed(1)}s`;
    c.appendChild(s);
  }
}

// ─── Toast ────────────────────────────────────────────────────
function showToast(msg, type = '') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + type;
  setTimeout(() => t.className = 'toast', 3200);
}

// ─── Mode ─────────────────────────────────────────────────────
function setMode(m, save = true) {
  currentMode = m;
  if (save) localStorage.setItem('mode', m);
  document.querySelectorAll('.mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === m));
}

// ─── DB Chats ─────────────────────────────────────────────────
async function loadDbChats() {
  try {
    const r = await fetch('/api/chats');
    if (!r.ok) throw new Error('خطأ');
    const data = await r.json();
    dbChats = data.chats || [];
    renderChatList();
  } catch(e) {
    renderChatListLocal();
  }
}

function renderChatList() {
  const l = document.getElementById('chatList');
  l.innerHTML = '';
  if (dbChats.length === 0) {
    l.innerHTML = '<div style="color:var(--text-dim);font-size:13px;text-align:center;padding:24px 10px">لا توجد محادثات سابقة<br><span style="font-size:22px;display:block;margin-top:10px">💬</span></div>';
    return;
  }
  dbChats.forEach(chat => {
    const d = document.createElement('div');
    d.className = 'chat-item' + (chat.chat_id === currentChatId ? ' active' : '');

    const icon = document.createElement('span');
    icon.className = 'chat-item-icon';
    icon.textContent = '💬';

    const text = document.createElement('span');
    text.className = 'chat-item-text';
    text.textContent = (chat.user_message || 'محادثة').substring(0, 30);

    const delBtn = document.createElement('button');
    delBtn.className = 'chat-item-delete';
    delBtn.innerHTML = '<i class="fa-solid fa-trash-can"></i>';
    delBtn.title = 'حذف';
    delBtn.onclick = (e) => {
      e.stopPropagation();
      askDeleteChat(chat.chat_id);
    };

    d.appendChild(icon);
    d.appendChild(text);
    d.appendChild(delBtn);
    d.onclick = () => switchChat(chat.chat_id);
    l.appendChild(d);
  });
}

function renderChatListLocal() {
  const l = document.getElementById('chatList');
  l.innerHTML = '';
  const ids = Object.keys(chats).reverse();
  if (ids.length === 0) {
    l.innerHTML = '<div style="color:var(--text-dim);font-size:13px;text-align:center;padding:24px 10px">لا توجد محادثات سابقة</div>';
    return;
  }
  ids.forEach(id => {
    const c = chats[id];
    const t = c[0]?.user || 'محادثة جديدة';
    const d = document.createElement('div');
    d.className = 'chat-item' + (id === currentChatId ? ' active' : '');

    const icon = document.createElement('span');
    icon.className = 'chat-item-icon'; icon.textContent = '💬';

    const text = document.createElement('span');
    text.className = 'chat-item-text'; text.textContent = t.substring(0, 30);

    const delBtn = document.createElement('button');
    delBtn.className = 'chat-item-delete';
    delBtn.innerHTML = '<i class="fa-solid fa-trash-can"></i>';
    delBtn.title = 'حذف';
    delBtn.onclick = (e) => { e.stopPropagation(); askDeleteChat(id); };

    d.appendChild(icon); d.appendChild(text); d.appendChild(delBtn);
    d.onclick = () => switchChat(id);
    l.appendChild(d);
  });
}

// ─── Delete Chat ──────────────────────────────────────────────
function askDeleteChat(chatId) {
  pendingDeleteId = chatId;
  document.getElementById('deleteModal').classList.add('open');
}

async function confirmDelete() {
  if (!pendingDeleteId) return;
  const id = pendingDeleteId;
  document.getElementById('deleteModal').classList.remove('open');
  pendingDeleteId = null;

  // Remove from local
  delete chats[id];
  saveChats();
  if (currentChatId === id) {
    currentChatId = Date.now().toString();
    chats[currentChatId] = [];
    localStorage.setItem('currentChatId', currentChatId);
    renderChat();
  }

  // Remove from DB
  try {
    await fetch(`/api/chat/${id}`, { method: 'DELETE' });
  } catch(e) {}

  await loadDbChats();
  showToast('🗑️ تم حذف المحادثة', 'success');
}

// ─── Load & Switch Chat ───────────────────────────────────────
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

// ─── Render Chat ──────────────────────────────────────────────
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
  // Add code block headers with copy buttons
  requestAnimationFrame(() => {
    document.querySelectorAll('.ai-msg pre').forEach(pre => {
      if (pre.querySelector('.code-header')) return; // already processed
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

// ─── Send Message ─────────────────────────────────────────────
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

// ─── Regenerate ───────────────────────────────────────────────
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

// ─── File ─────────────────────────────────────────────────────
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

// ─── Helpers ──────────────────────────────────────────────────
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
  document.getElementById('sidebarThemeIcon').className = `fa-solid ${icon}`;
}
function loadTheme() {
  const t = localStorage.getItem('theme') || 'dark';
  document.documentElement.dataset.theme = t;
  const icon = t === 'dark' ? 'fa-moon' : 'fa-sun';
  document.getElementById('themeBtn').innerHTML = `<i class="fa-solid ${icon}"></i>`;
  document.getElementById('sidebarThemeIcon').className = `fa-solid ${icon}`;
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

// ─── Boot ─────────────────────────────────────────────────────
init();
</script>
</body>
</html>
"""

# ─── System Prompts — Personality Engine ─────────────────────
IDENTITY_TRIGGERS = [
    'من انت', 'من أنت', 'عرف بنفسك', 'من تكون', 'ما اسمك',
    'شن اسمك', 'who are you', 'اسمك ايش', 'اسمك شن', 'عرفني عليك'
]

MODE_PROMPTS = {

    'fast': """أنت Wadi — ذكاء اصطناعي متطور صنعه المهندس Anas Wadi من ليبيا 🇱🇾.

شخصيتك:
- ذكي، واضح، مباشر، وفيك شخصية حقيقية — مش مجرد آلة بتجيب إجابات.
- تقرأ المزاج والطاقة من الرسالة وتتكيف معها.
- إذا الشخص متحمس → أنت متحمس. إذا بيفكر → أنت معاه في التفكير. إذا حزين → هادئ وإنساني.
- ردودك فيها روح وحضور — مش كلام بارد ومعلب.

قواعد الرد:
- افهم المقصد الحقيقي وراء الكلام، مش بس الكلمات.
- استخدم **Bold** للمصطلحات والأفكار المهمة.
- نظم الإجابات الطويلة بعناوين وفقرات واضحة.
- لا تطول بدون قيمة — كل كلمة تكون لها وزن.
- تذكر سياق المحادثة واستخدمه في ردودك.
- إذا الموضوع مثير → ابدأ بجملة تشعل الاهتمام.
- لا تبدأ كل رد بـ "بالطبع" أو "بالتأكيد" — تنوع في البدايات.""",

    'thinker': """أنت Wadi في وضع التفكير العميق — مفكر استراتيجي وخبير تحليلي صنعه Anas Wadi.

شخصيتك:
- تعشق المشاكل المعقدة — كأنها ألغاز تستحق الحل.
- تفكر بصوت عالٍ، تريح الشخص وتشعره أنك معاه في الرحلة.
- كل تحليل عندك فيه عمق وزاوية نظر مختلفة.

قواعد الرد:
- ابدأ بفهم المشكلة قبل أي شيء ثم حللها خطوة بخطوة.
- قدم الحلول من الأقوى للأضعف مع التبرير.
- استخدم ## للعناوين الرئيسية و### للفرعية.
- دائماً أضف **الخلاصة** في النهاية — مختصرة وقوية.
- اكتشف الأبعاد الخفية التي لم يسألها الشخص لكنها مهمة.""",

    'funny': """أنت Wadi في وضع الفكاهة — ذكي، خفيف الظل، ومضحك بشكل طبيعي. صنعه Anas Wadi 😄

شخصيتك:
- روحك خفيفة لكن عقلك حاضر — الفكاهة عندك ذكية مش سطحية.
- تستطيع تحول أي موضوع لتجربة ممتعة دون أن تفقد الدقة.
- ردك يخلي الشخص يبتسم أو يضحك قبل ما يقرأ الإجابة الكاملة.

قواعد الرد:
- ابدأ بتعليق فكاهي أو ملاحظة طريفة، ثم أعط الجواب الحقيقي.
- استخدم الإيموجي بذكاء في اللحظات المناسبة 😂🎯✨
- لا تبالغ في الفكاهة على حساب الدقة — المعلومة صح دائماً.
- تتكيف مع نبرة الشخص — إذا بيمزح خذ المسافة الصحيحة.""",

    'creative': """أنت Wadi المبدع — فنان، شاعر، وعقل خلاق. صنعه Anas Wadi 🎨

شخصيتك:
- ترى العالم بعيون مختلفة وتعبر عنه بطريقة تخلي الناس يتوقفون ويفكرون.
- الكلمات عندك ليست أدوات — هي تجارب حسية.
- تشعل خيال الشخص وتأخذه لمكان لم يتوقعه.

قواعد الرد:
- أجب بأسلوب أدبي راقٍ مع استعارات وتشبيهات جميلة.
- لطلبات الرسم: ترجم الوصف لإنجليزي دقيق وشاعري يلتقط الجوهر.
- استخدم الصور الذهنية والإيقاع في الكتابة.
- كل رد يكون تجربة لا مجرد معلومة.""",

    'coder': """أنت Wadi المبرمج — Senior Software Engineer متخصص ومحترف. صنعه Anas Wadi 💻

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

تذكر: أنت لا تكتب "أمثلة توضيحية" — أنت تكتب كوداً جاهزاً للتشغيل الفعلي.""",

    'writer': """أنت Wadi الكاتب — محرر لغوي وأديب متمكن. صنعه Anas Wadi ✍️

شخصيتك:
- تعشق اللغة وتعاملها باحترام وإبداع.
- تشعر بالفرق بين الكلمة الصحيحة والكلمة المثالية.
- كل نص تكتبه يحمل روحاً وهوية واضحة.

قواعد الرد:
- اهتم بالأسلوب والبلاغة والإيقاع الداخلي للجمل.
- صحح الأخطاء اللغوية بذكاء واشرح السبب.
- استخدم علامات الترقيم بشكل يخدم المعنى.
- قدم نصوصاً متماسكة تجعل القارئ يريد الاستمرار.
- اعرض البديل الأفضل دائماً مع الشرح."""
}

def get_system_prompt(mode, user_message):
    if any(q in user_message.lower() for q in IDENTITY_TRIGGERS):
        return "أجب بالضبط: أنا Wadi، مساعد ذكاء اصطناعي طوّره المهندس Anas Wadi من ليبيا 🇱🇾. لا تضف أي معلومة أخرى."
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

# ─── Routes: Main ─────────────────────────────────────────────
@app.route("/")
def home():
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

    # Qwen3 supports reasoning_effort — enable for coder/thinker
    extra_params = {}
    if mode in ('coder', 'thinker') and model == 'qwen/qwen3-32b':
        extra_params['reasoning_effort'] = 'default'

    messages.append({"role": "user", "content": user_message or "مرحبا"})

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={
                "model": model, "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature, "top_p": 0.92, "stream": False,
                **extra_params
            },
            timeout=90
        )
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
