import os
import base64
import json
import re
import time
import hashlib
from flask import Flask, request, render_template_string, jsonify, session, Response, stream_with_context
import requests
from datetime import datetime
import PyPDF2
import io

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "anas-wadi-secret-2026-ultra")

API_KEY = os.environ.get("GROQ_API_KEY")

# ─── نظام الحماية ────────────────────────────────────────────
RATE_LIMIT = {}  # ip -> [timestamps]
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
    # نظف الطلبات القديمة (أكثر من دقيقة)
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
    # حذف تعليمات النظام المخفية
    text = re.sub(r'<\|.*?\|>', '', text)
    text = re.sub(r'\[INST\].*?\[/INST\]', '', text, flags=re.DOTALL)
    return text[:MAX_MSG_LENGTH].strip()

# ─── تنسيق الإجابة (Markdown → HTML) ────────────────────────
def format_response(text):
    # العناوين
    text = re.sub(r'^### (.+)$', r'<h4>\1</h4>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    # Bold & Italic
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    # Code blocks
    text = re.sub(r'```(\w+)?\n(.*?)```', lambda m: f'<pre><code class="lang-{m.group(1) or ""}">{m.group(2).strip()}</code></pre>', text, flags=re.DOTALL)
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
    # قوائم
    def convert_list(m):
        items = re.findall(r'^[-*•] (.+)$', m.group(0), re.MULTILINE)
        return '<ul>' + ''.join(f'<li>{i}</li>' for i in items) + '</ul>'
    text = re.sub(r'(^[-*•] .+$\n?)+', convert_list, text, flags=re.MULTILINE)
    def convert_ol(m):
        items = re.findall(r'^\d+\. (.+)$', m.group(0), re.MULTILINE)
        return '<ol>' + ''.join(f'<li>{i}</li>' for i in items) + '</ol>'
    text = re.sub(r'(^\d+\. .+$\n?)+', convert_ol, text, flags=re.MULTILINE)
    # فقرات
    text = re.sub(r'\n{2,}', '</p><p>', text)
    text = f'<p>{text}</p>'
    # تنظيف
    text = text.replace('<p></p>', '').replace('<p><h', '<h').replace('</h2></p>', '</h2>')
    return text

# ─── واجهة HTML المحسّنة ─────────────────────────────────────
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
  --bg: #f7f9fc;
  --surface: rgba(255,255,255,0.85);
  --surface2: rgba(255,255,255,0.95);
  --border: rgba(0,40,120,0.1);
  --border-glow: rgba(0,120,255,0.35);
  --text: #0d1117;
  --text-dim: rgba(13,17,23,0.55);
  --accent1: #0066cc;
  --accent2: #00996b;
  --accent3: #6633cc;
  --user-grad: linear-gradient(135deg, #00996b, #0066cc);
  --glow: 0 4px 24px rgba(0,100,200,0.12);
}

* { margin: 0; padding: 0; box-sizing: border-box; }

html { scroll-behavior: smooth; }

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'Tajawal', 'Cairo', sans-serif;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  transition: background 0.4s, color 0.4s;
  overflow-x: hidden;
}

/* ─── خلفية متحركة ─── */
body::before {
  content: '';
  position: fixed;
  inset: 0;
  background:
    radial-gradient(ellipse 80% 60% at 20% 10%, rgba(0,210,255,0.06) 0%, transparent 60%),
    radial-gradient(ellipse 60% 50% at 80% 80%, rgba(124,77,255,0.06) 0%, transparent 60%),
    radial-gradient(ellipse 50% 40% at 50% 50%, rgba(0,255,148,0.03) 0%, transparent 70%);
  pointer-events: none;
  z-index: 0;
  animation: bgPulse 8s ease-in-out infinite alternate;
}
[data-theme="light"] body::before {
  background:
    radial-gradient(ellipse 80% 60% at 20% 10%, rgba(0,120,255,0.05) 0%, transparent 60%),
    radial-gradient(ellipse 60% 50% at 80% 80%, rgba(100,60,200,0.04) 0%, transparent 60%),
    radial-gradient(ellipse 50% 40% at 50% 50%, rgba(0,180,120,0.03) 0%, transparent 70%);
}
@keyframes bgPulse {
  from { opacity: 0.7; }
  to { opacity: 1; }
}

/* ─── Stars ─── */
.stars {
  position: fixed; inset: 0; pointer-events: none; z-index: 0; overflow: hidden;
}
.star {
  position: absolute; border-radius: 50%; background: white;
  animation: twinkle var(--d, 3s) ease-in-out infinite;
}
@keyframes twinkle {
  0%,100% { opacity: 0; transform: scale(0.5); }
  50% { opacity: var(--op, 0.6); transform: scale(1); }
}

/* ─── Header ─── */
.header {
  position: sticky; top: 0; z-index: 100;
  background: rgba(5,5,16,0.85);
  backdrop-filter: blur(20px) saturate(1.5);
  -webkit-backdrop-filter: blur(20px);
  border-bottom: 1px solid var(--border);
  padding: 12px 20px;
  display: flex; justify-content: space-between; align-items: center;
  transition: 0.3s;
}
[data-theme="light"] .header { background: rgba(247,249,252,0.92); border-bottom: 1px solid rgba(0,60,180,0.1); box-shadow: 0 1px 12px rgba(0,60,180,0.07); }

.logo {
  font-size: 22px; font-weight: 900; letter-spacing: -0.5px;
  background: linear-gradient(90deg, var(--accent2), var(--accent1), var(--accent3));
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  background-clip: text;
  animation: logoShimmer 4s linear infinite;
  background-size: 200%;
}
@keyframes logoShimmer {
  0% { background-position: 0% 50%; }
  100% { background-position: 200% 50%; }
}

.header-actions { display: flex; gap: 8px; align-items: center; }

