import time
import re
import logging
import bleach
from functools import wraps
from flask import request, jsonify, session

logger = logging.getLogger(__name__)

# ─── Rate Limiting ───────────────────────────────
user_requests = {}

def is_rate_limited(ip):
    """يمنع السبام: 20 طلب كل دقيقة"""
    now = time.time()
    if ip not in user_requests:
        user_requests[ip] = []

    # امسح الطلبات القديمة
    user_requests[ip] = [t for t in user_requests[ip] if now - t < 60]

    if len(user_requests[ip]) >= 20:
        logger.warning(f"Rate limit hit for IP: {ip}")
        return True

    user_requests[ip].append(now)
    return False

def require_not_limited(f):
    """Decorator نستخدمه في chat.py"""
    @wraps(f)
    def decorated(*args, **kwargs):
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        if is_rate_limited(ip):
            return jsonify({"error": "طلبات كثيرة. انتظر دقيقة"}), 429
        return f(*args, **kwargs)
    return decorated

# ─── Prompt Injection Protection ─────────────────
INJECTION_PATTERNS = [
    r"ignore (all|previous|above) instructions",
    r"you are now",
    r"system prompt",
    r"reveal your instructions",
    r"تجاهل التعليمات",
    r"أنت الآن",
    r"اكشف البرومبت"
]

def is_prompt_injection(text):
    """يكشف محاولات اختراق البوت"""
    text_lower = text.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text_lower):
            logger.warning(f"Prompt injection detected: {text[:50]}")
            return True
    return False

# ─── Input Sanitization ──────────────────────────
def sanitize_input(text):
    """ينظف HTML/XSS من مدخلات اليوزر"""
    if not text:
        return ""
    # احذف أي HTML + خلي 5000 حرف بس
    clean = bleach.clean(text, tags=[], strip=True)
    return clean[:5000]
