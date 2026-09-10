// ─── State ────────────────────────────────────────────────────
// رمز CSRF: يُقرأ مرة واحدة من meta tag (مُولَّد سيرفرياً بـ index.html)
// ويُرفق بكل طلب POST/DELETE — بلا صلاحية زمنية منفصلة عن الجلسة نفسها
// (WTF_CSRF_TIME_LIMIT=None بـconfig.py)، فيبقى صالحاً طول عمر الجلسة
// حتى لو الصفحة مفتوحة بمحادثة طويلة.
const CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]')?.content || '';
let currentChatId = localStorage.getItem('currentChatId') || Date.now().toString();
// true فقط لو آخر تعبئة لصندوق النص جاية من تفريغ صوتي (مايك أو وضع
// المحادثة الصوتية) — يُلتقط بلحظة الإرسال بالضبط داخل sendMessage
// نفسها (لا نقرأه لاحقاً بعد وقت غير معروف) حتى ما يختلط برسالة
// نصية عادية أرسلها المستخدم بعدها.
let wasVoiceInput = false;
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
const TOAST_ICONS = { success: 'fa-circle-check', error: 'fa-triangle-exclamation', '': 'fa-circle-info' };

function showToast(msg, type = '') {
  const t = document.getElementById('toast');
  t.innerHTML = '';
  const icon = document.createElement('i');
  icon.className = 'fa-solid ' + (TOAST_ICONS[type] || TOAST_ICONS['']);
  const text = document.createElement('span');
  text.textContent = msg;
  t.appendChild(icon);
  t.appendChild(text);
  t.className = 'toast show ' + type;
  // مدة أطول للرسائل الأطول من المعتاد (مثل تفاصيل خطأ تقنية بعدة
  // أسطر) — 3.2 ثانية كانت تختفي قبل ما يقدر المستخدم حتى يقرأها.
  const duration = Math.min(9000, Math.max(3200, msg.length * 65));
  clearTimeout(showToast._h);
  showToast._h = setTimeout(() => t.className = 'toast', duration);
}

// ─── Mode ─────────────────────────────────────────────────────
const MODE_META = {
  fast:     { icon: 'fa-bolt',            label: 'سريع'  },
  thinker:  { icon: 'fa-brain',           label: 'مفكر'  },
  funny:    { icon: 'fa-face-laugh-beam', label: 'فكاهي' },
  creative: { icon: 'fa-palette',         label: 'مبدع'  },
  coder:    { icon: 'fa-code',            label: 'مبرمج' },
  writer:   { icon: 'fa-pen-nib',         label: 'كاتب'  },
};

