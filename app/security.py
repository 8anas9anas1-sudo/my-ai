"""
كل ما يخص الأمان: تشفير كلمات المرور، تعقيم المدخلات، فلتر الحقن،
وعنوان IP الحقيقي للمستخدم خلف بروكسي Render.
"""
import os
import re
import hashlib
import secrets
import bcrypt
from flask import request

from app.config import Config


# ─── كلمات المرور ──────────────────────────────────────────────
def hash_password(password: str) -> str:
    """bcrypt يولّد ملحاً عشوائياً منفصلاً لكل مستخدم تلقائياً."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def is_legacy_sha256_hash(stored_hash: str) -> bool:
    """هاش SHA-256 القديم = 64 حرف hex. هاش bcrypt يبدأ دائماً بـ $2b$/$2a$."""
    return bool(re.fullmatch(r'[0-9a-f]{64}', stored_hash or ''))


def legacy_sha256_hash(password: str) -> str:
    """يطابق فقط الملح الافتراضي القديم؛ للحسابات المنشأة قبل الترقية لـ bcrypt."""
    legacy_salt = os.environ.get("PASSWORD_SALT", "anas-wadi-salt-2026")
    return hashlib.sha256(f"{legacy_salt}{password}".encode()).hexdigest()


def verify_legacy_password(password: str, stored_hash: str) -> bool:
    return secrets.compare_digest(legacy_sha256_hash(password), stored_hash)


def verify_bcrypt_password(password: str, stored_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), stored_hash.encode())
    except ValueError:
        return False


# ─── تعقيم المدخلات وفلتر الحقن ────────────────────────────────
def sanitize_input(text: str) -> str:
    text = re.sub(r'<\|.*?\|>', '', text or '')
    text = re.sub(r'\[INST\].*?\[/INST\]', '', text, flags=re.DOTALL)
    return text[:Config.MAX_MSG_LENGTH].strip()


def is_prompt_injection(text: str) -> bool:
    text_lower = (text or '').lower()
    return any(re.search(pattern, text_lower) for pattern in Config.BANNED_PATTERNS)


# ─── عنوان IP الحقيقي خلف بروكسي Render ────────────────────────
def get_client_ip() -> str:
    return request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
