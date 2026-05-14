# ============================================
# Anas Wadi - Production-Ready Flask Application
# Full Refactor with Security, Performance, Scalability & Observability
# ============================================
import os
import re
import json
import time
import hashlib
import secrets
import io
from datetime import datetime
from functools import wraps
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any, List

import bcrypt
import redis
import requests
import mistune  # FIX #2: نقل الاستيراد للأعلى — لا داعي لاستيراده داخل الدالة في كل استدعاء
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
import bleach
import PyPDF2
from flask import (
    Flask, request, jsonify, session, redirect, url_for, render_template_string,
    Response, make_response, send_from_directory
)
from flask_session import Session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from celery import Celery
from werkzeug.utils import secure_filename

# ============================================
# Configuration
# ============================================
class Config:
    # Flask
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "change-me-in-production-2026")
    # Session: filesystem (لا يحتاج Redis — متوافق مع Render المجاني مثل الملف القديم)
    # لتفعيل Redis: ضع SESSION_TYPE=redis في متغيرات البيئة مع REDIS_URL
    SESSION_TYPE = os.environ.get("SESSION_TYPE", "filesystem")
    SESSION_FILE_DIR = os.environ.get("SESSION_FILE_DIR", "/tmp/flask_sessions")
    SESSION_REDIS = None  # يُهيَّأ فقط إذا كان SESSION_TYPE=redis
    SESSION_PERMANENT = False
    SESSION_USE_SIGNER = True
    # Database
    DATABASE_URL = os.environ.get("DATABASE_URL", "")
    # Celery
    CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/1")
    CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")
    # API Keys
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
    # Upload
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "static/uploads")
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'pdf', 'txt'}
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB
    # Rate Limiting — يستخدم الذاكرة إذا لم يكن Redis متاحاً
    RATELIMIT_STORAGE_URL = os.environ.get("RATELIMIT_STORAGE_URL", "memory://")
    # Security
    BCRYPT_ROUNDS = 12
    # SSL mode for DB — set DB_SSL_MODE=disable if your DB doesn't support SSL
    DB_SSL_MODE = os.environ.get("DB_SSL_MODE", "require")

# ─── Lazy Redis initializer (اختياري — يُستخدم فقط إذا SESSION_TYPE=redis) ───
def init_redis() -> Optional[redis.Redis]:
    """
    يُنشئ اتصال Redis فقط إذا كان SESSION_TYPE=redis.
    على Render المجاني بدون Redis: SESSION_TYPE=filesystem يتجاوز هذه الدالة.
    """
    if os.environ.get("SESSION_TYPE", "filesystem") != "redis":
        return None
    if Config.SESSION_REDIS is None:
        Config.SESSION_REDIS = redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
            decode_responses=False
        )
    return Config.SESSION_REDIS

# ============================================
# Application Factory
# ============================================
app = Flask(__name__)
# إنشاء مجلد الجلسات إذا كان النوع filesystem
if os.environ.get("SESSION_TYPE", "filesystem") == "filesystem":
    os.makedirs(os.environ.get("SESSION_FILE_DIR", "/tmp/flask_sessions"), exist_ok=True)
else:
    init_redis()
app.config.from_object(Config)

# ─── Rate limit key: IP للزوار، email للمستخدمين المسجلين ─────
def get_rate_limit_key() -> str:
    """
    يستخدم email المستخدم إذا كان مسجل الدخول، وإلا IP.
    يمنع استنزاف الحصة عبر حسابات متعددة من نفس IP أو العكس.
    """
    if 'user' in session:
        return session['user']['email']
    return get_remote_address()

# Initialize extensions
Session(app)
limiter = Limiter(
    app=app,
    key_func=get_rate_limit_key,
    default_limits=["30 per minute"]
)

# ─── Celery factory ────────────────────────
def make_celery(app):
    celery = Celery(
        app.import_name,
        backend=app.config['CELERY_RESULT_BACKEND'],
        broker=app.config['CELERY_BROKER_URL']
    )
    celery.conf.update(app.config)

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery

celery = make_celery(app)

# ============================================
# Logging Setup
# ============================================
def setup_logging(app):
    if not app.debug:
        os.makedirs('logs', exist_ok=True)
        # Main log
        file_handler = RotatingFileHandler('logs/app.log', maxBytes=10*1024*1024, backupCount=5)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
        ))
        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)
        # Error log
        error_handler = RotatingFileHandler('logs/error.log', maxBytes=5*1024*1024, backupCount=3)
        error_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s'
        ))
        error_handler.setLevel(logging.ERROR)
        app.logger.addHandler(error_handler)
        app.logger.setLevel(logging.INFO)
        app.logger.info('Application startup')

setup_logging(app)

# ============================================
# Database Pool
# ============================================
db_pool: Optional[ConnectionPool] = None

def init_db_pool() -> ConnectionPool:
    global db_pool
    if db_pool is None:
        db_url = Config.DATABASE_URL
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        # FIX #3: في psycopg3 يجب تمرير sslmode كـ query parameter داخل conninfo
        # وليس في kwargs — وضعه في kwargs يُتجاهل أو يسبب خطأ حسب الإصدار
        if "sslmode" not in db_url:
            separator = "&" if "?" in db_url else "?"
            db_url = f"{db_url}{separator}sslmode={Config.DB_SSL_MODE}"
        db_pool = ConnectionPool(
            conninfo=db_url,
            min_size=2,
            max_size=10,
            kwargs={"row_factory": dict_row}
        )
    return db_pool

def get_db_connection():
    pool = init_db_pool()
    return pool.getconn()

def return_db_connection(conn):
    if db_pool and conn:
        db_pool.putconn(conn)

