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

_rate = {}
RATE_LIMIT = 20
RATE_WINDOW = 60

def check_rate(ip):
    now = time.time()
    bucket = _rate.get(ip, [])
    bucket = [t for t in bucket if now - t < RATE_WINDOW]
    if len(bucket) >= RATE_LIMIT:
        return False
    bucket.append(now)
    _rate[ip] = bucket
    return True

MODELS = {
    "fast": "llama-3.3-70b-versatile",
    "thinker": "llama-3.3-70b-versatile",
    "funny": "llama-3.3-70b-versatile",
    "creative": "llama-3.3-70b-versatile",
    "vision": "meta-llama/llama-4-scout-17b-16e-instruct",
}

IDENTITY = ["من انت", "من أنت", "عرف بنفسك", "من تكون", "شن اسمك", "who are you", "اسمك", "ما اسمك"]

def get_system(mode, msg):
    if any(q in msg.lower() for q in IDENTITY):
        return "أنت مساعد ذكاء اصطناعي متقدم اسمه Wadi، طوّره المهندس Anas Wadi من ليبيا 🇱🇾. عرّف نفسك بهذا فقط ولا تضف شيئاً آخر."
    p = {
        "fast": "أنت Wadi، مساعد ذكاء اصطناعي سريع ودقيق. أجب باختصار ووضوح. لا تتكلم أكثر من اللازم.",
        "thinker": "أنت Wadi، مساعد تفكير عميق. فكّر خطوة بخطوة، اعطِ إجابة منظمة وقوية. استخدم أرقام ونقاط عند الحاجة.",
        "funny": "أنت Wadi، مساعد فكاهي خفيف الظل. أجب بطريقة مضحكة وممتعة مع المعلومة الصحيحة. استخدم إيموجي 😄",
        "creative": "أنت Wadi، مساعد مبدع. أجب بأسلوب فني وخيالي ومميز. استخدم استعارات وأفكار غير متوقعة.",
    }
    return p.get(mode, p["fast"])

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
*, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }

:root {
  --bg: #0d0d14;
  --surface: #13131e;
  --surface2: #1a1a2a;
  --border: rgba(255,255,255,0.07);
  --border2: rgba(255,255,255,0.12);
  --text: #e8e8f0;
  --text2: #8888aa;
  --accent: #7c6aff;
  --accent2: #00e5ff;
  --user-bg: linear-gradient(135deg,#7c6aff,#00e5ff);
  --ai-bg: #13131e;
  --radius: 14px;
  --font: 'IBM Plex Sans Arabic', system-ui, sans-serif;
  --mono: 'JetBrains Mono', monospace;
}

html, body {
  height: 100%;
  font-family: var(--font);
  background: var(--bg);
  color: var(--text);
  overflow: hidden;
  width: 100%;
}

.app {
  display: flex;
  flex-direction: column;
  height: 100dvh;
  max-width: 1100px;
  margin: 0 auto;
  width: 100%;
  overflow: hidden;
}

header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  width: 100%;
  border-bottom: 1px solid var(--border);
  background: rgba(13,13,20,0.95);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  position: sticky;
  top: 0;
  z-index: 10;
  flex-shrink: 0;
}

.logo {
  font-size: 20px;
  font-weight: 700;
  letter-spacing: -0.5px;
  background: linear-gradient(90deg, var(--accent), var(--accent2));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.header-right { display: flex; gap: 8px; align-items: center; }

.icon-btn {
  width: 38px; height: 38px;
  display: flex; align-items: center; justify-content: center;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  color: var(--text2);
  cursor: pointer;
  font-size: 15px;
  transition: all.2s;
}
.icon-btn:hover { border-color: var(--accent); color: var(--accent); }
.icon-btn:active { transform: scale(0.95); }

.modes {
  display: flex;
  gap: 6px;
  padding: 10px 16px;
  overflow-x: auto;
  scrollbar-width: none;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
  flex-shrink: 0;
}
.modes::-webkit-scrollbar { display: none; }

.mode-btn {
  padding: 7px 16px;
  border-radius: 20px;
  border: 1px solid var(--border2);
  background: transparent;
  color: var(--text2);
  font-family: var(--font);
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  cursor: pointer;
  transition: all.2s;
}
.mode-btn.active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
  box-shadow: 0 0 16px rgba(124,106,255,0.35);
}
.mode-btn:hover:not(.active) { border-color: var(--accent); color: var(--text); }

.chat-area {
  overflow-y: auto;
  padding: 20px 16px;
  scroll-behavior: smooth;
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
  width: 100%;
}
.chat-area::-webkit-scrollbar { width: 4px; }
.chat-area::-webkit-scrollbar-track { background: transparent; }
.chat-area::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 4px; }

.welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 60%;
  gap: 12px;
  text-align: center;
  animation: fadeUp.6s ease;
  padding: 0 20px;
}
.welcome-icon {
  width: 64px; height: 64px;
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  border-radius: 20px;
  display: flex; align-items: center; justify-content: center;
  font-size: 28px;
  margin-bottom: 4px;
  box-shadow: 0 8px 32px rgba(124,106,255,0.3);
}
.welcome h2 { font-size: 22px; font-weight: 700; }
.welcome p { color: var(--text2); font-size: 14px; line-height: 1.7; max-width: 320px; }

.msg-row {
  display: flex;
  gap: 10px;
  animation: fadeUp.3s ease;
  margin-bottom: 12px;
  width: 100%;
}
.msg-row.user { flex-direction: row-reverse; }

.avatar {
  width: 34px; height: 34px; min-width: 34px;
  border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}
.avatar.ai-av { background: var(--surface2); border: 1px solid var(--border2); }
.avatar.user-av { background: linear-gradient(135deg, var(--accent), var(--accent2)); }

.bubble {
  max-width: 80%;
  padding: 12px 16px;
  border-radius: var(--radius);
  font-size: 15px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}
.bubble.user-bubble {
  background: var(--user-bg);
  color: #fff;
  border-radius: var(--radius) var(--radius) 4px var(--radius);
  box-shadow: 0 4px 16px rgba(124,106,255,0.25);
}
.bubble.ai-bubble {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius) var(--radius) 4px;
  color: var(--text);
}

.ai-bubble pre {
  background: #0a0a10;
  border: 1px solid var(--border2);
  border-radius: 8px;
  padding: 12px;
  margin: 10px 0;
  overflow-x: auto;
  font-family: var(--mono);
  font-size: 13px;
}
.ai-bubble code { font-family: var(--mono); font-size: 13px; }
.ai-bubble p { margin-bottom: 8px; }
.ai-bubble h3 { margin: 10px 0 6px; font-size: 15px; color: var(--accent2); }
.ai-bubble ul,.ai-bubble ol { padding-right: 18px; margin: 6px 0; }
.ai-bubble li { margin-bottom: 4px; }
.ai-bubble img { max-width: 100%; border-radius: 10px; margin-top: 10px; }

.bubble.typing::after {
  content: '▋';
  color: var(--accent);
  animation: blink.7s infinite;
}
@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
@keyframes fadeUp {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
</head>
<body>
<!-- HTML content continues here -->
</body>
</html>
"""
