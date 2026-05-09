import os
import base64
import json
import time
from flask import Flask, request, render_template_string, jsonify, Response, stream_with_context
import requests
import io

try:
    import PyPDF2
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(32).hex())

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# ─── Rate limiting (simple in-memory) ────────────────────────────────────────
_rate = {}
RATE_LIMIT = 20      # max requests
RATE_WINDOW = 60     # per N seconds

def check_rate(ip):
    now = time.time()
    bucket = _rate.get(ip, [])
    bucket = [t for t in bucket if now - t < RATE_WINDOW]
    if len(bucket) >= RATE_LIMIT:
        return False
    bucket.append(now)
    _rate[ip] = bucket
    return True

# ─── Models ───────────────────────────────────────────────────────────────────
MODELS = {
    "fast":     "llama-3.3-70b-versatile",          # fast & smart
    "thinker":  "deepseek-r1-distill-llama-70b",    # reasoning model
    "funny":    "llama-3.3-70b-versatile",
    "creative": "llama-3.3-70b-versatile",
    "vision":   "meta-llama/llama-4-scout-17b-16e-instruct",  # image understanding
}

# ─── System prompts ───────────────────────────────────────────────────────────
IDENTITY = ["من انت", "من أنت", "عرف بنفسك", "من تكون", "شن اسمك", "who are you", "اسمك", "ما اسمك"]

def get_system(mode, msg):
    if any(q in msg.lower() for q in IDENTITY):
        return "أنت مساعد ذكاء اصطناعي متقدم اسمه Wadi، طوّره المهندس Anas Wadi من ليبيا 🇱🇾. عرّف نفسك بهذا فقط ولا تضف شيئاً آخر."
    p = {
        "fast":     "أنت Wadi، مساعد ذكاء اصطناعي سريع ودقيق. أجب باختصار ووضوح. لا تتكلم أكثر من اللازم.",
        "thinker":  "أنت Wadi، مساعد تفكير عميق. فكّر خطوة بخطوة، اعطِ إجابة منظمة وقوية. استخدم أرقام ونقاط عند الحاجة.",
        "funny":    "أنت Wadi، مساعد فكاهي خفيف الظل. أجب بطريقة مضحكة وممتعة مع المعلومة الصحيحة. استخدم إيموجي 😄",
        "creative": "أنت Wadi، مساعد مبدع. أجب بأسلوب فني وخيالي ومميز. استخدم استعارات وأفكار غير متوقعة.",
    }
    return p.get(mode, p["fast"])

# ─── PDF helper ───────────────────────────────────────────────────────────────
def extract_pdf(f):
    if not HAS_PDF:
        return "PyPDF2 غير مثبت."
    try:
        r = PyPDF2.PdfReader(io.BytesIO(f.read()))
        txt = ""
        for page in r.pages[:12]:
            txt += (page.extract_text() or "") + "\n"
        return txt[:9000]
    except Exception as e:
        return f"خطأ في قراءة PDF: {e}"

