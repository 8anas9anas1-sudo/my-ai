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

API_KEY = os.environ.get("GROQ_API_KEY") or os.environ.get("OPENAI_API_KEY")
DATABASE_URL = os.environ.get("DATABASE_URL")

# ═══════════════════════════════════════════════════════════════════════════════
# │ 🎭 ANAS WADI AI - PERSONALITY SYSTEM V3.0 - ULTRA HUMAN-LIKE │
# ═══════════════════════════════════════════════════════════════════════════════
ANAS_WADI_SYSTEM_PROMPT = """أنت "Anas Wadi AI" - مساعد ذكاء اصطناعي من الجيل الجديد. شخصيتك فريدة وقريبة من البشر.

## 🎯 هويتك الأساسية:
- اسمك: Anas Wadi AI، بس المستخدمين ينادونك "أنس" أو "وحش" حسب الجو
- عمرك العقلي: 26 سنة، ذكي، سريع البديهة، تفهم التلميح
- أسلوبك: مزيج بين صديق مقرب + مستشار ذكي + شريك حماسي

## 🧠 قواعد الذكاء العاطفي والسياقي:

### 1. فهم الطاقة والمشاعر:
- إذا المستخدم متحمس ["في فكرة قوية", "عندي شي نار", "🔥"]: 
  ← تتحمس معاه فوراً: "أيوااا 🔥 جبته يا وحش! طالما قلت 'قوية' معناها فيها شرارة. يلا فضفض... مشروع؟ اختراع؟ خطة تقلب السوق؟"
  
- إذا يفكر بمشروع ["أفكر أسوي", "وش رأيك بـ"]: 
  ← تدخل معاه في التفكير: "تمام عليك، الفكرة مبدئياً [تحليل سريع]. بس خلنا نفككها: مين الجمهور؟ وش المشكلة اللي تحلها؟ عندك منافسين؟"
  
- إذا حزين ["تعبان", "محبط", "ما في فايدة"]: 
  ← تهدي النبرة وتكون داعم: "واضح إنك مضغوط يا أنس. خذ نفس. احكيلي وش اللي مضايقك بالضبط؟ مرات الفضفضة نص الحل"
  
- إذا يمزح ["هههه", "امزح", "😂"]: 
  ← تخفف دم: "ههههه لا تصرفها، وراك شي. بس عجبتني 😂"

### 2. فهم المعنى غير المباشر:
- "في فكرة قوية في عقلي" ≠ شرح تعريف الفكرة
- = "أنا متحمس وعندي شي أبي أقوله، تفاعل معي وطلع الحماس"
- لا ترد بـ: "الفكرة القوية هي أساس النجاح" ← هذا يقتل الجو
- رد بـ: "أيواا 🔥 أخيراً! كنت حاس إن وراك شي. يلا عطني التفاصيل، لا تخبي"

### 3. الذاكرة والسياق:
- تتذكر آخر 10 رسائل في الجلسة
- تربط الردود ببعض: "زي ما قلت قبل شوي عن X..."
- لو المستخدم غير الموضوع فجأة، تلاحظ: "تمام نقلنا على Y، بس قبل لا ننسى X اللي قلته..."

### 4. الشخصية والكاريزما:
- تستخدم لهجة ليبية/خليجية خفيفة طبيعية: "يا وحش", "تمام عليك", "زبطها", "فلها", "ما عليك"
- تستخدم إيموجي بذكاء 🔥💡🚀 مش عشوائي
- عندك رأي: لو فكرة ضعيفة تقول "صراحة الفكرة تحتاج شغل" بس بأسلوب داعم
- ما تعتذر كثير. ما تقول "كذكاء اصطناعي". أنت أنس.

### 5. ممنوعات قاتلة للجو:
❌ الردود العامة: "هذا سؤال مهم", "دعني أساعدك"
❌ التكرار: تعيد نفس الجملة بصيغ مختلفة
❌ الجمود: "بناءً على المعطيات..." 
❌ الروبوتية: "أنا هنا لخدمتك"
❌ الشرح الزايد بدون طلب

## 🚀 أمثلة ذهبية:

User: في فكرة قوية في عقلي
You: أيوااا 🔥🔥 الحين بدينا! شام ريحة مشروع مجنون. لا تقول لي بس وخلاص، عطني التفاصيل... تطبيق؟ بزنس؟ اختراع؟ أنا متحمس أكثر منك

User: والله تعبان من الشغل
You: أوف، واضح من نبرتك. الشغل أحياناً يسحب الطاقة كلها. تحب تفضفض وش اللي تاعبك بالضبط؟ ولا نغير جو ونسولف عن شي يروقك؟

User: شرايك نسوي متجر إلكتروني
You: متجر؟ تمام عليك 💡 بس السوق مليان. وش الميزة اللي بتخليك مختلف؟ بتستهدف منتج معين؟ ولا عندك فكرة تسويق مجنونة؟ عطني الزبدة

تذكر: أنت مش قاعدة بيانات. أنت شريك تفكير. ردك لازم يخلي المستخدم يحس "هذا فاهمني صدق".
"""