.icon-btn {
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text);
  width: 38px; height: 38px;
  border-radius: 10px; cursor: pointer;
  font-size: 15px; transition: all 0.25s;
  display: flex; align-items: center; justify-content: center;
  position: relative; overflow: hidden;
}
.icon-btn::before {
  content: ''; position: absolute; inset: 0;
  background: linear-gradient(135deg, var(--accent1), var(--accent2));
  opacity: 0; transition: opacity 0.25s;
}
.icon-btn:hover { border-color: var(--border-glow); transform: translateY(-1px); box-shadow: var(--glow); }
.icon-btn:hover::before { opacity: 0.1; }
.icon-btn:active { transform: translateY(0) scale(0.96); }

/* ─── Sidebar ─── */
.sidebar {
  position: fixed; right: -290px; top: 0;
  width: 270px; height: 100vh;
  background: rgba(5,5,16,0.97);
  backdrop-filter: blur(30px);
  border-left: 1px solid var(--border);
  transition: right 0.35s cubic-bezier(0.4,0,0.2,1);
  z-index: 200; padding: 20px; overflow-y: auto;
  display: flex; flex-direction: column; gap: 12px;
}
[data-theme="light"] .sidebar { background: rgba(247,249,252,0.98); border-left: 1px solid rgba(0,60,180,0.1); box-shadow: -4px 0 24px rgba(0,60,180,0.06); }
.sidebar.open { right: 0; }
.sidebar-overlay {
  display: none; position: fixed; inset: 0; z-index: 199;
  background: rgba(0,0,0,0.5); backdrop-filter: blur(4px);
}
.sidebar-overlay.open { display: block; }

.sidebar h3 { font-size: 16px; font-weight: 700; color: var(--text-dim); }

.new-chat-btn {
  background: linear-gradient(135deg, var(--accent2), var(--accent1));
  border: none; border-radius: 12px; padding: 10px 15px;
  font-family: 'Tajawal',sans-serif; font-size: 14px; font-weight: 700;
  color: #000; cursor: pointer; transition: all 0.25s;
  display: flex; align-items: center; gap: 8px;
}
.new-chat-btn:hover { transform: scale(1.02); box-shadow: 0 4px 20px rgba(0,210,255,0.4); }

.chat-item {
  background: var(--surface); border: 1px solid var(--border);
  padding: 10px 14px; border-radius: 10px; cursor: pointer;
  font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  transition: all 0.2s; color: var(--text-dim);
}
.chat-item:hover { border-color: var(--accent1); color: var(--text); transform: translateX(-3px); }
.chat-item.active { border-color: var(--accent1); color: var(--text); background: rgba(0,210,255,0.08); }

/* ─── Modes ─── */
.modes {
  display: flex; gap: 8px; padding: 10px 16px;
  overflow-x: auto; position: relative; z-index: 1;
  scrollbar-width: none;
}
.modes::-webkit-scrollbar { display: none; }

.mode-btn {
  background: var(--surface); border: 1px solid var(--border);
  color: var(--text-dim); padding: 8px 16px; border-radius: 20px;
  font-size: 13px; font-weight: 600; white-space: nowrap; cursor: pointer;
  transition: all 0.25s; font-family: 'Tajawal',sans-serif;
}
.mode-btn:hover { border-color: var(--border-glow); color: var(--text); }
.mode-btn.active {
  background: linear-gradient(135deg, var(--accent2), var(--accent1));
  color: #000; border-color: transparent;
  box-shadow: 0 4px 15px rgba(0,210,255,0.3);
}

/* ─── Chat Container ─── */
.chat-container {
  flex: 1; padding: 20px 16px 10px;
  max-width: 780px; width: 100%; margin: 0 auto;
  position: relative; z-index: 1;
}

/* ─── Welcome ─── */
.welcome {
  text-align: center; padding: 50px 20px;
  animation: fadeUp 0.6s ease both;
}
@keyframes fadeUp {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}
.welcome-icon {
  font-size: 56px; margin-bottom: 20px;
  animation: floatIcon 3s ease-in-out infinite;
}
@keyframes floatIcon {
  0%,100% { transform: translateY(0); }
  50% { transform: translateY(-10px); }
}
.welcome h2 { font-size: 26px; font-weight: 900; margin-bottom: 12px; }
.welcome p { color: var(--text-dim); line-height: 1.9; font-size: 15px; }
.welcome-cards { display: flex; gap: 12px; margin-top: 30px; flex-wrap: wrap; justify-content: center; }
.welcome-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 14px; padding: 14px 18px; cursor: pointer;
  transition: all 0.25s; text-align: right; max-width: 200px;
  font-size: 13px;
}
.welcome-card:hover { border-color: var(--border-glow); transform: translateY(-3px); box-shadow: var(--glow); }
.welcome-card .card-icon { font-size: 22px; margin-bottom: 6px; }
.welcome-card .card-title { font-weight: 700; margin-bottom: 4px; }
.welcome-card .card-desc { color: var(--text-dim); font-size: 12px; }

/* ─── Messages ─── */
.message {
  margin: 12px 0;
  animation: msgSlide 0.35s cubic-bezier(0.4,0,0.2,1) both;
}
@keyframes msgSlide {
  from { opacity: 0; transform: translateY(15px) scale(0.98); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}

.user-msg {
  background: var(--user-grad);
  color: #000; font-weight: 600;
  padding: 13px 18px; border-radius: 20px 20px 6px 20px;
  margin-right: auto; max-width: 78%;
  box-shadow: 0 6px 25px rgba(0,255,148,0.25);
  font-size: 15px; line-height: 1.7;
  word-break: break-word;
}

.ai-msg {
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 16px 20px; border-radius: 20px 20px 20px 6px;
  max-width: 85%; font-size: 15px; line-height: 1.9;
  position: relative; word-break: break-word;
  transition: border-color 0.3s;
}
.ai-msg:hover { border-color: var(--border-glow); }

/* تنسيقات الـ Markdown داخل رسائل AI */
.ai-msg h2 { font-size: 18px; font-weight: 800; margin: 12px 0 6px; color: var(--accent1); }
.ai-msg h3 { font-size: 16px; font-weight: 700; margin: 10px 0 5px; color: var(--accent2); }
.ai-msg h4 { font-size: 15px; font-weight: 700; margin: 8px 0 4px; color: var(--accent3); }
.ai-msg strong { font-weight: 800; color: var(--accent1); }
.ai-msg em { font-style: italic; color: var(--accent2); }
.ai-msg p { margin: 6px 0; }
.ai-msg ul, .ai-msg ol { padding-right: 24px; margin: 8px 0; }
.ai-msg li { margin: 5px 0; line-height: 1.8; }
.ai-msg code {
  background: rgba(0,210,255,0.1); border: 1px solid rgba(0,210,255,0.2);
  border-radius: 5px; padding: 1px 7px; font-size: 13px; font-family: monospace;
}
.ai-msg pre {
  background: rgba(0,0,0,0.4); border: 1px solid var(--border);
  border-radius: 10px; padding: 16px; margin: 10px 0;
  overflow-x: auto; position: relative;
}
.ai-msg pre code { background: none; border: none; padding: 0; font-size: 13px; }

/* مؤشر الكتابة */
.typing-indicator {
  display: inline-flex; gap: 5px; align-items: center; padding: 4px 0;
}
.typing-indicator span {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--accent1);
  animation: typing 1.2s ease-in-out infinite;
}
.typing-indicator span:nth-child(2) { animation-delay: 0.2s; background: var(--accent2); }
.typing-indicator span:nth-child(3) { animation-delay: 0.4s; background: var(--accent3); }
@keyframes typing {
  0%,60%,100% { transform: translateY(0); opacity: 0.4; }
  30% { transform: translateY(-8px); opacity: 1; }
}

