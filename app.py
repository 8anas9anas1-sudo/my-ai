import os
import base64
import json
from flask import Flask, request, render_template_string, jsonify, session
import requests
from datetime import datetime
import PyPDF2
import io

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "anas-wadi-secret-2026")

API_KEY = os.environ.get("GROQ_API_KEY")

HTML = """
<!DOCTYPE html>
<html dir="rtl" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#0A0A0A">
<title>✨ anas Wadi ✨</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>
@import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;900&display=swap');
:root[data-theme="dark"]{--bg:linear-gradient(135deg,#0A0A0A 0%,#1a1a2e 100%);--card:rgba(255,255,255,0.05);--text:#fff;--border:rgba(255,255,255,0.1);--user-bg:linear-gradient(135deg,#00ff99 0%,#00d4ff 100%);--user-text:#000;}
:root[data-theme="light"]{--bg:linear-gradient(135deg,#f0f9ff 0%,#e0f2fe 100%);--card:rgba(0,0,0,0.05);--text:#0A0A0A;--border:rgba(0,0,0,0.1);--user-bg:linear-gradient(135deg,#0077ff 0%,#00d4ff 100%);--user-text:#fff;}
*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--text);font-family:'Tajawal',Arial,sans-serif;min-height:100vh;display:flex;flex-direction:column;transition:0.3s;}
.header{background:rgba(0,0,0,0.4);backdrop-filter:blur(10px);padding:15px 20px;border-bottom:1px solid var(--border);position:sticky;top:0;z-index:10;display:flex;justify-content:space-between;align-items:center;}
.header h1{font-size:24px;font-weight:900;background:linear-gradient(90deg,#00ff99,#00d4ff,#7a5cff);-webkit-background-clip:text;-webkit-text-fill-color:transparent;}
.header-actions{display:flex;gap:10px;align-items:center}
.icon-btn{background:var(--card);border:1px solid var(--border);color:var(--text);width:40px;height:40px;border-radius:10px;cursor:pointer;font-size:16px;}
.icon-btn:hover{background:rgba(0,255,153,0.2)}
.sidebar{position:fixed;right:-300px;top:0;width:280px;height:100vh;background:rgba(0,0,0,0.95);backdrop-filter:blur(20px);border-left:1px solid var(--border);transition:0.3s;z-index:20;padding:20px;overflow-y:auto;}
.sidebar.open{right:0}
.sidebar h3{margin-bottom:15px}
.chat-item{background:var(--card);padding:12px;border-radius:10px;margin-bottom:10px;cursor:pointer;border:1px solid var(--border);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.chat-item:hover{border-color:#00ff99}
.chat-item.active{border-color:#00ff99;background:rgba(0,255,153,0.1)}
.modes{display:flex;gap:8px;margin-bottom:15px;padding:0 20px;overflow-x:auto;}
.mode-btn{background:var(--card);border:1px solid var(--border);color:var(--text);padding:10px 18px;border-radius:15px;font-size:14px;font-weight:600;white-space:nowrap;cursor:pointer;transition:0.2s;}
.mode-btn.active{background:linear-gradient(135deg,#00ff99 0%,#00d4ff 100%);color:#000;border:none}
.mode-btn:hover{border-color:#00ff99}
.chat-container{flex:1;padding:20px;max-width:800px;width:100%;margin:0 auto;overflow-y:auto;}
.message{margin:15px 0;animation:slideIn 0.3s ease;position:relative}
@keyframes slideIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
.user-msg{background:var(--user-bg);color:var(--user-text);padding:15px 20px;border-radius:20px 20px 5px 20px;margin-left:auto;max-width:80%;font-weight:500;box-shadow:0 5px 15px rgba(0,255,153,0.3);}
.ai-msg{background:var(--card);backdrop-filter:blur(10px);border:1px solid var(--border);padding:15px 20px;border-radius:20px 20px 20px 5px;max-width:80%;line-height:1.8;white-space:pre-wrap;}
.ai-msg img{max-width:100%;border-radius:10px;margin-top:10px}
.msg-actions{margin-top:8px;display:flex;gap:8px;opacity:0;transition:0.2s}
.message:hover.msg-actions{opacity:1}
.msg-btn{background:var(--card);border:1px solid var(--border);color:var(--text);padding:5px 10px;border-radius:8px;font-size:12px;cursor:pointer;}
.msg-btn:hover{border-color:#00ff99}
.file-badge{background:rgba(0,255,153,0.2);border:1px solid #00ff99;padding:8px 12px;border-radius:10px;margin-bottom:8px;font-size:13px;display:inline-block;}
.input-area{background:rgba(0,0,0,0.4);backdrop-filter:blur(10px);padding:15px;border-top:1px solid var(--border);position:sticky;bottom:0;}
.templates{display:flex;gap:8px;margin-bottom:10px;overflow-x:auto;padding-bottom:5px}
.template-btn{background:var(--card);border:1px solid var(--border);color:var(--text);padding:8px 15px;border-radius:20px;font-size:13px;white-space:nowrap;cursor:pointer;}
.template-btn:hover{border-color:#00ff99}
.input-wrapper{max-width:800px;margin:0 auto;display:flex;gap:10px;align-items:flex-end}
textarea{flex:1;background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:15px;padding:15px;font-size:16px;font-family:'Tajawal',Arial;resize:none;height:55px;max-height:120px;}
textarea:focus{outline:none;border-color:#00ff99}
#fileInput{display:none}
.send-btn{background:linear-gradient(135deg,#00ff99 0%,#00d4ff 100%);border:none;border-radius:15px;padding:0 25px;height:55px;font-size:18px;font-weight:700;cursor:pointer;color:#000;}
.send-btn:disabled{opacity:0.5;cursor:not-allowed}
.send-btn:hover:not(:disabled){transform:scale(1.05)}
.welcome{text-align:center;padding:40px 20px;opacity:0.9}
.welcome h2{font-size:24px;margin-bottom:15px}
.welcome p{line-height:1.8;margin-bottom:10px}
.support-btn{background:linear-gradient(135deg,#ff6b6b,#feca57);color:#000;border:none;padding:12px 25px;border-radius:15px;font-weight:700;cursor:pointer;margin-top:15px;}
.modal{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);z-index:30;justify-content:center;align-items:center;}
.modal.open{display:flex}
.modal-content{background:var(--bg);border:1px solid var(--border);padding:30px;border-radius:20px;max-width:500px;width:90%;text-align:center;}
.file-preview{background:var(--card);border:1px solid #00ff99;padding:10px;border-radius:10px;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center;}
.loading{display:inline-block;width:15px;height:15px;border:3px solid rgba(255,255,255,0.3);border-radius:50%;border-top-color:#00ff99;animation:spin 1s linear infinite;}
@keyframes spin{to{transform:rotate(360deg)}}
.toast{position:fixed;bottom:100px;left:50%;transform:translateX(-50%);background:rgba(255,0,0,0.9);color:#fff;padding:12px 20px;border-radius:10px;z-index:50;display:none;}
</style>
</head>
<body>
<div class="toast" id="toast"></div>
<div class="sidebar" id="sidebar">
  <h3>محادثاتي</h3>
  <button class="icon-btn" style="width:100%;margin-bottom:15px" onclick="newChat()"><i class="fa-solid fa-plus"></i> محادثة جديدة</button>
  <div id="chatList"></div>
</div>
<div class="header">
  <button class="icon-btn" onclick="toggleSidebar()"><i class="fa-solid fa-bars"></i></button>
  <h1>✨ anas Wadi ✨</h1>
  <div class="header-actions">
    <button class="icon-btn" onclick="toggleTheme()" title="الوضع الليلي"><i class="fa-solid fa-moon"></i></button>
    <button class="icon-btn" onclick="showSupport()" title="ادعمني"><i class="fa-solid fa-heart"></i></button>
  </div>
</div>
<div class="modes">
  <button class="mode-btn active" data-mode="fast" onclick="setMode('fast')">⚡ السريع</button>
  <button class="mode-btn" data-mode="thinker" onclick="setMode('thinker')">🧠 المفكر</button>
  <button class="mode-btn" data-mode="funny" onclick="setMode('funny')">😂 الفكاهي</button>
  <button class="mode-btn" data-mode="creative" onclick="setMode('creative')">🎨 المبدع</button>
</div>
<div class="chat-container" id="chatContainer">
  <div class="welcome" id="welcome">
    <h2>مرحباً بك في ✨anas Wadi✨</h2>
    <p>تم تطوير هذا الموقع على يد المهندس <b>Anas Wadi</b> من ليبيا 🇱🇾</p>
    <p>دعمكم يساعدنا نستمر ونقدم لكم الأفضل دائماً 💙</p>
    <button class="support-btn" onclick="showSupport()"><i class="fa-solid fa-mug-hot"></i> ادعمني بكوب قهوة</button>
  </div>
</div>
<div class="input-area">
  <div id="filePreview"></div>
  <div class="templates">
    <button class="template-btn" onclick="useTemplate('ارسم صورة: ')">🎨 ارسم صورة</button>
    <button class="template-btn" onclick="useTemplate('لخصلي الملف هذا')">📄 تلخيص ملف</button>
    <button class="template-btn" onclick="useTemplate('اكتبلي ايميل رسمي عن ')">📧 ايميل</button>
    <button class="template-btn" onclick="useTemplate('ترجم للعربية: ')">🌐 ترجمة</button>
  </div>
  <form class="input-wrapper" onsubmit="sendMessage(event)">
    <input type="file" id="fileInput" accept="image/*,.pdf" onchange="handleFile(this)">
    <button type="button" class="icon-btn" onclick="document.getElementById('fileInput').click()" title="ارفع صورة أو PDF"><i class="fa-solid fa-paperclip"></i></button>
    <textarea id="messageInput" placeholder="اكتب رسالتك هنا..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendMessage(event)}"></textarea>
    <button type="submit" class="send-btn" id="sendBtn"><i class="fa-solid fa-paper-plane"></i></button>
  </form>
</div>
<div class="modal" id="supportModal" onclick="closeModal(event)">
  <div class="modal-content">
    <h2 style="margin-bottom:15px">💙 شكراً لدعمك</h2>
    <p style="margin-bottom:20px;line-height:1.8">دعمكم هو اللي يخلينا نطور الموقع ونضيف ميزات جديدة. لو عجبك الموقع تقدر تدعمنا:</p>
    <a href="https://www.paypal.com" target="_blank" class="support-btn" style="display:inline-block;text-decoration:none"><i class="fa-brands fa-paypal"></i> ادعم عبر PayPal</a>
    <p style="margin-top:20px;font-size:13px;opacity:0.7">Anas Wadi - ليبيا 🇱🇾</p>
  </div>
</div>
<script>
let currentChatId=localStorage.getItem('currentChatId');let chats={};let currentFile=null;let currentMode=localStorage.getItem('mode')||'fast';let isSending=false;
function init(){try{chats=JSON.parse(localStorage.getItem('chats')||'{}')}catch(e){chats={}}
if(!currentChatId||!chats[currentChatId]){currentChatId=Date.now().toString();chats[currentChatId]=[];localStorage.setItem('currentChatId',currentChatId);saveChats()}
loadChats();renderChat();setMode(currentMode);loadTheme();}
function showToast(msg){const t=document.getElementById('toast');t.textContent=msg;t.style.display='block';setTimeout(()=>t.style.display='none',3000)}
function setMode(m){currentMode=m;localStorage.setItem('mode',m);document.querySelectorAll('.mode-btn').forEach(b=>b.classList.toggle('active',b.dataset.mode===m))}
function loadChats(){const l=document.getElementById('chatList');l.innerHTML='';Object.keys(chats).reverse().forEach(id=>{const c=chats[id];const t=c[0]?.user||'محادثة جديدة';const d=document.createElement('div');d.className='chat-item'+(id===currentChatId?' active':'');d.textContent=t.substring(0,30);d.onclick=()=>switchChat(id);l.appendChild(d)})}
function switchChat(id){currentChatId=id;localStorage.setItem('currentChatId',id);renderChat();loadChats();toggleSidebar()}
function newChat(){currentChatId=Date.now().toString();chats[currentChatId]=[];localStorage.setItem('currentChatId',currentChatId);saveChats();renderChat();loadChats();toggleSidebar();document.getElementById('welcome').style.display='block'}
function renderChat(){const c=document.getElementById('chatContainer');const h=chats[currentChatId]||[];if(h.length===0){c.innerHTML=`<div class="welcome" id="welcome"><h2>مرحباً بك في ✨anas Wadi✨</h2><p>تم تطوير هذا الموقع على يد المهندس <b>Anas Wadi</b> من ليبيا 🇱🇾</p><p>دعمكم يساعدنا نستمر ونقدم لكم الأفضل دائماً 💙</p><button class="support-btn" onclick="showSupport()"><i class="fa-solid fa-mug-hot"></i> ادعمني بكوب قهوة</button></div>`;return}
document.getElementById('welcome')?.remove();c.innerHTML='';h.forEach((m,i)=>{let u=escapeHtml(m.user);if(m.fileName){u=`<div class="file-badge"><i class="fa-solid fa-file"></i> ${escapeHtml(m.fileName)}</div>${u}`}let a=m.ai;if(m.imageUrl){a+=`<br><img src="${m.imageUrl}" alt="Generated Image">`}c.innerHTML+=`<div class="message"><div class="user-msg">${u}</div></div><div class="message"><div class="ai-msg">${a}</div><div class="msg-actions"><button class="msg-btn" onclick="copyText(${JSON.stringify(m.ai)})"><i class="fa-solid fa-copy"></i> نسخ</button><button class="msg-btn" onclick="regenerate(${i})"><i class="fa-solid fa-rotate"></i> إعادة</button></div></div>`});window.scrollTo(0,document.body.scrollHeight)}
function escapeHtml(t){const d=document.createElement('div');d.textContent=t||'';return d.innerHTML}
async function sendMessage(e){e.preventDefault();if(isSending)return;const i=document.getElementById('messageInput');const t=i.value.trim();if(!t&&!currentFile)return;isSending=true;document.getElementById('sendBtn').disabled=true;i.value='';const c=chats[currentChatId];const f=currentFile?currentFile.name:null;c.push({user:t||'حلل الملف',ai:'<span class="loading"></span> جاري التفكير...',fileName:f});saveChats();renderChat();const fd=new FormData();fd.append('message',t);fd.append('mode',currentMode);fd.append('history',JSON.stringify(c.slice(0,-1)));if(currentFile)fd.append('file',currentFile);try{const r=await fetch('/api/chat',{method:'POST',body:fd});const d=await r.json();c[c.length-1].ai=d.response;if(d.imageUrl)c[c.length-1].imageUrl=d.imageUrl}catch(err){c[c.length-1].ai='صار خطأ: '+err.message;showToast('صار خطأ في الإرسال')}finally{currentFile=null;document.getElementById('filePreview').innerHTML='';document.getElementById('fileInput').value='';saveChats();renderChat();isSending=false;document.getElementById('sendBtn').disabled=false}}
async function regenerate(i){if(isSending)return;isSending=true;const c=chats[currentChatId];const u=c[i].user;c[i].ai='<span class="loading"></span> جاري إعادة التوليد...';renderChat();const fd=new FormData();fd.append('message',u);fd.append('mode',currentMode);fd.append('history',JSON.stringify(c.slice(0,i)));try{const r=await fetch('/api/chat',{method:'POST',body:fd});const d=await r.json();c[i].ai=d.response;if(d.imageUrl)c[i].imageUrl=d.imageUrl;else delete c[i].imageUrl}catch(err){c[i].ai='صار خطأ: '+err.message;showToast('صار خطأ في الإرسال')}finally{saveChats();renderChat();isSending=false}}
function handleFile(i){if(i.files[0]){currentFile=i.files[0];const p=document.getElementById('filePreview');const ic=currentFile.type.includes('pdf')?'fa-file-pdf':'fa-image';p.innerHTML=`<div class="file-preview"><span><i class="fa-solid ${ic}"></i> ${escapeHtml(currentFile.name)}</span><button class="icon-btn" style="width:30px;height:30px" onclick="removeFile()"><i class="fa-solid fa-xmark"></i></button></div>`}}
function removeFile(){currentFile=null;document.getElementById('fileInput').value='';document.getElementById('filePreview').innerHTML=''}
function useTemplate(t){document.getElementById('messageInput').value=t;document.getElementById('messageInput').focus()}
function copyText(t){navigator.clipboard.writeText(t);showToast('تم النسخ ✅')}
function saveChats(){try{localStorage.setItem('chats',JSON.stringify(chats))}catch(e){showToast('الذاكرة ممتلئة! احذف بعض المحادثات')}}
function toggleSidebar(){document.getElementById('sidebar').classList.toggle('open')}
function toggleTheme(){const h=document.documentElement;const n=h.dataset.theme==='dark'?'light':'dark';h.dataset.theme=n;localStorage.setItem('theme',n)}
function loadTheme(){const t=localStorage.getItem('theme')||'dark';document.documentElement.dataset.theme=t}
function showSupport(){document.getElementById('supportModal').classList.add('open')}
function closeModal(e){if(e.target.classList.contains('modal')){e.target.classList.remove('open')}}
init();
</script>
</body>
</html>
"""