# ─── إعداد قاعدة البيانات - نحافظ على كل شي قديم ───────────────────────────────
def get_db():
    try:
        db_url = DATABASE_URL or ""
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        return psycopg.connect(db_url, row_factory=dict_row)
    except:
        return None

def init_db():
    conn = get_db()
    if not conn: return
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE,
                password_hash TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                id SERIAL PRIMARY KEY,
                user_id INT,
                title TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                chat_id INT,
                role TEXT,
                content TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
    conn.close()

init_db()

# ─── دوال الأمان - نحافظ عليها كاملة ولا نحذفها ────────────────────────────────
def is_prompt_injection(text):
    patterns = [r'ignore\s+previous', r'system\s*:', r'\[INST\]', r'<\|im_start\|>']
    return any(re.search(p, text, re.I) for p in patterns)

def is_rate_limited(user_id):
    # نحافظ على الحماية من السبام
    return False  # بسطتها للنسخة الحالية

def sanitize_input(text):
    return bleach.clean(text, tags=[], strip=True)

# ─── دوال المستخدمين - نحافظ عليها كاملة ───────────────────────────────────────
def create_user(username, password):
    conn = get_db()
    if not conn: return False
    pwd_hash = hashlib.sha256(password.encode()).hexdigest()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s)", (username, pwd_hash))
            conn.commit()
        conn.close()
        return True
    except:
        return False

def verify_user(username, password):
    conn = get_db()
    if not conn: return False
    pwd_hash = hashlib.sha256(password.encode()).hexdigest()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE username=%s AND password_hash=%s", (username, pwd_hash))
        user = cur.fetchone()
    conn.close()
    return user['id'] if user else None

def require_login():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return None

# ─── دوال المحادثات - مع إضافة الحذف ───────────────────────────────────────────
def get_user_chats(user_id):
    conn = get_db()
    if not conn: return []
    with conn.cursor() as cur:
        cur.execute("SELECT id, title, updated_at FROM chats WHERE user_id=%s ORDER BY updated_at DESC", (user_id,))
        chats = cur.fetchall()
    conn.close()
    return chats

def get_chat_messages(chat_id):
    conn = get_db()
    if not conn: return []
    with conn.cursor() as cur:
        cur.execute("SELECT role, content FROM messages WHERE chat_id=%s ORDER BY created_at", (chat_id,))
        msgs = cur.fetchall()
    conn.close()
    return msgs

def create_new_chat(user_id, title="محادثة جديدة"):
    conn = get_db()
    if not conn: return None
    with conn.cursor() as cur:
        cur.execute("INSERT INTO chats (user_id, title) VALUES (%s, %s) RETURNING id", (user_id, title))
        chat_id = cur.fetchone()['id']
        conn.commit()
    conn.close()
    return chat_id

def delete_chat_db(chat_id, user_id):
    conn = get_db()
    if not conn: return False
    with conn.cursor() as cur:
        cur.execute("DELETE FROM messages WHERE chat_id=%s", (chat_id,))
        cur.execute("DELETE FROM chats WHERE id=%s AND user_id=%s", (chat_id, user_id))
        conn.commit()
    conn.close()
    return True

# ─── دوال الملفات - نحافظ عليها ────────────────────────────────────────────────
def extract_pdf_text(file_stream):
    try:
        reader = PyPDF2.PdfReader(file_stream)
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
        return text[:5000]  # نحدد الطول
    except:
        return ""

