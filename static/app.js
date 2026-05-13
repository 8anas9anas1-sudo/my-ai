let currentChatId = null;
let isStreaming = false;

const chatBox = document.getElementById('chat-box');
const userInput = document.getElementById('user-input');
const fileInput = document.getElementById('file-input');
const fileNameDiv = document.getElementById('file-name');
const shareBtn = document.getElementById('share-btn');

// Auto resize textarea
userInput.addEventListener('input', () => {
    userInput.style.height = 'auto';
    userInput.style.height = userInput.scrollHeight + 'px';
});

// Enter to send
userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' &&!e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

// File name display
fileInput.addEventListener('change', () => {
    if (fileInput.files[0]) {
        fileNameDiv.textContent = `📎 ${fileInput.files[0].name}`;
    } else {
        fileNameDiv.textContent = '';
    }
});

function newChat() {
    currentChatId = null;
    chatBox.innerHTML = '<div class="welcome"><h1>محادثة جديدة 👋</h1></div>';
    shareBtn.style.display = 'none';
    loadChatList();
}

async function sendMessage() {
    if (isStreaming) return;

    const message = userInput.value.trim();
    const file = fileInput.files[0];
    const mode = document.getElementById('mode-select').value;

    if (!message &&!file) return;

    isStreaming = true;
    document.getElementById('send-btn').disabled = true;

    // Add user message to UI
    addMessage('user', message || '📎 ملف مرفق');
    userInput.value = '';
    userInput.style.height = 'auto';
    fileInput.value = '';
    fileNameDiv.textContent = '';

    // Create AI message bubble
    const aiMsgId = 'ai-' + Date.now();
    addMessage('ai', '', aiMsgId);

    // Prepare form data
    const formData = new FormData();
    formData.append('message', message);
    formData.append('mode', mode);
    if (currentChatId) formData.append('chat_id', currentChatId);
    if (file) formData.append('file', file);

    // Stream response
    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            body: formData
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let aiResponse = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value);
            const lines = chunk.split('\n');

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const data = line.slice(6);
                    if (data === '[DONE]') {
                        isStreaming = false;
                        document.getElementById('send-btn').disabled = false;
                        shareBtn.style.display = 'block';
                        loadChatList();
                        return;
                    }
                    if (data.startsWith('[CHAT_ID]')) {
                        currentChatId = data.replace('[CHAT_ID]', '').replace('[/CHAT_ID]', '');
                        continue;
                    }
                    aiResponse += data;
                    document.getElementById(aiMsgId).innerHTML = marked.parse(aiResponse);
                    chatBox.scrollTop = chatBox.scrollHeight;
                }
            }
        }
    } catch (err) {
        document.getElementById(aiMsgId).innerHTML = 'صار خطأ في الاتصال';
        console.error(err);
    } finally {
        isStreaming = false;
        document.getElementById('send-btn').disabled = false;
    }
}

function addMessage(role, content, id = null) {
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${role}`;
    msgDiv.innerHTML = `
        <div class="avatar">${role === 'user'? 'أنت' : 'AI'}</div>
        <div class="bubble" ${id? `id="${id}"` : ''}>${role === 'user'? content : ''}</div>
    `;
    chatBox.appendChild(msgDiv);
    chatBox.scrollTop = chatBox.scrollHeight;
}

async function loadChatList() {
    try {
        const res = await fetch('/api/chats');
        const chats = await res.json();
        const listDiv = document.getElementById('chat-list');
        listDiv.innerHTML = '';
        chats.forEach(chat => {
            const item = document.createElement('div');
            item.className = 'chat-item';
            item.textContent = chat.title;
            item.onclick = () => loadChat(chat.chat_id);
            listDiv.appendChild(item);
        });
    } catch (err) {
        console.error(err);
    }
}

async function shareChat() {
    if (!currentChatId) return;
    // نبعت طلب للباك إند يولد التوكن
    alert('ميزة المشاركة تشتغل من السيرفر - بنضيفها في التحديث الجاي');
}

// Load chats on start
loadChatList();
