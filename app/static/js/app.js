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
  const changed = m !== currentMode;
  currentMode = m;
  if (save) localStorage.setItem('mode', m);
  document.querySelectorAll('.mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === m));
  // وضع المبرمج له هوية بصرية مختلفة فعلياً (CSS تحت body[data-app-mode="coder"])
  // — لا مجرد تغيير لون تمييزي. نضيف/نحذف السمة بدل تركها فارغة حتى ما
  // تُطابق بالخطأ أي selector بقيمة فارغة.
  if (m === 'coder') document.body.setAttribute('data-app-mode', 'coder');
  else document.body.removeAttribute('data-app-mode');

  const meta = MODE_META[m] || MODE_META.fast;
  const pillIcon = document.getElementById('modePillIcon');
  const pillLabel = document.getElementById('modePillLabel');
  if (pillIcon) pillIcon.className = 'fa-solid ' + meta.icon;
  if (pillLabel) pillLabel.textContent = meta.label;
  document.getElementById('modeDropdown')?.classList.add('hidden');

  // ومضة بصرية خفيفة تؤكد تغيّر الوضع فعلياً — بدل ما يتغيّر الأيقونة
  // والنص بصمت تام بلا أي إشارة تفاعل محسوسة.
  if (changed) {
    const pill = document.getElementById('modePill');
    if (pill) {
      pill.classList.remove('flash');
      void pill.offsetWidth;  // إعادة تشغيل الأنيميشن لو المستخدم بدّل بسرعة أكتر من مرة
      pill.classList.add('flash');
    }
  }
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
      userImageUrl: m.uploaded_image_url, reasoning: m.reasoning || ''
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
    // لوحة "عرض التفكير" تظهر فقط لو الرد فعلاً مرّ بمرحلة تفكير/بحث
    // (m.reasoning موجود) — رسائل معاد تحميلها من قاعدة البيانات (بعد
    // تبديل محادثة أو تحديث الصفحة) ما فيها هذا الحقل، فالزر يختفي بهدوء
    // بدل ما يظهر فاضياً — التفكير غير مخزَّن دائماً، بجلسة العرض الحالية بس.
    const hasReasoning = !isTyping && !!(m.reasoning && m.reasoning.trim());
    c.innerHTML += `
      <div class="message">
        <div class="user-msg">${userContent}</div>
      </div>
      <div class="message">
        <div class="ai-msg" id="msg-${i}">${aiContent}${imgHtml}</div>
        ${isTyping ? '' : `
          <div class="msg-actions">
            <button class="msg-btn" onclick="copyText(${i})"><i class="fa-solid fa-copy"></i> نسخ الكل</button>
            <button class="msg-btn" onclick="regenerate(${i})"><i class="fa-solid fa-rotate"></i> إعادة</button>
            <button class="msg-btn" id="speak-btn-${i}" onclick="speakMessage(${i})"><i class="fa-solid fa-volume-high"></i> استماع</button>
            ${hasReasoning ? `<button class="msg-btn" id="reasoning-btn-${i}" onclick="toggleReasoningPanel(${i})"><img class="msg-btn-icon" src="/static/images/logo.png" alt=""> عرض التفكير</button>` : ''}
          </div>
          ${hasReasoning ? `<div class="reasoning-panel" id="reasoning-${i}">${escHtml(m.reasoning)}</div>` : ''}
        `}
      </div>`;
  });
  requestAnimationFrame(() => {
    document.querySelectorAll('.ai-msg pre').forEach(pre => {
      if (pre.querySelector('.code-header') || pre.querySelector('.file-header')) return;
      const code = pre.querySelector('code');
      const codeText = code ? code.innerText : '';
      const lang = (pre.dataset.lang || code?.className?.replace('lang-','') || 'code').toLowerCase();
      const filename = pre.dataset.filename || '';
      const header = document.createElement('div');
      if (filename) {
        // ملف حقيقي (صيغة lang:path من وضع المبرمج) — رأس مختلف فيه اسم
        // الملف وحجمه وزر "تحميل" حقيقي، بدل بادج اللغة العادي.
        pre.classList.add('file-block');
        header.className = 'file-header';
        header.innerHTML = `
          <span class="file-header-name"><i class="fa-solid fa-file-code"></i><bdi class="file-header-path">${escHtml(filename)}</bdi></span>
          <span class="file-header-actions">
            <span class="file-header-size">${humanFileSize(new Blob([codeText]).size)}</span>
            <button class="copy-code-btn" onclick="copyCodeBlock(this)" title="نسخ الكود">
              <i class="fa-regular fa-copy"></i>
            </button>
            <button class="download-file-btn" onclick="downloadCodeBlock(this)" title="تحميل الملف">
              <i class="fa-solid fa-download"></i> تحميل
            </button>
          </span>`;
      } else {
        header.className = 'code-header';
        header.innerHTML = `<span class="code-lang-badge">${lang}</span>
          <button class="copy-code-btn" onclick="copyCodeBlock(this)">
            <i class="fa-regular fa-copy"></i> نسخ
          </button>`;
      }
      pre.insertBefore(header, pre.firstChild);
    });

    // معاينة حية + شريط "تحميل المشروع كـ ZIP" — نفس الرد، بترتيب واحد:
    // المعاينة أولاً (لو فيه ملف HTML)، ثم زر الـZIP (لو ملفين فأكثر)،
    // فوق أول بلوك ملف مباشرة.
    document.querySelectorAll('.ai-msg').forEach(msg => {
      const fileBlocks = msg.querySelectorAll('pre.file-block');
      if (!fileBlocks.length) return;

      if (!msg.querySelector('.live-preview-card')) {
        const previewHtml = buildPreviewDoc(fileBlocks);
        if (previewHtml) msg.insertBefore(buildLivePreviewCard(previewHtml), fileBlocks[0]);
      }

      if (fileBlocks.length >= 2 && !msg.querySelector('.project-zip-bar')) {
        const bar = document.createElement('div');
        bar.className = 'project-zip-bar';
        bar.innerHTML = `<span><i class="fa-solid fa-box-archive"></i> ${fileBlocks.length} ملفات جاهزة</span>
          <button class="download-zip-btn" onclick="downloadProjectZip(this)">
            <i class="fa-solid fa-file-zipper"></i> تحميل المشروع كـ ZIP
          </button>`;
        msg.insertBefore(bar, fileBlocks[0]);
      }
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

// أسماء أدوات Groq المدمجة (config.py → BUILTIN_TOOLS) مقابل نص عربي
// مفهوم يظهر بمؤشر الحالة الحي.
const TOOL_STATUS_LABELS = {
  browser_search: 'يبحث في الويب...',
  code_interpreter: 'ينفذ كوداً...',
};

// يستبدل نقاط الكتابة الثابتة (اللي كانت تفضل مجمّدة طول مرحلة التفكير/
// البحث بصمت تام) بمؤشر حالة حي — نفس عنصر الفقاعة (#msg-{index})، بدون
// إعادة بناء الشات بالكامل، تماماً بنفس منطق updateStreamingContent أعلاه.
function updateStatusIndicator(index, label) {
  const el = document.getElementById('msg-' + index);
  if (el) {
    el.innerHTML = `<div class="status-indicator"><img class="status-icon" src="/static/images/logo.png" alt="">${escHtml(label)}</div>`;
    window.scrollTo(0, document.body.scrollHeight);
  }
}

// طي/فتح لوحة "عرض التفكير" — تعديل DOM مباشر بدل renderChat() الكامل،
// حتى لا يقطع أي بث حي شغّال بنفس اللحظة على رسالة تانية.
function toggleReasoningPanel(i) {
  const panel = document.getElementById('reasoning-' + i);
  const btn = document.getElementById('reasoning-btn-' + i);
  if (panel) panel.classList.toggle('open');
  if (btn) btn.classList.toggle('reasoning-open');
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

// يبني مستند HTML قابل للمعاينة من ملفات نفس الرد: يختار ملف HTML
// الرئيسي (يفضّل index.html)، ويدمج داخله أي CSS/JS محلي من نفس
// الرد بدل روابط <link>/<script src> الخارجية (لأن iframe عبر
// srcdoc ما يقدر يجلب ملفات فرعية منفصلة). روابط CDN/http الخارجية
// تُترك كما هي وتُحمَّل فعلياً من الشبكة كالعادة.
function buildPreviewDoc(fileBlockEls) {
  const files = {};
  fileBlockEls.forEach(pre => {
    const path = pre.dataset.filename || '';
    const code = pre.querySelector('code');
    if (path) files[path] = code ? code.innerText : '';
  });
  const htmlPaths = Object.keys(files).filter(p => /\.html?$/i.test(p));
  if (!htmlPaths.length) return null;
  const primaryPath = htmlPaths.find(p => /(^|\/)index\.html?$/i.test(p)) || htmlPaths[0];
  let html = files[primaryPath];

  function resolveLocal(ref) {
    if (!ref) return null;
    ref = ref.trim();
    if (/^([a-z][a-z0-9+.-]*:)?\/\//i.test(ref) || ref.startsWith('#') || ref.startsWith('data:')) return null;
    const cleaned = ref.split('?')[0].split('#')[0].replace(/^\.\//, '').replace(/^\//, '');
    if (files[cleaned] !== undefined) return files[cleaned];
    const base = cleaned.split('/').pop();
    const match = Object.keys(files).find(p => p.split('/').pop() === base);
    return match ? files[match] : null;
  }

  html = html.replace(/<link\b[^>]*>/gi, tag => {
    if (!/rel=["']stylesheet["']/i.test(tag)) return tag;
    const hrefMatch = tag.match(/href=["']([^"']+)["']/i);
    const css = hrefMatch ? resolveLocal(hrefMatch[1]) : null;
    return css !== null ? `<style>\n${css}\n</style>` : tag;
  });
  html = html.replace(/<script\b[^>]*\bsrc=["']([^"']+)["'][^>]*><\/script>/gi, (tag, src) => {
    const js = resolveLocal(src);
    return js !== null ? `<script>\n${js}\n</script>` : tag;
  });

  return html;
}

// بطاقة المعاينة المضمّنة بالرسالة — iframe داخل sandbox بدون
// allow-same-origin، أي الكود المولَّد يعمل داخل origin معزول تماماً:
// ما يقدر يقرأ كوكيز/جلسة/localStorage تبع الموقع الحقيقي، ولا يكدر
// ينادي أي endpoint بحساب المستخدم — بالضبط زي CodePen/JSFiddle.
function buildLivePreviewCard(previewHtml) {
  const wrap = document.createElement('div');
  wrap.className = 'live-preview-card';
  const toolbar = document.createElement('div');
  toolbar.className = 'live-preview-toolbar';
  toolbar.innerHTML = `<span class="live-preview-label"><i class="fa-solid fa-eye"></i> معاينة حية</span>
    <button class="live-preview-icon-btn" onclick="openFullscreenPreview(this)" title="ملء الشاشة">
      <i class="fa-solid fa-expand"></i>
    </button>`;
  const iframe = document.createElement('iframe');
  iframe.className = 'live-preview-frame';
  iframe.setAttribute('sandbox', 'allow-scripts allow-modals allow-forms');
  iframe.setAttribute('referrerpolicy', 'no-referrer');
  iframe.setAttribute('loading', 'lazy');
  iframe.srcdoc = previewHtml;
  wrap.appendChild(toolbar);
  wrap.appendChild(iframe);
  return wrap;
}

function openFullscreenPreview(btn) {
  const card = btn.closest('.live-preview-card');
  const srcFrame = card.querySelector('.live-preview-frame');
  document.getElementById('previewFullscreenFrame').srcdoc = srcFrame.srcdoc;
  document.getElementById('previewOverlay').classList.remove('hidden');
}

function closeFullscreenPreview() {
  document.getElementById('previewOverlay').classList.add('hidden');
  // نفرّغ srcdoc عشان نوقف أي سكربتات/صوت شغّالة بالمعاينة فور الإغلاق.
  document.getElementById('previewFullscreenFrame').srcdoc = '';
}

// يفتح نفس المعاينة بتبويب متصفح حقيقي منفصل — مفيد لو المستخدم يحب
// يشوفها بمساحة كاملة أو يشاركها بصفحة مستقلة.
//
// مهم: التبويب نفسه لازم يبقى بمحتوى من صياغتنا نحن (آمن)، والكود
// المولَّد فعلياً (من الذكاء الاصطناعي) يبقى بداخل iframe معزول
// بنفس sandbox المستخدم بالمعاينة المضمّنة. فتح الـblob مباشرة كان
// يفقد العزل بالكامل لأن blob: يرث نفس origin الصفحة اللي أنشأته.
function openPreviewInNewTab() {
  const html = document.getElementById('previewFullscreenFrame').srcdoc;
  if (!html) return;
  const escapedForAttr = html.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
  const wrapper = `<!DOCTYPE html><html><head><meta charset="utf-8">
<style>html,body{margin:0;height:100%}iframe{border:0;width:100%;height:100%}</style>
</head><body><iframe sandbox="allow-scripts allow-modals allow-forms"
referrerpolicy="no-referrer" srcdoc="${escapedForAttr}"></iframe></body></html>`;
  const blob = new Blob([wrapper], { type: 'text/html' });
  const url = URL.createObjectURL(blob);
  window.open(url, '_blank');
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

function humanFileSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  const kb = bytes / 1024;
  if (kb < 1024) return kb.toFixed(1) + ' KB';
  return (kb / 1024).toFixed(1) + ' MB';
}

// تحميل ملف واحد فعلياً (Blob + رابط تحميل مؤقت) — لا نسخ نص فقط.
// المتصفح ما يقدر ينشئ مجلدات فرعية حقيقية بالتحميل، فنستخدم اسم
// الملف الأخير من المسار فقط؛ المسار الكامل يبقى ظاهراً برأس البلوك
// نفسه حتى يعرف المستخدم وين يحط الملف يدوياً لو المشروع بمجلدات.
function downloadCodeBlock(btn) {
  const pre = btn.closest('pre');
  const code = pre.querySelector('code');
  const filename = pre.dataset.filename || 'file.txt';
  const text = code ? code.innerText : '';
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename.split('/').pop() || 'file.txt';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  showToast(`تم تحميل ${filename}`, 'success');
}

// تحميل كل ملفات نفس الرد كـ ZIP واحد (JSZip من cdnjs، محمّل بـindex.html)
// — يحافظ على مسارات الملفات الفرعية كاملة داخل الأرشيف نفسه.
async function downloadProjectZip(btn) {
  if (typeof JSZip === 'undefined') {
    showToast('تعذر تحميل أداة الضغط — تحقق من الاتصال وأعد المحاولة', 'error');
    return;
  }
  const msg = btn.closest('.ai-msg');
  const fileBlocks = msg ? msg.querySelectorAll('pre.file-block') : [];
  if (!fileBlocks.length) return;

  const zip = new JSZip();
  fileBlocks.forEach(pre => {
    const filename = pre.dataset.filename || 'file.txt';
    const code = pre.querySelector('code');
    zip.file(filename, code ? code.innerText : '');
  });

  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> جاري الضغط...';
  try {
    const blob = await zip.generateAsync({ type: 'blob' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'project.zip';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    showToast(`تم تحميل ${fileBlocks.length} ملفات كـ ZIP`, 'success');
  } catch (e) {
    showToast('تعذر إنشاء ملف ZIP', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }
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
async function streamChat(fd, { onFirstChunk, onChunk, onReasoning, onToolStart, onDone, onError } = {}) {
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
      } else if (evt.type === 'reasoning') {
        onReasoning && onReasoning(evt.content);
      } else if (evt.type === 'tool_start') {
        onToolStart && onToolStart(evt.tool);
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
      // أول إشارة تفكير أو استدعاء أداة توصل تبدّل نقاط الكتابة المجمّدة
      // بمؤشر حالة حي — بدل ما تفضل الفقاعة بلا أي تحديث طول مدة
      // التفكير/البحث ثم يظهر الرد كامل دفعة واحدة (المشكلة الأصلية).
      onReasoning: () => { if (c[msgIndex].ai === '__typing__') updateStatusIndicator(msgIndex, 'يفكر...'); },
      onToolStart: (tool) => {
        if (c[msgIndex].ai === '__typing__') updateStatusIndicator(msgIndex, TOOL_STATUS_LABELS[tool] || 'يعمل...');
      },
      onFirstChunk: () => { c[msgIndex].ai = ''; },
      onChunk: (rawAccum) => { c[msgIndex].ai = rawAccum; updateStreamingContent(msgIndex, rawAccum); },
      onDone: (evt) => {
        c[msgIndex].ai = evt.response;
        c[msgIndex].sanitized = true; // جاء من format_response المُعقَّم بالخادم
        c[msgIndex].rawAi = evt.rawResponse || evt.response;
        c[msgIndex].reasoning = evt.reasoning || '';
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
        c[msgIndex].ai = escHtml('حدث خطأ: ' + msg);
        c[msgIndex].sanitized = true;
        renderChat();
        if (triggeredByVoice && VoiceMode.state !== 'idle') startVoiceListening();
      }
    });
  } catch (err) {
    c[msgIndex].ai = escHtml('حدث خطأ: ' + err.message);
    c[msgIndex].sanitized = true;
    renderChat();
    showToast('تعذر الإرسال', 'error');
    if (triggeredByVoice && VoiceMode.state !== 'idle') startVoiceListening();
  } finally {
    // لو ما وصلنا هنا لا بـonDone ولا بـonError/catch (مثال: انقطاع
    // شبكة فعلي منتصف البث، أو تبديل تطبيق على موبايل يُعلّق الطلب)،
    // m.ai يبقى نصاً خاماً متراكماً من onChunk — لم يمرّ إطلاقاً بـ
    // bleach. لازم يُعقَّم هنا قبل ما يُحفَظ بـlocalStorage ويُعرَض
    // لاحقاً عبر renderChat كـHTML خام.
    if (c[msgIndex] && c[msgIndex].ai && c[msgIndex].ai !== '__typing__' && !c[msgIndex].sanitized) {
      c[msgIndex].ai = escHtml(c[msgIndex].ai) + '<br><em style="opacity:.6">(انقطع الاتصال قبل اكتمال الرد)</em>';
      c[msgIndex].sanitized = true;
      renderChat();
    }
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
      onReasoning: () => { if (c[i].ai === '__typing__') updateStatusIndicator(i, 'يفكر...'); },
      onToolStart: (tool) => {
        if (c[i].ai === '__typing__') updateStatusIndicator(i, TOOL_STATUS_LABELS[tool] || 'يعمل...');
      },
      onFirstChunk: () => { c[i].ai = ''; },
      onChunk: (rawAccum) => { c[i].ai = rawAccum; updateStreamingContent(i, rawAccum); },
      onDone: (evt) => {
        c[i].ai = evt.response; c[i].sanitized = true;
        c[i].rawAi = evt.rawResponse || evt.response;
        c[i].reasoning = evt.reasoning || '';
        c[i].id = evt.id;
        if (evt.imageUrl) c[i].imageUrl = evt.imageUrl; else delete c[i].imageUrl;
        saveChats(); renderChat();
      },
      onError: (msg) => { c[i].ai = escHtml('حدث خطأ: ' + msg); c[i].sanitized = true; renderChat(); }
    });
  } catch (err) {
    c[i].ai = escHtml('حدث خطأ: ' + err.message);
    c[i].sanitized = true;
    renderChat();
  } finally {
    // نفس حماية sendMessage: بث انقطع بلا onDone/onError يترك نصاً
    // خاماً غير مُعقَّم بـm.ai — يُعقَّم هنا قبل الحفظ والعرض.
    if (c[i] && c[i].ai && c[i].ai !== '__typing__' && !c[i].sanitized) {
      c[i].ai = escHtml(c[i].ai) + '<br><em style="opacity:.6">(انقطع الاتصال قبل اكتمال الرد)</em>';
      c[i].sanitized = true;
      renderChat();
    }
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
function copyText(i) {
  const c = chats[currentChatId] || [];
  const raw = (c[i] && (c[i].rawAi || c[i].ai)) || '';
  // بدون أي التفاف عبر innerHTML — rawAi نص شبه-عادي أصلاً، وتحليله
  // كـHTML لمجرد استخراج نص كان يُنفّذ onerror/onload حتى بعنصر غير
  // مرتبط بشجرة الصفحة (فخ أمني موثَّق لهذا النمط تحديداً).
  navigator.clipboard.writeText(raw)
    .then(() => showToast('تم النسخ', 'success'))
    .catch(() => showToast('تعذر النسخ', 'error'));
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
  currentVolume: 0,         // آخر RMS خام من المايك — يقرأه VoiceViz كل فريم أثناء الاستماع
  ttsAnalyser: null,        // Analyser مربوط بمقطع الكلام الحالي (وضع المكالمة فقط)
  ttsAnalyserData: null,
  ttsSourceNode: null,
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
  startVoiceViz();
  startVoiceListening();
}

function exitVoiceMode() {
  const wasListening = VoiceMode.state === 'listening';
  VoiceMode.state = 'idle';
  stopVoiceViz();
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
  if (VoiceMode.audioContext.state === 'suspended') VoiceMode.audioContext.resume().catch(() => {});
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
    VoiceMode.currentVolume = rms;  // يقرأها VoiceViz لرسم الموجة أثناء الاستماع
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
  updateVoiceVizGlow();
  if (VoiceViz.reduceMotion) drawVoiceOrb(performance.now());  // تفضيل تقليل الحركة: لقطة هادئة بدل حلقة رسم مستمرة
  const status = document.getElementById('voiceStatus');
  const labels = { listening: 'جاري الاستماع...', processing: 'جاري التفكير...', speaking: 'يتكلم...' };
  if (status) status.textContent = labels[state] || '';
  const stopBtn = document.getElementById('voiceModeStopBtn');
  if (stopBtn) stopBtn.classList.toggle('hidden', state !== 'listening');
}

// ─── تصور بصري تفاعلي لوضع المكالمة (كرة موجات حقيقية) ─────────
// كانت الكرة القديمة CSS keyframes بإيقاع ثابت دايماً — فبتحس نفس
// "نطة نابض" بغض النظر عن الصوت الفعلي. البديل هنا: canvas يرسم
// كل فريم بناءً على شدة صوت حقيقية (RMS): من المايك أثناء الاستماع
// (نفس تحليل VAD أعلاه)، ومن مقطع TTS نفسه أثناء الكلام عبر
// Web Audio Analyser مربوط بعنصر <audio>. الاستجابة سريعة للصعود
// وبطيئة جداً للنزول (VOICE_AMP_RISE/FALL) — هذا اللي يعطي إحساس
// "الموجة تكبر وتهدأ بسلاسة" المطلوب، مو قفزة مفاجئة.
// ملاحظة صراحة (نفس ملاحظة VAD فوق): ما عندي متصفح/مايك فعلي هنا
// لأختبرها بصوت بشري حقيقي، فالقيم أسفل (كل amp*/RMS_SCALE) قيم
// معقولة نظرياً بس ممكن تحتاج ضبط بسيط بعد أول تجربة حية عندك —
// لو حسيتها هادئة جداً أو حادة جداً، عدّل VOICE_RMS_MIC_SCALE أو
// VOICE_RMS_TTS_SCALE أدناه (تكبيرها = استجابة أقوى للصوت نفسه).
// نقطة تقنية أخرى تستاهل تجربة حية: بعض المتصفحات ممكن "تعزل"
// بيانات عنصر <audio> اللي مصدره data: URI عن الـ Analyser (حماية
// خصوصية قياسية)، وإذا صار هذا، الصوت بيشتغل عادي بس الموجة وقت
// الكلام بترجع تلقائياً لحركة تنفّس هادئة بدل التفاعل الحقيقي —
// مافيه انكسار بالميزة، بس يستاهل ملاحظة لو صار.
const VoiceViz = {
  canvas: null, ctx: null, wrap: null,
  raf: null, running: false,
  dpr: 1, size: 180,
  amp: 0,
  lastT: 0,
  ripples: [],
  reduceMotion: false,
};

const VOICE_RMS_MIC_SCALE = 7;    // يحوّل RMS المايك الخام (صغير عادة) لمدى تقريبي 0..1
const VOICE_RMS_TTS_SCALE = 5;    // نفس الفكرة لمقاطع صوت Wadi أثناء الكلام
const VOICE_AMP_RISE = 0.35;      // سرعة استجابة الموجة صعوداً (لحظة ارتفاع الصوت)
const VOICE_AMP_FALL = 0.06;      // سرعة الهبوط — بطيئة عمداً: "تخف بهدوء" مو توقف فجأة

function initVoiceViz() {
  VoiceViz.canvas = document.getElementById('voiceOrbCanvas');
  VoiceViz.wrap = document.getElementById('voiceOrbWrap');
  if (!VoiceViz.canvas) return;
  VoiceViz.ctx = VoiceViz.canvas.getContext('2d');
  VoiceViz.reduceMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  VoiceViz.dpr = Math.min(window.devicePixelRatio || 1, 2);
  VoiceViz.canvas.width = VoiceViz.size * VoiceViz.dpr;
  VoiceViz.canvas.height = VoiceViz.size * VoiceViz.dpr;
  VoiceViz.ctx.setTransform(VoiceViz.dpr, 0, 0, VoiceViz.dpr, 0, 0);
}

function voiceVizColors(mode) {
  const css = getComputedStyle(document.documentElement);
  const c1 = css.getPropertyValue('--accent1').trim() || '#00d2ff';
  const c2 = css.getPropertyValue('--accent2').trim() || '#00ff94';
  const c3 = css.getPropertyValue('--accent3').trim() || '#7c4dff';
  if (mode === 'processing') return { main: c3, sub: c1, glow: 'rgba(124,77,255,0.45)' };
  if (mode === 'speaking') return { main: c2, sub: c1, glow: 'rgba(0,255,148,0.45)' };
  return { main: c1, sub: c2, glow: 'rgba(0,210,255,0.4)' };  // listening وأي حالة افتراضية
}

function updateVoiceVizGlow() {
  if (!VoiceViz.wrap) return;
  VoiceViz.wrap.style.setProperty('--orb-glow-color', voiceVizColors(VoiceMode.state).glow);
}

function startVoiceViz() {
  if (!VoiceViz.canvas) return;
  VoiceViz.amp = 0;
  VoiceViz.ripples = [];
  updateVoiceVizGlow();
  if (VoiceViz.reduceMotion) {
    VoiceViz.running = false;
    drawVoiceOrb(performance.now());  // لقطة واحدة هادئة بدل حركة مستمرة
    return;
  }
  VoiceViz.running = true;
  VoiceViz.lastT = performance.now();
  if (VoiceViz.raf) cancelAnimationFrame(VoiceViz.raf);
  VoiceViz.raf = requestAnimationFrame(voiceVizFrame);
}

function stopVoiceViz() {
  VoiceViz.running = false;
  if (VoiceViz.raf) { cancelAnimationFrame(VoiceViz.raf); VoiceViz.raf = null; }
  if (VoiceMode.ttsSourceNode) { try { VoiceMode.ttsSourceNode.disconnect(); } catch (e) {} }
  if (VoiceMode.ttsAnalyser) { try { VoiceMode.ttsAnalyser.disconnect(); } catch (e) {} }
  VoiceMode.ttsSourceNode = null;
  VoiceMode.ttsAnalyser = null;
}

function voiceTargetAmplitude() {
  if (VoiceMode.state === 'listening') {
    return Math.min(1, (VoiceMode.currentVolume || 0) * VOICE_RMS_MIC_SCALE);
  }
  if (VoiceMode.state === 'speaking' && VoiceMode.ttsAnalyser && VoiceMode.ttsAnalyserData) {
    VoiceMode.ttsAnalyser.getByteTimeDomainData(VoiceMode.ttsAnalyserData);
    let sum = 0;
    const d = VoiceMode.ttsAnalyserData;
    for (let i = 0; i < d.length; i++) { const v = (d[i] - 128) / 128; sum += v * v; }
    return Math.min(1, Math.sqrt(sum / d.length) * VOICE_RMS_TTS_SCALE);
  }
  return 0;  // processing / idle — التنفّس الهادئ بداخل drawVoiceOrb يتكفّل بالحركة
}

function voiceVizFrame(now) {
  if (!VoiceViz.running) return;
  const dt = Math.min(64, now - VoiceViz.lastT);  // سقف يمنع قفزة الحركة لو الصفحة كانت بالخلفية
  VoiceViz.lastT = now;

  const target = voiceTargetAmplitude();
  const rate = target > VoiceViz.amp ? VOICE_AMP_RISE : VOICE_AMP_FALL;
  const prevAmp = VoiceViz.amp;
  VoiceViz.amp += (target - VoiceViz.amp) * rate * (dt / 16.67);
  if (VoiceViz.amp < 0.002) VoiceViz.amp = 0;

  // ريبل جديد (حلقة تنبعث كالماء) عند قفزة صوت واضحة فقط — لا كل فريم
  if (VoiceViz.amp - prevAmp > 0.16 && VoiceViz.ripples.length < 4) {
    VoiceViz.ripples.push({ r: 44, alpha: 0.5 });
  }

  drawVoiceOrb(now);
  VoiceViz.raf = requestAnimationFrame(voiceVizFrame);
}

function drawVoiceOrb(now) {
  const ctx = VoiceViz.ctx;
  if (!ctx) return;
  const s = VoiceViz.size, cx = s / 2, cy = s / 2;
  ctx.clearRect(0, 0, s, s);

  const colors = voiceVizColors(VoiceMode.state);
  const amp = VoiceViz.reduceMotion ? 0.15 : VoiceViz.amp;
  const t = now / 1000;

  const breathe = VoiceViz.reduceMotion ? 0 : (Math.sin(t * 0.9) * 0.5 + 0.5);  // تنفّس هادئ دائم حتى بصمت
  const restWobble = 3 + breathe * 3;
  const baseR = 40 + amp * 14;
  const wobbleAmp = restWobble + amp * 20;

  if (!VoiceViz.reduceMotion) {
    for (let i = VoiceViz.ripples.length - 1; i >= 0; i--) {
      const rp = VoiceViz.ripples[i];
      rp.r += 0.9 + amp * 1.8;
      rp.alpha *= 0.955;
      if (rp.alpha < 0.03 || rp.r > 82) { VoiceViz.ripples.splice(i, 1); continue; }
      ctx.beginPath();
      ctx.arc(cx, cy, rp.r, 0, Math.PI * 2);
      ctx.strokeStyle = colorToRgba(colors.main, rp.alpha * 0.55);
      ctx.lineWidth = 1.6;
      ctx.stroke();
    }
  }

  drawWaveBlob(ctx, cx, cy, baseR + 4, wobbleAmp * 0.8, 5, t * 1.3, colors.sub, 0.18 + amp * 0.22);
  drawWaveBlob(ctx, cx, cy, baseR, wobbleAmp, 4, t * 1.7 + 2, colors.main, 0.30 + amp * 0.35);

  const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, baseR * 0.9);
  core.addColorStop(0, colorToRgba('#ffffff', 0.9));
  core.addColorStop(0.35, colorToRgba(colors.main, 0.85));
  core.addColorStop(1, colorToRgba(colors.main, 0));
  ctx.beginPath();
  ctx.arc(cx, cy, baseR * 0.62, 0, Math.PI * 2);
  ctx.fillStyle = core;
  ctx.fill();

  if (VoiceMode.state === 'processing' && !VoiceViz.reduceMotion) {
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(t * 1.1);
    ctx.beginPath();
    ctx.arc(0, 0, baseR + 13, -0.5, 0.9);
    ctx.strokeStyle = colorToRgba(colors.sub, 0.55);
    ctx.lineWidth = 3;
    ctx.lineCap = 'round';
    ctx.stroke();
    ctx.restore();
  }
}

function drawWaveBlob(ctx, cx, cy, baseR, wobbleAmp, freq, phase, color, alpha) {
  const points = 72;
  ctx.beginPath();
  for (let i = 0; i <= points; i++) {
    const angle = (i / points) * Math.PI * 2;
    const wobble = Math.sin(angle * freq + phase) * wobbleAmp
                 + Math.sin(angle * (freq * 0.5) - phase * 0.6) * wobbleAmp * 0.4;
    const r = baseR + wobble;
    const x = cx + Math.cos(angle) * r;
    const y = cy + Math.sin(angle) * r;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.closePath();
  const grad = ctx.createRadialGradient(cx, cy, baseR * 0.2, cx, cy, baseR + wobbleAmp);
  grad.addColorStop(0, colorToRgba(color, alpha));
  grad.addColorStop(1, colorToRgba(color, 0));
  ctx.fillStyle = grad;
  ctx.fill();
}

function colorToRgba(color, alpha) {
  color = (color || '').trim();
  if (color.startsWith('rgb')) return color;  // يقبل rgb/rgba جاهزة كما هي
  let h = color.replace('#', '');
  if (h.length === 3) h = h.split('').map(c => c + c).join('');
  const num = parseInt(h, 16);
  if (isNaN(num)) return `rgba(0,210,255,${alpha})`;
  return `rgba(${(num >> 16) & 255},${(num >> 8) & 255},${num & 255},${alpha})`;
}

function attachTtsAnalyser(audioEl) {
  // يُستخدم فقط داخل وضع المكالمة الصوتية الكاملة (انظر استدعاءها بأسفل
  // playNextInQueue) — عشان ما نفتح/نربط Web Audio إضافي بلا داعي لما
  // المستخدم يسمع رسالة عادية بزر "استماع" برّه وضع المكالمة.
  try {
    if (!VoiceMode.audioContext || VoiceMode.audioContext.state === 'closed') {
      VoiceMode.audioContext = new (window.AudioContext || window.webkitAudioContext)();
    }
    const ctx = VoiceMode.audioContext;
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    if (VoiceMode.ttsSourceNode) { try { VoiceMode.ttsSourceNode.disconnect(); } catch (e) {} }
    if (VoiceMode.ttsAnalyser) { try { VoiceMode.ttsAnalyser.disconnect(); } catch (e) {} }
    const source = ctx.createMediaElementSource(audioEl);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    analyser.connect(ctx.destination);
    VoiceMode.ttsSourceNode = source;
    VoiceMode.ttsAnalyser = analyser;
    VoiceMode.ttsAnalyserData = new Uint8Array(analyser.frequencyBinCount);
  } catch (err) {
    // نادر، وغير قاتل: الصوت يكمل تشغيله عادي، بس الموجة ترجع لتنفّس
    // هادئ بدل التفاعل الحقيقي (انظر ملاحظة الخصوصية بأعلى قسم VoiceViz)
    console.error('Voice viz analyser error:', err && err.name, err && err.message);
    VoiceMode.ttsSourceNode = null;
    VoiceMode.ttsAnalyser = null;
  }
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
            // نعرض الخطأ دائماً ولو بوضع silent — الصمت مقصود للحالات
            // العادية (مثل عدم وجود نص للقراءة)، لا لإخفاء فشل حقيقي عن
            // المستخدم بوضع المكالمة الصوتية ويتركه بلا أي تفسير.
            showToast(evt.error || 'تعذر توليد الصوت', 'error');
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
      showToast('تعذر توليد الصوت', 'error');
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
  const audio = new Audio('data:audio/wav;base64,' + clip);
  currentAudioPlayer = audio;
  if (VoiceMode.state !== 'idle') attachTtsAnalyser(audio);  // داخل وضع المكالمة فقط — يغذّي الموجة بصوت الرد الفعلي
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
initVoiceViz();