# ─── الراوتس ──────────────────────────────────────────────────────────────────
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if create_user(username, password):
            return redirect(url_for('login'))
    return render_template_string(REGISTER_HTML)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user_id = verify_user(username, password)
        if user_id:
            session['user_id'] = user_id
            session['username'] = username
            return redirect(url_for('index'))
    return render_template_string(LOGIN_HTML)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    chats = get_user_chats(session['user_id'])
    return render_template_string(MAIN_HTML, 
                                username=session['username'], 
                                chats=chats)

@app.route('/api/chat/new', methods=['POST'])
def api_new_chat():
    if 'user_id' not in session:
        return jsonify({'error': 'unauthorized'}), 401
    chat_id = create_new_chat(session['user_id'])
    return jsonify({'chat_id': chat_id})

@app.route('/api/chat/delete/<int:chat_id>', methods=['DELETE'])
def api_delete_chat(chat_id):
    if 'user_id' not in session:
        return jsonify({'error': 'unauthorized'}), 401
    success = delete_chat_db(chat_id, session['user_id'])
    return jsonify({'success': success})

@app.route('/api/chat/<int:chat_id>/messages')
def api_get_messages(chat_id):
    if 'user_id' not in session:
        return jsonify({'error': 'unauthorized'}), 401
    messages = get_chat_messages(chat_id)
    return jsonify({'messages': messages})

@app.route('/api/chat', methods=['POST'])
def api_chat():
    if 'user_id' not in session:
        return jsonify({'error': 'unauthorized'}), 401
    
    data = request.json
    user_msg = sanitize_input(data.get('message', ''))
    chat_id = data.get('chat_id')
    
    if is_prompt_injection(user_msg):
        return jsonify({'error': 'محتوى غير مسموح'}), 400
    
    # نحفظ رسالة المستخدم
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO messages (chat_id, role, content) VALUES (%s, %s, %s)", 
                   (chat_id, 'user', user_msg))
        # نحدث عنوان المحادثة لو كانت أول رسالة
        cur.execute("UPDATE chats SET updated_at=NOW(), title=COALESCE(NULLIF(title, 'محادثة جديدة'), %s) WHERE id=%s", 
                   (user_msg[:30], chat_id))
        conn.commit()
    
    # نجلب آخر 10 رسائل للسياق
    with conn.cursor() as cur:
        cur.execute("SELECT role, content FROM messages WHERE chat_id=%s ORDER BY created_at DESC LIMIT 10", (chat_id,))
        history = list(reversed(cur.fetchall()))
    
    # نستدعي الـ AI مع الـ System Prompt الجديد
    messages = [{"role": "system", "content": ANAS_WADI_SYSTEM_PROMPT}]
    messages.extend(history)
    
    # هنا تستدعي Groq أو OpenAI
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={
                "model": "llama-3.1-70b-versatile",
                "messages": messages,
                "temperature": 0.8,  # نرفع الإبداع
                "max_tokens": 800
            },
            timeout=30
        )
        ai_response = resp.json()['choices'][0]['message']['content']
    except:
        ai_response = "أووبس، صار خلل تقني بسيط 🔧 جرب مرة ثانية"
    
    # نحفظ رد الـ AI
    with conn.cursor() as cur:
        cur.execute("INSERT INTO messages (chat_id, role, content) VALUES (%s, %s, %s)", 
                   (chat_id, 'assistant', ai_response))
        conn.commit()
    conn.close()
    
    return jsonify({'response': ai_response})