def extract_pdf_text(pdf_file):
    try:
        pdf_reader = PyPDF2.PdfReader(io.BytesIO(pdf_file.read()))
        text = ""
        for page in pdf_reader.pages[:10]:
            text += page.extract_text() + "\n"
        return text[:8000]
    except Exception as e:
        return f"خطأ في قراءة PDF: {str(e)}"

def get_system_prompt(mode, user_message):
    identity_questions = ['من انت', 'من أنت', 'عرف بنفسك', 'من تكون', 'شن اسمك', 'who are you', 'اسمك']
    if any(q in user_message.lower() for q in identity_questions):
        return "أنت مساعد ذكي. إذا سألك أحد 'من أنت' رد عليه بالضبط: أنا Wadi تم تطويري من قِبل المهندس Anas Wadi من ليبيا 🇱🇾. ولا تضيف شيء آخر."
    prompts = {
        'fast': "أنت مساعد ذكي سريع اسمه Wadi. جاوب باختصار ودقة. لا تتفلسف.",
        'thinker': "أنت مساعد مفكر عميق اسمه Wadi. فكر خطوة بخطوة واعط إجابة قوية وملخصة ومفيدة. لا تطل الكلام.",
        'funny': "أنت مساعد فكاهي مضحك اسمه Wadi. جاوب بنكتة وخفة دم بس اعط المعلومة الصحيحة. استخدم إيموجي 😂",
        'creative': "أنت مساعد مبدع فنان اسمه Wadi. لو طلب منك رسم صورة، حول الوصف لإنجليزي دقيق. لو سؤال عادي جاوب بإبداع وخيال."
    }
    return prompts.get(mode, prompts['fast'])