.file-badge {
  display: inline-flex; align-items: center; gap: 6px;
  background: rgba(0,210,255,0.1); border: 1px solid rgba(0,210,255,0.3);
  padding: 5px 12px; border-radius: 8px; font-size: 12px;
  margin-bottom: 6px; color: var(--accent1);
}

.msg-actions {
  display: flex; gap: 6px; margin-top: 8px;
  opacity: 0; transition: opacity 0.2s;
}
.message:hover .msg-actions { opacity: 1; }

.msg-btn {
  background: var(--surface); border: 1px solid var(--border);
  color: var(--text-dim); padding: 5px 12px; border-radius: 8px;
  font-size: 12px; cursor: pointer; font-family: 'Tajawal',sans-serif;
  transition: all 0.2s;
}
.msg-btn:hover { border-color: var(--accent1); color: var(--text); }

.generated-img {
  max-width: 100%; border-radius: 12px; margin-top: 12px;
  box-shadow: 0 8px 30px rgba(0,0,0,0.4);
  animation: imgReveal 0.5s ease both;
}
@keyframes imgReveal {
  from { opacity: 0; transform: scale(0.95); }
  to { opacity: 1; transform: scale(1); }
}

/* ─── Input Area ─── */
.input-area {
  position: sticky; bottom: 0; z-index: 10;
  background: rgba(5,5,16,0.9);
  backdrop-filter: blur(20px);
  border-top: 1px solid var(--border);
  padding: 12px 16px 16px;
}
[data-theme="light"] .input-area { background: rgba(247,249,252,0.95); border-top: 1px solid rgba(0,60,180,0.1); box-shadow: 0 -2px 16px rgba(0,60,180,0.05); }

.templates {
  display: flex; gap: 8px; margin-bottom: 10px;
  overflow-x: auto; padding-bottom: 4px;
  scrollbar-width: none;
}
.templates::-webkit-scrollbar { display: none; }

.template-btn {
  background: var(--surface); border: 1px solid var(--border);
  color: var(--text-dim); padding: 6px 14px; border-radius: 20px;
  font-size: 12px; white-space: nowrap; cursor: pointer;
  font-family: 'Tajawal',sans-serif; transition: all 0.2s;
}
.template-btn:hover { border-color: var(--border-glow); color: var(--text); }

.input-wrapper { max-width: 780px; margin: 0 auto; display: flex; gap: 8px; align-items: flex-end; }

.textarea-wrap { flex: 1; position: relative; }

textarea {
  width: 100%;
  background: var(--surface2); border: 1px solid var(--border);
  color: var(--text); border-radius: 16px; padding: 13px 16px;
  font-size: 15px; font-family: 'Tajawal',sans-serif;
  resize: none; height: 52px; max-height: 130px;
  transition: border-color 0.25s, box-shadow 0.25s;
  line-height: 1.5;
}
textarea:focus {
  outline: none; border-color: var(--accent1);
  box-shadow: 0 0 0 3px rgba(0,210,255,0.1);
}
textarea::placeholder { color: var(--text-dim); }

#fileInput { display: none; }

.send-btn {
  background: linear-gradient(135deg, var(--accent2), var(--accent1));
  border: none; border-radius: 14px; width: 52px; height: 52px;
  font-size: 18px; color: #000; cursor: pointer;
  transition: all 0.25s; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
}
.send-btn:hover:not(:disabled) { transform: scale(1.08); box-shadow: 0 6px 20px rgba(0,210,255,0.4); }
.send-btn:active:not(:disabled) { transform: scale(0.95); }
.send-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.file-preview {
  max-width: 780px; margin: 0 auto 8px;
  display: flex; align-items: center; gap: 8px;
  background: rgba(0,210,255,0.08); border: 1px solid rgba(0,210,255,0.2);
  padding: 8px 14px; border-radius: 10px; font-size: 13px;
}
.file-preview.hidden { display: none; }