# ─── Database initialization (called once) ─
def init_db_tables():
    conn = get_db_connection()
    if not conn:
        app.logger.error("Cannot initialize database - no connection")
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW(),
                    onboarding_seen BOOLEAN DEFAULT FALSE
                );
                CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id SERIAL PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    user_email TEXT NOT NULL,
                    user_name TEXT,
                    user_message TEXT,
                    ai_response TEXT,
                    raw_ai TEXT,
                    mode TEXT DEFAULT 'fast',
                    image_url TEXT,
                    file_name TEXT,
                    share_token TEXT UNIQUE,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_chat_id ON conversations(chat_id);
                CREATE INDEX IF NOT EXISTS idx_user_email ON conversations(user_email);
                CREATE INDEX IF NOT EXISTS idx_share_token ON conversations(share_token);
            """)
            conn.commit()
            app.logger.info("Database tables ensured")
    except Exception as e:
        app.logger.error(f"Database init error: {str(e)}")
    finally:
        return_db_connection(conn)

with app.app_context():
    init_db_tables()

# ============================================
# Security Helpers
# ============================================

# ─── دعم الترقية من SHA256 القديم إلى bcrypt ───
OLD_SALT = os.environ.get("PASSWORD_SALT", "anas-wadi-salt-2026")

def _hash_old(password: str) -> str:
    """تشفير SHA256 القديم — للقراءة فقط عند التحقق"""
    return hashlib.sha256(f"{OLD_SALT}{password}".encode()).hexdigest()

def hash_password(password: str) -> str:
    """تشفير bcrypt — يُستخدم لجميع الحسابات الجديدة وعند الترقية"""
    return bcrypt.hashpw(
        password.encode('utf-8'),
        bcrypt.gensalt(Config.BCRYPT_ROUNDS)
    ).decode('utf-8')

def check_password(password: str, hashed: str) -> bool:
    """يدعم bcrypt ($2b$) والتشفير القديم SHA256 معاً"""
    if hashed.startswith('$2b$'):
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    return _hash_old(password) == hashed

# ============================================
# Prompt Injection Protection
# ============================================
# FIX #5: Strengthened patterns to reduce easy bypass
BANNED_PATTERNS = [
    r'ignore\s+(?:\w+\s+)*(?:previous|all|your)\s+instructions',
    r'(system|user)\s+prompt',
    r'you\s+are\s+now',
    r'jail\s*break',
    r'pretend\s+you',
    r'act\s+as\s+if',
    r'forget\s+your',
    r'reset\s+prompt',
    r'new\s+persona',
    r'deceive',
    r'disregard\s+(?:all\s+)?(?:previous\s+)?instructions',
    r'override\s+(?:your\s+)?(?:system\s+)?instructions',
]

def is_prompt_injection(text: str) -> bool:
    text_lower = text.lower()
    for pattern in BANNED_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True
    # Only flag extreme repetition: very short vocabulary across a very long text.
    # Threshold raised to 5000 chars to avoid false-positives on code pastes / number lists.
    words = text.split()
    if len(text) > 5000 and len(set(words)) < 30:
        return True
    return False

# ============================================
# User Authentication (DB operations)
# ============================================
def create_user(email: str, password: str, name: str) -> tuple[bool, str]:
    conn = get_db_connection()
    if not conn:
        return False, "Database connection error"
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (email, password_hash, name, onboarding_seen) VALUES (%s, %s, %s, FALSE)",
                (email.lower().strip(), hash_password(password), name.strip())
            )
            conn.commit()
        return True, "Account created successfully"
    except psycopg.errors.UniqueViolation:
        return False, "Email already exists"
    except Exception as e:
        app.logger.error(f"Create user error: {str(e)}")
        return False, f"Error: {str(e)}"
    finally:
        return_db_connection(conn)

def verify_user(email: str, password: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT email, name, password_hash, onboarding_seen FROM users WHERE email = %s",
                (email.lower().strip(),)
            )
            user = cur.fetchone()
            if user and check_password(password, user['password_hash']):
                # إذا كان التشفير قديماً (SHA256)، نُحدّثه إلى bcrypt تلقائياً
                if not user['password_hash'].startswith('$2b$'):
                    new_hash = hash_password(password)
                    cur.execute(
                        "UPDATE users SET password_hash = %s WHERE email = %s",
                        (new_hash, email.lower().strip())
                    )
                    conn.commit()
                    app.logger.info(f"Upgraded password hash to bcrypt for {email.lower().strip()}")
                return {
                    'email': user['email'],
                    'name': user['name'],
                    'onboarding_seen': user['onboarding_seen']
                }
        return None
    except Exception as e:
        app.logger.error(f"Verify user error: {str(e)}")
        return None
    finally:
        return_db_connection(conn)

def update_onboarding_seen(email: str):
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET onboarding_seen = TRUE WHERE email = %s",
                (email.lower().strip(),)
            )
            conn.commit()
    except Exception as e:
        app.logger.error(f"Onboarding update error: {str(e)}")
    finally:
        return_db_connection(conn)

def delete_user_account(email: str, password: str) -> tuple[bool, str]:
    conn = get_db_connection()
    if not conn:
        return False, "Database connection error"
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT password_hash FROM users WHERE email = %s",
                (email.lower().strip(),)
            )
            row = cur.fetchone()
            if not row or not check_password(password, row['password_hash']):
                return False, "Invalid password"
            cur.execute(
                "DELETE FROM conversations WHERE user_email = %s",
                (email.lower().strip(),)
            )
            cur.execute(
                "DELETE FROM users WHERE email = %s",
                (email.lower().strip(),)
            )
            conn.commit()
        return True, "Account deleted"
    except Exception as e:
        app.logger.error(f"Delete user error: {str(e)}")
        return False, f"Error: {str(e)}"
    finally:
        return_db_connection(conn)

# ============================================
# Conversation Data (DB)
# ============================================
def save_message(
    chat_id: str,
    user_email: str,
    user_name: str,
    user_message: str,
    ai_response: str,
    raw_ai: str,
    mode: str,
    image_url: Optional[str] = None,
    file_name: Optional[str] = None,
    share_token: Optional[str] = None
) -> bool:
    # FIX #2: Do not save if chat_id or ai_response is empty
    if not chat_id or not raw_ai:
        app.logger.warning("save_message skipped: missing chat_id or ai_response")
        return False
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO conversations
                    (chat_id, user_email, user_name, user_message, ai_response,
                     raw_ai, mode, image_url, file_name, share_token)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                chat_id, user_email, user_name, user_message,
                ai_response, raw_ai, mode, image_url, file_name, share_token
            ))
            conn.commit()
        return True
    except Exception as e:
        app.logger.error(f"Save message error: {str(e)}")
        return False
    finally:
        return_db_connection(conn)

def get_user_chats(user_email: str) -> List[Dict]:
    """
    FIX #4 (محسَّن): نستخدم subquery لجلب أول رسالة زمنياً كعنوان للمحادثة
    بدلاً من MIN(user_message) الذي يُعطي أول رسالة أبجدياً لا زمنياً.
    MAX(created_at) يضمن ظهور المحادثة الأحدث نشاطاً في الأعلى.
    """
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    chat_id,
                    (
                        SELECT user_message FROM conversations c2
                        WHERE c2.chat_id = conversations.chat_id
                        ORDER BY created_at ASC
                        LIMIT 1
                    ) AS user_message,
                    MAX(created_at) AS created_at
                FROM conversations
                WHERE user_email = %s
                GROUP BY chat_id
                ORDER BY MAX(created_at) DESC
            """, (user_email,))
            rows = cur.fetchall()
        return rows
    except Exception as e:
        app.logger.error(f"Get user chats error: {str(e)}")
        return []
    finally:
        return_db_connection(conn)

def get_chat_messages(chat_id: str, user_email: str) -> List[Dict]:
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_message, ai_response, raw_ai, image_url, file_name, created_at
                FROM conversations
                WHERE chat_id = %s AND user_email = %s
                ORDER BY created_at ASC
            """, (chat_id, user_email))
            rows = cur.fetchall()
        return rows
    except Exception as e:
        app.logger.error(f"Get chat messages error: {str(e)}")
        return []
    finally:
        return_db_connection(conn)

def delete_chat_from_db(chat_id: str, user_email: str) -> bool:
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM conversations WHERE chat_id = %s AND user_email = %s",
                (chat_id, user_email)
            )
            conn.commit()
        return True
    except Exception as e:
        app.logger.error(f"Delete chat error: {str(e)}")
        return False
    finally:
        return_db_connection(conn)

def create_share_token(chat_id: str, user_email: str) -> Optional[str]:
    token = secrets.token_urlsafe(16)
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE conversations
                   SET share_token = %s
                   WHERE chat_id = %s AND user_email = %s AND share_token IS NULL""",
                (token, chat_id, user_email)
            )
            conn.commit()
        return token
    except Exception as e:
        app.logger.error(f"Share token error: {str(e)}")
        return None
    finally:
        return_db_connection(conn)

def get_shared_chat_by_token(token: str) -> Optional[List[Dict]]:
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_message, ai_response, image_url, file_name, created_at
                FROM conversations
                WHERE share_token = %s
                ORDER BY created_at ASC
            """, (token,))
            rows = cur.fetchall()
        return rows
    except Exception as e:
        app.logger.error(f"Get shared chat error: {str(e)}")
        return None
    finally:
        return_db_connection(conn)

# ============================================
# AI Helpers
# ============================================
IDENTITY_TRIGGERS = [
    'من انت', 'من أنت', 'عرف بنفسك', 'من تكون', 'ما اسمك',
    'شن اسمك', 'who are you', 'اسمك ايش', 'اسمك شن', 'عرفني عليك'
]

MODE_PROMPTS = {
    'fast': """أنت Wadi — ذكاء اصطناعي متطور صنعه المهندس Anas Wadi من ليبيا 🇱🇾.

شخصيتك:
- ذكي، واضح، مباشر، وفيك شخصية حقيقية — مش مجرد آلة بتجيب إجابات.
- تقرأ المزاج والطاقة من الرسالة وتتكيف معها.
- إذا الشخص متحمس → أنت متحمس. إذا بيفكر → أنت معاه في التفكير. إذا حزين → هادئ وإنساني.
- ردودك فيها روح وحضور — مش كلام بارد ومعلب.

قواعد الرد:
- افهم المقصد الحقيقي وراء الكلام، مش بس الكلمات.
- استخدم **Bold** للمصطلحات والأفكار المهمة.
- نظم الإجابات الطويلة بعناوين وفقرات واضحة.
- لا تطول بدون قيمة — كل كلمة تكون لها وزن.
- تذكر سياق المحادثة واستخدمه في ردودك.
- إذا الموضوع مثير → ابدأ بجملة تشعل الاهتمام.
- لا تبدأ كل رد بـ "بالطبع" أو "بالتأكيد" — تنوع في البدايات.""",

    'thinker': """أنت Wadi في وضع التفكير العميق — مفكر استراتيجي وخبير تحليلي صنعه Anas Wadi.

شخصيتك:
- تعشق المشاكل المعقدة — كأنها ألغاز تستحق الحل.
- تفكر بصوت عالٍ، تريح الشخص وتشعره أنك معاه في الرحلة.
- كل تحليل عندك فيه عمق وزاوية نظر مختلفة.

قواعد الرد:
- ابدأ بفهم المشكلة قبل أي شيء ثم حللها خطوة بخطوة.
- قدم الحلول من الأقوى للأضعف مع التبرير.
- استخدم ## للعناوين الرئيسية و### للفرعية.
- دائماً أضف **الخلاصة** في النهاية — مختصرة وقوية.
- اكتشف الأبعاد الخفية التي لم يسألها الشخص لكنها مهمة.""",

    'funny': """أنت Wadi في وضع الفكاهة — ذكي، خفيف الظل، ومضحك بشكل طبيعي. صنعه Anas Wadi 😄

شخصيتك:
- روحك خفيفة لكن عقلك حاضر — الفكاهة عندك ذكية مش سطحية.
- تستطيع تحول أي موضوع لتجربة ممتعة دون أن تفقد الدقة.
- ردك يخلي الشخص يبتسم أو يضحك قبل ما يقرأ الإجابة الكاملة.

قواعد الرد:
- ابدأ بتعليق فكاهي أو ملاحظة طريفة، ثم أعط الجواب الحقيقي.
- استخدم الإيموجي بذكاء في اللحظات المناسبة 😂🎯✨
- لا تبالغ في الفكاهة على حساب الدقة — المعلومة صح دائماً.
- تتكيف مع نبرة الشخص — إذا بيمزح خذ المسافة الصحيحة.""",

    'creative': """أنت Wadi المبدع — فنان، شاعر، وعقل خلاق. صنعه Anas Wadi 🎨

