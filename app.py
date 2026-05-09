import os
import base64
import json
import re
import time
import hashlib
from flask import Flask, request, render_template_string, jsonify, session, Response, stream_with_context, render_template
import requests
from datetime import datetime
import PyPDF2
import io

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "anas-wadi-secret-2026-ultra")

API_KEY = os.environ.get("GROQ_API_KEY")
FIREBASE_API_KEY = os.environ.get("FIREBASE_API_KEY")
FIREBASE_APP_ID = os.environ.get("FIREBASE_APP_ID")
OWNER_EMAIL = os.environ.get("OWNER_EMAIL")

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

# ─── ملف الاقتراحات ────────────────────────────────
SUGGESTIONS_FILE = "suggestions.json"

def load_suggestions():
    if os.path.exists(SUGGESTIONS_FILE):
        with open(SUGGESTIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_suggestion(text, user_email=None):
    suggestions = load_suggestions()
    suggestions.append({
        "text": text,
        "email": user_email,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M")
    })
    with open(SUGGESTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(suggestions, f, ensure_ascii=False, indent=2)

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

# ─── تنسيق الإجابة (Markdown → HTML) ────────────────────────
def format_response(text):
    text = re.sub(r'^### (.+)$', r'<h4>\1</h4>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    text = re.sub(r'```(\w+)?\n(.*?)```', lambda m: f'<pre><code class="lang-{m.group(1) or ""}">{m.group(2).strip()}</code></pre>', text, flags=re.DOTALL)
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
    def convert_list(m):
        items = re.findall(r'^[-*•] (.+)$', m.group(0), re.MULTILINE)
        return '<ul>' + ''.join(f'<li>{i}</li>' for i in items) + '</ul>'
    text = re.sub(r'(^[-*•].+$\n?)+', convert_list, text, flags=re.MULTILINE)
    def convert_ol(m):
        items = re.findall(r'^\d+\. (.+)$', m.group(0), re.MULTILINE)
        return '<ol>' + ''.join(f'<li>{i}</li>' for i in items) + '</ol>'
    text = re.sub(r'(^\d+\..+$\n?)+', convert_ol, text, flags=re.MULTILINE)
    text = re.sub(r'\n{2,}', '</p><p>', text)
    text = f'<p>{text}</p>'
    text = text.replace('<p></p>', '').replace('<p><h', '<h').replace('</h2></p>', '</h2>')
    return text

# ─── واجهة HTML ─────────────────────────────────────
HTML = r"""
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
:root {
  --bg: #050510;
  --surface: rgba(255,255,255,0.04);
  --surface2: rgba(255,255,255,0.08);
  --border: rgba(255,255,255,0.08);
  --border-glow: rgba(0,210,255,0.3);
  --text: #e8eaf6;
  --text-dim: rgba(232,234,246,0.6);
  --accent1: #00d2ff;
  --accent2: #00ff94;
  --accent3: #7c4dff;
  --user-grad: linear-gradient(135deg, #00ff94, #00d2ff);
  --glow: 0 0 30px rgba(0,210,255,0.15);
}
[data-theme="light"] {
  --bg: #f0f4ff;
  --surface: rgba(0,0,0,0.04);
  --surface2: rgba(0,0,0,0.08);
  --border: rgba(0,0,0,0.1);
  --border-glow: rgba(0,100,200,0.3);
  --text: #0d1117;
  --text-dim: rgba(13,17,23,0.6);
  --accent1: #0077cc;
  --accent2: #00aa66;
  --glow: 0 0 30px rgba(0,100,200,0.1);
}
* { margin: 0; padding: 0; box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  background: var(--bg); color: var(--text);
  font-family: 'Tajawal', 'Cairo', sans-serif;
  min-height: 100vh; display: flex; flex-direction: column;
  transition: background 0.4s, color 0.4s; overflow-x: hidden;
}
body::before {
  content: ''; position: fixed; inset: 0;
  background:
    radial-gradient(ellipse 80% 60% at 20% 10%, rgba(0,210,255,0.06) 0%, transparent 60%),
    radial-gradient(ellipse 60% 50% at 80% 80%, rgba(124,77,255,0.06) 0%, transparent 60%),
    radial-gradient(ellipse 50% 40% at 50% 50%, rgba(0,255,148,0.03) 0%, transparent 70%);
  pointer-events: none; z-index: 0;
  animation: bgPulse 8s ease-in-out infinite alternate;
}
@keyframes bgPulse { from{opacity:0.7} to{opacity:1} }
.stars { position:fixed; inset:0; pointer-events:none; z-index:0; overflow:hidden; }
.star {
  position:absolute; border-radius:50%; background:white;
  animation: twinkle var(--d,3s) ease-in-out infinite;
}
@keyframes twinkle {
  0%,100%{opacity:0;transform:scale(0.5)}
  50%{opacity:var(--op,0.6);transform:scale(1)}
}
.header {
  position:sticky; top:0; z-index:100;
  background:rgba(5,5,16,0.85);
  backdrop-filter:blur(20px) saturate(1.5);
  border-bottom:1px solid var(--border);
  padding:12px 20px;
  display:flex; justify-content:space-between; align-items:center;
  transition:0.3s;
}
.logo {
  font-size:22px; font-weight:900; letter-spacing:-0.5px;
  background:linear-gradient(90deg,var(--accent2),var(--accent1),var(--accent3));
  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  background-clip:text; animation:logoShimmer 4s linear infinite; background-size:200%;
}
@keyframes logoShimmer { 0%{background-position:0% 50%} 100%{background-position:200% 50%} }
.header-actions { display:flex; gap:8px; align-items:center; }
.icon-btn {
  background:var(--surface); border:1px solid var(--border); color:var(--text);
  width:38px; height:38px; border-radius:10px; cursor:pointer;
  font-size:15px; transition:all 0.25s;
  display:flex; align-items:center; justify-content:center;
  position:relative; overflow:hidden;
}
.icon-btn::before {
  content:''; position:absolute; inset:0;
  background:linear-gradient(135deg,var(--accent1),var(--accent2));
  opacity:0; transition:opacity 0.25s;
}
.icon-btn:hover { border-color:var(--border-glow); transform:translateY(-1px); box-shadow:var(--glow); }
.icon-btn:hover::before { opacity:0.1; }
.icon-btn:active { transform:translateY(0) scale(0.96); }
.sidebar {
  position:fixed; right:-290px; top:0; width:270px; height:100vh;
  background:rgba(5,5,16,0.97); backdrop-filter:blur(30px);
  border-left:1px solid var(--border);
  transition:right 0.35s cubic-bezier(0.4,0,0.2,1);
  z-index:200; padding:20px; overflow-y:auto;
  display:flex; flex-direction:column; gap:12px;
}
.sidebar.open { right:0; }
.sidebar-overlay {
  display:none; position:fixed; inset:0; z-index:199;
  background:rgba(0,0,0,0.5); backdrop-filter:blur(4px);
}
.sidebar-overlay.open { display:block; }
.sidebar h3 { font-size:16px; font-weight:700; color:var(--text-dim); }
.new-chat-btn {
  background:linear-gradient(135deg,var(--accent2),var(--accent1));
  border:none; border-radius:12px; padding:10px 15px;
  font-family:'Tajawal',sans-serif; font-size:14px; font-weight:700;
  color:#000; cursor:pointer; transition:all 0.25s;
  display:flex; align-items:center; gap:8px;
}
.new-chat-btn:hover { transform:scale(1.02); box-shadow:0 4px 20px rgba(0,210,255,0.4); }
.chat-item {
  background:var(--surface); border:1px solid var(--border);
  padding:10px 14px; border-radius:10px; cursor:pointer;
  font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  transition:all 0.2s; color:var(--text-dim);
}
.chat-item:hover { border-color:var(--accent1); color:var(--text); transform:translateX(-3px); }
.chat-item.active { border-color:var(--accent1); color:var(--text); background:rgba(0,210,255,0.08); }
.modes {
  display:flex; gap:8px; padding:10px 16px;
  overflow-x:auto; position:relative; z-index:1; scrollbar-width:none;
}
.modes::-webkit-scrollbar { display:none; }
.mode-btn {
  background:var(--surface); border:1px solid var(--border);
  color:var(--text-dim); padding:8px 16px; border-radius:20px;
  font-size:13px; font-weight:600; white-space:nowrap; cursor:pointer;
  transition:all 0.25s; font-family:'Tajawal',sans-serif;
}
.mode-btn:hover { border-color:var(--border-glow); color:var(--text); }
.mode-btn.active {
  background:linear-gradient(135deg,var(--accent2),var(--accent1));
  color:#000; border-color:transparent;
  box-shadow:0 4px 15px rgba(0,210,255,0.3);
}
.chat-container {
  flex:1; padding:20px 16px 10px;
  max-width:780px; width:100%; margin:0 auto;
  position:relative; z-index:1;
}
.welcome { text-align:center; padding:50px 20px; animation:fadeUp 0.6s ease both; }
@keyframes fadeUp { from{opacity:0;transform:translateY(20px)} to{opacity:1;transform:translateY(0)} }
.welcome-icon { font-size:56px; margin-bottom:20px; animation:floatIcon 3s ease-in-out infinite; }
@keyframes floatIcon { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-10px)} }
.welcome h2 { font-size:26px; font-weight:900; margin-bottom:12px; }
.welcome p { color:var(--text-dim); line-height:1.9; font-size:15px; }
.welcome-cards { display:flex; gap:12px; margin-top:30px; flex-wrap:wrap; justify-content:center; }
.welcome-card {
  background:var(--surface); border:1px solid var(--border);
  border-radius:14px; padding:14px 18px; cursor:pointer;
  transition:all 0.25s; text-align:right; max-width:200px; font-size:13px;
}
.welcome-card:hover { border-color:var(--border-glow); transform:translateY(-3px); box-shadow:var(--glow); }
.message { margin:12px 0; animation:msgSlide 0.35s cubic-bezier(0.4,0,0.2,1) both; }
@keyframes msgSlide { from{opacity:0;transform:translateY(15px) scale(0.98)} to{opacity:1;transform:translateY(0) scale(1)} }
.user-msg {
  background:var(--user-grad); color:#000; font-weight:600;
  padding:13px 18px; border-radius:20px 20px 6px 20px;
  margin-right:auto; max-width:78%;
  box-shadow:0 6px 25px rgba(0,255,148,0.25);
  font-size:15px; line-height:1.7; word-break:break-word;
}
.ai-msg {
  background:var(--surface); border:1px solid var(--border);
  padding:16px 20px; border-radius:20px 20px 20px 6px;
  max-width:85%; font-size:15px; line-height:1.9;
  position:relative; word-break:break-word; transition:border-color 0.3s;
}
.ai-msg:hover { border-color:var(--border-glow); }
.ai-msg h2 { font-size:18px; font-weight:800; margin:12px 0 6px; color:var(--accent1); }
.ai-msg h3 { font-size:16px; font-weight:700; margin:10px 0 5px; color:var(--accent2); }
.ai-msg h4 { font-size:15px; font-weight:700; margin:8px 0 4px; color:var(--accent3); }
.ai-msg strong { font-weight:800; color:var(--accent1); }
.ai-msg em { font-style:italic; color:var(--accent2); }
.ai-msg p { margin:6px 0; }
.ai-msg ul,.ai-msg ol { padding-right:24px; margin:8px 0; }
.ai-msg li { margin:5px 0; line-height:1.8; }
.ai-msg code {
  background:rgba(0,210,255,0.1); border:1px solid rgba(0,210,255,0.2);
  border-radius:5px; padding:1px 7px; font-size:13px; font-family:monospace;
}
.ai-msg pre {
  background:rgba(0,0,0,0.4); border:1px solid var(--border);
  border-radius:10px; padding:16px; margin:10px 0; overflow-x:auto;
}
.ai-msg pre code { background:none; border:none; padding:0; font-size:13px; }
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
  max-width:100%; border-radius:12px; margin-top:12px;
  box-shadow:0 8px 30px rgba(0,0,0,0.4); animation:imgReveal 0.5s ease both;
}
@keyframes imgReveal { from{opacity:0;transform:scale(0.95)} to{opacity:1;transform:scale(1)} }
.input-area {
  position:sticky; bottom:0; z-index:10;
  background:rgba(5,5,16,0.9); backdrop-filter:blur(20px);
  border-top:1px solid var(--border); padding:12px 16px 16px;
}
.templates {
  display:flex; gap:8px; margin-bottom:10px;
  overflow-x:auto; padding-bottom:4px; scrollbar-width:none;
}
.templates::-webkit-scrollbar { display:none; }
.template-btn {
  background:var(--surface); border:1px solid var(--border);
  color:var(--text-dim); padding:6px 14px; border-radius:20px;
  font-size:12px; white-space:nowrap; cursor:pointer;
  font-family:'Tajawal',sans-serif; transition:all 0.2s;
}
.template-btn:hover { border-color:var(--border-glow); color:var(--text); }
.input-wrapper { max-width:780px; margin:0 auto; display:flex; gap:8px; align-items:flex-end; }
.textarea-wrap { flex:1; position:relative; }
textarea {
  width:100%; background:var(--surface2); border:1px solid var(--border);
  color:var(--text); border-radius:16px; padding:13px 16px;
  font-size:15px; font-family:'Tajawal',sans-serif;
  resize:none; height:52px; max-height:130px;
  transition:border-color 0.25s,box-shadow 0.25s; line-height:1.5;
}
textarea:focus { outline:none; border-color:var(--accent1); box-shadow:0 0 0 3px rgba(0,210,255,0.1); }
textarea::placeholder { color:var(--text-dim); }
#fileInput { display:none; }
.send-btn {
  background:linear-gradient(135deg,var(--accent2),var(--accent1));
  border:none; border-radius:14px; width:52px; height:52px;
  font-size:18px; color:#000; cursor:pointer;
  transition:all 0.25s; flex-shrink:0;
  display:flex; align-items:center; justify-content:center;
}
.send-btn:hover:not(:disabled) { transform:scale(1.08); box-shadow:0 6px 20px rgba(0,210,255,0.4); }
.send-btn:active:not(:disabled) { transform:scale(0.95); }
.send-btn:disabled { opacity:0.5; cursor:not-allowed; }
.file-preview {
  max-width:780px; margin:0 auto 8px;
  display:flex; align-items:center; gap:8px;
  background:rgba(0,210,255,0.08); border:1px solid rgba(0,210,255,0.2);
  padding:8px 14px; border-radius:10px; font-size:13px;
}
.file-preview.hidden { display:none; }
.modal {
  display:none; position:fixed; inset:0; z-index:300;
  justify-content:center; align-items:center;
  background:rgba(0,0,0,0.7); backdrop-filter:blur(8px);
}
.modal.open { display:flex; }
.modal-content {
  background:var(--bg); border:1px solid var(--border);
  padding:32px; border-radius:20px;
  max-width:460px; width:90%; text-align:center;
  animation:modalPop 0.3s cubic-bezier(0.4,0,0.2,1);
}
@keyframes modalPop { from{opacity:0;transform:scale(0.9) translateY(20px)} to{opacity:1;transform:scale(1) translateY(0)} }
.support-btn {
  display:inline-flex; align-items:center; gap:8px;
  background:linear-gradient(135deg,#ff6b6b,#feca57);
  color:#000; border:none; padding:12px 28px;
  border-radius:14px; font-weight:800; cursor:pointer;
  font-family:'Tajawal',sans-serif; font-size:15px;
  text-decoration:none; margin-top:16px; transition:all 0.25s;
}
.support-btn:hover { transform:scale(1.05); box-shadow:0 6px 20px rgba(254,202,87,0.4); }
.toast {
  position:fixed; bottom:90px; left:50%;
  transform:translateX(-50%) translateY(20px);
  background:rgba(20,20,40,0.95); color:var(--text);
  border:1px solid var(--border); padding:10px 22px;
  border-radius:12px; z-index:999; font-size:13px;
  opacity:0; transition:all 0.3s; pointer-events:none; backdrop-filter:blur(10px);
}
.toast.show { opacity:1; transform:translateX(-50%) translateY(0); }
.toast.error { border-color:rgba(255,80,80,0.5); color:#ff8080; }
.toast.success { border-color:rgba(0,255,148,0.5); color:var(--accent2); }
::-webkit-scrollbar { width:5px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:var(--border); border-radius:10px; }
::-webkit-scrollbar-thumb:hover { background:var(--accent1); }
@media (max-width:480px) {
  .ai-msg,.user-msg { max-width:95%; }
  .welcome h2 { font-size:20px; }
  .welcome-cards { gap:8px; }
  .welcome-card { max-width:160px; }
}
</style>
</head>
<body>

<div class="stars" id="stars"></div>
<div class="sidebar-overlay" id="sidebarOverlay" onclick="closeSidebar()"></div>

<div class="sidebar" id="sidebar">
  <h3>💬 محادثاتي</h3>
  <button class="new-chat-btn" onclick="newChat()">
    <i class="fa-solid fa-plus"></i> محادثة جديدة
  </button>
  <div id="chatList"></div>
</div>

<header class="header">
  <button class="icon-btn" onclick="toggleSidebar()" title="القائمة">
    <i class="fa-solid fa-bars"></i>
  </button>
  <span class="logo">✨ Anas Wadi</span>
  <div class="header-actions">
    <button class="icon-btn" onclick="toggleTheme()" title="تغيير الوضع" id="themeBtn">
      <i class="fa-solid fa-moon"></i>
    </button>
    <button class="icon-btn" onclick="showSupport()" title="ادعمني">
      <i class="fa-solid fa-heart" style="color:#ff6b6b"></i>
    </button>
  </div>
</header>

<div class="modes">
  <button class="mode-btn active" data-mode="fast" onclick="setMode('fast')">⚡ سريع</button>
  <button class="mode-btn" data-mode="thinker" onclick="setMode('thinker')">🧠 مفكر</button>
  <button class="mode-btn" data-mode="funny" onclick="setMode('funny')">😂 فكاهي</button>
  <button class="mode-btn" data-mode="creative" onclick="setMode('creative')">🎨 مبدع</button>
  <button class="mode-btn" data-mode="coder" onclick="setMode('coder')">💻 مبرمج</button>
  <button class="mode-btn" data-mode="writer" onclick="setMode('writer')">✍️ كاتب</button>
</div>

<div class="chat-container" id="chatContainer">
  <div class="welcome" id="welcome">
    <div class="welcome-icon">🌊</div>
    <h2>مرحباً بك في <span style="background:linear-gradient(90deg,#00ff94,#00d2ff);-webkit-background-clip:text;-webkit-text-fill-color:transparent">Anas Wadi</span></h2>
    <p>تم تطوير هذا الذكاء الاصطناعي بيد المهندس <strong>Anas Wadi</strong> من ليبيا 🇱🇾</p>
    <p style="margin-top:6px">كيف يمكنني مساعدتك اليوم؟</p>
    <div class="welcome-cards">
      <div class="welcome-card" onclick="useTemplate('ارسم صورة: ')">
        <div style="font-size:22px;margin-bottom:6px">🎨</div>
        <div style="font-weight:700;margin-bottom:4px">رسم صورة</div>
        <div style="color:var(--text-dim);font-size:12px">توليد صور فائقة الجودة</div>
      </div>
      <div class="welcome-card" onclick="useTemplate('اشرحلي ')">
        <div style="font-size:22px;margin-bottom:6px">💡</div>
        <div style="font-weight:700;margin-bottom:4px">شرح وتحليل</div>
        <div style="color:var(--text-dim);font-size:12px">أشرح أي موضوع تريده</div>
      </div>
      <div class="welcome-card" onclick="useTemplate('اكتبلي كود ')">
        <div style="font-size:22px;margin-bottom:6px">💻</div>
        <div style="font-weight:700;margin-bottom:4px">برمجة</div>
        <div style="color:var(--text-dim);font-size:12px">أكتب ويصلح الكود</div>
      </div>
      <div class="welcome-card" onclick="document.getElementById('fileInput').click()">
        <div style="font-size:22px;margin-bottom:6px">📄</div>
        <div style="font-weight:700;margin-bottom:4px">تحليل ملف</div>
        <div style="color:var(--text-dim);font-size:12px">PDF أو صورة</div>
      </div>
    </div>
  </div>
</div>

<div class="input-area">
  <div class="file-preview hidden" id="filePreview">
    <i class="fa-solid fa-file" style="color:var(--accent1)"></i>
    <span id="fileName">ملف مرفق</span>
    <button class="msg-btn" onclick="removeFile()" style="margin-right:auto">
      <i class="fa-solid fa-xmark"></i>
    </button>
  </div>
  <div class="templates">
    <button class="template-btn" onclick="useTemplate('ارسم صورة: ')">🎨 ارسم</button>
    <button class="template-btn" onclick="useTemplate('لخصلي الملف')">📄 لخص</button>
    <button class="template-btn" onclick="useTemplate('اكتبلي ايميل رسمي عن ')">📧 ايميل</button>
    <button class="template-btn" onclick="useTemplate('ترجم للعربية: ')">🌐 ترجم</button>
    <button class="template-btn" onclick="useTemplate('اكتبلي كود بلغة ')">💻 كود</button>
    <button class="template-btn" onclick="useTemplate('اشرحلي بالتفصيل ')">💡 شرح</button>
  </div>
  <div class="input-wrapper">
    <input type="file" id="fileInput" accept="image/*,.pdf" onchange="handleFile(this)">
    <button type="button" class="icon-btn" onclick="document.getElementById('fileInput').click()" title="ارفع ملف">
      <i class="fa-solid fa-paperclip"></i>
    </button>
    <div class="textarea-wrap">
      <textarea id="messageInput"
        placeholder="اكتب رسالتك هنا... (Enter للإرسال، Shift+Enter لسطر جديد)"
        onkeydown="handleKey(event)"
        oninput="autoResize(this)"></textarea>
    </div>
    <button class="send-btn" id="sendBtn" onclick="sendMessage()" title="إرسال">
      <i class="fa-solid fa-paper-plane"></i>
    </button>
  </div>
</div>

<div class="toast" id="toast"></div>

<div class="modal" id="supportModal" onclick="closeModalClick(event)">
  <div class="modal-content">
    <div style="font-size:40px;margin-bottom:15px">💙</div>
    <h2 style="margin-bottom:12px;font-size:22px">شكراً لدعمك!</h2>
    <p style="color:var(--text-dim);line-height:1.8">
      إذا أعجبك هذا التطبيق وأفادك،<br>يسعدني دعمك لمواصلة التطوير
    </p>
    <a href="#" class="support-btn" target="_blank">
      <i class="fa-solid fa-heart"></i> ادعمني الآن
    </a>
    <br><br>
    <button class="msg-btn" onclick="closeModal()" style="font-size:14px;padding:8px 20px">لاحقاً</button>
  </div>
</div>

<script>
(function() {
  const s = document.getElementById('stars');
  for (let i = 0; i < 80; i++) {
    const el = document.createElement('div');
    el.className = 'star';
    const size = Math.random() * 2.5 + 0.5;
    el.style.cssText = `left:${Math.random()*100}%;top:${Math.random()*100}%;width:${size}px;height:${size}px;--d:${2+Math.random()*4}s;--op:${0.3+Math.random()*0.7};animation-delay:${Math.random()*4}s`;
    s.appendChild(el);
  }
})();

let currentMode = 'fast';
let currentFile = null;
let currentFileType = null;
let currentFileData = null;
let chatHistory = [];
let allChats = JSON.parse(localStorage.getItem('chats') || '[]');
let currentChatId = null;
let isLoading = false;

const MODES = {
  fast:     { icon:'⚡', prompt:'أنت مساعد ذكي سريع ومختصر. أجب بإيجاز ودقة.' },
  thinker:  { icon:'🧠', prompt:'أنت مفكر عميق. حلل المشكلة من زوايا متعددة وأعطِ إجابة شاملة ومنطقية.' },
  funny:    { icon:'😂', prompt:'أنت مساعد فكاهي خفيف الظل. أضف لمسة من الطرافة لإجاباتك مع الإفادة.' },
  creative: { icon:'🎨', prompt:'أنت مبدع موهوب. فكر خارج الصندوق وقدم أفكاراً إبداعية ومميزة.' },
  coder:    { icon:'💻', prompt:'أنت مبرمج خبير. اكتب كوداً نظيفاً موثقاً مع شرح واضح.' },
  writer:   { icon:'✍️', prompt:'أنت كاتب محترف. اكتب بأسلوب راقٍ وجذاب مع مراعاة القواعد اللغوية.' }
};

function setMode(mode) {
  currentMode = mode;
  document.querySelectorAll('.mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  showToast(MODES[mode].icon + ' وضع ' + mode, 'success');
}

function toggleTheme() {
  const html = document.documentElement;
  const isDark = html.getAttribute('data-theme') === 'dark';
  html.setAttribute('data-theme', isDark ? 'light' : 'dark');
  document.getElementById('themeBtn').innerHTML = isDark
    ? '<i class="fa-solid fa-sun"></i>'
    : '<i class="fa-solid fa-moon"></i>';
  localStorage.setItem('theme', isDark ? 'light' : 'dark');
}

const savedTheme = localStorage.getItem('theme') || 'dark';
document.documentElement.setAttribute('data-theme', savedTheme);
if (savedTheme === 'light') document.getElementById('themeBtn').innerHTML = '<i class="fa-solid fa-sun"></i>';

function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('sidebarOverlay').classList.toggle('open');
  renderChatList();
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidebarOverlay').classList.remove('open');
}

function renderChatList() {
  const list = document.getElementById('chatList');
  list.innerHTML = allChats.length === 0
    ? '<div style="color:var(--text-dim);font-size:13px;text-align:center;padding:20px">لا توجد محادثات بعد</div>'
    : allChats.slice().reverse().map(c =>
        `<div class="chat-item ${c.id === currentChatId ? 'active' : ''}" onclick="loadChat('${c.id}')">
          ${c.title || 'محادثة جديدة'}
        </div>`
      ).join('');
}

function newChat() {
  if (chatHistory.length > 0) saveCurrentChat();
  chatHistory = [];
  currentChatId = null;
  currentFile = null;
  currentFileData = null;
  document.getElementById('chatContainer').innerHTML = `<div class="welcome" id="welcome">
    <div class="welcome-icon">🌊</div>
    <h2>مرحباً بك في <span style="background:linear-gradient(90deg,#00ff94,#00d2ff);-webkit-background-clip:text;-webkit-text-fill-color:transparent">Anas Wadi</span></h2>
    <p>كيف يمكنني مساعدتك اليوم؟</p>
  </div>`;
  document.getElementById('filePreview').classList.add('hidden');
  closeSidebar();
}

function saveCurrentChat() {
  if (chatHistory.length === 0) return;
  const title = chatHistory[0]?.content?.substring(0, 30) || 'محادثة';
  const id = currentChatId || Date.now().toString();
  currentChatId = id;
  const existing = allChats.findIndex(c => c.id === id);
  const chat = { id, title, history: chatHistory, time: Date.now() };
  if (existing >= 0) allChats[existing] = chat;
  else allChats.push(chat);
  localStorage.setItem('chats', JSON.stringify(allChats));
}

function loadChat(id) {
  const chat = allChats.find(c => c.id === id);
  if (!chat) return;
  chatHistory = chat.history;
  currentChatId = id;
  const container = document.getElementById('chatContainer');
  container.innerHTML = '';
  chatHistory.forEach(msg => {
    appendMessage(msg.content, msg.role === 'user');
  });
  closeSidebar();
  scrollToBottom();
}

function handleFile(input) {
  const file = input.files[0];
  if (!file) return;
  if (file.size > 10 * 1024 * 1024) { showToast('❌ الملف كبير جداً (الحد 10MB)', 'error'); return; }
  currentFile = file;
  document.getElementById('fileName').textContent = file.name;
  document.getElementById('filePreview').classList.remove('hidden');
  const reader = new FileReader();
  reader.onload = e => {
    currentFileData = e.target.result.split(',')[1];
    currentFileType = file.type;
  };
  reader.readAsDataURL(file);
}

function removeFile() {
  currentFile = null; currentFileData = null; currentFileType = null;
  document.getElementById('filePreview').classList.add('hidden');
  document.getElementById('fileInput').value = '';
}

function autoResize(el) {
  el.style.height = '52px';
  el.style.height = Math.min(el.scrollHeight, 130) + 'px';
}

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
}

function useTemplate(t) {
  const inp = document.getElementById('messageInput');
  inp.value = t; inp.focus(); autoResize(inp);
}

function scrollToBottom() {
  const c = document.getElementById('chatContainer');
  c.scrollTo({ top: c.scrollHeight, behavior: 'smooth' });
}

function showToast(msg, type = '') {
  const t = document.getElementById('toast');
  t.textContent = msg; t.className = 'toast show ' + type;
  setTimeout(() => t.className = 'toast', 2500);
}

function appendMessage(html, isUser = false, id = null) {
  const welcome = document.getElementById('welcome');
  if (welcome) welcome.remove();
  const container = document.getElementById('chatContainer');
  const div = document.createElement('div');
  div.className = 'message';
  if (id) div.id = id;
  if (isUser) {
    div.innerHTML = `<div class="user-msg">${html}</div>`;
  } else {
    div.innerHTML = `
      <div class="ai-msg">${html}</div>
      <div class="msg-actions">
        <button class="msg-btn" onclick="copyText(this)"><i class="fa-regular fa-copy"></i> نسخ</button>
      </div>`;
  }
  container.appendChild(div);
  scrollToBottom();
  return div;
}

function copyText(btn) {
  const text = btn.closest('.message').querySelector('.ai-msg').innerText;
  navigator.clipboard.writeText(text).then(() => showToast('✅ تم النسخ', 'success'));
}

function formatMarkdown(text) {
  return `<p>${text
    .replace(/^### (.+)$/gm, '<h4>$1</h4>')
    .replace(/^## (.+)$/gm, '<h3>$1</h3>')
    .replace(/^# (.+)$/gm, '<h2>$1</h2>')
    .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/```(\w+)?\n([\s\S]*?)```/g, (_, lang, code) =>
      `<pre><code class="lang-${lang||''}">${code.trim()}</code></pre>`)
    .replace(/`(.+?)`/g, '<code>$1</code>')
    .replace(/\n\n/g, '</p><p>')
  }</p>`.replace(/<p><\/p>/g, '').replace(/<p>(<h[234]>)/g, '$1');
}

async function sendMessage() {
  if (isLoading) return;
  const inp = document.getElementById('messageInput');
  const msg = inp.value.trim();
  if (!msg && !currentFile) return;

  isLoading = true;
  document.getElementById('sendBtn').disabled = true;
  inp.value = ''; autoResize(inp);

  const displayMsg = currentFile
    ? `<div class="file-badge"><i class="fa-solid fa-file"></i>${currentFile.name}</div><br>${msg || 'حلل هذا الملف'}`
    : msg;
  appendMessage(displayMsg, true);
  appendMessage('<div class="typing-indicator"><span></span><span></span><span></span></div>', false, 'typing');

  const body = {
    message: msg || 'حلل هذا الملف',
    mode: currentMode,
    system: MODES[currentMode].prompt,
    history: chatHistory.slice(-10)
  };
  if (currentFileData) {
    body.file_data = currentFileData;
    body.file_type = currentFileType;
  }

  chatHistory.push({ role: 'user', content: msg || 'تحليل ملف' });

  try {
    const res = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const data = await res.json();
    document.getElementById('typing')?.remove();

    if (data.error) {
      appendMessage('⚠️ ' + data.error);
      showToast('❌ ' + data.error, 'error');
    } else if (data.image_url) {
      appendMessage(`<img src="${data.image_url}" class="generated-img" alt="صورة مولدة">`);
      chatHistory.push({ role: 'assistant', content: '[صورة مولدة]' });
    } else {
      const formatted = formatMarkdown(data.reply || '');
      appendMessage(formatted);
      chatHistory.push({ role: 'assistant', content: data.reply || '' });
    }
    saveCurrentChat();
    if (currentFile) removeFile();
  } catch (err) {
    document.getElementById('typing')?.remove();
    appendMessage('❌ خطأ في الاتصال بالخادم');
    showToast('❌ خطأ في الاتصال', 'error');
  }

  isLoading = false;
  document.getElementById('sendBtn').disabled = false;
}

function showSupport() { document.getElementById('supportModal').classList.add('open'); }
function closeModal() { document.getElementById('supportModal').classList.remove('open'); }
function closeModalClick(e) { if (e.target === document.getElementById('supportModal')) closeModal(); }
</script>
</body>
</html>
"""


@app.route("/")
def home():
    return render_template_string(HTML)


@app.route("/chat", methods=["POST"])
def chat():
    ip = get_client_ip()
    if is_rate_limited(ip):
        return jsonify({"error": "تجاوزت الحد المسموح به. انتظر دقيقة."}), 429

    data = request.get_json()
    user_message = sanitize_input(data.get("message", ""))
    mode = data.get("mode", "fast")
    system_prompt = data.get("system", "أنت مساعد ذكي مفيد.")
    history = data.get("history", [])
    file_data = data.get("file_data")
    file_type = data.get("file_type", "")

    if not user_message and not file_data:
        return jsonify({"error": "لا توجد رسالة"}), 400

    if is_prompt_injection(user_message):
        return jsonify({"error": "تم اكتشاف محاولة تجاوز غير مسموح بها."}), 400

    # بناء الرسائل
    messages = [{"role": "system", "content": system_prompt}]
    for h in history[-8:]:
        if h.get("role") in ("user", "assistant"):
            messages.append({"role": h["role"], "content": h["content"]})

    # إضافة الملف إن وُجد
    if file_data:
        if "image" in file_type:
            messages.append({
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{file_type};base64,{file_data}"}},
                    {"type": "text", "text": user_message or "حلل هذه الصورة"}
                ]
            })
        else:
            import base64 as b64
            try:
                pdf_text = b64.b64decode(file_data).decode('utf-8', errors='ignore')[:3000]
            except:
                pdf_text = "[ملف PDF - لا يمكن قراءته]"
            messages.append({"role": "user", "content": f"محتوى الملف:\n{pdf_text}\n\nالسؤال: {user_message}"})
    else:
        messages.append({"role": "user", "content": user_message})

    try:
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": messages,
            "max_tokens": 2048,
            "temperature": 0.7
        }
        resp = requests.post("https://api.groq.com/openai/v1/chat/completions",
                             headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        reply = result["choices"][0]["message"]["content"]
        return jsonify({"reply": reply})
    except requests.exceptions.Timeout:
        return jsonify({"error": "انتهت مهلة الطلب. حاول مرة أخرى."}), 504
    except Exception as e:
        return jsonify({"error": f"خطأ: {str(e)}"}), 500


@app.route("/suggest", methods=["POST"])
def suggest():
    data = request.get_json()
    text = data.get("text", "")
    user_email = data.get("email", None)
    if not text:
        return jsonify({"error": "No suggestion provided"}), 400
    save_suggestion(text, user_email)
    return jsonify({"status": "success"})


@app.route("/admin")
def admin():
    user_email = request.args.get("email", "")
    if user_email != OWNER_EMAIL:
        return "Access Denied", 403
    suggestions = load_suggestions()
    rows = "".join(
        f"<tr><td>{s.get('time','')}</td><td>{s.get('email','')}</td><td>{s.get('text','')}</td></tr>"
        for s in suggestions
    )
    return f"""<html><body dir='rtl'>
    <h2>لوحة الإدارة - الاقتراحات</h2>
    <table border='1' cellpadding='8'>
      <tr><th>الوقت</th><th>البريد</th><th>الاقتراح</th></tr>
      {rows}
    </table></body></html>"""


if __name__ == "__main__":
    app.run(debug=True)