function setMode(m, save = true) {
  currentMode = m;
  if (save) localStorage.setItem('mode', m);
  document.querySelectorAll('.mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === m));

  const meta = MODE_META[m] || MODE_META.fast;
  const pillIcon = document.getElementById('modePillIcon');
  const pillLabel = document.getElementById('modePillLabel');
  if (pillIcon) pillIcon.className = 'fa-solid ' + meta.icon;
  if (pillLabel) pillLabel.textContent = meta.label;
  document.getElementById('modeDropdown')?.classList.add('hidden');
}

function toggleModeDropdown() {
  document.getElementById('toolsPopup')?.classList.add('hidden');
  document.getElementById('modeDropdown')?.classList.toggle('hidden');
}
function toggleToolsPopup() {
  document.getElementById('modeDropdown')?.classList.add('hidden');
  document.getElementById('toolsPopup')?.classList.toggle('hidden');
}
function closeToolsPopup() {
  document.getElementById('toolsPopup')?.classList.add('hidden');
}
document.addEventListener('click', (e) => {
  const modeWrap = document.querySelector('.mode-selector-wrap');
  const toolsWrap = document.querySelector('.tools-wrap');
  if (modeWrap && !modeWrap.contains(e.target)) document.getElementById('modeDropdown')?.classList.add('hidden');
  if (toolsWrap && !toolsWrap.contains(e.target)) document.getElementById('toolsPopup')?.classList.add('hidden');
});

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
    l.innerHTML = '<div style="color:var(--text-dim);font-size:13px;text-align:center;padding:24px 10px">لا توجد محادثات سابقة<br><i class="fa-regular fa-comments" style="font-size:22px;display:block;margin-top:10px;opacity:0.6"></i></div>';
    return;
  }
  dbChats.forEach(chat => {
    const d = document.createElement('div');
    d.className = 'chat-item' + (chat.chat_id === currentChatId ? ' active' : '');

    const icon = document.createElement('i');
    icon.className = 'chat-item-icon fa-solid fa-comment';

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

    const icon = document.createElement('i');
    icon.className = 'chat-item-icon fa-solid fa-comment';

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

  delete chats[id];
  saveChats();
  if (currentChatId === id) {
    currentChatId = Date.now().toString();
    chats[currentChatId] = [];
    localStorage.setItem('currentChatId', currentChatId);
    renderChat();
  }

  try {
    await fetch(`/api/chat/${id}`, { method: 'DELETE', headers: { 'X-CSRFToken': CSRF_TOKEN } });
  } catch(e) {}

  await loadDbChats();
  showToast('تم حذف المحادثة', 'success');
}

// ─── Load & Switch Chat ───────────────────────────────────────
async function loadChatFromDb(chatId) {
  try {
    const r = await fetch(`/api/chat/${chatId}`);
    if (!r.ok) throw new Error('خطأ');
    const data = await r.json();
    const messages = data.messages || [];
    chats[chatId] = messages.map(m => ({
      id: m.id, user: m.user_message, ai: m.ai_response,
      rawAi: m.raw_ai, imageUrl: m.image_url, fileName: m.file_name,
      userImageUrl: m.uploaded_image_url
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
      <img src="/static/images/logo.png" alt="Wadi" class="welcome-icon-img">
      <h2>مرحباً في <span style="background:linear-gradient(90deg,#00ff94,#00d2ff);-webkit-background-clip:text;-webkit-text-fill-color:transparent">Wadi</span></h2>
      <p>تم تطوير هذا الذكاء الاصطناعي بيد المهندس <strong>Anas Wadi</strong> من ليبيا <i class="fa-solid fa-flag"></i></p>
      <p style="margin-top:6px">كيف يمكنني مساعدتك اليوم؟</p>
      <div class="welcome-cards">
        <div class="welcome-card" onclick="useTemplate('ارسم صورة: ')"><div class="card-icon"><i class="fa-solid fa-palette"></i></div><div class="card-title">رسم صورة</div><div class="card-desc">توليد صور فائقة الجودة</div></div>
        <div class="welcome-card" onclick="useTemplate('اشرحلي ')"><div class="card-icon"><i class="fa-solid fa-lightbulb"></i></div><div class="card-title">شرح وتحليل</div><div class="card-desc">أشرح أي موضوع تريده</div></div>
        <div class="welcome-card" onclick="setMode('coder');useTemplate('اصنعلي مشروع ')"><div class="card-icon"><i class="fa-solid fa-code"></i></div><div class="card-title">مشروع كامل</div><div class="card-desc">موقع، API، بوت — جاهز للتشغيل</div></div>
        <div class="welcome-card" onclick="document.getElementById('fileInput').click()"><div class="card-icon"><i class="fa-solid fa-file-lines"></i></div><div class="card-title">تحليل ملف</div><div class="card-desc">PDF أو صورة</div></div>
      </div>
    </div>`;
    return;
  }
  c.innerHTML = '';
  h.forEach((m, i) => {
    const isTyping = m.ai === '__typing__';
    let userContent = escHtml(m.user);
    if (m.fileName) userContent = `<div class="file-badge"><i class="fa-solid fa-file"></i> ${escHtml(m.fileName)}</div><br>${userContent}`;
    if (m.userImageUrl) userContent += `<br><img class="generated-img" src="${escHtml(m.userImageUrl)}" alt="الصورة المرفوعة" loading="lazy" onclick="window.open(this.src,'_blank')">`;
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
          <button class="msg-btn" id="speak-btn-${i}" onclick="speakMessage(${i})"><i class="fa-solid fa-volume-high"></i> استماع</button>
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

// أثناء البث الحي نحدّث فقاعة الرسالة فقط (نص خام غير منسّق بعد، لتفادي
// عرض Markdown نصف مكتمل) بدل إعادة بناء الشات بالكامل في كل قطعة —
// هذا يحل أيضاً مشكلة أداء renderChat() القديمة عند التحديث المتكرر.
function updateStreamingContent(index, rawText) {
  const el = document.getElementById('msg-' + index);
  if (el) {
    el.textContent = rawText;
    window.scrollTo(0, document.body.scrollHeight);
  }
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
  }).catch(() => showToast('تعذر النسخ', 'error'));
}

function escHtml(t) {
  if (!t) return '';
  const d = document.createElement('div');
  d.textContent = t;
  return d.innerHTML;
}

// ─── Streaming (SSE عبر fetch) ─────────────────────────────────
// نتعامل مع البث عبر fetch + ReadableStream بدل EventSource، لأن
// EventSource يدعم GET فقط ولا يسمح بإرسال FormData/ملفات.
async function streamChat(fd, { onFirstChunk, onChunk, onDone, onError } = {}) {
  let r;
  try {
    r = await fetch('/api/chat', { method: 'POST', headers: { 'X-CSRFToken': CSRF_TOKEN }, body: fd });
  } catch (e) {
    onError && onError('تعذر الاتصال بالخادم');
    return;
  }
  if (!r.ok || !r.body) {
    let msg = 'خطأ في الخادم';
    try { const j = await r.json(); msg = j.error || msg; } catch(e) {}
    onError && onError(msg);
    return;
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let raw = '';
  let gotFirstChunk = false;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop();
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith('data:')) continue;
      let evt;
      try { evt = JSON.parse(line.slice(5).trim()); } catch(e) { continue; }
      if (evt.type === 'chunk') {
        if (!gotFirstChunk) { gotFirstChunk = true; onFirstChunk && onFirstChunk(); }
        raw += evt.content;
        onChunk && onChunk(raw);
      } else if (evt.type === 'error') {
        onError && onError(evt.error || 'حدث خطأ');
      } else if (evt.type === 'done') {
        onDone && onDone(evt);
      }
    }
  }
}

// ─── Send Message ─────────────────────────────────────────────
async function sendMessage() {
  if (isSending) return;
  const inp = document.getElementById('messageInput');
  const t = inp.value.trim();
  if (!t && !currentFile) return;
  isSending = true;
  // يُلتقط هنا بالضبط — لحظة الإرسال الفعلية، لا أي وقت لاحق — حتى
  // نربط "التشغيل التلقائي" بنفس الرسالة اللي جاءت فعلاً من الصوت.
  const triggeredByVoice = wasVoiceInput;
  wasVoiceInput = false;
  document.getElementById('sendBtn').disabled = true;
  inp.value = ''; inp.style.height = '52px';
  if (!chats[currentChatId]) chats[currentChatId] = [];
  const c = chats[currentChatId];
  const fName = currentFile ? currentFile.name : null;
  // معاينة فورية محلية (blob URL) للصورة المرفوعة قبل اكتمال الرفع
  // للتخزين الدائم — تُستبدل بالرابط الدائم بمجرد وصول رد الخادم.
  const isImageFile = currentFile && currentFile.type && currentFile.type.startsWith('image/');
  const localPreviewUrl = isImageFile ? URL.createObjectURL(currentFile) : null;
  c.push({ user: t || 'حلل الملف', ai: '__typing__', fileName: fName, userImageUrl: localPreviewUrl });
  saveChats(); renderChat();
  const msgIndex = c.length - 1;

  // ملاحظة: لا نرسل history من المتصفح — الخادم يبني السياق من قاعدة
  // البيانات مباشرة (chat_id + المستخدم) لمنع التلاعب بسجل المحادثة.
  const fd = new FormData();
  fd.append('message', t);
  fd.append('mode', currentMode);
  fd.append('chat_id', currentChatId);
  if (currentFile) fd.append('file', currentFile);

  try {
    await streamChat(fd, {
      onFirstChunk: () => { c[msgIndex].ai = ''; },
      onChunk: (rawAccum) => { c[msgIndex].ai = rawAccum; updateStreamingContent(msgIndex, rawAccum); },
      onDone: (evt) => {
        c[msgIndex].ai = evt.response;
        c[msgIndex].rawAi = evt.rawResponse || evt.response;
        c[msgIndex].id = evt.id;
        if (evt.imageUrl) c[msgIndex].imageUrl = evt.imageUrl;
        // الرابط الدائم من التخزين يستبدل المعاينة المؤقتة (لو التخزين
        // غير مفعّل بالخادم، تبقى المعاينة المحلية لهذه الجلسة فقط)
        if (evt.uploadedImageUrl) c[msgIndex].userImageUrl = evt.uploadedImageUrl;
        saveChats(); renderChat();
        loadDbChats();
        // تشغيل تلقائي: لو السؤال جاء بالصوت، الرد يتكلم لوحده بلا ما
        // يحتاج المستخدم يدوس زر "استماع" يدوياً — يقفل حلقة المحادثة.
        // بوضع المحادثة الصوتية (VoiceMode معرَّفة أسفل هذا الملف، لكن
        // آمن الوصول لها هنا لأن sendMessage تُستدعى فقط بعد اكتمال
        // تحميل السكربت كله) نرجع نستمع تلقائياً بعد ما يخلص الصوت.
        if (triggeredByVoice) {
          const inVoiceMode = VoiceMode.state !== 'idle';
          if (inVoiceMode) setVoiceState('speaking');
          speakMessage(msgIndex, {
            silent: true,
            onDone: inVoiceMode ? () => { if (VoiceMode.state !== 'idle') startVoiceListening(); } : null
          });
        }
      },
      onError: (msg) => {
        showToast(msg, 'error');
        c[msgIndex].ai = 'حدث خطأ: ' + msg;
        renderChat();
      }
    });
  } catch (err) {
    c[msgIndex].ai = 'حدث خطأ: ' + err.message;
    renderChat();
    showToast('تعذر الإرسال', 'error');
  } finally {
    if (localPreviewUrl) URL.revokeObjectURL(localPreviewUrl);
    currentFile = null;
    document.getElementById('filePreview').classList.add('hidden');
    document.getElementById('fileInput').value = '';
    saveChats();
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
  // نمرر id الرسالة الحالية بدل تاريخ كامل من المتصفح — الخادم يبني
  // السياق من قاعدة البيانات ويستثني هذه الرسالة وما بعدها تلقائياً.
  if (c[i].id) fd.append('regenerate_message_id', c[i].id);
  try {
    await streamChat(fd, {
      onFirstChunk: () => { c[i].ai = ''; },
      onChunk: (rawAccum) => { c[i].ai = rawAccum; updateStreamingContent(i, rawAccum); },
      onDone: (evt) => {
        c[i].ai = evt.response; c[i].rawAi = evt.rawResponse || evt.response;
        c[i].id = evt.id;
        if (evt.imageUrl) c[i].imageUrl = evt.imageUrl; else delete c[i].imageUrl;
        saveChats(); renderChat();
      },
      onError: (msg) => { c[i].ai = 'حدث خطأ: ' + msg; renderChat(); }
    });
  } catch (err) {
    c[i].ai = 'حدث خطأ: ' + err.message;
    renderChat();
  } finally {
    saveChats(); isSending = false;
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
  showToast('تم النسخ', 'success');
}
function saveChats() {
  try { localStorage.setItem('chats', JSON.stringify(chats)); }
  catch(e) { showToast('الذاكرة ممتلئة — احذف محادثات قديمة', 'error'); }
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

// ─── الصوت: تسجيل → نص (STT) ────────────────────────────────────
let mediaRecorder = null;
let recordedChunks = [];
let recordingStartTime = null;
let recordingTimerHandle = null;

function voiceSupported() {
  return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
}

// ─── تصنيف أخطاء المايكروفون ومعالجتها بوضوح ──────────────────
// المشكلة الشائعة: لو المستخدم حظر إذن المايك لهذا الموقع مسبقاً (ولو
// بالخطأ)، المتصفح لا يعيد إظهار نافذة "سماح/حظر" الأصلية أبداً بعدها
// — يرفض getUserMedia فوراً وبصمت. رسالة توست عامة بهذي الحالة تدوّخ
// المستخدم لأنه يفهمها كأن الموقع "ما يعطيه خيار السماح" أصلاً، بينما
// الحل الفعلي يتطلب خطوة يدوية من إعدادات المتصفح نفسه. نفرّق هنا بين
// هذي الحالة وبقية الأخطاء (لا يوجد مايك، المايك مستخدَم من تطبيق
// آخر...) ونوجّه لكل حالة بالحل الصحيح تحديداً.
function classifyMicError(err) {
  const name = err && err.name;
  if (name === 'NotAllowedError' || name === 'PermissionDeniedError') return 'blocked';
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') return 'not-found';
  if (name === 'NotReadableError' || name === 'TrackStartError') return 'in-use';
  if (name === 'SecurityError') return 'insecure';
  return 'unknown';
}

function handleMicError(err) {
  const kind = classifyMicError(err);
  console.error('Mic error:', err && err.name, err && err.message, err);
  if (kind === 'blocked') {
    document.getElementById('micPermissionModal')?.classList.add('open');
    return;
  }
  const messages = {
    'not-found': '⚠️ ما تم العثور على مايكروفون متصل بجهازك',
    'in-use': '⚠️ المايكروفون مستخدَم حالياً من تطبيق أو تبويب آخر — أغلقه وحاول مجدداً',
    'insecure': '⚠️ الوصول للمايكروفون يتطلب اتصالاً آمناً (HTTPS)',
    'unknown': '⚠️ تعذر الوصول للمايكروفون — تأكد من السماح بالإذن',
  };
  // تفاصيل تقنية مؤقتة أثناء التشخيص — تساعدنا نحدد السبب الدقيق لو
  // التصنيف أعلاه ما طابق الخطأ الفعلي (متصفحات أندرويد معروفة بأخطاء
  // غير قياسية أحياناً). نحذف هذا السطر لاحقاً بعد ما نحدد السبب فعلياً.
  const detail = err && err.name ? `\nتفاصيل تقنية: ${err.name}${err.message ? ' — ' + err.message : ''}` : '';
  showToast((messages[kind] || messages.unknown) + detail, 'error');
}

// نتحقق من حالة الإذن *قبل* محاولة الوصول الفعلي لو المتصفح يدعم
// Permissions API (Safari لا يدعمها) — يكشف حالة "محظور مسبقاً" بشكل
// صريح ومباشر، بدل الاعتماد فقط على تفسير رفض getUserMedia لاحقاً.
async function getMicStream() {
  try {
    if (navigator.permissions && navigator.permissions.query) {
      const status = await navigator.permissions.query({ name: 'microphone' });
      if (status.state === 'denied') {
        const blockedErr = new Error('Microphone permission blocked');
        blockedErr.name = 'NotAllowedError';
        throw blockedErr;
      }
    }
  } catch (e) {
    if (e.name === 'NotAllowedError') throw e;
    // فشل استعلام الأذونات نفسه (متصفح لا يدعم 'microphone' بهذا الاستعلام،
    // مثل Safari) — نتجاهله ونكمل لمحاولة getUserMedia العادية.
  }
  return navigator.mediaDevices.getUserMedia({ audio: true });
}

function initVoiceUI() {
  // الزر يبقى مخفياً افتراضياً بالـ HTML لو المتصفح ما يدعم التسجيل —
  // نُظهره فقط لو الدعم مؤكد، بدل زر مكسور يفشل عند الضغط عليه.
  if (voiceSupported()) {
    document.getElementById('micBtn').classList.remove('hidden');
    document.getElementById('voiceModeBtn')?.classList.remove('hidden');
  }
  // addEventListener('input') يفعّل فقط بكتابة حقيقية من المستخدم —
  // تعيين .value برمجياً (زي تعبئة النص بعد التفريغ الصوتي) ما يطلق
  // هذا الحدث إطلاقاً، فهذا آمن: يلغي علم "جاء من صوت" فقط لو
  // المستخدم عدّل النص فعلياً بإصبعه بعد التفريغ.
  document.getElementById('messageInput')?.addEventListener('input', () => {
    wasVoiceInput = false;
  });
}

async function toggleRecording() {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
    return;
  }
  if (!voiceSupported()) {
    showToast('متصفحك لا يدعم التسجيل الصوتي', 'error');
    return;
  }
  try {
    const stream = await getMicStream();
    recordedChunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) recordedChunks.push(e.data); };
    mediaRecorder.onstop = () => {
      stream.getTracks().forEach(t => t.stop());  // يطفي مؤشر المايك بالمتصفح
      clearInterval(recordingTimerHandle);
      document.getElementById('recordingBar').classList.add('hidden');
      document.getElementById('micBtn').classList.remove('recording');
      const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType || 'audio/webm' });
      if (blob.size > 0) uploadRecording(blob);
    };
    mediaRecorder.start();
    document.getElementById('micBtn').classList.add('recording');
    document.getElementById('recordingBar').classList.remove('hidden');
    recordingStartTime = Date.now();
    updateRecordingTimer();
    recordingTimerHandle = setInterval(updateRecordingTimer, 500);
  } catch (err) {
    handleMicError(err);
  }
}

function updateRecordingTimer() {
  const elapsed = Math.floor((Date.now() - recordingStartTime) / 1000);
  const m = Math.floor(elapsed / 60), s = elapsed % 60;
  document.getElementById('recordingTime').textContent = `${m}:${String(s).padStart(2, '0')}`;
  if (elapsed >= 60 && mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();  // حد أقصى دفاعي — يمنع تسجيلاً بلا نهاية لو المستخدم نسي يوقفه
    showToast('وصلت للحد الأقصى للتسجيل (دقيقة)', '');
  }
}

async function uploadRecording(blob) {
  showToast('جاري تحويل الصوت لنص...', '');
  const fd = new FormData();
  fd.append('audio', blob, 'recording.webm');
  try {
    const r = await fetch('/api/transcribe', { method: 'POST', headers: { 'X-CSRFToken': CSRF_TOKEN }, body: fd });
    if (!r.ok) throw new Error('خطأ بالخادم');
    const d = await r.json();
    if (d.error) { showToast(d.error, 'error'); return; }
    const inp = document.getElementById('messageInput');
    inp.value = (inp.value ? inp.value + ' ' : '') + d.text;
    wasVoiceInput = true;  // الرسالة القادمة (لو أُرسلت) رح تتكلم لوحدها تلقائياً
    autoResize(inp);
    inp.focus();
  } catch (err) {
    showToast('تعذر تحويل الصوت لنص', 'error');
  }
}

// ─── وضع المحادثة الصوتية ────────────────────────────────────
// حلقة كاملة بلا أزرار بينية: استماع (بكشف صمت تلقائي) → تفريغ لنص
// → إرسال → رد يتحوّل لصوت تلقائياً → رجوع للاستماع، وهكذا لحد ما
// تدوس "إنهاء". كشف الصمت (VAD) هنا مبني على قياس مستوى الصوت
// بمكتبة Web Audio القياسية — منطق سليم ومُتَّبع، لكن بصراحة: بيئة
// التطوير هنا بلا متصفح حقيقي ولا مايك، فما قدرت أختبره بصوت بشري
// فعلي ولا أضبط VAD_SILENCE_THRESHOLD تجريبياً. زر "خلصت" اليدوي
// موجود عمداً كخط رجعة أكيد لو الكشف التلقائي احتاج ضبطاً بعد
// التجربة الحية (ضجيج غرفة، حساسية مايك مختلفة، إلخ).
const VoiceMode = {
  state: 'idle',            // idle | listening | processing | speaking
  mediaRecorder: null,
  recordedChunks: [],
  audioContext: null,
  vadRafId: null,
  speechDetected: false,
  silenceStartTime: null,
  recordStartTime: null,
};

const VAD_SILENCE_THRESHOLD = 0.02;     // مستوى صوت أقل من هذا = صمت — يحتاج ضبطاً فعلياً بعد أول تجربة حية
const VAD_SILENCE_DURATION_MS = 1600;   // صمت متواصل بهذي المدة بعد كلام فعلي = المستخدم خلص كلامه
const VOICE_MODE_MAX_RECORD_MS = 60000; // سقف أمان — نفس حد المايك العادي بالضبط

function getSelectedVoice() {
  return localStorage.getItem('ttsVoiceAr') || 'fahad';
}
function setSelectedVoice(voice) {
  localStorage.setItem('ttsVoiceAr', voice);
}

function enterVoiceMode() {
  if (!voiceSupported()) {
    showToast('متصفحك لا يدعم التسجيل الصوتي', 'error');
    return;
  }
  const sel = document.getElementById('voiceSelect');
  if (sel) sel.value = getSelectedVoice();
  const transcript = document.getElementById('voiceTranscript');
  if (transcript) transcript.textContent = '';
  document.getElementById('voiceModeOverlay').classList.remove('hidden');
  VoiceMode.state = 'listening';  // يُضبط فوراً حتى startVoiceListening ما يرفض نفسه
  startVoiceListening();
}

function exitVoiceMode() {
  const wasListening = VoiceMode.state === 'listening';
  VoiceMode.state = 'idle';
  if (VoiceMode.vadRafId) { cancelAnimationFrame(VoiceMode.vadRafId); VoiceMode.vadRafId = null; }
  if (wasListening && VoiceMode.mediaRecorder && VoiceMode.mediaRecorder.state === 'recording') {
    VoiceMode.mediaRecorder.stop();
  }
  stopSpeaking();
  document.getElementById('voiceModeOverlay').classList.add('hidden');
}

async function startVoiceListening() {
  if (VoiceMode.state === 'idle') return;  // خرجنا من وضع المحادثة قبل ما نبدأ فعلياً
  VoiceMode.state = 'listening';
  setVoiceState('listening');
  VoiceMode.speechDetected = false;
  VoiceMode.silenceStartTime = null;
  VoiceMode.recordStartTime = Date.now();
  VoiceMode.recordedChunks = [];

  try {
    const stream = await getMicStream();
    if (VoiceMode.state !== 'listening') {
      stream.getTracks().forEach(t => t.stop());  // خرجنا أثناء انتظار إذن المايك
      return;
    }
    VoiceMode.mediaRecorder = new MediaRecorder(stream);
    VoiceMode.mediaRecorder.ondataavailable = e => { if (e.data.size > 0) VoiceMode.recordedChunks.push(e.data); };
    VoiceMode.mediaRecorder.onstop = () => {
      stream.getTracks().forEach(t => t.stop());  // يطفي مؤشر المايك بالمتصفح فوراً
      handleVoiceRecordingStopped(VoiceMode.mediaRecorder.mimeType || 'audio/webm');
    };
    VoiceMode.mediaRecorder.start();
    setupVAD(stream);
  } catch (err) {
    exitVoiceMode();
    handleMicError(err);
  }
}

function setupVAD(stream) {
  if (!VoiceMode.audioContext || VoiceMode.audioContext.state === 'closed') {
    VoiceMode.audioContext = new (window.AudioContext || window.webkitAudioContext)();
  }
  const source = VoiceMode.audioContext.createMediaStreamSource(stream);
  const analyser = VoiceMode.audioContext.createAnalyser();
  analyser.fftSize = 512;
  source.connect(analyser);
  const data = new Uint8Array(analyser.frequencyBinCount);

  function checkVolume() {
    if (VoiceMode.state !== 'listening') return;  // توقفنا أو تحوّلنا لحالة تانية — نوقف حلقة الفحص
    analyser.getByteTimeDomainData(data);
    let sumSquares = 0;
    for (let i = 0; i < data.length; i++) {
      const v = (data[i] - 128) / 128;
      sumSquares += v * v;
    }
    const rms = Math.sqrt(sumSquares / data.length);
    const now = Date.now();

    if (rms > VAD_SILENCE_THRESHOLD) {
      VoiceMode.speechDetected = true;
      VoiceMode.silenceStartTime = null;
    } else if (VoiceMode.speechDetected) {
      if (VoiceMode.silenceStartTime === null) VoiceMode.silenceStartTime = now;
      else if (now - VoiceMode.silenceStartTime > VAD_SILENCE_DURATION_MS) {
        manualStopListening();
        return;
      }
    }

    if (now - VoiceMode.recordStartTime > VOICE_MODE_MAX_RECORD_MS) {
      manualStopListening();
      return;
    }
    VoiceMode.vadRafId = requestAnimationFrame(checkVolume);
  }
  VoiceMode.vadRafId = requestAnimationFrame(checkVolume);
}

function manualStopListening() {
  if (VoiceMode.state !== 'listening') return;
  if (VoiceMode.vadRafId) { cancelAnimationFrame(VoiceMode.vadRafId); VoiceMode.vadRafId = null; }
  if (VoiceMode.mediaRecorder && VoiceMode.mediaRecorder.state === 'recording') {
    VoiceMode.mediaRecorder.stop();  // يشغّل onstop أعلاه → handleVoiceRecordingStopped
  }
}

async function handleVoiceRecordingStopped(mimeType) {
  if (VoiceMode.state !== 'listening') return;  // خرجنا من وضع المحادثة أثناء التسجيل
  if (!VoiceMode.speechDetected || VoiceMode.recordedChunks.length === 0) {
    await startVoiceListening();  // صمت كامل بلا كلام فعلي — نرجع نستمع بدون إرسال شيء فاضي
    return;
  }

  setVoiceState('processing');
  const blob = new Blob(VoiceMode.recordedChunks, { type: mimeType });
  const fd = new FormData();
  fd.append('audio', blob, 'voice.webm');

  try {
    const r = await fetch('/api/transcribe', { method: 'POST', headers: { 'X-CSRFToken': CSRF_TOKEN }, body: fd });
    if (!r.ok) throw new Error('خطأ بالخادم');
    const d = await r.json();
    if (VoiceMode.state !== 'processing') return;  // خرجنا من وضع المحادثة أثناء انتظار الرد
    if (d.error || !d.text || !d.text.trim()) {
      showToast(d.error || 'ما قدرنا نفهم الكلام، حاول مجدداً', 'error');
      await startVoiceListening();
      return;
    }
    const transcript = document.getElementById('voiceTranscript');
    if (transcript) transcript.textContent = d.text;
    const inp = document.getElementById('messageInput');
    inp.value = d.text;
    wasVoiceInput = true;
    // sendMessage تتكفّل بالباقي: تنقل الحالة لـ'speaking' وترجعنا
    // لـ'listening' تلقائياً بعد ما يخلص الصوت (انظر onDone بأعلى
    // هذا الملف — triggeredByVoice + VoiceMode.state).
    await sendMessage();
  } catch (err) {
    if (VoiceMode.state === 'processing') {
      showToast('تعذر تحويل الصوت لنص', 'error');
      await startVoiceListening();
    }
  }
}

function setVoiceState(state) {
  VoiceMode.state = state;
  const orb = document.getElementById('voiceOrb');
  const status = document.getElementById('voiceStatus');
  if (orb) orb.className = 'voice-orb voice-orb-' + state;
  const labels = { listening: 'جاري الاستماع...', processing: 'جاري التفكير...', speaking: 'يتكلم...' };
  if (status) status.textContent = labels[state] || '';
  const stopBtn = document.getElementById('voiceModeStopBtn');
  if (stopBtn) stopBtn.classList.toggle('hidden', state !== 'listening');
}

// ─── الصوت: نص → استماع (TTS) — بث حي، يبدأ التشغيل فور أول مقطع ──
let currentAudioPlayer = null;
let currentSpeakingIndex = null;
let audioQueue = [];
let audioQueuePlaying = false;
let audioStreamDone = false;
// عند وضع المحادثة الصوتية، هذا الاستدعاء يُفعَّل بعد انتهاء الصوت
// فعلياً (كل المقاطع خلصت تشغيلها) — يُستخدم للرجوع للاستماع تلقائياً.
let onSpeakFullyDone = null;

function stopSpeaking() {
  if (currentAudioPlayer) { currentAudioPlayer.pause(); currentAudioPlayer = null; }
  if (currentSpeakingIndex !== null) {
    const btn = document.getElementById('speak-btn-' + currentSpeakingIndex);
    if (btn) { btn.classList.remove('speaking'); btn.innerHTML = '<i class="fa-solid fa-volume-high"></i> استماع'; }
  }
  currentSpeakingIndex = null;
  audioQueue = [];
  audioQueuePlaying = false;
  audioStreamDone = false;
  onSpeakFullyDone = null;
}

async function speakMessage(i, opts) {
  opts = opts || {};
  // ضغط على نفس الزر أثناء التشغيل = إيقاف (ما ينطبق على استدعاء وضع
  // المحادثة الصوتية التلقائي — opts.silent يتخطى هذا الشرط)
  if (!opts.silent && currentSpeakingIndex === i) { stopSpeaking(); return; }
  stopSpeaking();

  const c = chats[currentChatId];
  const text = (c[i].rawAi || c[i].ai || '').replace(/<[^>]*>/g, ' ');
  if (!text.trim()) {
    if (!opts.silent) showToast('لا يوجد نص لقراءته', 'error');
    if (opts.onDone) opts.onDone();
    return;
  }

  const btn = document.getElementById('speak-btn-' + i);
  const originalHtml = btn ? btn.innerHTML : '';
  if (btn) { btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> تجهيز...'; }
  currentSpeakingIndex = i;
  audioQueue = [];
  audioQueuePlaying = false;
  audioStreamDone = false;
  onSpeakFullyDone = opts.onDone || null;
  let gotFirstClip = false;

  const fd = new FormData();
  fd.append('text', text);
  fd.append('lang', 'ar');
  fd.append('voice', getSelectedVoice());

  try {
    const r = await fetch('/api/speak', { method: 'POST', headers: { 'X-CSRFToken': CSRF_TOKEN }, body: fd });
    if (!r.ok || !r.body) throw new Error('فشل الاتصال بخدمة الصوت');

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (currentSpeakingIndex !== i) { reader.cancel().catch(() => {}); return; }  // تبدّل المستخدم لرسالة/تشغيل تاني
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop();
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith('data:')) continue;
        let evt;
        try { evt = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }

        if (evt.type === 'clip') {
          if (!gotFirstClip) {
            gotFirstClip = true;
            if (btn) { btn.innerHTML = '<i class="fa-solid fa-stop"></i> إيقاف'; btn.classList.add('speaking'); }
          }
          enqueueAudioClip(evt.audio, i);
        } else if (evt.type === 'error') {
          if (!gotFirstClip) {
            if (!opts.silent) showToast(evt.error || 'تعذر توليد الصوت', 'error');
            if (btn) btn.innerHTML = originalHtml;
            currentSpeakingIndex = null;
            const cb = onSpeakFullyDone; onSpeakFullyDone = null;
            if (cb) cb();
          }
          return;
        } else if (evt.type === 'done') {
          markAudioStreamDone(i);
        }
      }
    }
  } catch (err) {
    if (!gotFirstClip) {
      if (!opts.silent) showToast('تعذر توليد الصوت', 'error');
      if (btn) btn.innerHTML = originalHtml;
      currentSpeakingIndex = null;
      const cb = onSpeakFullyDone; onSpeakFullyDone = null;
      if (cb) cb();
    }
  }
}

// طابور تشغيل: يضيف مقاطع أثناء وصولها بالبث، ويشغّلها بالتسلسل فوراً
// بمجرد جهوزية أول واحد — لا ننتظر اكتمال كل الرد قبل أول صوت.
function enqueueAudioClip(base64, msgIndex) {
  audioQueue.push(base64);
  if (!audioQueuePlaying) playNextInQueue(msgIndex);
}

function markAudioStreamDone(msgIndex) {
  audioStreamDone = true;
  if (!audioQueuePlaying && audioQueue.length === 0) finishSpeaking(msgIndex);
}

function playNextInQueue(msgIndex) {
  if (currentSpeakingIndex !== msgIndex) { audioQueuePlaying = false; return; }
  if (audioQueue.length === 0) {
    audioQueuePlaying = false;
    if (audioStreamDone) finishSpeaking(msgIndex);
    return;  // ننتظر مقطع جديد يوصل عبر enqueueAudioClip، أو إشارة انتهاء البث
  }
  audioQueuePlaying = true;
  const clip = audioQueue.shift();
  const audio = new Audio('data:audio/mp3;base64,' + clip);
  currentAudioPlayer = audio;
  audio.onended = () => playNextInQueue(msgIndex);
  audio.onerror = () => playNextInQueue(msgIndex);
  audio.play().catch(() => playNextInQueue(msgIndex));
}

function finishSpeaking(msgIndex) {
  if (currentSpeakingIndex !== msgIndex) return;
  const btn = document.getElementById('speak-btn-' + msgIndex);
  if (btn) { btn.classList.remove('speaking'); btn.innerHTML = '<i class="fa-solid fa-volume-high"></i> استماع'; }
  currentSpeakingIndex = null;
  const cb = onSpeakFullyDone; onSpeakFullyDone = null;
  if (cb) cb();
}

// ─── Boot ─────────────────────────────────────────────────────
init();
initVoiceUI();