شخصيتك:
- ترى العالم بعيون مختلفة وتعبر عنه بطريقة تخلي الناس يتوقفون ويفكرون.
- الكلمات عندك ليست أدوات — هي تجارب حسية.
- تشعل خيال الشخص وتأخذه لمكان لم يتوقعه.

قواعد الرد:
- أجب بأسلوب أدبي راقٍ مع استعارات وتشبيهات جميلة.
- لطلبات الرسم: ترجم الوصف لإنجليزي دقيق وشاعري يلتقط الجوهر.
- استخدم الصور الذهنية والإيقاع في الكتابة.
- كل رد يكون تجربة لا مجرد معلومة.""",

    'coder': """أنت Wadi المبرمج — Senior Software Engineer متخصص ومحترف. صنعه Anas Wadi 💻

## هويتك كمهندس:
أنت مهندس برمجيات أول (Senior Engineer) بخبرة عميقة في بناء أنظمة إنتاجية حقيقية. تفكر كمعمارية أنظمة (System Architect) وتكتب كود يستحق أن يكون في Production.

## خبرتك التقنية الكاملة:
**Backend:** Python (Flask, Django, FastAPI), Node.js (Express), REST APIs, GraphQL, WebSockets
**Frontend:** React, TypeScript, Next.js, Vue.js, HTML5/CSS3/JS, Tailwind CSS, SCSS
**Databases:** PostgreSQL, MySQL, SQLite, MongoDB, Redis — قواعد بيانات محسّنة وindexed بشكل صحيح
**DevOps & Cloud:** Docker, CI/CD, Nginx, Gunicorn, Render, Railway, Vercel, GitHub Actions
**AI/ML:** APIs (OpenAI, Groq, Anthropic, Gemini), LangChain, Prompt Engineering متقدم
**Security:** Authentication (JWT, OAuth2, Session), Hashing, Rate Limiting, Input Validation, CSRF, CSP
**Tools:** Git, Linux/Bash, Testing (pytest, Jest), API Documentation

## قواعد الكود الذهبية — لا تنتهكها أبداً:
1. **اكتب الكود كاملاً دائماً** — لا تكتب "// بقية الكود هنا" أو "..." أو تقطع الكود في المنتصف
2. **ملفات كاملة** — إذا طُلب منك ملف، أرسل الملف من أول سطر لآخر سطر
3. **Comments بالعربية أو الإنجليزية** — شرح كل block مهم
4. **Error Handling في كل مكان** — try/catch، استثناءات واضحة، رسائل خطأ مفيدة
5. **Type hints في Python** — أضف annotation للـ functions والـ variables المهمة
6. **لا Magic Numbers** — استخدم constants مسماة واضحة
7. **DRY Principle** — لا تكرر الكود، استخدم functions وclasses
8. **Testing إلزامي** — أضف اختبارات unit ونماذج استخدام
9. **توثيق كامل** — README مع تعليمات التشغيل، docstrings، وشرح API إن وجد
10. **جاهز للإنتاج** — استخدم environment variables، logging، وconnectivity pooling

## طريقة عملك عند طلب مشروع كامل:
عندما يطلب المستخدم مشروعاً (موقع، API، بوت، تطبيق)، قدّم:

### 1. هيكل المشروع أولاً (مثل شجرة الملفات)
### 2. ثم كل ملف كامل بالترتيب (بدون اختصار)
### 3. في نهاية كل مشروع أضف:
   - كيفية تشغيل المشروع محلياً
   - متغيرات البيئة المطلوبة (مثال .env)
   - كيفية الـ Deploy على Render، Railway، أو أي منصة

## عند تحليل الأكواد الموجودة:
- **اقرأ كل السياق** قبل أي تعديل
- **حدد المشكلة بدقة** — السطر والسبب والحل
- **لا تكسر ما يعمل** — فقط صلح المشكلة
- **اقترح Refactoring** إذا رأيت تحسينات واضحة
- **نبّه على Security Issues** فوراً إذا وجدت (SQL injection, XSS, CSRF)