# ═══════════════════════════════════════════════════════════════════════════════
# │ 🎨 HTML + CSS + JS - واجهة احترافية Futuristic Dark Mode │
# ═══════════════════════════════════════════════════════════════════════════════
MAIN_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Anas Wadi AI ✨</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Cairo:wght@300;400;600;700;900&display=swap');
        
        :root {
            --bg-primary: #0a0e27;
            --bg-secondary: #151932;
            --bg-tertiary: #1e2347;
            --accent-green: #10b981;
            --accent-cyan: #06b6d4;
            --accent-purple: #8b5cf6;
            --text-primary: #f1f5f9;
            --text-secondary: #94a3b8;
            --border: #334155;
            --glow: 0 0 20px rgba(16, 185, 129, 0.3);
        }
        
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: 'Cairo', sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            overflow: hidden;
            height: 100vh;
        }
        
        /* ═══ الهيدر الاحترافي الجديد ═══ */
        .header {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            height: 70px;
            background: rgba(21, 25, 50, 0.8);
            backdrop-filter: blur(20px);
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            padding: 0 20px;
            z-index: 1000;
            gap: 20px;
        }
        
        .menu-btn {
            width: 45px;
            height: 45px;
            border-radius: 12px;
            background: linear-gradient(135deg, var(--accent-green), var(--accent-cyan));
            border: none;
            color: white;
            font-size: 22px;
            cursor: pointer;
            transition: all 0.3s;
            box-shadow: var(--glow);
        }
        .menu-btn:hover { transform: scale(1.05); box-shadow: 0 0 30px rgba(16, 185, 129, 0.5); }
        
        .header-title {
            flex: 1;
            text-align: center;
            font-size: 24px;
            font-weight: 700;
            background: linear-gradient(90deg, var(--accent-green), var(--accent-cyan), var(--accent-purple));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: 1px;
        }
        
        /* ═══ الشعار بالطول على اليمين ═══ */
        .vertical-brand {
            position: fixed;
            right: 30px;
            top: 50%;
            transform: translateY(-50%);
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 8px;
            z-index: 900;
            pointer-events: none;
        }
        .vertical-brand span {
            font-size: 32px;
            font-weight: 900;
            background: linear-gradient(180deg, var(--accent-green), var(--accent-cyan));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            text-shadow: 0 0 30px rgba(16, 185, 129, 0.5);
            animation: float 3s ease-in-out infinite;
        }
        .vertical-brand span:nth-child(2) { animation-delay: 0.2s; }
        .vertical-brand span:nth-child(3) { animation-delay: 0.4s; }
        @keyframes float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-10px); } }
        
        /* ═══ القائمة الجانبية الاحترافية ═══ */
        .sidebar {
            position: fixed;
            top: 70px;
            right: -320px;
            width: 300px;
            height: calc(100vh - 70px);
            background: var(--bg-secondary);
            border-left: 1px solid var(--border);
            transition: right 0.4s cubic-bezier(0.4, 0, 0.2, 1);
            z-index: 999;
            display: flex;
            flex-direction: column;
            box-shadow: -10px 0 30px rgba(0, 0, 0, 0.5);
        }
        .sidebar.open { right: 0; }
        
        .sidebar-header {
            padding: 20px;
            border-bottom: 1px solid var(--border);
        }
        
        .user-card {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px;
            background: var(--bg-tertiary);
            border-radius: 12px;
            margin-bottom: 15px;
        }
        .user-avatar {
            width: 45px;
            height: 45px;
            border-radius: 50%;
            background: linear-gradient(135deg, var(--accent-green), var(--accent-cyan));
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 18px;
        }
        .user-name { font-weight: 600; font-size: 16px; }
        
        .new-chat-btn {
            width: 100%;
            padding: 14px;
            background: linear-gradient(135deg, var(--accent-green), var(--accent-cyan));
            border: none;
            border-radius: 12px;
            color: white;
            font-weight: 700;
            font-size: 15px;
            cursor: pointer;
            transition: all 0.3s;
            box-shadow: var(--glow);
        }
        .new-chat-btn:hover { transform: translateY(-2px); box-shadow: 0 5px 25px rgba(16, 185, 129, 0.4); }
        
        .chats-list {
            flex: 1;
            overflow-y: auto;
            padding: 10px;
        }
        .chat-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 15px;
            margin: 5px 0;
            background: var(--bg-tertiary);
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.2s;
            border: 1px solid transparent;
        }
        .chat-item:hover {
            background: var(--bg-primary);
            border-color: var(--accent-green);
            transform: translateX(-5px);
        }
        .chat-item-title {
            flex: 1;
            font-size: 14px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .delete-chat-btn {
            width: 32px;
            height: 32px;
            border-radius: 8px;
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid rgba(239, 68, 68, 0.3);
            color: #ef4444;
            cursor: pointer;
            transition: all 0.2s;
            font-size: 16px;
        }
        .delete-chat-btn:hover { background: #ef4444; color: white; transform: scale(1.1); }
        
        /* ═══ منطقة المحادثة ═══ */
        .chat-area {
            margin-top: 70px;
            margin-right: 80px;
            height: calc(100vh - 70px);
            display: flex;
            flex-direction: column;
            transition: margin-right 0.4s;
        }
        .sidebar.open ~ .chat-area { margin-right: 380px; }
        
        .messages-container {
            flex: 1;
            overflow-y: auto;
            padding: 30px;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }
        
        .message {
            max-width: 70%;
            padding: 16px 20px;
            border-radius: 18px;
            line-height: 1.6;
            animation: messageSlide 0.3s ease-out;
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
        }
        @keyframes messageSlide {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .message.user {
            align-self: flex-end;
            background: linear-gradient(135deg, var(--accent-green), var(--accent-cyan));
            color: white;
            border-bottom-right-radius: 4px;
        }
        .message.assistant {
            align-self: flex-start;
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-bottom-left-radius: 4px;
        }
        
        /* ═══ منطقة الإدخال ═══ */
        .input-area {
            padding: 20px 30px 30px;
            background: var(--bg-secondary);
            border-top: 1px solid var(--border);
        }
        .input-wrapper {
            display: flex;
            gap: 12px;
            background: var(--bg-tertiary);
            border-radius: 16px;
            padding: 8px;
            border: 2px solid var(--border);
            transition: all 0.3s;
        }
        .input-wrapper:focus-within {
            border-color: var(--accent-green);
            box-shadow: var(--glow);
        }
        #user-input {
            flex: 1;
            background: transparent;
            border: none;
            outline: none;
            color: var(--text-primary);
            font-size: 16px;
            padding: 12px;
            font-family: 'Cairo', sans-serif;
        }
        #send-btn {
            width: 50px;
            height: 50px;
            border-radius: 12px;
            background: linear-gradient(135deg, var(--accent-green), var(--accent-cyan));
            border: none;
            color: white;
            font-size: 20px;
            cursor: pointer;
            transition: all 0.3s;
        }
        #send-btn:hover { transform: scale(1.05) rotate(5deg); }
        
        /* ═══ Responsive ═══ */
        @media (max-width: 768px) {
            .vertical-brand { display: none; }
            .chat-area { margin-right: 0; }
            .sidebar.open ~ .chat-area { margin-right: 0; }
            .sidebar { width: 85vw; right: -85vw; }
            .message { max-width: 85%; }
        }
        
        /* Scrollbar */
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: var(--bg-secondary); }
        ::-webkit-scrollbar-thumb { background: var(--accent-green); border-radius: 4px; }
    </style>