@app.route("/")
def home():
    return render_template_string(HTML)

@app.route("/api/chat", methods=["POST"])
def chat():
    if not API_KEY:
        return jsonify({"response": "⚠️ مفتاح API مش مضاف. ضيف GROQ_API_KEY في Render"})
    user_message = request.form.get("message", "").strip()
    mode = request.form.get("mode", "fast")
    history = request.form.get("history", "[]")
    file = request.files.get("file")
    messages = [{"role": "system", "content": get_system_prompt(mode, user_message)}]
    try:
        history_data = json.loads(history)
        for msg in history_data:
            messages.append({"role": "user", "content": msg["user"]})
            messages.append({"role": "assistant", "content": msg["ai"]})
    except:
        pass
    model = "llama-3.3-70b-versatile" if mode == 'thinker' else "openai/gpt-oss-120b"
    image_url = None
    if user_message.startswith('ارسم صورة:') or (mode == 'creative' and 'ارسم' in user_message):
        prompt = user_message.replace('ارسم صورة:', '').strip()
        encoded_prompt = requests.utils.quote(prompt)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true"
        ai_response = f"تم توليد الصورة بنجاح 🎨\nالوصف: {prompt}"
        return jsonify({"response": ai_response, "imageUrl": image_url})
    if file:
        if file.filename.lower().endswith('.pdf'):
            pdf_text = extract_pdf_text(file)
            user_message = f"محتوى الملف PDF:\n{pdf_text}\n\nسؤال المستخدم: {user_message or 'لخصلي الملف'}"
        elif file.content_type.startswith('image/'):
            img_base64 = base64.b64encode(file.read()).decode()
            messages.append({"role": "user","content": [{"type": "text", "text": user_message or "حلل الصورة هذي"},{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_base64}"}}]})
            model = "meta-llama/llama-4-scout-17b-16e-instruct"
            data = {"model": model, "messages": messages, "max_tokens": 2048}
            headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
            try:
                response = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data, timeout=60)
                result = response.json()
                ai_response = result["choices"][0]["message"]["content"] if response.status_code == 200 else f"خطأ: {result.get('error', {}).get('message')}"
            except Exception as e:
                ai_response = f"صار خطأ: {str(e)}"
            return jsonify({"response": ai_response})
    messages.append({"role": "user", "content": user_message or "مرحبا"})
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    data = {"model": model, "messages": messages, "max_tokens": 2048, "temperature": 0.7 if mode == 'funny' else 0.3}
    try:
        response = requests.post("https://api.groq.com/openai/v1/chat/completions",headers=headers, json=data, timeout=60)
        result = response.json()
        if response.status_code == 200:
            ai_response = result["choices"][0]["message"]["content"]
        else:
            ai_response = f"خطأ: {result.get('error', {}).get('message', 'مشكلة غير معروفة')}"
    except Exception as e:
        ai_response = f"صار خطأ في الاتصال: {str(e)}"
    return jsonify({"response": ai_response, "imageUrl": image_url})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