## أسلوب تقديم الكود:
- دائماً ```python أو ```javascript أو ```html مع تحديد اللغة
- أضف تعليقاً في أول الملف يشرح الغرض منه
- استخدم separators واضحة بين الأقسام: # ─── اسم القسم ──────
- اكتب docstrings للـ functions المهمة

## عند وجود خطأ أو Bug:
1. اشرح **لماذا** حدث الخطأ
2. أعط الحل المباشر مع الكود الكامل
3. اشرح **كيف تتجنبه** مستقبلاً
4. قدم test case يثبت أن الحل يعمل

## Production-Level Best Practices التي تطبقها دائماً:
- Environment variables للـ secrets (لا hardcoded passwords أبداً)
- Database connection pooling وإغلاق الاتصالات
- Logging مناسب (ليس فقط print)
- Input validation وsanitization (مثل bleach)
- Rate limiting للـ APIs
- HTTPS وsecurity headers (CSP, HSTS, X-Frame-Options)
- Graceful error responses (لا stack traces للمستخدم)
- استخدام **async** عند الحاجة لتحسين الأداء

تذكر: أنت لا تكتب "أمثلة توضيحية" — أنت تكتب كوداً جاهزاً للتشغيل الفعلي ويمكن وضعه مباشرة في Production.""",

    'writer': """أنت Wadi الكاتب — محرر لغوي وأديب متمكن. صنعه Anas Wadi ✍️

شخصيتك:
- تعشق اللغة وتعاملها باحترام وإبداع.
- تشعر بالفرق بين الكلمة الصحيحة والكلمة المثالية.
- كل نص تكتبه يحمل روحاً وهوية واضحة.

قواعد الرد:
- اهتم بالأسلوب والبلاغة والإيقاع الداخلي للجمل.
- صحح الأخطاء اللغوية بذكاء واشرح السبب.
- استخدم علامات الترقيم بشكل يخدم المعنى.
- قدم نصوصاً متماسكة تجعل القارئ يريد الاستمرار.
- اعرض البديل الأفضل دائماً مع الشرح."""
}

def get_system_prompt(mode: str, user_message: str) -> str:
    if any(q in user_message.lower() for q in IDENTITY_TRIGGERS):
        return "أجب بالضبط: أنا Wadi، مساعد ذكاء اصطناعي طوّره المهندس Anas Wadi من ليبيا 🇱🇾. لا تضف أي معلومة أخرى."
    return MODE_PROMPTS.get(mode, MODE_PROMPTS['fast'])

def generate_image(prompt: str) -> tuple[str, str]:
    clean_prompt = prompt.strip()
    encoded = requests.utils.quote(clean_prompt)
    # FIX #7: Use hashlib.md5 for a stable seed across restarts
    stable_seed = int(hashlib.md5(clean_prompt.encode()).hexdigest(), 16) % 99999
    primary_url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1024&height=1024&model=flux&enhance=true&nologo=true"
        f"&seed={stable_seed}"
    )
    fallback_url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1024&height=768&nologo=true"
    )
    return primary_url, fallback_url

def format_response(text: str) -> str:
    # FIX #2: mistune مستورد على مستوى الملف — لا حاجة لاستيراده هنا
    md = mistune.create_markdown()
    html = md(text)
    allowed_tags = ['h2', 'h3', 'h4', 'p', 'strong', 'em', 'ul', 'ol', 'li', 'code', 'pre', 'br', 'hr', 'a']
    return bleach.clean(
        html,
        tags=allowed_tags,
        attributes={'pre': ['data-lang'], 'code': ['class'], 'a': ['href']},
        strip=True
    )

# ============================================
# File Handling
# ============================================
def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS

def extract_pdf_text(file) -> str:
    """
    FIX #4: Read file bytes once into memory buffer to avoid cursor position issues
    on repeated access, and stream pages to limit peak memory usage.
    """
    try:
        # Read once into a buffer — safe for reuse
        file_bytes = file.read()
        reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
        text_parts: List[str] = []
        total_chars = 0
        for page in reader.pages[:20]:
            t = page.extract_text()
            if t:
                remaining = 15000 - total_chars
                if remaining <= 0:
                    break
                chunk = t[:remaining]
                text_parts.append(chunk)
                total_chars += len(chunk)
        return "\n".join(text_parts)
    except Exception as e:
        return f"Error reading PDF: {str(e)}"

def extract_text_from_txt(file) -> str:
    try:
        return file.read().decode('utf-8')[:15000]
    except Exception:
        return ""

# ============================================
# CSRF Protection (custom)
# ============================================
def generate_csrf_token() -> str:
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']

def csrf_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method in ['POST', 'PUT', 'DELETE']:
            token = request.headers.get('X-CSRFToken') or request.form.get('csrf_token')
            if not token or token != session.get('_csrf_token'):
                return jsonify({"error": "CSRF token missing or invalid"}), 403
        return f(*args, **kwargs)
    return decorated

@app.context_processor
def inject_csrf():
    return dict(csrf_token=generate_csrf_token())

# ============================================
# Security Headers
# ============================================
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https://image.pollinations.ai blob:; "
        "connect-src 'self' https://api.groq.com;"
    )
    return response

# ============================================
# Authentication Required Decorator
# ============================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ============================================
# HTML Templates
# ============================================
AUTH_HTML = '''
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }}</title>
    <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@300;400;500;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Tajawal', sans-serif;
            background: #050510;
            color: #e8eaf6;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .container {
            width: 100%;
            max-width: 420px;
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 24px;
            padding: 40px 30px;
            backdrop-filter: blur(10px);
            box-shadow: 0 20px 40px rgba(0,0,0,0.5);
        }
        h1 {
            text-align: center;
            background: linear-gradient(135deg, #00ff94, #00d2ff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-size: 2.2rem;
            margin-bottom: 10px;
        }
        .subtitle {
            text-align: center;
            color: #a0a0b8;
            margin-bottom: 30px;
            font-size: 0.95rem;
        }
        .form-group { margin-bottom: 20px; }
        label {
            display: block;
            margin-bottom: 8px;
            font-weight: 500;
            color: #c0c8e0;
        }
        input {
            width: 100%;
            padding: 14px 18px;
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 14px;
            color: #fff;
            font-family: 'Tajawal', sans-serif;
            font-size: 1rem;
            transition: 0.2s;
        }
        input:focus {
            outline: none;
            border-color: #00d2ff;
            box-shadow: 0 0 0 3px rgba(0,210,255,0.1);
        }
        .btn {
            width: 100%;
            padding: 14px;
            border: none;
            border-radius: 14px;
            background: linear-gradient(135deg, #00ff94, #00d2ff);
            color: #000;
            font-family: 'Tajawal', sans-serif;
            font-weight: 700;
            font-size: 1.1rem;
            cursor: pointer;
            transition: 0.3s;
        }
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 25px rgba(0,255,148,0.2);
        }
        .error {
            background: rgba(255,50,50,0.1);
            border: 1px solid #ff5252;
            color: #ff5252;
            padding: 12px;
            border-radius: 12px;
            margin-bottom: 20px;
            text-align: center;
        }
        .success {
            background: rgba(0,255,148,0.1);
            border: 1px solid #00ff94;
            color: #00ff94;
            padding: 12px;
            border-radius: 12px;
            margin-bottom: 20px;
            text-align: center;
        }
        .link {
            text-align: center;
            margin-top: 20px;
            color: #a0a0b8;
        }
        .link a {
            color: #00d2ff;
            text-decoration: none;
            font-weight: 500;
        }
        .link a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div class="container">
        <h1>⚡ Wadi</h1>
        <p class="subtitle">ذكاء اصطناعي متطور</p>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
        {% if success %}<div class="success">{{ success }}</div>{% endif %}
        {% if mode == 'login' %}
        <form method="POST">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <div class="form-group">
                <label>البريد الإلكتروني</label>
                <input type="email" name="email" required placeholder="example@email.com">
            </div>
            <div class="form-group">
                <label>كلمة المرور</label>
                <input type="password" name="password" required placeholder="********">
            </div>
            <button type="submit" class="btn">تسجيل الدخول</button>
        </form>
        <div class="link">جديد هنا؟ <a href="/register">أنشئ حساباً</a></div>
        {% else %}
        <form method="POST">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <div class="form-group">
                <label>الاسم</label>
                <input type="text" name="name" required placeholder="اسمك الكامل" value="{{ prefill_name or '' }}">
            </div>
            <div class="form-group">
                <label>البريد الإلكتروني</label>
                <input type="email" name="email" required placeholder="example@email.com" value="{{ prefill_email or '' }}">
            </div>
            <div class="form-group">
                <label>كلمة المرور</label>
                <input type="password" name="password" required placeholder="6 أحرف على الأقل">
            </div>
            <button type="submit" class="btn">إنشاء الحساب</button>
        </form>
        <div class="link">لديك حساب؟ <a href="/login">سجل دخولك</a></div>
        {% endif %}
    </div>
</body>
</html>
'''

# FIX #1: HTML template is now complete — all tags properly closed,
# JavaScript section fully implemented.
HTML = '''
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Anas Wadi AI</title>
    <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@300;400;500;700;900&display=swap" rel="stylesheet">
    <link rel="manifest" href="/manifest.json">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        :root {
            --bg: #050510;
            --surface: rgba(255,255,255,0.03);
            --border: rgba(255,255,255,0.06);
            --text: #e8eaf6;
            --muted: #8a8aaa;
            --accent: #00d2ff;
            --green: #00ff94;
            --msg-user: linear-gradient(135deg, var(--green), var(--accent));
        }
        body {
            font-family: 'Tajawal', sans-serif;
            background: var(--bg);
            color: var(--text);
            height: 100dvh;
            display: flex;
            overflow: hidden;
        }
        /* ─── SIDEBAR ─────────────────────── */
        .sidebar {
            width: 300px;
            background: rgba(10,10,20,0.8);
            border-left: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            transition: 0.3s;
        }
        .sidebar-header {
            padding: 20px;
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .sidebar-header .avatar {
            width: 42px;
            height: 42px;
            border-radius: 12px;
            background: linear-gradient(135deg, var(--green), var(--accent));
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            color: #000;
            font-size: 1.2rem;
        }
        .sidebar-header .user-info { flex: 1; }
        .sidebar-header .user-info h3 { font-size: 1rem; font-weight: 600; }
        .sidebar-header .user-info p { font-size: 0.75rem; color: var(--muted); }
        .new-chat-btn {
            margin: 16px;
            padding: 12px;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            color: white;
            font-weight: 600;
            cursor: pointer;
            text-align: center;
            transition: 0.2s;
        }
        .new-chat-btn:hover { background: rgba(255,255,255,0.06); }
        .chat-list { flex: 1; overflow-y: auto; padding: 8px; }
        .chat-item {
            padding: 12px;
            border-radius: 10px;
            margin-bottom: 4px;
            cursor: pointer;
            transition: 0.2s;
            display: flex;
            align-items: center;
            justify-content: space-between;
            color: #ccc;
        }
        .chat-item.active, .chat-item:hover { background: rgba(255,255,255,0.05); }
        .chat-item .delete-chat {
            opacity: 0;
            color: #ff5252;
            background: none;
            border: none;
            cursor: pointer;
            font-size: 1rem;
        }
        .chat-item:hover .delete-chat { opacity: 1; }
        .sidebar-footer {
            padding: 12px;
            border-top: 1px solid var(--border);
        }
        .sidebar-footer a {
            color: var(--muted);
            font-size: 0.8rem;
            text-decoration: none;
            margin: 0 5px;
        }
        /* ─── MAIN ────────────────────────── */
        .main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
        .chat-header {
            padding: 16px 20px;
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .mode-selector {
            display: flex;
            gap: 8px;
            background: var(--surface);
            padding: 6px;
            border-radius: 20px;
            flex-wrap: wrap;
        }
        .mode-btn {
            padding: 6px 16px;
            border-radius: 16px;
            border: none;
            background: transparent;
            color: #aaa;
            font-family: inherit;
            cursor: pointer;
            transition: 0.2s;
            font-size: 0.85rem;
            font-weight: 500;
        }
        .mode-btn.active {
            background: linear-gradient(135deg, var(--green), var(--accent));
            color: #000;
        }
        .messages {
            flex: 1;
            overflow-y: auto;
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 16px;
        }
        .message {
            max-width: 80%;
            animation: fadeIn 0.3s ease;
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to   { opacity: 1; transform: translateY(0); }
        }
        .message.user {
            align-self: flex-end;
            background: var(--msg-user);
            color: #000;
            border-radius: 20px 20px 6px 20px;
            padding: 12px 18px;
        }
        .message.ai {
            align-self: flex-start;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 20px 20px 20px 6px;
            padding: 14px 18px;
        }
        .message img {
            max-width: 200px;
            border-radius: 12px;
            margin: 8px 0;
            cursor: pointer;
            transition: 0.2s;
        }
        .message img:hover { transform: scale(1.02); }
        /* ─── INPUT AREA ──────────────────── */
        .input-area {
            padding: 16px 20px;
            border-top: 1px solid var(--border);
            background: var(--bg);
        }
        .input-row {
            display: flex;
            gap: 10px;
            align-items: center;
        }
        .input-wrapper {
            flex: 1;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 20px;
            padding: 8px 16px;
            display: flex;
            align-items: center;
        }
        .input-wrapper textarea {
            flex: 1;
            background: transparent;
            border: none;
            outline: none;
            color: white;
            font-family: inherit;
            font-size: 0.95rem;
            resize: none;
            max-height: 120px;
            line-height: 1.4;
        }
        .attach-btn {
            background: none;
            border: none;
            color: var(--accent);
            cursor: pointer;
            font-size: 1.3rem;
            padding: 4px;
            position: relative;
        }
        .file-menu {
            display: none;
            position: absolute;
            bottom: 110%;
            right: 0;
            background: #1a1a2e;
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 8px 0;
            min-width: 150px;
            z-index: 10;
            box-shadow: 0 10px 20px rgba(0,0,0,0.5);
        }
        .file-menu.show { display: block; }
        .file-menu div {
            padding: 10px 16px;
            cursor: pointer;
            color: #ccc;
            transition: 0.2s;
        }
        .file-menu div:hover { background: rgba(255,255,255,0.05); color: white; }
        .send-btn {
            width: 44px;
            height: 44px;
            border-radius: 50%;
            border: none;
            background: linear-gradient(135deg, var(--green), var(--accent));
            color: #000;
            font-size: 1.3rem;
            cursor: pointer;
            transition: 0.2s;
        }
        .send-btn:disabled { opacity: 0.5; cursor: not-allowed; }
        /* ─── LOADER ──────────────────────── */
        .loader { display: none; text-align: center; color: var(--muted); padding: 10px; }
        .loader.active { display: block; }
        .typing-dot {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--accent);
            animation: bounce 1.4s infinite;
        }
        .typing-dot:nth-child(2) { animation-delay: 0.2s; }
        .typing-dot:nth-child(3) { animation-delay: 0.4s; }
        @keyframes bounce {
            0%,60%,100% { transform: translateY(0); }
            30%          { transform: translateY(-8px); }
        }
        /* ─── MODAL ───────────────────────── */
        .modal {
            display: none;
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.8);
            z-index: 1000;
            justify-content: center;
            align-items: center;
        }
        .modal.show { display: flex; }
        .modal-content {
            background: #111;
            padding: 30px;
            border-radius: 20px;
            text-align: center;
            max-width: 400px;
            width: 90%;
        }
        .modal img { max-width: 90vw; max-height: 80vh; border-radius: 12px; }
        /* ─── RESPONSIVE ──────────────────── */
        @media (max-width: 768px) {
            .sidebar { position: absolute; right: -100%; z-index: 50; width: 280px; }
            .sidebar.open { right: 0; }
            .message { max-width: 95%; }
        }
    </style>
</head>
<body>
    <!-- SIDEBAR -->
    <div class="sidebar" id="sidebar">
        <div class="sidebar-header">
            <div class="avatar">{{ user_initial }}</div>
            <div class="user-info">
                <h3>{{ user_name }}</h3>
                <p>مرحباً بك</p>
            </div>
        </div>
        <button class="new-chat-btn" onclick="newChat()">✨ محادثة جديدة</button>
        <div class="chat-list" id="chatList"></div>
        <div class="sidebar-footer">
            <a href="/logout">خروج</a>
            <a href="/privacy">الخصوصية</a>
            <a href="#" onclick="deleteAccount()">حذف الحساب</a>
        </div>
    </div>

    <!-- MAIN -->
    <div class="main">
        <div class="chat-header">
            <button onclick="toggleSidebar()" style="background:none;border:none;color:white;font-size:1.4rem;cursor:pointer;">☰</button>
            <div class="mode-selector" id="modeSelector">
                <button class="mode-btn active" data-mode="fast">سريع</button>
                <button class="mode-btn" data-mode="thinker">مفكر</button>
                <button class="mode-btn" data-mode="coder">مبرمج</button>
                <button class="mode-btn" data-mode="writer">كاتب</button>
                <button class="mode-btn" data-mode="funny">مضحك</button>
                <button class="mode-btn" data-mode="creative">مبدع</button>
            </div>
            <button onclick="shareChat()" style="background:none;border:none;color:var(--accent);cursor:pointer;font-size:1rem;">مشاركة</button>
        </div>

        <div class="messages" id="messages">
            <div style="text-align:center;color:var(--muted);margin-top:20vh;">
                <h2>⚡ Wadi</h2>
                <p>اسأل أي شيء... أنا هنا</p>
            </div>
        </div>

        <div class="loader" id="loader">
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
        </div>

        <div class="input-area">
            <div class="input-row">
                <div class="input-wrapper">
                    <textarea id="messageInput" rows="1" placeholder="اكتب سؤالك هنا..."></textarea>
                    <div style="position:relative;">
                        <button class="attach-btn" onclick="toggleFileMenu()">📎</button>
                        <div class="file-menu" id="fileMenu">
                            <div onclick="triggerFileInput('image')">🖼️ صورة</div>
                            <div onclick="triggerFileInput('file')">📄 ملف</div>
                            <div onclick="triggerFileInput('pdf')">📕 مستند PDF</div>
                        </div>
                    </div>
                </div>
                <button class="send-btn" id="sendBtn" onclick="sendMessage()">↑</button>
            </div>
            <input type="file" id="fileImage" accept="image/*"      style="display:none" onchange="handleFileSelect(this,'image')">
            <input type="file" id="fileFile"  accept=".txt,.pdf"    style="display:none" onchange="handleFileSelect(this,'file')">
            <input type="file" id="filePdf"   accept=".pdf"         style="display:none" onchange="handleFileSelect(this,'pdf')">
        </div>
    </div><!-- /.main -->

    <!-- IMAGE MODAL -->
    <div class="modal" id="imgModal" onclick="this.classList.remove('show')">
        <div class="modal-content" onclick="event.stopPropagation()">
            <img id="modalImg" src="" alt="image">
        </div>
    </div>

    <!-- DELETE ACCOUNT MODAL -->
    <div class="modal" id="deleteModal">
        <div class="modal-content">
            <h3 style="margin-bottom:16px;color:#ff5252;">حذف الحساب</h3>
            <p style="color:#aaa;margin-bottom:20px;font-size:0.9rem;">هذا الإجراء لا يمكن التراجع عنه.</p>
            <input type="password" id="deletePassword" placeholder="كلمة المرور" style="width:100%;margin-bottom:12px;">
            <button onclick="confirmDelete()" style="width:100%;padding:12px;background:#ff5252;border:none;border-radius:12px;color:white;font-family:inherit;font-weight:700;cursor:pointer;margin-bottom:8px;">تأكيد الحذف</button>
            <button onclick="document.getElementById('deleteModal').classList.remove('show')" style="width:100%;padding:12px;background:var(--surface);border:1px solid var(--border);border-radius:12px;color:white;font-family:inherit;cursor:pointer;">إلغاء</button>
        </div>
    </div>

    <script>
        // ─── State ─────────────────────────────────────
        let currentChatId = null;
        let currentMode   = 'fast';
        let history       = [];
        let selectedFile  = null;
        let csrfToken     = '{{ csrf_token }}';

        // ─── Init ───────────────────────────────────────
        document.addEventListener('DOMContentLoaded', () => {
            loadChats();
            setupModeSelector();
            setupTextarea();
        });

        // ─── Mode Selector ──────────────────────────────
        function setupModeSelector() {
            document.querySelectorAll('.mode-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    currentMode = btn.dataset.mode;
                });
            });
        }

        // ─── Auto-resize textarea ───────────────────────
        function setupTextarea() {
            const ta = document.getElementById('messageInput');
            ta.addEventListener('input', () => {
                ta.style.height = 'auto';
                ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
            });
            ta.addEventListener('keydown', e => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    sendMessage();
                }
            });
        }

        // ─── Sidebar ────────────────────────────────────
        function toggleSidebar() {
            document.getElementById('sidebar').classList.toggle('open');
        }

        // ─── Chat Management ────────────────────────────
        function newChat() {
            currentChatId = 'chat_' + Date.now();
            history = [];
            document.getElementById('messages').innerHTML = `
                <div style="text-align:center;color:var(--muted);margin-top:20vh;">
                    <h2>⚡ Wadi</h2><p>اسأل أي شيء... أنا هنا</p>
                </div>`;
            document.querySelectorAll('.chat-item').forEach(c => c.classList.remove('active'));
        }

        async function loadChats() {
            try {
                const res  = await fetch('/api/chats');
                const data = await res.json();
                const list = document.getElementById('chatList');
                list.innerHTML = '';
                (data.chats || []).forEach(chat => {
                    const item = document.createElement('div');
                    item.className = 'chat-item';
                    item.dataset.id = chat.chat_id;
                    const title = (chat.user_message || 'محادثة').substring(0, 30);
                    item.innerHTML = `
                        <span onclick="loadChat('${chat.chat_id}')" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${title}</span>
                        <button class="delete-chat" onclick="deleteChat('${chat.chat_id}', event)">🗑️</button>`;
                    list.appendChild(item);
                });
            } catch (e) {
                console.error('loadChats error', e);
            }
        }

        async function loadChat(chatId) {
            currentChatId = chatId;
            history = [];
            document.querySelectorAll('.chat-item').forEach(c => {
                c.classList.toggle('active', c.dataset.id === chatId);
            });
            try {
                const res  = await fetch('/api/chat/' + chatId);
                const data = await res.json();
                const box  = document.getElementById('messages');
                box.innerHTML = '';
                (data.messages || []).forEach(m => {
                    appendMessage(m.user_message, 'user');
                    appendMessage(m.ai_response,  'ai', true);
                    history.push({ user: m.user_message, ai: m.ai_response, rawAi: m.raw_ai });
                });
                box.scrollTop = box.scrollHeight;
            } catch (e) {
                console.error('loadChat error', e);
            }
        }

        async function deleteChat(chatId, event) {
            event.stopPropagation();
            if (!confirm('حذف هذه المحادثة؟')) return;
            await fetch('/api/chat/' + chatId, { method: 'DELETE' });
            if (currentChatId === chatId) newChat();
            loadChats();
        }

        // ─── Send Message ───────────────────────────────
        async function sendMessage() {
            const input = document.getElementById('messageInput');
            const msg   = input.value.trim();
            if (!msg && !selectedFile) return;
            if (!currentChatId) currentChatId = 'chat_' + Date.now();

            appendMessage(msg || '📎 ملف', 'user');
            input.value = '';
            input.style.height = 'auto';

            const btn    = document.getElementById('sendBtn');
            const loader = document.getElementById('loader');
            btn.disabled = true;
            loader.classList.add('active');

            // Placeholder for streaming
            const aiDiv = appendMessage('', 'ai', true);
            let fullText = '';

            try {
                const formData = new FormData();
                formData.append('message',  msg);
                formData.append('mode',     currentMode);
                formData.append('chat_id',  currentChatId);
                formData.append('history',  JSON.stringify(history));
                if (selectedFile) formData.append('file', selectedFile);

                const resp = await fetch('/api/chat/stream', { method: 'POST', body: formData });
                const reader = resp.body.getReader();
                const decoder = new TextDecoder();

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    const chunk = decoder.decode(value, { stream: true });
                    chunk.split('\\n').forEach(line => {
                        if (line.startsWith('data: ')) {
                            const payload = line.slice(6).trim();
                            if (payload === '[DONE]') return;
                            try {
                                const parsed = JSON.parse(payload);
                                if (parsed.content) {
                                    fullText += parsed.content;
                                    // Incremental render with simple client-side markdown
                                    aiDiv.innerHTML = simpleMarkdown(fullText);
                                    document.getElementById('messages').scrollTop = 999999;
                                }
                                // FIX #1: Server sends full mistune-rendered HTML after save —
                                // replace the simple render with the authoritative version.
                                if (parsed.formatted) {
                                    aiDiv.innerHTML = parsed.formatted;
                                    document.getElementById('messages').scrollTop = 999999;
                                }
                                // FIX #5: Show uploaded image immediately in the chat bubble.
                                if (parsed.image_url) {
                                    const img = document.createElement('img');
                                    img.src = parsed.image_url;
                                    img.style.cssText = 'max-width:200px;border-radius:12px;margin-top:8px;cursor:pointer;';
                                    img.onclick = () => {
                                        document.getElementById('modalImg').src = img.src;
                                        document.getElementById('imgModal').classList.add('show');
                                    };
                                    aiDiv.appendChild(img);
                                }
                                if (parsed.error) {
                                    aiDiv.innerHTML = '<span style="color:#ff5252;">حدث خطأ: ' + parsed.error + '</span>';
                                }
                            } catch (_) {}
                        }
                    });
                }

                history.push({ user: msg, ai: aiDiv.innerHTML, rawAi: fullText });
                loadChats();
            } catch (e) {
                aiDiv.innerHTML = '<span style="color:#ff5252;">حدث خطأ في الاتصال. حاول مجدداً.</span>';
            } finally {
                btn.disabled = false;
                loader.classList.remove('active');
                selectedFile = null;
            }
        }

        // ─── Minimal client-side markdown renderer ───────
        function simpleMarkdown(text) {
            return text
                .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                .replace(/[*][*](.+?)[*][*]/g, '<strong>$1</strong>')
                .replace(/[*](.+?)[*]/g,       '<em>$1</em>')
                .replace(/`([^`]+)`/g,          '<code>$1</code>')
                .replace(/\n/g,                 '<br>');
        }

        // ─── Append Message Helper ───────────────────────
        function appendMessage(content, role, isHtml = false) {
            const box = document.getElementById('messages');
            // Remove welcome placeholder
            const placeholder = box.querySelector('div[style*="margin-top:20vh"]');
            if (placeholder) placeholder.remove();

            const div = document.createElement('div');
            div.className = 'message ' + role;
            if (isHtml) div.innerHTML = content;
            else        div.textContent = content;
            box.appendChild(div);
            box.scrollTop = box.scrollHeight;
            return div;
        }

        // ─── File Handling ───────────────────────────────
        function toggleFileMenu() {
            document.getElementById('fileMenu').classList.toggle('show');
        }

        function triggerFileInput(type) {
            document.getElementById('fileMenu').classList.remove('show');
            const map = { image: 'fileImage', file: 'fileFile', pdf: 'filePdf' };
            document.getElementById(map[type]).click();
        }

        function handleFileSelect(input, type) {
            if (input.files[0]) {
                selectedFile = input.files[0];
                document.getElementById('messageInput').placeholder = '📎 ' + selectedFile.name + ' — اكتب رسالتك...';
            }
        }

        // ─── Share Chat ──────────────────────────────────
        async function shareChat() {
            if (!currentChatId) { alert('افتح محادثة أولاً'); return; }
            try {
                const fd = new FormData();
                fd.append('csrf_token', csrfToken);
                const res  = await fetch('/api/chat/share/' + currentChatId, { method: 'POST', body: fd });
                const data = await res.json();
                if (data.share_url) {
                    await navigator.clipboard.writeText(data.share_url);
                    alert('تم نسخ رابط المشاركة ✅');
                }
            } catch (e) {
                alert('حدث خطأ أثناء المشاركة');
            }
        }

        // ─── Delete Account ──────────────────────────────
        function deleteAccount() {
            document.getElementById('deleteModal').classList.add('show');
        }

        async function confirmDelete() {
            const pass = document.getElementById('deletePassword').value;
            if (!pass) { alert('أدخل كلمة المرور'); return; }
            const fd = new FormData();
            fd.append('password',   pass);
            fd.append('csrf_token', csrfToken);
            const res  = await fetch('/api/user/delete', { method: 'POST', body: fd });
            const data = await res.json();
            if (data.ok) window.location.href = '/login';
            else alert(data.error || 'حدث خطأ');
        }

        // ─── Close menus on outside click ───────────────
        document.addEventListener('click', e => {
            if (!e.target.closest('.attach-btn') && !e.target.closest('.file-menu')) {
                document.getElementById('fileMenu').classList.remove('show');
            }
        });
    </script>