# ─── HTML ─────────────────────────────────────────────────────────────────────
HTML = r"""
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#0d0d14">
<title>Wadi AI</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>
/* ── Reset & Variables ── */
*, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }

:root {
  --bg:        #0d0d14;
  --surface:   #13131e;
  --surface2:  #1a1a2a;
  --border:    rgba(255,255,255,0.07);
  --border2:   rgba(255,255,255,0.12);
  --text:      #e8e8f0;
  --text2:     #8888aa;
  --accent:    #7c6aff;
  --accent2:   #00e5ff;
  --user-bg:   linear-gradient(135deg,#7c6aff,#00e5ff);
  --radius:    16px;
  --font:      'IBM Plex Sans Arabic', system-ui, sans-serif;
  --mono:      'JetBrains Mono', monospace;
}

/* prevent iOS zoom on input focus */
html { -webkit-text-size-adjust: 100%; touch-action: manipulation; }

html, body {
  height: 100%;
  width: 100%;
  font-family: var(--font);
  background: var(--bg);
  color: var(--text);
  overflow: hidden;
  /* iOS bounce fix */
  position: fixed;
  overscroll-behavior: none;
}

/* ── Layout — full screen, no max-width cap ── */
.app {
  display: grid;
  grid-template-rows: auto auto 1fr auto;
  height: 100dvh;
  width: 100%;
  /* respect notch / home bar */
  padding-top: env(safe-area-inset-top);
  padding-bottom: 0;
}

/* ── Header ── */
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
  background: rgba(13,13,20,0.97);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  position: sticky;
  top: 0;
  z-index: 10;
}

.logo {
  font-size: 19px;
  font-weight: 700;
  background: linear-gradient(90deg, var(--accent), var(--accent2));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.header-right { display: flex; gap: 8px; align-items: center; }

/* bigger tap targets for mobile */
.icon-btn {
  width: 44px; height: 44px;
  display: flex; align-items: center; justify-content: center;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  color: var(--text2);
  cursor: pointer;
  font-size: 17px;
  transition: all .18s;
  -webkit-tap-highlight-color: transparent;
}
.icon-btn:active { transform: scale(0.92); background: var(--surface2); }

/* ── Mode tabs ── */
.modes {
  display: flex;
  gap: 7px;
  padding: 9px 12px;
  overflow-x: auto;
  scrollbar-width: none;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
  -webkit-overflow-scrolling: touch;
}
.modes::-webkit-scrollbar { display: none; }

.mode-btn {
  padding: 8px 18px;
  border-radius: 22px;
  border: 1px solid var(--border2);
  background: transparent;
  color: var(--text2);
  font-family: var(--font);
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
  transition: all .18s;
  -webkit-tap-highlight-color: transparent;
}
.mode-btn.active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
  box-shadow: 0 0 18px rgba(124,106,255,0.4);
}
.mode-btn:active { transform: scale(0.94); }

/* ── Chat area ── */
.chat-area {
  overflow-y: auto;
  overflow-x: hidden;
  padding: 14px 12px 10px;
  scroll-behavior: smooth;
  display: flex;
  flex-direction: column;
  gap: 2px;
  -webkit-overflow-scrolling: touch;
  overscroll-behavior-y: contain;
}
.chat-area::-webkit-scrollbar { display: none; }

/* ── Welcome ── */
.welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 55%;
  gap: 14px;
  text-align: center;
  padding: 0 20px;
  animation: fadeUp .5s ease;
}
.welcome-icon {
  width: 70px; height: 70px;
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  border-radius: 22px;
  display: flex; align-items: center; justify-content: center;
  font-size: 30px;
  box-shadow: 0 10px 36px rgba(124,106,255,0.35);
}
.welcome h2 { font-size: 24px; font-weight: 700; }
.welcome p  { color: var(--text2); font-size: 15px; line-height: 1.75; }

/* ── Message rows — full width bubbles on mobile ── */
.msg-row {
  display: flex;
  gap: 8px;
  animation: fadeUp .28s ease;
  margin-bottom: 10px;
  align-items: flex-end;
}
.msg-row.user { flex-direction: row-reverse; }

.avatar {
  width: 30px; height: 30px; min-width: 30px;
  border-radius: 9px;
  display: flex; align-items: center; justify-content: center;
  font-size: 14px;
  margin-bottom: 2px;
}
.avatar.ai-av   { background: var(--surface2); border: 1px solid var(--border2); }
.avatar.user-av { background: linear-gradient(135deg, var(--accent), var(--accent2)); }

.bubble {
  /* nearly full width on phone */
  max-width: calc(100% - 46px);
  padding: 11px 14px;
  border-radius: var(--radius);
  font-size: 16px;
  line-height: 1.78;
  white-space: pre-wrap;
  word-break: break-word;
}
.bubble.user-bubble {
  background: var(--user-bg);
  color: #fff;
  border-radius: var(--radius) var(--radius) 4px var(--radius);
  box-shadow: 0 4px 18px rgba(124,106,255,0.22);
}
.bubble.ai-bubble {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius) var(--radius) var(--radius) 4px;
  color: var(--text);
}

/* code blocks */
.ai-bubble pre {
  background: #09090f;
  border: 1px solid var(--border2);
  border-radius: 10px;
  padding: 12px;
  margin: 10px 0;
  overflow-x: auto;
  font-family: var(--mono);
  font-size: 13px;
  -webkit-overflow-scrolling: touch;
}
.ai-bubble code { font-family: var(--mono); font-size: 13px; }
.ai-bubble p  { margin-bottom: 8px; }
.ai-bubble h3 { margin: 10px 0 6px; font-size: 16px; color: var(--accent2); }
.ai-bubble ul, .ai-bubble ol { padding-right: 20px; margin: 6px 0; }
.ai-bubble li { margin-bottom: 5px; }
.ai-bubble img { max-width: 100%; border-radius: 12px; margin-top: 10px; }

.bubble.typing::after {
  content: '▋';
  color: var(--accent);
  animation: blink .7s infinite;
}
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }

/* msg actions — always visible on mobile (no hover) */
.msg-actions {
  display: flex;
  gap: 6px;
  margin-top: 6px;
  opacity: 1;
}
.msg-action-btn {
  background: var(--surface2);
  border: 1px solid var(--border);
  color: var(--text2);
  padding: 6px 12px;
  border-radius: 10px;
  font-size: 13px;
  cursor: pointer;
  font-family: var(--font);
  -webkit-tap-highlight-color: transparent;
}
.msg-action-btn:active { transform: scale(0.94); }

.file-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(124,106,255,0.15);
  border: 1px solid rgba(124,106,255,0.3);
  border-radius: 8px;
  padding: 5px 10px;
  font-size: 13px;
  color: var(--accent2);
  margin-bottom: 6px;
}

/* ── Input area ── */
.input-area {
  border-top: 1px solid var(--border);
  background: var(--bg);
  /* extra bottom padding for home bar */
  padding: 8px 12px calc(8px + env(safe-area-inset-bottom));
}

.file-preview-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  background: var(--surface);
  border: 1px solid var(--border2);
  border-radius: 12px;
  margin-bottom: 7px;
  font-size: 13px;
  color: var(--accent2);
}
.file-preview-bar button {
  margin-right: auto;
  background: none;
  border: none;
  color: var(--text2);
  cursor: pointer;
  font-size: 18px;
  padding: 4px;
}

.templates {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  margin-bottom: 8px;
  scrollbar-width: none;
  -webkit-overflow-scrolling: touch;
}
.templates::-webkit-scrollbar { display: none; }
.tpl-btn {
  padding: 7px 15px;
  border-radius: 22px;
  border: 1px solid var(--border2);
  background: transparent;
  color: var(--text2);
  font-size: 13px;
  white-space: nowrap;
  cursor: pointer;
  font-family: var(--font);
  -webkit-tap-highlight-color: transparent;
}
.tpl-btn:active { background: var(--surface2); }

.composer {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}

textarea#inp {
  flex: 1;
  background: var(--surface);
  border: 1px solid var(--border2);
  border-radius: 14px;
  color: var(--text);
  font-family: var(--font);
  /* 16px prevents iOS auto-zoom */
  font-size: 16px;
  padding: 13px 14px;
  resize: none;
  height: 50px;
  max-height: 120px;
  line-height: 1.5;
  transition: border-color .2s;
  -webkit-appearance: none;
  /* stop iOS adding inner shadow */
  box-shadow: none;
}
textarea#inp:focus { outline: none; border-color: var(--accent); }
textarea#inp::placeholder { color: var(--text2); }

.send-btn {
  width: 50px; height: 50px; min-width: 50px;
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  border: none;
  border-radius: 14px;
  color: #fff;
  font-size: 20px;
  cursor: pointer;
  transition: transform .15s;
  display: flex; align-items: center; justify-content: center;
  -webkit-tap-highlight-color: transparent;
}
.send-btn:active { transform: scale(0.92); }
.send-btn:disabled { opacity: 0.4; }

#fileInput { display: none; }

/* ── Sidebar — full width on small phones ── */
.overlay {
  display: none;
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.65);
  backdrop-filter: blur(3px);
  z-index: 19;
}
.overlay.open { display: block; }

.sidebar {
  position: fixed;
  top: 0; right: -100%;
  width: min(80vw, 300px);
  height: 100dvh;
  background: var(--surface);
  border-left: 1px solid var(--border);
  z-index: 20;
  transition: right .3s cubic-bezier(.4,0,.2,1);
  display: flex;
  flex-direction: column;
  padding: calc(20px + env(safe-area-inset-top)) 16px 20px;
  gap: 12px;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}
.sidebar.open { right: 0; }
.sidebar h3 { font-size: 14px; color: var(--text2); font-weight: 600; letter-spacing: .5px; }

.new-chat-btn {
  display: flex; align-items: center; gap: 8px;
  background: rgba(124,106,255,0.15);
  border: 1px solid rgba(124,106,255,0.3);
  border-radius: 12px;
  color: var(--accent);
  font-family: var(--font);
  font-size: 15px;
  padding: 13px 14px;
  cursor: pointer;
  font-weight: 600;
  -webkit-tap-highlight-color: transparent;
}
.new-chat-btn:active { background: rgba(124,106,255,0.28); }

.chat-item {
  padding: 12px 14px;
  border-radius: 12px;
  border: 1px solid var(--border);
  background: transparent;
  cursor: pointer;
  font-size: 14px;
  color: var(--text2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  -webkit-tap-highlight-color: transparent;
}
.chat-item:active { background: var(--surface2); }
.chat-item.active { background: rgba(124,106,255,0.1); border-color: var(--accent); color: var(--text); }

/* ── Toast ── */
.toast {
  position: fixed;
  bottom: calc(80px + env(safe-area-inset-bottom));
  left: 50%;
  transform: translateX(-50%) translateY(16px);
  background: var(--surface2);
  border: 1px solid var(--border2);
  color: var(--text);
  padding: 11px 22px;
  border-radius: 12px;
  font-size: 14px;
  z-index: 50;
  opacity: 0;
  pointer-events: none;
  transition: all .28s;
  white-space: nowrap;
}
.toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }

/* ── Animations ── */
@keyframes fadeUp {
  from { opacity: 0; transform: translateY(10px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* ── Thinking block ── */
.thinking-block {
  background: rgba(124,106,255,0.08);
  border: 1px solid rgba(124,106,255,0.2);
  border-radius: 10px;
  padding: 10px 14px;
  margin-bottom: 10px;
  font-size: 14px;
  color: var(--text2);
}
.thinking-block summary { cursor: pointer; color: var(--accent); font-weight: 600; }

</style>
</head>
<body>

<div class="toast" id="toast"></div>

<!-- Sidebar overlay -->
<div class="overlay" id="overlay" onclick="closeSidebar()"></div>

<!-- Sidebar -->
<div class="sidebar" id="sidebar">
  <h3>المحادثات</h3>
  <button class="new-chat-btn" onclick="newChat()"><i class="fa-solid fa-plus"></i> محادثة جديدة</button>
  <div id="chatList"></div>
</div>

<div class="app">

  <!-- Header — زر القائمة يمين، الشعار وسط، سلة يسار -->
  <header style="direction:ltr;">
    <button class="icon-btn" onclick="openSidebar()"><i class="fa-solid fa-bars"></i></button>
    <span class="logo">✦ Wadi AI</span>
    <button class="icon-btn" onclick="clearChat()" title="مسح المحادثة"><i class="fa-solid fa-trash-can"></i></button>
  </header>

  <!-- Modes -->
  <div class="modes">
    <button class="mode-btn active" data-mode="fast"     onclick="setMode('fast')">⚡ سريع</button>
    <button class="mode-btn"        data-mode="thinker"  onclick="setMode('thinker')">🧠 مفكر</button>
    <button class="mode-btn"        data-mode="funny"    onclick="setMode('funny')">😄 فكاهي</button>
    <button class="mode-btn"        data-mode="creative" onclick="setMode('creative')">🎨 مبدع</button>
  </div>

  <!-- Chat -->
  <div class="chat-area" id="chatArea">
    <div class="welcome" id="welcome">
      <div class="welcome-icon">✦</div>
      <h2>مرحباً، أنا Wadi</h2>
      <p>مساعدك الذكي من تطوير المهندس Anas Wadi 🇱🇾<br>اسألني أي شيء أو أرفع ملفاً</p>
    </div>
  </div>

  <!-- Input -->
  <div class="input-area">
    <div id="filePreviewBar" style="display:none" class="file-preview-bar">
      <i class="fa-solid fa-file"></i>
      <span id="filePreviewName"></span>
      <button onclick="removeFile()"><i class="fa-solid fa-xmark"></i></button>
    </div>

    <div class="templates">
      <button class="tpl-btn" onclick="useTpl('ارسم صورة: ')">🖼 ارسم صورة</button>
      <button class="tpl-btn" onclick="useTpl('لخصلي هذا الملف')">📄 لخص ملف</button>
      <button class="tpl-btn" onclick="useTpl('اكتبلي ايميل رسمي عن ')">📧 إيميل</button>
      <button class="tpl-btn" onclick="useTpl('ترجم للعربية: ')">🌐 ترجمة</button>
      <button class="tpl-btn" onclick="useTpl('اشرحلي ')">💡 اشرح</button>
    </div>

    <div class="composer" style="direction:ltr;">
      <button class="send-btn" id="sendBtn" onclick="send()">
        <i class="fa-solid fa-paper-plane"></i>
      </button>
      <textarea id="inp" placeholder="اكتب رسالتك…" style="direction:rtl; text-align:right;"
        onkeydown="onKey(event)" oninput="resize(this)"></textarea>
      <input type="file" id="fileInput" accept="image/*,.pdf" onchange="handleFile(this)">
      <button class="icon-btn" onclick="document.getElementById('fileInput').click()" title="رفع ملف">
        <i class="fa-solid fa-paperclip"></i>
      </button>
    </div>
  </div>

</div>

<script>
// ── State ────────────────────────────────────────────────────────────────────
let chats       = {};
let currentId   = null;
let mode        = 'fast';
let currentFile = null;
let sending     = false;
let abortCtrl   = null;

// ── Init ─────────────────────────────────────────────────────────────────────
(function init() {
  try { chats = JSON.parse(localStorage.getItem('wadi_chats') || '{}'); } catch { chats = {}; }
  currentId = localStorage.getItem('wadi_cid');
  mode      = localStorage.getItem('wadi_mode') || 'fast';

  if (!currentId || !chats[currentId]) newChat(false);

  setMode(mode, false);
  renderAll();
})();

// ── Chats ─────────────────────────────────────────────────────────────────────
function newChat(save = true) {
  currentId = Date.now().toString();
  chats[currentId] = [];
  localStorage.setItem('wadi_cid', currentId);
  if (save) saveChats();
  renderAll();
  closeSidebar();
}

function switchChat(id) {
  currentId = id;
  localStorage.setItem('wadi_cid', id);
  renderAll();
  closeSidebar();
}

function clearChat() {
  chats[currentId] = [];
  saveChats();
  renderAll();
  toast('تم مسح المحادثة');
}

function saveChats() {
  try { localStorage.setItem('wadi_chats', JSON.stringify(chats)); }
  catch { toast('الذاكرة ممتلئة! احذف محادثات قديمة'); }
}

// ── Render ────────────────────────────────────────────────────────────────────
function renderAll() {
  renderChat();
  renderSidebar();
}

function renderSidebar() {
  const el = document.getElementById('chatList');
  const ids = Object.keys(chats).reverse();
  el.innerHTML = ids.map(id => {
    const first = chats[id][0]?.user || 'محادثة جديدة';
    const active = id === currentId ? ' active' : '';
    return `<div class="chat-item${active}" onclick="switchChat('${id}')">${esc(first.substring(0,32))}</div>`;
  }).join('');
}

function renderChat() {
  const area = document.getElementById('chatArea');
  const msgs  = chats[currentId] || [];

  if (msgs.length === 0) {
    area.innerHTML = `<div class="welcome" id="welcome">
      <div class="welcome-icon">✦</div>
      <h2>مرحباً، أنا Wadi</h2>
      <p>مساعدك الذكي من تطوير المهندس Anas Wadi 🇱🇾<br>اسألني أي شيء أو أرفع ملفاً</p>
    </div>`;
    return;
  }

  area.innerHTML = msgs.map((m, i) => msgHTML(m, i)).join('');
  scrollBottom();
}

function msgHTML(m, i) {
  // User bubble
  let userContent = '';
  if (m.fileName) userContent += `<div class="file-badge"><i class="fa-solid fa-file"></i> ${esc(m.fileName)}</div><br>`;
  userContent += esc(m.user);

  // AI bubble
  let aiContent = m.thinking
    ? `<details class="thinking-block"><summary>💭 التفكير</summary>${esc(m.thinking)}</details>${formatAI(m.ai)}`
    : formatAI(m.ai);

  if (m.imageUrl) aiContent += `<br><img src="${m.imageUrl}" alt="صورة">`;

  return `
  <div class="msg-row user">
    <div class="avatar user-av">👤</div>
    <div>
      <div class="bubble user-bubble">${userContent}</div>
    </div>
  </div>
  <div class="msg-row ai">
    <div class="avatar ai-av">✦</div>
    <div>
      <div class="bubble ai-bubble${m.loading ? ' typing' : ''}" id="bubble-${i}">${aiContent}</div>
      <div class="msg-actions">
        <button class="msg-action-btn" onclick="copyMsg(${i})"><i class="fa-solid fa-copy"></i> نسخ</button>
        <button class="msg-action-btn" onclick="regen(${i})"><i class="fa-solid fa-rotate"></i> إعادة</button>
      </div>
    </div>
  </div>`;
}

// Simple markdown-lite formatter
function formatAI(text) {
  if (!text) return '';
  return text
    .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h3>$1</h3>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
    .replace(/\n{2,}/g, '</p><p>')
    .replace(/\n/g, '<br>');
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

function scrollBottom() {
  const a = document.getElementById('chatArea');
  a.scrollTop = a.scrollHeight;
}

// ── Send ──────────────────────────────────────────────────────────────────────
async function send() {
  if (sending) { abort(); return; }
  const inp  = document.getElementById('inp');
  const text = inp.value.trim();
  if (!text && !currentFile) return;

  inp.value = '';
  resize(inp);

  sending = true;
  document.getElementById('sendBtn').innerHTML = '<i class="fa-solid fa-stop"></i>';

  const msgs = chats[currentId];
  const idx  = msgs.length;
  const fileName = currentFile?.name || null;

  msgs.push({ user: text || 'حلل الملف', ai: '', loading: true, fileName });
  saveChats();
  renderChat();

  const fd = new FormData();
  fd.append('message', text);
  fd.append('mode', mode);
  fd.append('history', JSON.stringify(msgs.slice(0, -1)));
  if (currentFile) fd.append('file', currentFile);

  clearFilePreview();

  abortCtrl = new AbortController();

  try {
    const res = await fetch('/api/chat', { method: 'POST', body: fd, signal: abortCtrl.signal });

    if (!res.ok) throw new Error('خطأ في الاتصال');

    const contentType = res.headers.get('content-type') || '';

    if (contentType.includes('text/event-stream')) {
      // Streaming
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let aiText = '';
      let thinkText = '';
      let inThink = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value);
        const lines = chunk.split('\n');
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const raw = line.slice(6).trim();
          if (raw === '[DONE]') break;
          try {
            const d = JSON.parse(raw);
            const delta = d.choices?.[0]?.delta?.content || '';
            if (!delta) continue;

            // deepseek thinking tags
            if (delta.includes('<think>')) { inThink = true; continue; }
            if (delta.includes('</think>')) { inThink = false; continue; }

            if (inThink) thinkText += delta;
            else aiText += delta;

            msgs[idx].ai      = aiText;
            msgs[idx].thinking = thinkText || undefined;
            msgs[idx].loading  = true;

            // live DOM update
            const bubble = document.getElementById(`bubble-${idx}`);
            if (bubble) {
              bubble.innerHTML = thinkText
                ? `<details class="thinking-block"><summary>💭 التفكير</summary>${esc(thinkText)}</details>${formatAI(aiText)}`
                : formatAI(aiText);
            }
            scrollBottom();
          } catch {}
        }
      }
      msgs[idx].loading = false;

    } else {
      // JSON fallback
      const data = await res.json();
      msgs[idx].ai       = data.response || 'لا يوجد رد';
      msgs[idx].imageUrl = data.imageUrl || undefined;
      msgs[idx].loading  = false;
    }

  } catch (err) {
    if (err.name !== 'AbortError') {
      msgs[idx].ai = '⚠️ ' + err.message;
    } else {
      msgs[idx].ai = msgs[idx].ai || '(تم الإيقاف)';
    }
    msgs[idx].loading = false;
  }

  saveChats();
  renderChat();
  sending = false;
  document.getElementById('sendBtn').innerHTML = '<i class="fa-solid fa-paper-plane"></i>';
}

function abort() {
  abortCtrl?.abort();
  sending = false;
  document.getElementById('sendBtn').innerHTML = '<i class="fa-solid fa-paper-plane"></i>';
}

async function regen(i) {
  if (sending) return;
  const msgs = chats[currentId];
  const userMsg = msgs[i].user;
  msgs[i] = { user: userMsg, ai: '', loading: true };
  saveChats();
  renderChat();

  sending = true;
  document.getElementById('sendBtn').innerHTML = '<i class="fa-solid fa-stop"></i>';

  const fd = new FormData();
  fd.append('message', userMsg);
  fd.append('mode', mode);
  fd.append('history', JSON.stringify(msgs.slice(0, i)));
  abortCtrl = new AbortController();

  try {
    const res = await fetch('/api/chat', { method: 'POST', body: fd, signal: abortCtrl.signal });
    const contentType = res.headers.get('content-type') || '';

    if (contentType.includes('text/event-stream')) {
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let aiText = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value);
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          const raw = line.slice(6).trim();
          if (raw === '[DONE]') break;
          try {
            const d = JSON.parse(raw);
            aiText += d.choices?.[0]?.delta?.content || '';
            msgs[i].ai = aiText;
            const bubble = document.getElementById(`bubble-${i}`);
            if (bubble) bubble.innerHTML = formatAI(aiText);
            scrollBottom();
          } catch {}
        }
      }
      msgs[i].loading = false;
    } else {
      const data = await res.json();
      msgs[i].ai = data.response || '';
      msgs[i].loading = false;
    }
  } catch (err) {
    msgs[i].ai = err.name === 'AbortError' ? msgs[i].ai || '(تم الإيقاف)' : '⚠️ ' + err.message;
    msgs[i].loading = false;
  }

  saveChats();
  renderChat();
  sending = false;
  document.getElementById('sendBtn').innerHTML = '<i class="fa-solid fa-paper-plane"></i>';
}

// ── File ──────────────────────────────────────────────────────────────────────
function handleFile(el) {
  if (!el.files[0]) return;
  currentFile = el.files[0];
  const bar  = document.getElementById('filePreviewBar');
  const name = document.getElementById('filePreviewName');
  name.textContent = currentFile.name;
  bar.style.display = 'flex';
}
function removeFile() {
  currentFile = null;
  document.getElementById('fileInput').value = '';
  clearFilePreview();
}
function clearFilePreview() {
  currentFile = null;
  document.getElementById('fileInput').value = '';
  document.getElementById('filePreviewBar').style.display = 'none';
}

// ── UI helpers ────────────────────────────────────────────────────────────────
function setMode(m, save = true) {
  mode = m;
  if (save) localStorage.setItem('wadi_mode', m);
  document.querySelectorAll('.mode-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.mode === m));
}

function onKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
}

function resize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 130) + 'px';
}

function useTpl(t) {
  const inp = document.getElementById('inp');
  inp.value = t;
  inp.focus();
  resize(inp);
}

function copyMsg(i) {
  const text = chats[currentId][i]?.ai || '';
  navigator.clipboard.writeText(text).then(() => toast('تم النسخ ✓'));
}

function openSidebar()  { document.getElementById('sidebar').classList.add('open'); document.getElementById('overlay').classList.add('open'); }
function closeSidebar() { document.getElementById('sidebar').classList.remove('open'); document.getElementById('overlay').classList.remove('open'); }

function toast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2500);
}
</script>
</body>
</html>
"""

# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route("/")
def home():
    return render_template_string(HTML)


@app.route("/api/chat", methods=["POST"])
def chat():
    # Rate limit
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    if not check_rate(ip):
        return jsonify({"response": "⚠️ أرسلت طلبات كثيرة جداً، انتظر دقيقة ثم أعد المحاولة."}), 429

    if not GROQ_API_KEY:
        return jsonify({"response": "⚠️ مفتاح GROQ_API_KEY غير مضاف في إعدادات البيئة."})

    user_message = request.form.get("message", "").strip()
    req_mode     = request.form.get("mode", "fast")
    history_raw  = request.form.get("history", "[]")
    file         = request.files.get("file")

    # Build message list
    messages = [{"role": "system", "content": get_system(req_mode, user_message)}]
    try:
        for m in json.loads(history_raw):
            messages.append({"role": "user",      "content": m.get("user", "")})
            messages.append({"role": "assistant",  "content": m.get("ai",  "")})
    except Exception:
        pass

    # ── Image generation ──────────────────────────────────────────────────────
    draw_triggers = ['ارسم صورة', 'ارسم لي', 'ولد صورة', 'generate image', 'draw']
    if any(t in user_message.lower() for t in draw_triggers):
        prompt = user_message
        for t in draw_triggers:
            prompt = prompt.replace(t, '').replace(':', '').strip()
        encoded = requests.utils.quote(prompt)
        image_url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true&enhance=true"
        return jsonify({"response": f"✅ تم توليد الصورة\nالوصف: {prompt}", "imageUrl": image_url})

    # ── File handling ─────────────────────────────────────────────────────────
    model = MODELS.get(req_mode, MODELS["fast"])

    if file:
        fname = (file.filename or "").lower()
        if fname.endswith(".pdf"):
            pdf_text    = extract_pdf(file)
            user_message = f"محتوى ملف PDF:\n{pdf_text}\n\nطلب المستخدم: {user_message or 'لخص الملف'}"
        elif file.content_type and file.content_type.startswith("image/"):
            img_b64 = base64.b64encode(file.read()).decode()
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text",      "text": user_message or "حلل هذه الصورة"},
                    {"type": "image_url", "image_url": {"url": f"data:{file.content_type};base64,{img_b64}"}}
                ]
            })
            model = MODELS["vision"]
            # Vision: no streaming, return JSON
            headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
            data    = {"model": model, "messages": messages, "max_tokens": 2048}
            try:
                r   = requests.post("https://api.groq.com/openai/v1/chat/completions",
                                    headers=headers, json=data, timeout=60)
                res = r.json()
                ai  = res["choices"][0]["message"]["content"] if r.status_code == 200 \
                      else f"خطأ: {res.get('error',{}).get('message','')}"
            except Exception as e:
                ai = f"⚠️ {e}"
            return jsonify({"response": ai})

    messages.append({"role": "user", "content": user_message or "مرحبا"})

    # ── Streaming response ────────────────────────────────────────────────────
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type":  "application/json",
    }
    payload = {
        "model":       model,
        "messages":    messages,
        "max_tokens":  2048,
        "temperature": 0.7 if req_mode in ("funny", "creative") else 0.4,
        "stream":      True,
    }

    def generate():
        try:
            with requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers, json=payload,
                stream=True, timeout=60
            ) as r:
                for line in r.iter_lines():
                    if line:
                        yield line.decode() + "\n\n"
        except Exception as e:
            yield f"data: {{\"error\": \"{e}\"}}\n\n"

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