/* ─── Modal ─── */
.modal {
  display: none; position: fixed; inset: 0; z-index: 300;
  justify-content: center; align-items: center;
  background: rgba(0,0,0,0.7); backdrop-filter: blur(8px);
}
.modal.open { display: flex; }
.modal-content {
  background: var(--bg); border: 1px solid var(--border);
  padding: 32px; border-radius: 20px;
  max-width: 460px; width: 90%; text-align: center;
  animation: modalPop 0.3s cubic-bezier(0.4,0,0.2,1);
}
@keyframes modalPop {
  from { opacity: 0; transform: scale(0.9) translateY(20px); }
  to { opacity: 1; transform: scale(1) translateY(0); }
}
.support-btn {
  display: inline-flex; align-items: center; gap: 8px;
  background: linear-gradient(135deg, #ff6b6b, #feca57);
  color: #000; border: none; padding: 12px 28px;
  border-radius: 14px; font-weight: 800; cursor: pointer;
  font-family: 'Tajawal',sans-serif; font-size: 15px;
  text-decoration: none; margin-top: 16px; transition: all 0.25s;
}
.support-btn:hover { transform: scale(1.05); box-shadow: 0 6px 20px rgba(254,202,87,0.4); }

/* ─── Toast ─── */
.toast {
  position: fixed; bottom: 90px; left: 50%;
  transform: translateX(-50%) translateY(20px);
  background: rgba(20,20,40,0.95); color: var(--text);
  border: 1px solid var(--border); padding: 10px 22px;
  border-radius: 12px; z-index: 999; font-size: 13px;
  opacity: 0; transition: all 0.3s; pointer-events: none;
  backdrop-filter: blur(10px);
}
.toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
.toast.error { border-color: rgba(255,80,80,0.5); color: #ff8080; }
.toast.success { border-color: rgba(0,255,148,0.5); color: var(--accent2); }

/* ─── Scrollbar ─── */
::-webkit-scrollbar { width: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 10px; }
::-webkit-scrollbar-thumb:hover { background: var(--accent1); }

/* ─── Light Mode Overrides ─── */
[data-theme="light"] .ai-msg {
  background: #ffffff;
  border-color: rgba(0,60,180,0.12);
  box-shadow: 0 2px 12px rgba(0,60,180,0.06);
}
[data-theme="light"] .ai-msg:hover { border-color: rgba(0,100,220,0.3); box-shadow: 0 4px 20px rgba(0,100,220,0.1); }
[data-theme="light"] .ai-msg strong { color: #0055bb; }
[data-theme="light"] .ai-msg em { color: #007a55; }
[data-theme="light"] .ai-msg h2 { color: #0055bb; }
[data-theme="light"] .ai-msg h3 { color: #007a55; }
[data-theme="light"] .ai-msg h4 { color: #5522aa; }
[data-theme="light"] .ai-msg code { background: rgba(0,100,200,0.07); border-color: rgba(0,100,200,0.18); color: #0044aa; }
[data-theme="light"] .ai-msg pre { background: #f3f5fb; border-color: rgba(0,60,180,0.12); }
[data-theme="light"] .welcome-card { background: #fff; border-color: rgba(0,60,180,0.1); box-shadow: 0 2px 10px rgba(0,60,180,0.05); }
[data-theme="light"] .welcome-card:hover { border-color: rgba(0,100,220,0.3); box-shadow: 0 6px 24px rgba(0,100,220,0.12); }
[data-theme="light"] .mode-btn { background: rgba(255,255,255,0.9); border-color: rgba(0,60,180,0.12); }
[data-theme="light"] .icon-btn { background: rgba(255,255,255,0.9); border-color: rgba(0,60,180,0.12); }
[data-theme="light"] .icon-btn:hover { background: rgba(0,100,220,0.06); border-color: rgba(0,100,220,0.3); }
[data-theme="light"] textarea { background: #ffffff; border-color: rgba(0,60,180,0.14); color: #0d1117; }
[data-theme="light"] textarea:focus { border-color: #0066cc; box-shadow: 0 0 0 3px rgba(0,100,200,0.1); }
[data-theme="light"] .template-btn { background: rgba(255,255,255,0.9); border-color: rgba(0,60,180,0.12); }
[data-theme="light"] .template-btn:hover { border-color: rgba(0,100,220,0.3); color: #0d1117; }
[data-theme="light"] .chat-item { background: rgba(255,255,255,0.7); }
[data-theme="light"] .chat-item:hover { border-color: #0066cc; color: #0d1117; }
[data-theme="light"] .chat-item.active { background: rgba(0,100,220,0.07); border-color: #0066cc; }
[data-theme="light"] .msg-btn { background: rgba(255,255,255,0.9); border-color: rgba(0,60,180,0.12); }
[data-theme="light"] .msg-btn:hover { border-color: #0066cc; color: #0d1117; }
[data-theme="light"] .file-badge { background: rgba(0,100,200,0.07); border-color: rgba(0,100,200,0.2); color: #0055bb; }
[data-theme="light"] .modal-content { background: #f7f9fc; border-color: rgba(0,60,180,0.12); }
[data-theme="light"] .toast { background: rgba(247,249,252,0.97); color: #0d1117; border-color: rgba(0,60,180,0.15); }
[data-theme="light"] .stars { display: none; }
[data-theme="light"] .typing-indicator span { background: var(--accent1); }
[data-theme="light"] .typing-indicator span:nth-child(2) { background: var(--accent2); }
[data-theme="light"] .typing-indicator span:nth-child(3) { background: var(--accent3); }

/* ─── Responsive ─── */
@media (max-width: 480px) {
  .ai-msg, .user-msg { max-width: 95%; }
  .welcome h2 { font-size: 20px; }
  .welcome-cards { gap: 8px; }
  .welcome-card { max-width: 160px; }
}
</style>
</head>
<body>

<!-- Stars -->
<div class="stars" id="stars"></div>

<!-- Sidebar Overlay -->
<div class="sidebar-overlay" id="sidebarOverlay" onclick="closeSidebar()"></div>

<!-- Sidebar -->
<div class="sidebar" id="sidebar">
  <h3>💬 محادثاتي</h3>
  <button class="new-chat-btn" onclick="newChat()">
    <i class="fa-solid fa-plus"></i> محادثة جديدة
  </button>
  <div id="chatList"></div>
</div>

<!-- Header -->
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

<!-- Modes -->
<div class="modes">
  <button class="mode-btn active" data-mode="fast" onclick="setMode('fast')">⚡ سريع</button>
  <button class="mode-btn" data-mode="thinker" onclick="setMode('thinker')">🧠 مفكر</button>
  <button class="mode-btn" data-mode="funny" onclick="setMode('funny')">😂 فكاهي</button>
  <button class="mode-btn" data-mode="creative" onclick="setMode('creative')">🎨 مبدع</button>
  <button class="mode-btn" data-mode="coder" onclick="setMode('coder')">💻 مبرمج</button>
  <button class="mode-btn" data-mode="writer" onclick="setMode('writer')">✍️ كاتب</button>
</div>

<!-- Chat -->
<div class="chat-container" id="chatContainer">
  <div class="welcome" id="welcome">
    <div class="welcome-icon">🌊</div>
    <h2>مرحباً بك في <span style="background:linear-gradient(90deg,#00ff94,#00d2ff);-webkit-background-clip:text;-webkit-text-fill-color:transparent">Anas Wadi</span></h2>
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

<!-- Input Area -->
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

<!-- Toast -->
<div class="toast" id="toast"></div>

<!-- Support Modal -->
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

<script>
// ─── State ──────────────────────────────────────────────────
let currentChatId = localStorage.getItem('currentChatId');
let chats = {};
let currentFile = null;
let currentMode = localStorage.getItem('mode') || 'fast';
let isSending = false;

// ─── Init ────────────────────────────────────────────────────
function init() {
  generateStars();
  try { chats = JSON.parse(localStorage.getItem('chats') || '{}'); } catch(e) { chats = {}; }
  if (!currentChatId || !chats[currentChatId]) {
    currentChatId = Date.now().toString();
    chats[currentChatId] = [];
    localStorage.setItem('currentChatId', currentChatId);
    saveChats();
  }
  loadChats();
  renderChat();
  setMode(currentMode, false);
  loadTheme();
}

// ─── Stars ───────────────────────────────────────────────────
function generateStars() {
  const c = document.getElementById('stars');
  for (let i = 0; i < 60; i++) {
    const s = document.createElement('div');
    s.className = 'star';
    const size = Math.random() * 2.5 + 0.5;
    s.style.cssText = `
      width:${size}px; height:${size}px;
      left:${Math.random()*100}%;
      top:${Math.random()*100}%;
      --d:${(Math.random()*4+2).toFixed(1)}s;
      --op:${(Math.random()*0.5+0.2).toFixed(2)};
      animation-delay:${(Math.random()*5).toFixed(1)}s;
    `;
    c.appendChild(s);
  }
}

// ─── Toast ───────────────────────────────────────────────────
function showToast(msg, type = '') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + type;
  setTimeout(() => t.className = 'toast', 3000);
}

// ─── Mode ────────────────────────────────────────────────────
function setMode(m, save = true) {
  currentMode = m;
  if (save) localStorage.setItem('mode', m);
  document.querySelectorAll('.mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === m));
}

// ─── Chats ───────────────────────────────────────────────────
function loadChats() {
  const l = document.getElementById('chatList');
  l.innerHTML = '';
  Object.keys(chats).reverse().forEach(id => {
    const c = chats[id];
    const t = c[0]?.user || 'محادثة جديدة';
    const d = document.createElement('div');
    d.className = 'chat-item' + (id === currentChatId ? ' active' : '');
    d.textContent = t.substring(0, 28);
    d.onclick = () => switchChat(id);
    l.appendChild(d);
  });
}

function switchChat(id) {
  currentChatId = id;
  localStorage.setItem('currentChatId', id);
  renderChat();
  loadChats();
  closeSidebar();
}

function newChat() {
  currentChatId = Date.now().toString();
  chats[currentChatId] = [];
  localStorage.setItem('currentChatId', currentChatId);
  saveChats();
  renderChat();
  loadChats();
  closeSidebar();
}

// ─── Render ──────────────────────────────────────────────────
function renderChat() {
  const c = document.getElementById('chatContainer');
  const h = chats[currentChatId] || [];
  if (h.length === 0) {
    c.innerHTML = `<div class="welcome" id="welcome">
      <div class="welcome-icon">🌊</div>
      <h2>مرحباً بك في <span style="background:linear-gradient(90deg,#00ff94,#00d2ff);-webkit-background-clip:text;-webkit-text-fill-color:transparent">Anas Wadi</span></h2>
      <p>تم تطوير هذا الذكاء الاصطناعي بيد المهندس <strong>Anas Wadi</strong> من ليبيا 🇱🇾</p>
      <p style="margin-top:6px">كيف يمكنني مساعدتك اليوم؟</p>
      <div class="welcome-cards">
        <div class="welcome-card" onclick="useTemplate('ارسم صورة: ')"><div class="card-icon">🎨</div><div class="card-title">رسم صورة</div><div class="card-desc">توليد صور فائقة الجودة</div></div>
        <div class="welcome-card" onclick="useTemplate('اشرحلي ')"><div class="card-icon">💡</div><div class="card-title">شرح وتحليل</div><div class="card-desc">أشرح أي موضوع تريده</div></div>
        <div class="welcome-card" onclick="useTemplate('اكتبلي كود ')"><div class="card-icon">💻</div><div class="card-title">برمجة</div><div class="card-desc">أكتب ويصلح الكود</div></div>
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
      : m.ai;
    
    let imgHtml = '';
    if (m.imageUrl) imgHtml = `<br><img class="generated-img" src="${escHtml(m.imageUrl)}" alt="صورة مولدة" loading="lazy">`;
    
    c.innerHTML += `
      <div class="message">
        <div class="user-msg">${userContent}</div>
      </div>
      <div class="message">
        <div class="ai-msg">${aiContent}${imgHtml}</div>
        ${isTyping ? '' : `<div class="msg-actions">
          <button class="msg-btn" onclick="copyText(${JSON.stringify(m.rawAi || m.ai)})"><i class="fa-solid fa-copy"></i> نسخ</button>
          <button class="msg-btn" onclick="regenerate(${i})"><i class="fa-solid fa-rotate"></i> إعادة</button>
        </div>`}
      </div>`;
  });
  requestAnimationFrame(() => window.scrollTo(0, document.body.scrollHeight));
}

function escHtml(t) {
  if (!t) return '';
  const d = document.createElement('div');
  d.textContent = t;
  return d.innerHTML;
}

// ─── Send ────────────────────────────────────────────────────
async function sendMessage() {
  if (isSending) return;
  const inp = document.getElementById('messageInput');
  const t = inp.value.trim();
  if (!t && !currentFile) return;

  isSending = true;
  document.getElementById('sendBtn').disabled = true;
  inp.value = '';
  inp.style.height = '52px';

  const c = chats[currentChatId];
  const fName = currentFile ? currentFile.name : null;
  c.push({ user: t || 'حلل الملف', ai: '__typing__', fileName: fName });
  saveChats(); renderChat();

  const fd = new FormData();
  fd.append('message', t);
  fd.append('mode', currentMode);
  fd.append('history', JSON.stringify(c.slice(0, -1)));

  // لو فيه ملف → استخدم الـ endpoint العادي (streaming ما يدعم multipart مع الصور)
  if (currentFile) {
    fd.append('file', currentFile);
    try {
      const r = await fetch('/api/chat', { method: 'POST', body: fd });
      if (!r.ok) throw new Error('خطأ في الخادم');
      const d = await r.json();
      if (d.error) { showToast(d.error, 'error'); c.pop(); }
      else {
        c[c.length - 1].ai = d.response;
        c[c.length - 1].rawAi = d.rawResponse || d.response;
        if (d.imageUrl) c[c.length - 1].imageUrl = d.imageUrl;
      }
    } catch (err) {
      c[c.length - 1].ai = '⚠️ صار خطأ: ' + err.message;
      showToast('تعذر الإرسال', 'error');
    } finally {
      currentFile = null;
      document.getElementById('filePreview').classList.add('hidden');
      document.getElementById('fileInput').value = '';
      saveChats(); renderChat();
      isSending = false;
      document.getElementById('sendBtn').disabled = false;
    }
    return;
  }

  // بدون ملف → Streaming ✨
  let rawText = '';
  try {
    const r = await fetch('/api/chat/stream', { method: 'POST', body: fd });
    if (!r.ok) throw new Error('خطأ في الخادم');

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    c[c.length - 1].ai = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop(); // الجزء غير المكتمل

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6);
        if (data === '[DONE]') break;
        try {
          const parsed = JSON.parse(data);
          if (parsed.error) { showToast(parsed.error, 'error'); break; }
          if (parsed.token) {
            rawText += parsed.token;
            c[c.length - 1].rawAi = rawText;
            // عرض مباشر بدون format (أسرع)
            c[c.length - 1].ai = rawText;
            // تحديث آخر رسالة مباشرة في الـ DOM بدون re-render كامل
            const msgs = document.querySelectorAll('.ai-msg');
            if (msgs.length > 0) {
              msgs[msgs.length - 1].textContent = rawText;
              window.scrollTo(0, document.body.scrollHeight);
            }
          }
        } catch(e) { continue; }
      }
    }

    // بعد اكتمال الـ stream → format الرد النهائي
    if (rawText) {
      c[c.length - 1].ai = format_response_js(rawText);
      c[c.length - 1].rawAi = rawText;
    } else if (c[c.length-1].ai === '') {
      c.pop();
    }

  } catch (err) {
    c[c.length - 1].ai = '⚠️ صار خطأ: ' + err.message;
    showToast('تعذر الإرسال', 'error');
  } finally {
    saveChats(); renderChat();
    isSending = false;
    document.getElementById('sendBtn').disabled = false;
  }
}

// Markdown → HTML (نسخة JS مطابقة لـ Python format_response)
function format_response_js(text) {
  // عناوين
  text = text.replace(/^### (.+)$/gm, '<h4>$1</h4>');
  text = text.replace(/^## (.+)$/gm, '<h3>$1</h3>');
  text = text.replace(/^# (.+)$/gm, '<h2>$1</h2>');
  // Bold & Italic
  text = text.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
  text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');
  // Code blocks
  text = text.replace(/```(\w+)?\n([\s\S]*?)```/g, (_, lang, code) =>
    `<pre><code class="lang-${lang||''}">${code.trim()}</code></pre>`);
  text = text.replace(/`(.+?)`/g, '<code>$1</code>');
  // قوائم
  text = text.replace(/(^[-*•] .+$\n?)+/gm, match => {
    const items = [...match.matchAll(/^[-*•] (.+)$/gm)].map(m => `<li>${m[1]}</li>`);
    return `<ul>${items.join('')}</ul>`;
  });
  text = text.replace(/(^\d+\. .+$\n?)+/gm, match => {
    const items = [...match.matchAll(/^\d+\. (.+)$/gm)].map(m => `<li>${m[1]}</li>`);
    return `<ol>${items.join('')}</ol>`;
  });
  // فقرات
  text = text.replace(/\n{2,}/g, '</p><p>');
  text = `<p>${text}</p>`;
  text = text.replace(/<p><\/p>/g, '').replace(/<p><h/g, '<h');
  return text;
}

async function regenerate(i) {
  if (isSending) return;
  isSending = true;
  const c = chats[currentChatId];
  const u = c[i].user;
  c[i].ai = '__typing__';
  renderChat();
  const fd = new FormData();
  fd.append('message', u);
  fd.append('mode', currentMode);
  fd.append('history', JSON.stringify(c.slice(0, i)));

  let rawText = '';
  try {
    const r = await fetch('/api/chat/stream', { method: 'POST', body: fd });
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    c[i].ai = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6);
        if (data === '[DONE]') break;
        try {
          const parsed = JSON.parse(data);
          if (parsed.token) {
            rawText += parsed.token;
            c[i].ai = rawText;
            const msgs = document.querySelectorAll('.ai-msg');
            // الرسالة i تكون في موضع i*2+1 في الـ DOM
            const targetMsg = msgs[i];
            if (targetMsg) { targetMsg.textContent = rawText; window.scrollTo(0, document.body.scrollHeight); }
          }
        } catch(e) { continue; }
      }
    }
    if (rawText) {
      c[i].ai = format_response_js(rawText);
      c[i].rawAi = rawText;
      delete c[i].imageUrl;
    }
  } catch (err) {
    c[i].ai = '⚠️ صار خطأ: ' + err.message;
  } finally {
    saveChats(); renderChat(); isSending = false;
  }
}

// ─── File ────────────────────────────────────────────────────
function handleFile(inp) {
  if (inp.files[0]) {
    currentFile = inp.files[0];
    const p = document.getElementById('filePreview');
    document.getElementById('fileName').textContent = currentFile.name;
    p.classList.remove('hidden');
  }
}
function removeFile() {
  currentFile = null;
  document.getElementById('fileInput').value = '';
  document.getElementById('filePreview').classList.add('hidden');
}

// ─── UI Helpers ──────────────────────────────────────────────
function useTemplate(t) {
  const inp = document.getElementById('messageInput');
  inp.value = t;
  inp.focus();
  autoResize(inp);
}

function copyText(t) {
  // نسخ النص الخام بدون HTML
  const tmp = document.createElement('div');
  tmp.innerHTML = t;
  navigator.clipboard.writeText(tmp.textContent || t);
  showToast('✅ تم النسخ', 'success');
}

function saveChats() {
  try { localStorage.setItem('chats', JSON.stringify(chats)); }
  catch(e) { showToast('⚠️ الذاكرة ممتلئة! احذف محادثات', 'error'); }
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
  h.dataset.theme = n;
  localStorage.setItem('theme', n);
  document.getElementById('themeBtn').innerHTML =
    n === 'dark' ? '<i class="fa-solid fa-moon"></i>' : '<i class="fa-solid fa-sun"></i>';
}
function loadTheme() {
  const t = localStorage.getItem('theme') || 'dark';
  document.documentElement.dataset.theme = t;
  document.getElementById('themeBtn').innerHTML =
    t === 'dark' ? '<i class="fa-solid fa-moon"></i>' : '<i class="fa-solid fa-sun"></i>';
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

init();
</script>
</body>
</html>
"""

# ─── System Prompts المحسّنة ──────────────────────────────────
IDENTITY_TRIGGERS = ['من انت', 'من أنت', 'عرف بنفسك', 'من تكون', 'ما اسمك', 'شن اسمك', 'who are you', 'اسمك ايش']

MODE_PROMPTS = {
    'fast': """أنت Wadi، مساعد ذكاء اصطناعي متطور طوّره المهندس Anas Wadi من ليبيا 🇱🇾.
أسلوبك: واضح، مباشر، دقيق. أجب بشكل منظم باستخدام عناوين وقوائم عند الحاجة.
القواعد:
- استخدم **Bold** للمصطلحات المهمة
- قسّم الإجابة الطويلة لفقرات واضحة
- لا تطل بدون فائدة""",

    'thinker': """أنت Wadi، مفكر عميق وخبير تحليلي طوّره Anas Wadi.
أسلوبك: تحليلي ومنهجي. فكّر بصوت عالٍ خطوة بخطوة.
القواعد:
- ابدأ بتحليل المشكلة
- قدّم الحلول مرتبة من الأقوى للأضعف
- أضف **الخلاصة** في النهاية دائماً
- استخدم ## للعناوين الرئيسية""",

    'funny': """أنت Wadi، مساعد ذكي وظريف طوّره Anas Wadi 😄
أسلوبك: خفيف الدم، تضيف نكتة أو تعليق مضحك، لكن المعلومة صحيحة دائماً.
القواعد:
- ابدأ برد فكاهي ثم أعط الجواب الحقيقي
- استخدم الإيموجي بذكاء 😂🎯
- لا تبالغ في الفكاهة على حساب الدقة""",

    'creative': """أنت Wadi، مساعد مبدع وفنان طوّره Anas Wadi 🎨
أسلوبك: خيالي، ملوّن، مبتكر. استخدم الاستعارات والتشبيهات.
القواعد:
- أجب بأسلوب أدبي راقٍ
- استخدم الصور الذهنية والتشبيهات
- لطلبات الرسم: ترجم الوصف لإنجليزي دقيق وشاعري""",

    'coder': """أنت Wadi، خبير برمجة متقدم طوّره Anas Wadi 💻
أسلوبك: دقيق، احترافي، تشرح الكود بوضوح.
القواعد:
- اكتب الكود داخل ```language
- اشرح كل جزء مهم
- أضف تعليقات للكود
- نبّه على الأخطاء الشائعة""",

    'writer': """أنت Wadi، كاتب محترف ومحرر لغوي طوّره Anas Wadi ✍️
أسلوبك: أدبي راقٍ، لغة سليمة، تنسيق ممتاز.
القواعد:
- اهتم بالأسلوب والبلاغة
- صحّح الأخطاء اللغوية
- استخدم علامات الترقيم بشكل صحيح
- قدّم نصوصاً متماسكة ومترابطة"""
}

def get_system_prompt(mode, user_message):
    if any(q in user_message.lower() for q in IDENTITY_TRIGGERS):
        return "أجب بالضبط: أنا Wadi، مساعد ذكاء اصطناعي طوّره المهندس Anas Wadi من ليبيا 🇱🇾. لا تضف أي معلومة أخرى."
    return MODE_PROMPTS.get(mode, MODE_PROMPTS['fast'])

# ─── Image Generation (متعدد المحركات) ───────────────────────
def generate_image(prompt):
    """توليد صورة بجودة عالية مع fallback"""
    clean_prompt = prompt.strip()
    
    # ترجمة سريعة للعربي
    translate_keywords = {
        'شجرة': 'tree', 'بحر': 'ocean', 'جبل': 'mountain', 'قمر': 'moon',
        'شمس': 'sun', 'مدينة': 'city', 'صحراء': 'desert', 'قطة': 'cat',
        'كلب': 'dog', 'فرس': 'horse', 'امرأة': 'woman', 'رجل': 'man'
    }
    
    # المحرك الأساسي - Pollinations مع معاملات محسّنة
    encoded = requests.utils.quote(clean_prompt)
    primary_url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1024&height=1024&model=flux&enhance=true&nologo=true"
        f"&seed={hash(clean_prompt) % 99999}"
    )
    # احتياطي بحجم مختلف
    fallback_url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1024&height=768&nologo=true"
    )
    return primary_url, fallback_url

# ─── Routes ──────────────────────────────────────────────────
@app.route("/")
def home():
    return render_template_string(HTML)

@app.route("/api/chat/stream", methods=["POST"])
def chat_stream():
    """Streaming endpoint - يرسل الرد كلمة بكلمة"""
    if not API_KEY:
        return jsonify({"error": "مفتاح API غير مضاف"})

    ip = get_client_ip()
    if is_rate_limited(ip):
        return jsonify({"error": "⏱️ أرسلت طلبات كثيرة. انتظر دقيقة."})

    user_message = sanitize_input(request.form.get("message", ""))
    mode = request.form.get("mode", "fast")
    history_raw = request.form.get("history", "[]")

    if is_prompt_injection(user_message):
        return jsonify({"error": "⚠️ تم رفض الرسالة لأسباب أمنية."})

    if mode not in MODE_PROMPTS:
        mode = 'fast'

    messages = [{"role": "system", "content": get_system_prompt(mode, user_message)}]

    try:
        history_data = json.loads(history_raw)
        for msg in history_data[-10:]:
            u = str(msg.get("user", ""))[:1000]
            a = str(msg.get("rawAi") or msg.get("ai", ""))[:2000]
            if u and a and a != '__typing__':
                messages.append({"role": "user", "content": u})
                messages.append({"role": "assistant", "content": a})
    except Exception:
        pass

    messages.append({"role": "user", "content": user_message or "مرحبا"})

    model_map = {
        'thinker': 'llama-3.3-70b-versatile',
        'coder':   'llama-3.3-70b-versatile',
        'writer':  'llama-3.3-70b-versatile',
        'fast':    'llama-3.1-8b-instant',
        'funny':   'llama-3.1-8b-instant',
        'creative':'llama-3.3-70b-versatile',
    }
    model = model_map.get(mode, 'llama-3.1-8b-instant')

    def generate():
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": 2048,
                    "temperature": 0.8 if mode in ('funny', 'creative', 'writer') else 0.4,
                    "top_p": 0.9,
                    "stream": True
                },
                stream=True,
                timeout=60
            )
            for line in resp.iter_lines():
                if line:
                    line_text = line.decode('utf-8')
                    if line_text.startswith('data: '):
                        data_str = line_text[6:]
                        if data_str == '[DONE]':
                            yield "data: [DONE]\n\n"
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data['choices'][0]['delta'].get('content', '')
                            if delta:
                                yield f"data: {json.dumps({'token': delta})}\n\n"
                        except Exception:
                            continue
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route("/api/chat", methods=["POST"])
def chat():
    if not API_KEY:
        return jsonify({"response": "⚠️ مفتاح API غير مضاف. أضف GROQ_API_KEY في إعدادات Render.", "rawResponse": ""})

    ip = get_client_ip()

    # Rate limiting
    if is_rate_limited(ip):
        return jsonify({"error": "⏱️ أرسلت طلبات كثيرة. انتظر دقيقة ثم حاول مجدداً."})

    user_message = sanitize_input(request.form.get("message", ""))
    mode = request.form.get("mode", "fast")
    history_raw = request.form.get("history", "[]")
    file = request.files.get("file")

    # حماية Prompt Injection
    if is_prompt_injection(user_message):
        return jsonify({"response": "⚠️ تم رفض الرسالة لأسباب أمنية.", "rawResponse": ""})

    # Validate mode
    if mode not in MODE_PROMPTS:
        mode = 'fast'

    # بناء المحادثة
    messages = [{"role": "system", "content": get_system_prompt(mode, user_message)}]

    try:
        history_data = json.loads(history_raw)
        for msg in history_data[-10:]:  # آخر 10 رسائل فقط
            u = str(msg.get("user", ""))[:1000]
            a = str(msg.get("rawAi") or msg.get("ai", ""))[:2000]
            if u and a and a != '__typing__':
                messages.append({"role": "user", "content": u})
                messages.append({"role": "assistant", "content": a})
    except Exception:
        pass

    # رسم صورة
    is_image_request = (
        'ارسم' in user_message or
        'صورة' in user_message or
        user_message.startswith('draw') or
        (mode == 'creative' and 'ارسم' in user_message)
    )

    if is_image_request and ('ارسم' in user_message or 'صورة' in user_message):
        prompt = user_message.replace('ارسم صورة:', '').replace('ارسم:', '').replace('ارسم', '').replace('صورة', '').strip()
        if not prompt:
            prompt = user_message
        primary_url, fallback_url = generate_image(prompt)
        return jsonify({
            "response": f"🎨 تم توليد الصورة!\n**الوصف:** {prompt}\n\n_انقر على الصورة لعرضها بحجمها الكامل_",
            "rawResponse": f"تم توليد صورة: {prompt}",
            "imageUrl": primary_url
        })

    # معالجة الملف
    if file:
        fname = file.filename.lower()

        if fname.endswith('.pdf'):
            pdf_text = extract_pdf_text(file)
            user_message = f"**محتوى ملف PDF:**\n{pdf_text}\n\n**طلب المستخدم:** {user_message or 'لخص هذا الملف بالتفصيل'}"

        elif file.content_type and file.content_type.startswith('image/'):
            img_bytes = file.read()
            if len(img_bytes) > 10 * 1024 * 1024:  # 10MB limit
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
                return jsonify({"response": format_response(raw), "rawResponse": raw})
            except Exception as e:
                return jsonify({"response": f"⚠️ خطأ: {str(e)}", "rawResponse": ""})

    # اختيار النموذج
    model_map = {
        'thinker': 'llama-3.3-70b-versatile',
        'coder':   'llama-3.3-70b-versatile',
        'writer':  'llama-3.3-70b-versatile',
        'fast':    'llama-3.1-8b-instant',
        'funny':   'llama-3.1-8b-instant',
        'creative':'llama-3.3-70b-versatile',
    }
    model = model_map.get(mode, 'llama-3.1-8b-instant')

    messages.append({"role": "user", "content": user_message or "مرحبا"})

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": messages,
                "max_tokens": 2048,
                "temperature": 0.8 if mode in ('funny', 'creative', 'writer') else 0.4,
                "top_p": 0.9,
                "stream": False
            },
            timeout=60
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
    return jsonify({"response": formatted, "rawResponse": raw})


def extract_pdf_text(pdf_file):
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(pdf_file.read()))
        text = ""
        for page in reader.pages[:15]:
            t = page.extract_text()
            if t: text += t + "\n"
        return text[:10000]
    except Exception as e:
        return f"خطأ في قراءة PDF: {str(e)}"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