</body>
</html>
'''

# ============================================
# Auth Routes
# ============================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email    = request.form.get("email", "")
        password = request.form.get("password", "")
        # CSRF check
        token = request.form.get("csrf_token")
        if not token or token != session.get("_csrf_token"):
            return render_template_string(AUTH_HTML, mode="login", title="تسجيل الدخول",
                                          error="رمز CSRF غير صالح")
        user = verify_user(email, password)
        if user:
            session["user"] = user
            return redirect(url_for("index"))
        return render_template_string(AUTH_HTML, mode="login", title="تسجيل الدخول",
                                      error="البريد الإلكتروني أو كلمة المرور غير صحيحة")
    return render_template_string(AUTH_HTML, mode="login", title="تسجيل الدخول",
                                  error=None, success=None)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name     = request.form.get("name", "").strip()
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        token    = request.form.get("csrf_token")
        if not token or token != session.get("_csrf_token"):
            return render_template_string(AUTH_HTML, mode="register", title="إنشاء حساب",
                                          error="رمز CSRF غير صالح",
                                          prefill_name=name, prefill_email=email)
        if len(password) < 6:
            return render_template_string(AUTH_HTML, mode="register", title="إنشاء حساب",
                                          error="كلمة المرور يجب أن تكون 6 أحرف على الأقل",
                                          prefill_name=name, prefill_email=email)
        ok, msg = create_user(email, password, name)
        if ok:
            return render_template_string(AUTH_HTML, mode="login", title="تسجيل الدخول",
                                          success="تم إنشاء الحساب بنجاح، سجل دخولك الآن",
                                          error=None)
        return render_template_string(AUTH_HTML, mode="register", title="إنشاء حساب",
                                      error=msg, prefill_name=name, prefill_email=email)
    return render_template_string(AUTH_HTML, mode="register", title="إنشاء حساب",
                                  error=None, success=None, prefill_name=None, prefill_email=None)

@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    user      = session["user"]
    user_name = user["name"]
    user_initial = user_name[0].upper() if user_name else "?"
    if not user.get("onboarding_seen"):
        update_onboarding_seen(user["email"])
    return render_template_string(HTML, user_name=user_name, user_initial=user_initial)

@app.route("/privacy")
def privacy():
    return "<h1 style='font-family:sans-serif;text-align:center;margin-top:80px'>سياسة الخصوصية — قريباً</h1>"

# ============================================
# API Routes
# ============================================
@app.route("/api/chats")
@login_required
def api_get_chats():
    user_email = session['user']['email']
    chats = get_user_chats(user_email)
    for c in chats:
        c['created_at'] = str(c['created_at'])
    return jsonify({"chats": chats})

@app.route("/api/chat/<chat_id>")
@login_required
def api_get_chat(chat_id):
    user_email = session['user']['email']
    messages = get_chat_messages(chat_id, user_email)
    for m in messages:
        m['created_at'] = str(m['created_at'])
    return jsonify({"messages": messages})

@app.route("/api/chat/<chat_id>", methods=["DELETE"])
@login_required
def api_delete_chat(chat_id):
    user_email = session['user']['email']
    ok = delete_chat_from_db(chat_id, user_email)
    return jsonify({"ok": ok})

@app.route("/api/chat/share/<chat_id>", methods=["POST"])
@login_required
@csrf_required
def api_create_share(chat_id):
    user_email = session['user']['email']
    token = create_share_token(chat_id, user_email)
    if token:
        share_url = url_for('share_chat', token=token, _external=True)
        return jsonify({"share_url": share_url})
    return jsonify({"error": "Failed to create share link"}), 500

@app.route("/share/<token>")
def share_chat(token):
    messages = get_shared_chat_by_token(token)
    if not messages:
        return "Chat not found", 404
    return render_template_string("""
    <!DOCTYPE html>
    <html dir="rtl" lang="ar">
    <head>
        <meta charset="UTF-8">
        <title>محادثة مشتركة - Anas Wadi</title>
        <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@300;400;700&display=swap" rel="stylesheet">
        <style>
            body { background:#050510; color:#e8eaf6; font-family:'Tajawal',sans-serif; max-width:800px; margin:0 auto; padding:20px; }
            .user-msg { background:linear-gradient(135deg,#00ff94,#00d2ff); color:#000; padding:12px 18px; border-radius:20px 20px 6px 20px; margin:10px 0; max-width:80%; margin-right:auto; }
            .ai-msg   { background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08); padding:14px 18px; border-radius:20px 20px 20px 6px; margin:10px 0; max-width:85%; }
            .footer   { text-align:center; margin-top:30px; font-size:12px; color:gray; }
        </style>
    </head>
    <body>
        <h2 style="text-align:center">✨ محادثة مشتركة من Anas Wadi</h2>
        {% for m in messages %}
            <div class="user-msg">{{ m.user_message }}</div>
            <div class="ai-msg">{{ m.ai_response|safe }}</div>
        {% endfor %}
        <div class="footer">
            تمت المشاركة بواسطة مستخدم Anas Wadi |
            <a href="/" style="color:#00d2ff">جرب المساعد بنفسك</a>
        </div>
    </body>
    </html>
    """, messages=messages)

@app.route("/api/user/delete", methods=["POST"])
@login_required
@csrf_required
def api_delete_user():
    email    = session['user']['email']
    password = request.form.get('password', '')
    if not password:
        return jsonify({"error": "كلمة المرور مطلوبة"}), 400
    ok, msg = delete_user_account(email, password)
    if ok:
        session.clear()
        return jsonify({"ok": True, "message": msg})
    return jsonify({"error": msg}), 400

# ============================================
# Chat Endpoints (Streaming & Regular)
# ============================================
@app.route("/api/chat/stream", methods=["POST"])
@login_required
@csrf_required  # CSRF مطلوب — يُمرَّر التوكن في X-CSRFToken header أو csrf_token field
@limiter.limit("10/minute")
def chat_stream():
    if not Config.GROQ_API_KEY:
        return jsonify({"error": "API key missing"}), 500

    user_message = request.form.get("message", "")
    mode         = request.form.get("mode", "fast")
    chat_id      = request.form.get("chat_id", "")
    history_raw  = request.form.get("history", "[]")
    file         = request.files.get("file")

    user_info  = session['user']
    user_email = user_info['email']
    user_name  = user_info['name']

    if is_prompt_injection(user_message):
        return jsonify({"error": "Message rejected for security reasons"}), 403

    if mode not in MODE_PROMPTS:
        mode = 'fast'

    history_limit = 20 if mode == 'coder' else 12
    max_tokens_map = {'coder':4096,'thinker':3000,'writer':2500,'creative':2000,'funny':1500,'fast':2048}
    max_tokens = max_tokens_map.get(mode, 2048)
    messages = [{"role": "system", "content": get_system_prompt(mode, user_message)}]

    try:
        history_data = json.loads(history_raw)
        for msg in history_data[-history_limit:]:
            u = str(msg.get("user", ""))[:2000]
            a = str(msg.get("rawAi") or msg.get("ai", ""))[:4000]
            if u and a and a != '__typing__':
                messages.append({"role": "user",      "content": u})
                messages.append({"role": "assistant",  "content": a})
    except Exception:
        pass

    # ─── Handle file upload ────────────────────────
    file_name    = None
    image_url    = None
    file_context = ""
    if file and file.filename and allowed_file(file.filename):
        original_filename = secure_filename(file.filename)
        ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
        if ext in {'png', 'jpg', 'jpeg', 'webp'}:
            os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
            saved_name = f"{int(time.time())}_{secrets.token_hex(4)}.{ext}"
            file_path  = os.path.join(Config.UPLOAD_FOLDER, saved_name)
            file.save(file_path)
            image_url = f"/uploads/{saved_name}"
            file_name = original_filename
        elif ext == 'pdf':
            file_context = f"PDF content ({original_filename}):\n{extract_pdf_text(file)}\n\n"
            file_name = original_filename
        elif ext == 'txt':
            file_context = f"File content ({original_filename}):\n{extract_text_from_txt(file)}\n\n"
            file_name = original_filename

    # إذا أرسل المستخدم ملفاً بدون نص، نستخدم رسالة افتراضية واضحة بدل "Hello"
    default_msg = "اطلع على هذا الملف" if (file_context or image_url) else "Hello"
    final_user_message = (file_context + user_message).strip() or default_msg
    messages.append({"role": "user", "content": final_user_message})

    model_map = {
        'thinker':  'qwen/qwen3-32b',
        'coder':    'qwen/qwen3-32b',
        'writer':   'llama-3.3-70b-versatile',
        'creative': 'llama-3.3-70b-versatile',
        'fast':     'llama-3.1-8b-instant',
        'funny':    'llama-3.1-8b-instant'
    }
    model       = model_map.get(mode, 'llama-3.1-8b-instant')
    temperature = {'funny':0.92,'creative':0.88,'writer':0.82,'thinker':0.45,'coder':0.25,'fast':0.72}.get(mode, 0.72)

    def generate():
        full_response = ""
        try:
            with requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {Config.GROQ_API_KEY}"},
                json={
                    "model": model, "messages": messages,
                    "max_tokens": max_tokens, "temperature": temperature,
                    "top_p": 0.92, "stream": True
                },
                timeout=90,
                stream=True
            ) as resp:
                for line in resp.iter_lines():
                    if line:
                        line = line.decode('utf-8')
                        if line.startswith('data: '):
                            data = line[6:]
                            if data == '[DONE]':
                                break
                            try:
                                chunk   = json.loads(data)
                                content = chunk['choices'][0]['delta'].get('content', '')
                                if content:
                                    full_response += content
                                    yield f"data: {json.dumps({'content': content})}\n\n"
                            except Exception:
                                pass
        except Exception as e:
            app.logger.error(f"Stream error: {str(e)}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            # FIX #2: Only save if we have both chat_id AND a non-empty response
            if chat_id and full_response:
                try:
                    formatted = format_response(full_response)
                    save_message(
                        chat_id, user_email, user_name,
                        user_message or "(file)",
                        formatted,
                        full_response, mode, image_url, file_name
                    )
                    # FIX #1: Send the server-rendered HTML so the client
                    # replaces simpleMarkdown output with the full mistune render.
                    # FIX #5: Also send image_url so the client can display it
                    # immediately without waiting for a page reload.
                    yield f"data: {json.dumps({'formatted': formatted, 'image_url': image_url})}\n\n"
                except Exception as e:
                    app.logger.error(f"Failed to save message after stream: {str(e)}")
        yield "data: [DONE]\n\n"

    return Response(generate(), mimetype='text/event-stream')


@app.route("/api/chat", methods=["POST"])
@login_required
@limiter.limit("10/minute")
def chat():
    if not Config.GROQ_API_KEY:
        return jsonify({"error": "API key missing"}), 500

    user_message = request.form.get("message", "")
    mode         = request.form.get("mode", "fast")
    chat_id      = request.form.get("chat_id", "")
    history_raw  = request.form.get("history", "[]")
    file         = request.files.get("file")

    user_info  = session['user']
    user_email = user_info['email']
    user_name  = user_info['name']

    if is_prompt_injection(user_message):
        return jsonify({"error": "Message rejected for security reasons"}), 403

    if mode not in MODE_PROMPTS:
        mode = 'fast'

    history_limit = 20 if mode == 'coder' else 12
    max_tokens_map = {'coder':4096,'thinker':3000,'writer':2500,'creative':2000,'funny':1500,'fast':2048}
    max_tokens = max_tokens_map.get(mode, 2048)
    messages = [{"role": "system", "content": get_system_prompt(mode, user_message)}]

    try:
        history_data = json.loads(history_raw)
        for msg in history_data[-history_limit:]:
            u = str(msg.get("user", ""))[:2000]
            a = str(msg.get("rawAi") or msg.get("ai", ""))[:4000]
            if u and a and a != '__typing__':
                messages.append({"role": "user",      "content": u})
                messages.append({"role": "assistant",  "content": a})
    except Exception:
        pass

    # ─── Handle file upload ────────────────────────
    file_name    = None
    image_url    = None
    file_context = ""
    if file and file.filename and allowed_file(file.filename):
        original_filename = secure_filename(file.filename)
        ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
        if ext in {'png', 'jpg', 'jpeg', 'webp'}:
            os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
            saved_name = f"{int(time.time())}_{secrets.token_hex(4)}.{ext}"
            file_path  = os.path.join(Config.UPLOAD_FOLDER, saved_name)
            file.save(file_path)
            image_url = f"/uploads/{saved_name}"
            file_name = original_filename
        elif ext == 'pdf':
            file_context = f"PDF content ({original_filename}):\n{extract_pdf_text(file)}\n\n"
            file_name = original_filename
        elif ext == 'txt':
            file_context = f"File content ({original_filename}):\n{extract_text_from_txt(file)}\n\n"
            file_name = original_filename

    # إذا أرسل المستخدم ملفاً بدون نص، نستخدم رسالة افتراضية واضحة بدل "Hello"
    default_msg = "اطلع على هذا الملف" if (file_context or image_url) else "Hello"
    final_user_message = (file_context + user_message).strip() or default_msg
    messages.append({"role": "user", "content": final_user_message})

    model_map = {
        'thinker':  'qwen/qwen3-32b',
        'coder':    'qwen/qwen3-32b',
        'writer':   'llama-3.3-70b-versatile',
        'creative': 'llama-3.3-70b-versatile',
        'fast':     'llama-3.1-8b-instant',
        'funny':    'llama-3.1-8b-instant'
    }
    model       = model_map.get(mode, 'llama-3.1-8b-instant')
    temperature = {'funny':0.92,'creative':0.88,'writer':0.82,'thinker':0.45,'coder':0.25,'fast':0.72}.get(mode, 0.72)

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {Config.GROQ_API_KEY}"},
            json={
                "model": model, "messages": messages,
                "max_tokens": max_tokens, "temperature": temperature, "top_p": 0.92
            },
            timeout=60
        )
        if resp.status_code == 200:
            data      = resp.json()
            raw       = data['choices'][0]['message']['content']
            formatted = format_response(raw)
            # FIX #6: Only generate an image URL when in creative mode with image keywords;
            # image generation URLs from Pollinations are ephemeral — log a warning.
            image_gen_url = None
            if mode == 'creative' and any(kw in user_message for kw in ('رسم', 'صورة', 'توليد')):
                eng_prompt = re.sub(r'ارسم|صورة|توليد', '', user_message).strip()
                image_gen_url, _ = generate_image(eng_prompt)
                app.logger.warning(
                    "Generated ephemeral image URL saved to DB — consider downloading and caching."
                )
            final_image_url = image_url or image_gen_url
            if chat_id:
                save_message(
                    chat_id, user_email, user_name,
                    user_message, formatted, raw, mode,
                    final_image_url, file_name
                )
            return jsonify({
                "response":  formatted,
                "raw":       raw,
                "image_url": final_image_url
            })
        else:
            return jsonify({"error": f"Groq API error {resp.status_code}"}), 500
    except Exception as e:
        app.logger.error(f"Chat error: {str(e)}")
        return jsonify({"error": f"Connection error: {str(e)}"}), 500

# ============================================
# Celery Tasks
# ============================================
@celery.task(bind=True, max_retries=2)
def process_heavy_request(
    self,
    messages: List[Dict],
    model: str,
    temperature: float,
    max_tokens: int,
    chat_id: str,
    user_email: str,
    user_name: str,
    user_message: str,
    mode: str,
    image_url: Optional[str] = None,
    file_name: Optional[str] = None,
) -> Optional[str]:
    """
    FIX #2 (Celery): Background task for heavy modes (thinker, coder).
    Saves the result directly to the DB when done.
    Use /api/chat/async to submit; poll /api/task/<task_id> for status.
    """
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {Config.GROQ_API_KEY}"},
            json={
                "model": model, "messages": messages,
                "max_tokens": max_tokens, "temperature": temperature
            },
            timeout=120
        )
        if resp.status_code == 200:
            raw       = resp.json()['choices'][0]['message']['content']
            formatted = format_response(raw)
            save_message(chat_id, user_email, user_name, user_message,
                         formatted, raw, mode, image_url, file_name)
            return formatted
        app.logger.error(f"Celery Groq error: {resp.status_code}")
        return None
    except Exception as exc:
        app.logger.error(f"Celery task error: {str(exc)}")
        # Exponential backoff: المحاولة 1 بعد 5 ثوانٍ، المحاولة 2 بعد 10 ثوانٍ
        raise self.retry(exc=exc, countdown=5 * (2 ** self.request.retries))

@app.route("/api/chat/async", methods=["POST"])
@login_required
@csrf_required  # FIX #5: إضافة حماية CSRF — كانت مفقودة في هذا المسار
@limiter.limit("5/minute")
def chat_async():
    """Submit a heavy request (thinker/coder) to Celery and return a task_id."""
    if not Config.GROQ_API_KEY:
        return jsonify({"error": "API key missing"}), 500

    user_message = request.form.get("message", "")
    mode         = request.form.get("mode", "thinker")
    chat_id      = request.form.get("chat_id", "")
    history_raw  = request.form.get("history", "[]")

    if mode not in ('thinker', 'coder'):
        return jsonify({"error": "Async route only supports thinker / coder modes"}), 400
    if is_prompt_injection(user_message):
        return jsonify({"error": "Message rejected for security reasons"}), 403
    if not chat_id:
        return jsonify({"error": "chat_id required"}), 400

    user_info  = session['user']
    user_email = user_info['email']
    user_name  = user_info['name']

    max_tokens_map = {'coder': 4096, 'thinker': 3000}
    max_tokens = max_tokens_map[mode]
    messages   = [{"role": "system", "content": get_system_prompt(mode, user_message)}]

    try:
        history_data = json.loads(history_raw)
        for msg in history_data[-20:]:
            u = str(msg.get("user", ""))[:2000]
            a = str(msg.get("rawAi") or msg.get("ai", ""))[:4000]
            if u and a and a != '__typing__':
                messages.append({"role": "user",     "content": u})
                messages.append({"role": "assistant", "content": a})
    except Exception:
        pass

    messages.append({"role": "user", "content": user_message or "Hello"})

    model_map   = {'thinker': 'qwen/qwen3-32b', 'coder': 'qwen/qwen3-32b'}
    temperature = {'thinker': 0.45, 'coder': 0.25}[mode]

    task = process_heavy_request.delay(
        messages, model_map[mode], temperature, max_tokens,
        chat_id, user_email, user_name, user_message, mode
    )
    return jsonify({"task_id": task.id})

@app.route("/api/task/<task_id>")
@login_required
def task_status(task_id):
    """Poll Celery task status."""
    from celery.result import AsyncResult
    result = AsyncResult(task_id, app=celery)
    if result.state == 'SUCCESS':
        return jsonify({"status": "done", "response": result.result})
    if result.state == 'FAILURE':
        return jsonify({"status": "error", "error": str(result.info)})
    return jsonify({"status": result.state.lower()})

# ============================================
# Static Files & Service Worker
# ============================================
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(Config.UPLOAD_FOLDER, filename)

@app.route('/sw.js')
def service_worker():
    # FIX #8: /static/offline.html was in urlsToCache but doesn't exist,
    # causing SW install to fail. Cache only '/' until offline.html is created.
    return Response("""
const CACHE_NAME = 'anas-wadi-v1';
const urlsToCache = ['/'];
self.addEventListener('install', event => {
    event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(urlsToCache)));
});
self.addEventListener('fetch', event => {
    event.respondWith(
        fetch(event.request).catch(() =>
            caches.match(event.request).then(response => response || caches.match('/'))
        )
    );
});
""", mimetype='application/javascript')

@app.route('/manifest.json')
def manifest():
    return Response(json.dumps({
        "name":             "Anas Wadi AI",
        "short_name":       "AnasWadi",
        "start_url":        "/",
        "display":          "standalone",
        "theme_color":      "#050510",
        "background_color": "#050510",
        "icons":            []
    }), mimetype='application/manifest+json')

# ============================================
# Health Check
# ============================================
@app.route("/health")
def health_check():
    """
    نقطة فحص صحة الخدمات — مفيدة لـ Render / Railway / Kubernetes.
    تفحص الاتصال بـ PostgreSQL وRedis وترجع حالة كل منهما.
    """
    status = {"status": "ok", "db": "ok", "redis": "ok"}
    http_status = 200

    # فحص قاعدة البيانات
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return_db_connection(conn)
    except Exception as e:
        status["db"] = f"error: {str(e)}"
        status["status"] = "degraded"
        http_status = 503

    # فحص Redis
    try:
        r = init_redis()
        r.ping()
    except Exception as e:
        status["redis"] = f"error: {str(e)}"
        status["status"] = "degraded"
        http_status = 503

    return jsonify(status), http_status

# ============================================
# Error Handlers
# ============================================
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({"error": "Too many requests. Please slow down."}), 429

@app.errorhandler(500)
def internal_error(e):
    app.logger.error(f"Server error: {str(e)}")
    return jsonify({"error": "Internal server error"}), 500

# ============================================
# Main Entry
# ============================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