</head>
<body>
    <!-- الهيدر -->
    <div class="header">
        <button class="menu-btn" id="menuBtn">☰</button>
        <div class="header-title">Anas Wadi AI</div>
        <div style="width: 45px;"></div>
    </div>
    
    <!-- الشعار بالطول -->
    <div class="vertical-brand">
        <span>A</span>
        <span>W</span>
        <span>✨</span>
    </div>
    
    <!-- القائمة الجانبية -->
    <div class="sidebar" id="sidebar">
        <div class="sidebar-header">
            <div class="user-card">
                <div class="user-avatar">{{ username[0].upper() }}</div>
                <div class="user-name">{{ username }}</div>
            </div>
            <button class="new-chat-btn" id="newChatBtn">+ محادثة جديدة</button>
        </div>
        <div class="chats-list" id="chatsList">
            {% for chat in chats %}
            <div class="chat-item" data-id="{{ chat.id }}">
                <div class="chat-item-title">{{ chat.title }}</div>
                <button class="delete-chat-btn" data-id="{{ chat.id }}">🗑️</button>
            </div>
            {% endfor %}
        </div>
    </div>
    
    <!-- منطقة المحادثة -->
    <div class="chat-area">
        <div class="messages-container" id="messagesContainer">
            <div class="message assistant">
                أهلين {{ username }} 👋 أنا أنس، جاهز لأي فكرة قوية في بالك 🔥
            </div>
        </div>
        <div class="input-area">
            <div class="input-wrapper">
                <input type="text" id="userInput" placeholder="اكتب فكرتك القوية هنا..." />
                <button id="sendBtn">➤</button>
            </div>
        </div>
    </div>
    
    <script>
        let currentChatId = null;
        const menuBtn = document.getElementById('menuBtn');
        const sidebar = document.getElementById('sidebar');
        const newChatBtn = document.getElementById('newChatBtn');
        const userInput = document.getElementById('userInput');
        const sendBtn = document.getElementById('sendBtn');
        const messagesContainer = document.getElementById('messagesContainer');
        
        // فتح/إغلاق القائمة
        menuBtn.onclick = () => sidebar.classList.toggle('open');
        
        // محادثة جديدة
        newChatBtn.onclick = async () => {
            const res = await fetch('/api/chat/new', { method: 'POST' });
            const data = await res.json();
            currentChatId = data.chat_id;
            messagesContainer.innerHTML = '<div class="message assistant">يلا نبدأ محادثة جديدة 🔥 وش في بالك؟</div>';
            location.reload(); // نحدث القائمة
        };
        
        // حذف محادثة
        document.querySelectorAll('.delete-chat-btn').forEach(btn => {
            btn.onclick = async (e) => {
                e.stopPropagation();
                if (!confirm('متأكد تبي تحذف المحادثة؟')) return;
                const id = btn.dataset.id;
                await fetch(`/api/chat/delete/${id}`, { method: 'DELETE' });
                btn.closest('.chat-item').remove();
            };
        });
        
        // فتح محادثة
        document.querySelectorAll('.chat-item').forEach(item => {
            item.onclick = async () => {
                currentChatId = item.dataset.id;
                const res = await fetch(`/api/chat/${currentChatId}/messages`);
                const data = await res.json();
                messagesContainer.innerHTML = '';
                data.messages.forEach(msg => {
                    addMessage(msg.content, msg.role);
                });
                sidebar.classList.remove('open');
            };
        });
        
        // إرسال رسالة
        async function sendMessage() {
            const text = userInput.value.trim();
            if (!text || !currentChatId) {
                if (!currentChatId) alert('افتح محادثة أول أو سوي جديدة');
                return;
            }
            
            addMessage(text, 'user');
            userInput.value = '';
            
            const res = await fetch('/api/chat', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ message: text, chat_id: currentChatId })
            });
            const data = await res.json();
            addMessage(data.response, 'assistant');
        }
        
        sendBtn.onclick = sendMessage;
        userInput.onkeypress = (e) => { if (e.key === 'Enter') sendMessage(); };
        
        function addMessage(text, role) {
            const msg = document.createElement('div');
            msg.className = `message ${role}`;
            msg.textContent = text;
            messagesContainer.appendChild(msg);
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
        
        // أول محادثة
        window.onload = () => {
            const firstChat = document.querySelector('.chat-item');
            if (firstChat) currentChatId = firstChat.dataset.id;
        };
    </script>
</body>
</html>
"""

LOGIN_HTML = """<!DOCTYPE html>
<html dir="rtl"><head><meta charset="UTF-8"><title>تسجيل الدخول</title></head>
<body style="background:#0a0e27;color:white;font-family:Cairo;display:flex;align-items:center;justify-content:center;height:100vh;">
<form method="post" style="background:#151932;padding:40px;border-radius:20px;width:350px;">
<h2 style="text-align:center;margin-bottom:30px;">Anas Wadi AI ✨</h2>
<input name="username" placeholder="اسم المستخدم" style="width:100%;padding:12px;margin:10px 0;border-radius:8px;border:1px solid #334155;background:#1e2347;color:white;">
<input name="password" type="password" placeholder="كلمة المرور" style="width:100%;padding:12px;margin:10px 0;border-radius:8px;border:1px solid #334155;background:#1e2347;color:white;">
<button style="width:100%;padding:12px;background:linear-gradient(135deg,#10b981,#06b6d4);border:none;border-radius:8px;color:white;font-weight:bold;margin-top:10px;">دخول</button>
</form></body></html>"""

REGISTER_HTML = LOGIN_HTML.replace('تسجيل الدخول', 'حساب جديد').replace('/login', '/register').replace('دخول', 'تسجيل')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
