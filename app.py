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
    # Session: Redis
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
    # Rate Limiting
    RATELIMIT_STORAGE_URL = os.environ.get("RATELIMIT_STORAGE_URL", "memory://")
    # Security
    BCRYPT_ROUNDS = 12
    # SSL mode for DB — set DB_SSL_MODE=disable if your DB doesn't support SSL
    DB_SSL_MODE = os.environ.get("DB_SSL_MODE", "require")

# ─── Lazy Redis initializer (اختياري — يُستخدم فقط إذا SESSION_TYPE=redis) ───
def init_redis() -> Optional[redis.Redis]:
    if os.environ.get("SESSION_TYPE", "filesystem") != "redis":
        return None
    if Config.SESSION_REDIS is None:
        Config.SESSION_REDIS = redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            socket_connect_timeout=5, socket_timeout=5,
            retry_on_timeout=True, health_check_interval=30, decode_responses=False
        )
    return Config.SESSION_REDIS

# ============================================
# Application Factory
# ============================================
app = Flask(__name__)
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
            # 1. إنشاء الجداول الأساسية إذا لم تكن موجودة
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
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
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_chat_id ON conversations(chat_id);
                CREATE INDEX IF NOT EXISTS idx_user_email ON conversations(user_email);
            """)
            # 2. إضافة الأعمدة الجديدة إذا كانت مفقودة (ترقية آمنة)
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                                   WHERE table_name='users' AND column_name='onboarding_seen') THEN
                        ALTER TABLE users ADD COLUMN onboarding_seen BOOLEAN DEFAULT FALSE;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                                   WHERE table_name='conversations' AND column_name='share_token') THEN
                        ALTER TABLE conversations ADD COLUMN share_token TEXT UNIQUE;
                        CREATE INDEX IF NOT EXISTS idx_share_token ON conversations(share_token);
                    END IF;
                END $$;
            """)
            conn.commit()
            app.logger.info("Database tables and migrations ensured")
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
    return hashlib.sha256(f"{OLD_SALT}{password}".encode()).hexdigest()

def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode('utf-8'),
        bcrypt.gensalt(Config.BCRYPT_ROUNDS)
    ).decode('utf-8')

def check_password(password: str, hashed: str) -> bool:
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
                if not user['password_hash'].startswith('$2b$'):
                    cur.execute("UPDATE users SET password_hash = %s WHERE email = %s",
                                (hash_password(password), email.lower().strip()))
                    conn.commit()
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
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com data:; "
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
<meta name="theme-color" content="#050510">
<title>✨ Anas Wadi — {{ title }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@300;400;500;700;900&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body {
  min-height:100vh; background:#050510; color:#e8eaf6;
  font-family:'Tajawal',sans-serif;
  display:flex; align-items:center; justify-content:center;
  overflow:hidden;
}
body::before {
  content:''; position:fixed; inset:0;
  background:
    radial-gradient(ellipse 80% 60% at 20% 10%, rgba(0,210,255,0.08) 0%, transparent 60%),
    radial-gradient(ellipse 60% 50% at 80% 80%, rgba(124,77,255,0.08) 0%, transparent 60%);
  pointer-events:none;
}
.stars { position:fixed; inset:0; pointer-events:none; overflow:hidden; }
.star {
  position:absolute; border-radius:50%; background:white;
  animation:twinkle var(--d,3s) ease-in-out infinite;
}
@keyframes twinkle {
  0%,100%{opacity:0;transform:scale(0.5);}
  50%{opacity:var(--op,0.5);transform:scale(1);}
}
.card {
  background:rgba(255,255,255,0.04);
  border:1px solid rgba(255,255,255,0.08);
  border-radius:24px; padding:40px 36px;
  width:100%; max-width:420px; margin:20px;
  backdrop-filter:blur(20px);
  animation:cardPop 0.5s cubic-bezier(0.4,0,0.2,1) both;
  position:relative; z-index:1;
}
@keyframes cardPop {
  from{opacity:0;transform:translateY(30px) scale(0.97);}
  to{opacity:1;transform:translateY(0) scale(1);}
}
.logo {
  text-align:center; font-size:28px; font-weight:900;
  background:linear-gradient(90deg,#00ff94,#00d2ff,#7c4dff);
  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  background-clip:text; margin-bottom:8px;
  background-size:200%;
  animation:shimmer 4s linear infinite;
}
@keyframes shimmer{0%{background-position:0%}100%{background-position:200%}}
.subtitle { text-align:center; color:rgba(232,234,246,0.5); font-size:14px; margin-bottom:32px; }
.tabs {
  display:flex; background:rgba(255,255,255,0.04);
  border-radius:14px; padding:4px; margin-bottom:28px;
  border:1px solid rgba(255,255,255,0.06);
}
.tab {
  flex:1; padding:10px; text-align:center; border-radius:10px;
  font-size:14px; font-weight:700; cursor:pointer;
  font-family:'Tajawal',sans-serif; border:none;
  transition:all 0.25s; color:rgba(232,234,246,0.5);
  background:transparent;
}
.tab.active {
  background:linear-gradient(135deg,#00ff94,#00d2ff);
  color:#000;
}
.form { display:flex; flex-direction:column; gap:16px; }
.input-group { position:relative; }
.input-group i {
  position:absolute; right:16px; top:50%; transform:translateY(-50%);
  color:rgba(232,234,246,0.35); font-size:15px; pointer-events:none;
}
input[type=text], input[type=email], input[type=password] {
  width:100%; background:rgba(255,255,255,0.06);
  border:1px solid rgba(255,255,255,0.1);
  color:#e8eaf6; border-radius:14px;
  padding:13px 46px 13px 16px;
  font-size:15px; font-family:'Tajawal',sans-serif;
  transition:all 0.25s; direction:rtl;
}
input:focus {
  outline:none;
  border-color:rgba(0,210,255,0.5);
  box-shadow:0 0 0 3px rgba(0,210,255,0.1);
  background:rgba(255,255,255,0.08);
}
input::placeholder { color:rgba(232,234,246,0.35); }
.btn {
  background:linear-gradient(135deg,#00ff94,#00d2ff);
  border:none; border-radius:14px; padding:14px;
  color:#000; font-weight:800; font-size:16px;
  font-family:'Tajawal',sans-serif; cursor:pointer;
  transition:all 0.25s; margin-top:4px;
}
.btn:hover { transform:translateY(-2px); box-shadow:0 8px 25px rgba(0,210,255,0.35); }
.btn:active { transform:scale(0.98); }
.error-msg {
  background:rgba(255,80,80,0.1); border:1px solid rgba(255,80,80,0.3);
  color:#ff8080; padding:10px 16px; border-radius:10px;
  font-size:13px; text-align:center;
  {% if not error %}display:none;{% endif %}
}
.success-msg {
  background:rgba(0,255,148,0.1); border:1px solid rgba(0,255,148,0.3);
  color:#00ff94; padding:10px 16px; border-radius:10px;
  font-size:13px; text-align:center;
  {% if not success %}display:none;{% endif %}
}
.divider { text-align:center; color:rgba(232,234,246,0.3); font-size:12px; margin:4px 0; }
</style>
</head>
<body>
<div class="stars" id="stars"></div>
<div class="card">
  <div class="logo">✨ Anas Wadi</div>
  <div class="subtitle">مساعد الذكاء الاصطناعي 🇱🇾</div>

  <div class="tabs">
    <button class="tab {% if mode == 'login' %}active{% endif %}"
      onclick="location.href='/login'">
      <i class="fa-solid fa-right-to-bracket"></i> دخول
    </button>
    <button class="tab {% if mode == 'register' %}active{% endif %}"
      onclick="location.href='/register'">
      <i class="fa-solid fa-user-plus"></i> حساب جديد
    </button>
  </div>

  {% if error %}
  <div class="error-msg">⚠️ {{ error }}</div>
  {% endif %}
  {% if success %}
  <div class="success-msg">✅ {{ success }}</div>
  {% endif %}

  {% if mode == 'login' %}
  <form class="form" method="POST" action="/login">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <div class="input-group">
      <i class="fa-solid fa-envelope"></i>
      <input type="email" name="email" placeholder="البريد الإلكتروني" required autofocus>
    </div>
    <div class="input-group">
      <i class="fa-solid fa-lock"></i>
      <input type="password" name="password" placeholder="كلمة المرور" required>
    </div>
    <button type="submit" class="btn">
      <i class="fa-solid fa-right-to-bracket"></i> دخول
    </button>
  </form>
  {% else %}
  <form class="form" method="POST" action="/register">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <div class="input-group">
      <i class="fa-solid fa-user"></i>
      <input type="text" name="name" placeholder="الاسم" required autofocus
             value="{{ prefill_name or '' }}">
    </div>
    <div class="input-group">
      <i class="fa-solid fa-envelope"></i>
      <input type="email" name="email" placeholder="البريد الإلكتروني" required
             value="{{ prefill_email or '' }}">
    </div>
    <div class="input-group">
      <i class="fa-solid fa-lock"></i>
      <input type="password" name="password" placeholder="كلمة المرور (6 أحرف على الأقل)" required minlength="6">
    </div>
    <button type="submit" class="btn">
      <i class="fa-solid fa-user-plus"></i> إنشاء حساب
    </button>
  </form>
  {% endif %}
</div>

<script>
const s = document.getElementById('stars');
for(let i=0;i<55;i++){
  const el = document.createElement('div');
  el.className='star';
  const sz = Math.random()*2.5+0.5;
  el.style.cssText=`width:${sz}px;height:${sz}px;left:${Math.random()*100}%;top:${Math.random()*100}%;--d:${(Math.random()*4+2).toFixed(1)}s;--op:${(Math.random()*0.4+0.2).toFixed(2)};animation-delay:${(Math.random()*5).toFixed(1)}s`;
  s.appendChild(el);
}
</script>
</body>
</html>
'''

HTML = '''
<!DOCTYPE html>
<html dir="rtl" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#050510">
<link rel="manifest" href="/manifest.json">
<title>✨ Anas Wadi ✨</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@300;400;500;700;900&family=Cairo:wght@300;400;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>
/* ═══════════════════════════════════════════
   CSS VARIABLES — Dark / Light Theme
═══════════════════════════════════════════ */
:root {
  --bg: #050510;
  --surface: rgba(255,255,255,0.04);
  --surface2: rgba(255,255,255,0.08);
  --surface3: rgba(255,255,255,0.12);
  --border: rgba(255,255,255,0.08);
  --border-glow: rgba(0,210,255,0.35);
  --text: #e8eaf6;
  --text-dim: rgba(232,234,246,0.55);
  --text-muted: rgba(232,234,246,0.35);
  --accent1: #00d2ff;
  --accent2: #00ff94;
  --accent3: #7c4dff;
  --accent4: #ff6b6b;
  --user-grad: linear-gradient(135deg, #00ff94, #00d2ff);
  --glow: 0 0 30px rgba(0,210,255,0.15);
  --glow-strong: 0 0 40px rgba(0,210,255,0.3);
  --sidebar-bg: rgba(4,4,14,0.98);
  --header-bg: rgba(5,5,16,0.88);
}
[data-theme="light"] {
  --bg: #f0f4ff;
  --surface: rgba(0,0,0,0.04);
  --surface2: rgba(0,0,0,0.07);
  --surface3: rgba(0,0,0,0.11);
  --border: rgba(0,0,0,0.1);
  --border-glow: rgba(0,100,200,0.3);
  --text: #0d1117;
  --text-dim: rgba(13,17,23,0.6);
  --text-muted: rgba(13,17,23,0.38);
  --accent1: #0077cc;
  --accent2: #00aa66;
  --accent3: #5533cc;
  --glow: 0 0 30px rgba(0,100,200,0.1);
  --glow-strong: 0 0 40px rgba(0,100,200,0.2);
  --sidebar-bg: rgba(235,240,255,0.98);
  --header-bg: rgba(240,244,255,0.88);
}

/* ═══════════════════════════════════════════
   RESET & BASE
═══════════════════════════════════════════ */
* { margin:0; padding:0; box-sizing:border-box; }
html { scroll-behavior:smooth; }
body {
  background:var(--bg); color:var(--text);
  font-family:'Tajawal','Cairo',sans-serif;
  min-height:100vh; display:flex; flex-direction:column;
  transition:background 0.4s, color 0.4s; overflow-x:hidden;
}
body::before {
  content:''; position:fixed; inset:0; pointer-events:none; z-index:0;
  background:
    radial-gradient(ellipse 80% 60% at 20% 10%, rgba(0,210,255,0.06) 0%, transparent 60%),
    radial-gradient(ellipse 60% 50% at 80% 80%, rgba(124,77,255,0.06) 0%, transparent 60%),
    radial-gradient(ellipse 50% 40% at 50% 50%, rgba(0,255,148,0.03) 0%, transparent 70%);
  animation:bgPulse 8s ease-in-out infinite alternate;
}
@keyframes bgPulse { from{opacity:0.7} to{opacity:1} }

/* ═══════════════════════════════════════════
   STARS
═══════════════════════════════════════════ */
.stars { position:fixed; inset:0; pointer-events:none; z-index:0; overflow:hidden; }
.star {
  position:absolute; border-radius:50%; background:white;
  animation:twinkle var(--d,3s) ease-in-out infinite;
}
@keyframes twinkle {
  0%,100%{opacity:0;transform:scale(0.5)}
  50%{opacity:var(--op,0.6);transform:scale(1)}
}

/* ═══════════════════════════════════════════
   HEADER — Clean, Professional, Mobile-First
═══════════════════════════════════════════ */
.header {
  position:sticky; top:0; z-index:100;
  background:var(--header-bg);
  backdrop-filter:blur(24px) saturate(1.6);
  -webkit-backdrop-filter:blur(24px);
  border-bottom:1px solid var(--border);
  padding:0 16px;
  height:58px;
  display:flex; align-items:center; gap:10px;
  transition:background 0.3s;
}

/* Left slot: menu button */
.header-left { display:flex; align-items:center; gap:8px; flex-shrink:0; }

/* Center slot: logo takes full remaining space */
.header-center { flex:1; display:flex; justify-content:center; align-items:center; }

/* Right slot: actions */
.header-right { display:flex; align-items:center; gap:6px; flex-shrink:0; }

.logo {
  font-size:20px; font-weight:900; letter-spacing:-0.3px; white-space:nowrap;
  background:linear-gradient(90deg,var(--accent2),var(--accent1),var(--accent3));
  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  background-clip:text; animation:logoShimmer 4s linear infinite; background-size:200%;
  text-shadow:none;
}
@keyframes logoShimmer{0%{background-position:0% 50%}100%{background-position:200% 50%}}

.icon-btn {
  background:var(--surface); border:1px solid var(--border);
  color:var(--text); width:38px; height:38px;
  border-radius:10px; cursor:pointer; font-size:15px;
  transition:all 0.22s; display:flex; align-items:center; justify-content:center;
  position:relative; overflow:hidden; text-decoration:none; flex-shrink:0;
}
.icon-btn::before {
  content:''; position:absolute; inset:0;
  background:linear-gradient(135deg,var(--accent1),var(--accent2));
  opacity:0; transition:opacity 0.22s;
}
.icon-btn:hover { border-color:var(--border-glow); transform:translateY(-1px); box-shadow:var(--glow); }
.icon-btn:hover::before { opacity:0.12; }
.icon-btn:active { transform:scale(0.94); }
.icon-btn i { position:relative; z-index:1; }

/* ═══════════════════════════════════════════
   SIDEBAR — Professional with User Profile & Delete
═══════════════════════════════════════════ */
.sidebar {
  position:fixed; right:-300px; top:0;
  width:280px; height:100vh;
  background:var(--sidebar-bg); backdrop-filter:blur(30px);
  border-left:1px solid var(--border);
  transition:right 0.36s cubic-bezier(0.4,0,0.2,1);
  z-index:200; display:flex; flex-direction:column;
}
[data-theme="light"] .sidebar { box-shadow:-4px 0 30px rgba(0,0,0,0.08); }
.sidebar.open { right:0; }

.sidebar-overlay {
  display:none; position:fixed; inset:0; z-index:199;
  background:rgba(0,0,0,0.5); backdrop-filter:blur(4px);
  transition:opacity 0.3s;
}
.sidebar-overlay.open { display:block; }

/* Sidebar user profile card */
.sidebar-profile {
  display:flex; align-items:center; gap:12px;
  padding:20px 16px 14px;
  border-bottom:1px solid var(--border);
  background:var(--surface);
}
.sidebar-avatar {
  width:42px; height:42px; border-radius:12px; flex-shrink:0;
  background:linear-gradient(135deg,var(--accent2),var(--accent1));
  display:flex; align-items:center; justify-content:center;
  font-size:18px; font-weight:900; color:#000;
  box-shadow:0 4px 14px rgba(0,210,255,0.3);
}
.sidebar-profile-info { flex:1; min-width:0; }
.sidebar-profile-name { font-size:14px; font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.sidebar-profile-badge {
  display:inline-flex; align-items:center; gap:4px;
  font-size:10px; color:var(--accent2); margin-top:2px;
  background:rgba(0,255,148,0.1); padding:2px 8px; border-radius:20px;
  border:1px solid rgba(0,255,148,0.2);
}

/* Sidebar top actions */
.sidebar-actions { padding:12px 14px; }
.new-chat-btn {
  background:linear-gradient(135deg,var(--accent2),var(--accent1));
  border:none; border-radius:12px; padding:11px 16px;
  font-family:'Tajawal',sans-serif; font-size:14px; font-weight:700;
  color:#000; cursor:pointer; transition:all 0.25s; width:100%;
  display:flex; align-items:center; gap:8px; justify-content:center;
  box-shadow:0 4px 18px rgba(0,210,255,0.25);
}
.new-chat-btn:hover { transform:translateY(-1px); box-shadow:0 6px 24px rgba(0,210,255,0.4); }
.new-chat-btn:active { transform:scale(0.98); }

/* Sidebar chat list */
.sidebar-label {
  padding:4px 16px 8px;
  font-size:11px; font-weight:700; color:var(--text-muted);
  text-transform:uppercase; letter-spacing:1px;
}
.chat-list-scroll { flex:1; overflow-y:auto; padding:0 10px 10px; }
.chat-list-scroll::-webkit-scrollbar { width:3px; }
.chat-list-scroll::-webkit-scrollbar-thumb { background:var(--border); border-radius:10px; }

.chat-item {
  display:flex; align-items:center; gap:8px;
  padding:9px 12px; border-radius:10px; cursor:pointer;
  transition:all 0.2s; margin-bottom:3px;
  border:1px solid transparent;
  position:relative;
}
.chat-item:hover { background:var(--surface2); border-color:var(--border); }
.chat-item.active { background:rgba(0,210,255,0.08); border-color:rgba(0,210,255,0.25); }
.chat-item-icon { font-size:14px; flex-shrink:0; opacity:0.7; }
.chat-item-text {
  flex:1; font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  color:var(--text-dim);
}
.chat-item.active .chat-item-text { color:var(--text); }
.chat-item-delete {
  opacity:0; flex-shrink:0; width:24px; height:24px;
  border-radius:6px; border:none; background:rgba(255,80,80,0.12);
  color:#ff6b6b; cursor:pointer; font-size:11px;
  display:flex; align-items:center; justify-content:center;
  transition:all 0.18s;
}
.chat-item:hover .chat-item-delete { opacity:1; }
.chat-item-delete:hover { background:rgba(255,80,80,0.25); transform:scale(1.1); }

/* Sidebar footer */
.sidebar-footer {
  padding:12px 14px; border-top:1px solid var(--border);
  display:flex; gap:8px;
}
.sidebar-footer-btn {
  flex:1; background:var(--surface); border:1px solid var(--border);
  color:var(--text-dim); padding:9px 12px; border-radius:10px;
  font-family:'Tajawal',sans-serif; font-size:12px; font-weight:600;
  cursor:pointer; transition:all 0.2s; display:flex; align-items:center; gap:6px; justify-content:center;
}
.sidebar-footer-btn:hover { border-color:var(--border-glow); color:var(--text); }

/* ═══════════════════════════════════════════
   MODE BUTTONS
═══════════════════════════════════════════ */
.modes {
  display:flex; gap:7px; padding:10px 16px;
  overflow-x:auto; position:relative; z-index:1; scrollbar-width:none;
  border-bottom:1px solid var(--border);
}
.modes::-webkit-scrollbar { display:none; }
.mode-btn {
  background:var(--surface); border:1px solid var(--border);
  color:var(--text-dim); padding:7px 15px; border-radius:20px;
  font-size:12px; font-weight:600; white-space:nowrap; cursor:pointer;
  transition:all 0.22s; font-family:'Tajawal',sans-serif;
}
.mode-btn:hover { border-color:var(--border-glow); color:var(--text); }
.mode-btn.active {
  background:linear-gradient(135deg,var(--accent2),var(--accent1));
  color:#000; border-color:transparent;
  box-shadow:0 3px 14px rgba(0,210,255,0.3);
  font-weight:800;
}

/* ═══════════════════════════════════════════
   CHAT CONTAINER
═══════════════════════════════════════════ */
.chat-container {
  flex:1; padding:20px 16px 10px;
  max-width:800px; width:100%; margin:0 auto; position:relative; z-index:1;
}

/* ═══════════════════════════════════════════
   WELCOME SCREEN
═══════════════════════════════════════════ */
.welcome { text-align:center; padding:44px 20px; animation:fadeUp 0.6s ease both; }
@keyframes fadeUp { from{opacity:0;transform:translateY(20px)} to{opacity:1;transform:translateY(0)} }
.welcome-icon { font-size:54px; margin-bottom:18px; animation:floatIcon 3s ease-in-out infinite; display:block; }
@keyframes floatIcon { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-10px)} }
.welcome h2 { font-size:24px; font-weight:900; margin-bottom:10px; }
.welcome p { color:var(--text-dim); line-height:1.9; font-size:15px; }
.welcome-cards { display:flex; gap:10px; margin-top:28px; flex-wrap:wrap; justify-content:center; }
.welcome-card {
  background:var(--surface); border:1px solid var(--border);
  border-radius:14px; padding:14px 16px; cursor:pointer;
  transition:all 0.25s; text-align:right; min-width:140px; max-width:190px; font-size:13px;
}
.welcome-card:hover {
  border-color:var(--border-glow); transform:translateY(-4px);
  box-shadow:var(--glow-strong); background:var(--surface2);
}
.welcome-card .card-icon { font-size:22px; margin-bottom:6px; }
.welcome-card .card-title { font-weight:800; margin-bottom:4px; }
.welcome-card .card-desc { color:var(--text-dim); font-size:11px; }

/* ═══════════════════════════════════════════
   MESSAGES
═══════════════════════════════════════════ */
.message { margin:14px 0; animation:msgSlide 0.35s cubic-bezier(0.4,0,0.2,1) both; }
@keyframes msgSlide { from{opacity:0;transform:translateY(16px) scale(0.97)} to{opacity:1;transform:translateY(0) scale(1)} }

.user-msg {
  background:var(--user-grad); color:#000; font-weight:700;
  padding:13px 18px; border-radius:20px 20px 6px 20px;
  max-width:78%; width:fit-content;
  margin-right:auto; margin-left:0;
  box-shadow:0 6px 24px rgba(0,255,148,0.22);
  font-size:15px; line-height:1.75; word-break:break-word;
}
.message-user { display:flex; justify-content:flex-end; }

.ai-msg {
  background:var(--surface);
  border:1px solid var(--border);
  padding:16px 20px; border-radius:20px 20px 20px 6px;
  max-width:86%; font-size:15px; line-height:1.95;
  position:relative; word-break:break-word;
  transition:border-color 0.3s, box-shadow 0.3s;
}
.ai-msg:hover { border-color:var(--border-glow); box-shadow:var(--glow); }
.ai-msg h2 { font-size:18px; font-weight:900; margin:14px 0 6px; color:var(--accent1); }
.ai-msg h3 { font-size:16px; font-weight:800; margin:12px 0 5px; color:var(--accent2); }
.ai-msg h4 { font-size:15px; font-weight:700; margin:10px 0 4px; color:var(--accent3); }
.ai-msg strong { font-weight:900; color:var(--accent1); }
.ai-msg em { font-style:italic; color:var(--accent2); opacity:0.9; }
.ai-msg p { margin:7px 0; }
.ai-msg ul, .ai-msg ol { padding-right:24px; margin:10px 0; }
.ai-msg li { margin:6px 0; line-height:1.85; }
.ai-msg code {
  background:rgba(0,210,255,0.1); border:1px solid rgba(0,210,255,0.2);
  border-radius:5px; padding:2px 8px; font-size:13px; font-family:monospace;
  color:var(--accent1);
}
.ai-msg pre {
  background:#0d1117; border:1px solid rgba(0,210,255,0.18);
  border-radius:12px; margin:14px 0;
  overflow:hidden; position:relative;
  box-shadow:0 4px 20px rgba(0,0,0,0.35);
}
[data-theme="light"] .ai-msg pre { background:#1a1f2e; }
.ai-msg pre code {
  background:none; border:none; padding:16px; font-size:13px;
  color:#e6edf3; display:block; overflow-x:auto;
  font-family:'Fira Code','Cascadia Code','Consolas',monospace;
  line-height:1.7; white-space:pre;
}
/* Code block header with language badge + copy button */
.code-header {
  display:flex; align-items:center; justify-content:space-between;
  background:rgba(0,210,255,0.07); border-bottom:1px solid rgba(0,210,255,0.15);
  padding:7px 14px; font-size:12px;
}
.code-lang-badge {
  color:var(--accent1); font-family:monospace; font-weight:700;
  text-transform:uppercase; letter-spacing:0.5px;
}
.copy-code-btn {
  background:var(--surface2); border:1px solid var(--border);
  color:var(--text-dim); padding:3px 10px; border-radius:6px;
  font-size:11px; cursor:pointer; font-family:'Tajawal',sans-serif;
  transition:all 0.2s; display:flex; align-items:center; gap:4px;
}
.copy-code-btn:hover { border-color:var(--accent1); color:var(--accent1); background:rgba(0,210,255,0.08); }
.copy-code-btn.copied { border-color:var(--accent2); color:var(--accent2); }

/* Typing indicator */
.typing-indicator { display:inline-flex; gap:5px; align-items:center; padding:4px 0; }
.typing-indicator span {
  width:7px; height:7px; border-radius:50%; background:var(--accent1);
  animation:typing 1.2s ease-in-out infinite;
}
.typing-indicator span:nth-child(2) { animation-delay:0.2s; background:var(--accent2); }
.typing-indicator span:nth-child(3) { animation-delay:0.4s; background:var(--accent3); }
@keyframes typing {
  0%,60%,100%{transform:translateY(0);opacity:0.4}
  30%{transform:translateY(-8px);opacity:1}
}

/* Message extras */
.file-badge {
  display:inline-flex; align-items:center; gap:6px;
  background:rgba(0,210,255,0.1); border:1px solid rgba(0,210,255,0.3);
  padding:5px 12px; border-radius:8px; font-size:12px;
  margin-bottom:6px; color:var(--accent1);
}
.msg-actions { display:flex; gap:6px; margin-top:8px; opacity:0; transition:opacity 0.2s; }
.message:hover .msg-actions { opacity:1; }
.msg-btn {
  background:var(--surface); border:1px solid var(--border);
  color:var(--text-dim); padding:5px 12px; border-radius:8px;
  font-size:12px; cursor:pointer; font-family:'Tajawal',sans-serif; transition:all 0.2s;
}
.msg-btn:hover { border-color:var(--accent1); color:var(--text); }

.generated-img {
  max-width:100%; border-radius:14px; margin-top:12px; display:block;
  box-shadow:0 8px 30px rgba(0,0,0,0.4); animation:imgReveal 0.5s ease both;
}
@keyframes imgReveal { from{opacity:0;transform:scale(0.94)} to{opacity:1;transform:scale(1)} }

/* ═══════════════════════════════════════════
   INPUT AREA
═══════════════════════════════════════════ */
.input-area {
  position:sticky; bottom:0; z-index:10;
  background:var(--header-bg); backdrop-filter:blur(20px);
  border-top:1px solid var(--border); padding:10px 16px 14px;
}

.templates {
  display:flex; gap:7px; margin-bottom:9px;
  overflow-x:auto; padding-bottom:3px; scrollbar-width:none;
  max-width:800px; margin-right:auto; margin-left:auto;
}
.templates::-webkit-scrollbar { display:none; }
.template-btn {
  background:var(--surface); border:1px solid var(--border);
  color:var(--text-dim); padding:6px 14px; border-radius:20px;
  font-size:12px; white-space:nowrap; cursor:pointer;
  font-family:'Tajawal',sans-serif; transition:all 0.2s;
}
.template-btn:hover { border-color:var(--border-glow); color:var(--text); background:var(--surface2); }

.input-wrapper { max-width:800px; margin:0 auto; display:flex; gap:8px; align-items:flex-end; }
.textarea-wrap { flex:1; position:relative; }
textarea {
  width:100%; background:var(--surface2); border:1px solid var(--border);
  color:var(--text); border-radius:16px; padding:13px 16px;
  font-size:15px; font-family:'Tajawal',sans-serif;
  resize:none; height:52px; max-height:130px;
  transition:border-color 0.25s, box-shadow 0.25s; line-height:1.5;
}
textarea:focus {
  outline:none; border-color:var(--accent1);
  box-shadow:0 0 0 3px rgba(0,210,255,0.1);
}
textarea::placeholder { color:var(--text-muted); }
#fileInput { display:none; }
.send-btn {
  background:linear-gradient(135deg,var(--accent2),var(--accent1));
  border:none; border-radius:14px; width:52px; height:52px;
  font-size:18px; color:#000; cursor:pointer; transition:all 0.25s; flex-shrink:0;
  display:flex; align-items:center; justify-content:center;
  box-shadow:0 4px 18px rgba(0,210,255,0.25);
}
.send-btn:hover:not(:disabled) { transform:scale(1.08); box-shadow:0 6px 22px rgba(0,210,255,0.45); }
.send-btn:active:not(:disabled) { transform:scale(0.95); }
.send-btn:disabled { opacity:0.45; cursor:not-allowed; }

.file-preview {
  max-width:800px; margin:0 auto 8px;
  display:flex; align-items:center; gap:8px;
  background:rgba(0,210,255,0.07); border:1px solid rgba(0,210,255,0.2);
  padding:8px 14px; border-radius:10px; font-size:13px;
}
.file-preview.hidden { display:none; }

/* ═══════════════════════════════════════════
   MODALS
═══════════════════════════════════════════ */
.modal {
  display:none; position:fixed; inset:0; z-index:300;
  justify-content:center; align-items:center;
  background:rgba(0,0,0,0.7); backdrop-filter:blur(8px);
}
.modal.open { display:flex; }
.modal-content {
  background:var(--bg); border:1px solid var(--border);
  padding:32px; border-radius:22px; max-width:460px; width:90%;
  text-align:center; animation:modalPop 0.3s cubic-bezier(0.4,0,0.2,1);
}
@keyframes modalPop { from{opacity:0;transform:scale(0.88) translateY(22px)} to{opacity:1;transform:scale(1) translateY(0)} }
.support-btn {
  display:inline-flex; align-items:center; gap:8px;
  background:linear-gradient(135deg,#ff6b6b,#feca57);
  color:#000; border:none; padding:12px 28px;
  border-radius:14px; font-weight:800; cursor:pointer;
  font-family:'Tajawal',sans-serif; font-size:15px;
  text-decoration:none; margin-top:16px; transition:all 0.25s;
}
.support-btn:hover { transform:scale(1.05); box-shadow:0 6px 20px rgba(254,202,87,0.4); }

/* Confirm delete modal */
.confirm-modal-content {
  background:var(--bg); border:1px solid rgba(255,80,80,0.3);
  padding:28px; border-radius:20px; max-width:340px; width:90%;
  text-align:center; animation:modalPop 0.3s cubic-bezier(0.4,0,0.2,1);
}
.confirm-modal-content h3 { font-size:18px; margin-bottom:10px; }
.confirm-modal-content p { color:var(--text-dim); font-size:14px; margin-bottom:20px; }
.confirm-btns { display:flex; gap:10px; justify-content:center; }
.confirm-btn-del {
  background:linear-gradient(135deg,#ff4444,#ff6b6b);
  border:none; border-radius:12px; padding:10px 24px;
  color:#fff; font-weight:700; font-family:'Tajawal',sans-serif;
  font-size:14px; cursor:pointer; transition:all 0.2s;
}
.confirm-btn-del:hover { transform:scale(1.04); }
.confirm-btn-cancel {
  background:var(--surface2); border:1px solid var(--border);
  border-radius:12px; padding:10px 24px;
  color:var(--text); font-weight:700; font-family:'Tajawal',sans-serif;
  font-size:14px; cursor:pointer; transition:all 0.2s;
}
.confirm-btn-cancel:hover { border-color:var(--border-glow); }

/* ═══════════════════════════════════════════
   TOAST
═══════════════════════════════════════════ */
.toast {
  position:fixed; bottom:90px; left:50%;
  transform:translateX(-50%) translateY(20px);
  background:rgba(20,20,40,0.95); color:var(--text);
  border:1px solid var(--border); padding:10px 22px;
  border-radius:12px; z-index:999; font-size:13px;
  opacity:0; transition:all 0.3s; pointer-events:none; backdrop-filter:blur(12px);
  white-space:nowrap;
}
.toast.show { opacity:1; transform:translateX(-50%) translateY(0); }
.toast.error { border-color:rgba(255,80,80,0.5); color:#ff8080; }
.toast.success { border-color:rgba(0,255,148,0.5); color:var(--accent2); }

/* ═══════════════════════════════════════════
   SCROLLBAR
═══════════════════════════════════════════ */
::-webkit-scrollbar { width:5px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:var(--border); border-radius:10px; }
::-webkit-scrollbar-thumb:hover { background:var(--accent1); }

/* ═══════════════════════════════════════════
   RESPONSIVE — Mobile First
═══════════════════════════════════════════ */
@media (max-width:480px) {
  .ai-msg, .user-msg { max-width:96%; }
  .welcome h2 { font-size:20px; }
  .welcome-cards { gap:8px; }
  .welcome-card { min-width:130px; max-width:155px; }
  .logo { font-size:18px; }
  .header { padding:0 10px; height:54px; }
  .icon-btn { width:36px; height:36px; font-size:14px; }
  .modes { padding:8px 12px; gap:6px; }
  .mode-btn { padding:6px 12px; font-size:11px; }
}
@media (max-width:360px) {
  .header-right { gap:4px; }
  .logo { font-size:16px; }
}
</style>
</head>
<body>

<!-- Stars background -->
<div class="stars" id="stars"></div>

<!-- Sidebar overlay -->
<div class="sidebar-overlay" id="sidebarOverlay" onclick="closeSidebar()"></div>

<!-- ═══════ SIDEBAR ═══════ -->
<div class="sidebar" id="sidebar">
  <!-- User profile -->
  <div class="sidebar-profile">
    <div class="sidebar-avatar" id="sidebarAvatar">{{ user_initial }}</div>
    <div class="sidebar-profile-info">
      <div class="sidebar-profile-name" id="sidebarName">{{ user_name }}</div>
      <div class="sidebar-profile-badge"><i class="fa-solid fa-circle" style="font-size:7px;color:#00ff94"></i> متصل الآن</div>
    </div>
  </div>

  <!-- New chat button -->
  <div class="sidebar-actions">
    <button class="new-chat-btn" onclick="newChat()">
      <i class="fa-solid fa-plus"></i> محادثة جديدة
    </button>
  </div>

  <!-- Chat list -->
  <div class="sidebar-label">المحادثات السابقة</div>
  <div class="chat-list-scroll">
    <div id="chatList">
      <div style="color:var(--text-dim);font-size:13px;text-align:center;padding:20px">
        <div class="typing-indicator" style="justify-content:center;margin-bottom:8px"><span></span><span></span><span></span></div>
        جاري التحميل...
      </div>
    </div>
  </div>

  <!-- Footer -->
  <div class="sidebar-footer">
    <button class="sidebar-footer-btn" onclick="toggleTheme()">
      <i class="fa-solid fa-moon" id="sidebarThemeIcon"></i> الوضع
    </button>
    <button class="sidebar-footer-btn" onclick="shareCurrentChat()">
      <i class="fa-solid fa-share-nodes" style="color:var(--accent1)"></i> شارك
    </button>
    <button class="sidebar-footer-btn" onclick="showSupport()">
      <i class="fa-solid fa-heart" style="color:#ff6b6b"></i> دعم
    </button>
    <a href="/logout" class="sidebar-footer-btn" style="text-decoration:none;color:inherit">
      <i class="fa-solid fa-right-from-bracket"></i> خروج
    </a>
  </div>
  <div style="padding:0 14px 14px">
    <button class="sidebar-footer-btn" onclick="showDeleteAccount()" style="width:100%;color:#ff6b6b;border-color:rgba(255,80,80,0.3)">
      <i class="fa-solid fa-trash"></i> حذف الحساب
    </button>
  </div>
</div>

<!-- ═══════ HEADER ═══════ -->
<header class="header">
  <div class="header-left">
    <button class="icon-btn" onclick="toggleSidebar()" title="القائمة">
      <i class="fa-solid fa-bars"></i>
    </button>
  </div>

  <div class="header-center">
    <span class="logo">✨ Anas Wadi ✨</span>
  </div>

  <div class="header-right">
    <button class="icon-btn" onclick="toggleTheme()" title="تغيير الوضع" id="themeBtn">
      <i class="fa-solid fa-moon"></i>
    </button>
    <button class="icon-btn" onclick="newChat()" title="محادثة جديدة">
      <i class="fa-solid fa-plus"></i>
    </button>
    <button class="icon-btn" onclick="showSupport()" title="ادعمني">
      <i class="fa-solid fa-heart" style="color:#ff6b6b"></i>
    </button>
  </div>
</header>

<!-- Mode selector -->
<div class="modes">
  <button class="mode-btn active" data-mode="fast" onclick="setMode('fast')">⚡ سريع</button>
  <button class="mode-btn" data-mode="thinker" onclick="setMode('thinker')">🧠 مفكر</button>
  <button class="mode-btn" data-mode="funny" onclick="setMode('funny')">😂 فكاهي</button>
  <button class="mode-btn" data-mode="creative" onclick="setMode('creative')">🎨 مبدع</button>
  <button class="mode-btn" data-mode="coder" onclick="setMode('coder')">💻 مبرمج</button>
  <button class="mode-btn" data-mode="writer" onclick="setMode('writer')">✍️ كاتب</button>
</div>

<!-- Chat messages -->
<div class="chat-container" id="chatContainer">
  <div class="welcome" id="welcome">
    <span class="welcome-icon">🌊</span>
    <h2>مرحباً في <span style="background:linear-gradient(90deg,#00ff94,#00d2ff);-webkit-background-clip:text;-webkit-text-fill-color:transparent">Anas Wadi</span></h2>
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

<!-- Input area -->
<div class="input-area">
  <div class="file-preview hidden" id="filePreview">
    <i class="fa-solid fa-file" style="color:var(--accent1)"></i>
    <span id="fileName">ملف مرفق</span>
    <button class="msg-btn" onclick="removeFile()" style="margin-right:auto">
      <i class="fa-solid fa-xmark"></i>
    </button>
  </div>
  <div class="templates" id="templateBtns">
    <button class="template-btn" onclick="useTemplate('ارسم صورة: ')">🎨 ارسم</button>
    <button class="template-btn" onclick="useTemplate('لخصلي الملف')">📄 لخص</button>
    <button class="template-btn" onclick="useTemplate('اكتبلي ايميل رسمي عن ')">📧 ايميل</button>
    <button class="template-btn" onclick="useTemplate('ترجم للعربية: ')">🌐 ترجم</button>
    <button class="template-btn" onclick="setMode('coder');useTemplate('اصنعلي مشروع Flask كامل: ')">🏗️ مشروع</button>
    <button class="template-btn" onclick="setMode('coder');useTemplate('اكتبلي API بـ Flask يحتوي على ')">⚙️ API</button>
    <button class="template-btn" onclick="setMode('coder');useTemplate('صلحلي هذا الكود:\n')">🔧 صلح Bug</button>
    <button class="template-btn" onclick="useTemplate('اشرحلي بالتفصيل ')">💡 شرح</button>
  </div>
  <div class="input-wrapper">
    <input type="file" id="fileInput" accept="image/*,.pdf" onchange="handleFile(this)">
    <button type="button" class="icon-btn" onclick="document.getElementById('fileInput').click()" title="ارفع ملف">
      <i class="fa-solid fa-paperclip"></i>
    </button>
    <div class="textarea-wrap">
      <textarea id="messageInput"
        placeholder="اكتب رسالتك... (Enter للإرسال، Shift+Enter لسطر جديد)"
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

<!-- Support modal -->
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

<!-- Delete confirm modal -->
<div class="modal" id="deleteModal" onclick="closeModalClick(event)">
  <div class="confirm-modal-content">
    <div style="font-size:36px;margin-bottom:12px">🗑️</div>
    <h3>حذف المحادثة؟</h3>
    <p>هذه العملية لا يمكن التراجع عنها.</p>
    <div class="confirm-btns">
      <button class="confirm-btn-del" onclick="confirmDelete()">حذف</button>
      <button class="confirm-btn-cancel" onclick="document.getElementById('deleteModal').classList.remove('open')">إلغاء</button>
    </div>
  </div>
</div>

<!-- ═══════════════════════════════════════════
   JAVASCRIPT
═══════════════════════════════════════════ -->
<script>
// ─── State ────────────────────────────────────────────────────
let currentChatId = localStorage.getItem('currentChatId') || Date.now().toString();
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
function showToast(msg, type = '') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + type;
  setTimeout(() => t.className = 'toast', 3200);
}

// ─── Mode ─────────────────────────────────────────────────────
function setMode(m, save = true) {
  currentMode = m;
  if (save) localStorage.setItem('mode', m);
  document.querySelectorAll('.mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === m));
}

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
    l.innerHTML = '<div style="color:var(--text-dim);font-size:13px;text-align:center;padding:24px 10px">لا توجد محادثات سابقة<br><span style="font-size:22px;display:block;margin-top:10px">💬</span></div>';
    return;
  }
  dbChats.forEach(chat => {
    const d = document.createElement('div');
    d.className = 'chat-item' + (chat.chat_id === currentChatId ? ' active' : '');

    const icon = document.createElement('span');
    icon.className = 'chat-item-icon';
    icon.textContent = '💬';

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

    const icon = document.createElement('span');
    icon.className = 'chat-item-icon'; icon.textContent = '💬';

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

  // Remove from local
  delete chats[id];
  saveChats();
  if (currentChatId === id) {
    currentChatId = Date.now().toString();
    chats[currentChatId] = [];
    localStorage.setItem('currentChatId', currentChatId);
    renderChat();
  }

  // Remove from DB
  try {
    await fetch(`/api/chat/${id}`, { method: 'DELETE' });
  } catch(e) {}

  await loadDbChats();
  showToast('🗑️ تم حذف المحادثة', 'success');
}

// ─── Load & Switch Chat ───────────────────────────────────────
async function loadChatFromDb(chatId) {
  try {
    const r = await fetch(`/api/chat/${chatId}`);
    if (!r.ok) throw new Error('خطأ');
    const data = await r.json();
    const messages = data.messages || [];
    chats[chatId] = messages.map(m => ({
      user: m.user_message, ai: m.ai_response,
      rawAi: m.raw_ai, imageUrl: m.image_url, fileName: m.file_name
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
      <span class="welcome-icon">🌊</span>
      <h2>مرحباً في <span style="background:linear-gradient(90deg,#00ff94,#00d2ff);-webkit-background-clip:text;-webkit-text-fill-color:transparent">Anas Wadi</span></h2>
      <p>تم تطوير هذا الذكاء الاصطناعي بيد المهندس <strong>Anas Wadi</strong> من ليبيا 🇱🇾</p>
      <p style="margin-top:6px">كيف يمكنني مساعدتك اليوم؟</p>
      <div class="welcome-cards">
        <div class="welcome-card" onclick="useTemplate('ارسم صورة: ')"><div class="card-icon">🎨</div><div class="card-title">رسم صورة</div><div class="card-desc">توليد صور فائقة الجودة</div></div>
        <div class="welcome-card" onclick="useTemplate('اشرحلي ')"><div class="card-icon">💡</div><div class="card-title">شرح وتحليل</div><div class="card-desc">أشرح أي موضوع تريده</div></div>
        <div class="welcome-card" onclick="setMode('coder');useTemplate('اصنعلي مشروع ')"><div class="card-icon">💻</div><div class="card-title">مشروع كامل</div><div class="card-desc">موقع، API، بوت — جاهز للتشغيل</div></div>
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
      : (m.ai || '');
    let imgHtml = '';
    if (m.imageUrl) imgHtml = `<br><img class="generated-img" src="${escHtml(m.imageUrl)}" alt="صورة مولدة" loading="lazy" onclick="window.open(this.src,'_blank')">`;
    c.innerHTML += `
      <div class="message message-user">
        <div class="user-msg">${userContent}</div>
      </div>
      <div class="message">
        <div class="ai-msg" id="msg-${i}">${aiContent}${imgHtml}</div>
        ${isTyping ? '' : `<div class="msg-actions">
          <button class="msg-btn" onclick="copyText(${JSON.stringify(m.rawAi || m.ai)})"><i class="fa-solid fa-copy"></i> نسخ الكل</button>
          <button class="msg-btn" onclick="regenerate(${i})"><i class="fa-solid fa-rotate"></i> إعادة</button>
        </div>`}
      </div>`;
  });
  // Add code block headers with copy buttons
  requestAnimationFrame(() => {
    document.querySelectorAll('.ai-msg pre').forEach(pre => {
      if (pre.querySelector('.code-header')) return; // already processed
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
  }).catch(() => showToast('⚠️ تعذر النسخ', 'error'));
}

function escHtml(t) {
  if (!t) return '';
  const d = document.createElement('div');
  d.textContent = t;
  return d.innerHTML;
}

// ─── Send Message ─────────────────────────────────────────────
async function sendMessage() {
  if (isSending) return;
  const inp = document.getElementById('messageInput');
  const t = inp.value.trim();
  if (!t && !currentFile) return;
  isSending = true;
  document.getElementById('sendBtn').disabled = true;
  inp.value = ''; inp.style.height = '52px';
  if (!chats[currentChatId]) chats[currentChatId] = [];
  const c = chats[currentChatId];
  const fName = currentFile ? currentFile.name : null;
  c.push({ user: t || 'حلل الملف', ai: '__typing__', fileName: fName });
  saveChats(); renderChat();
  const fd = new FormData();
  fd.append('message', t);
  fd.append('mode', currentMode);
  fd.append('chat_id', currentChatId);
  fd.append('history', JSON.stringify(c.slice(0, -1)));
  if (currentFile) fd.append('file', currentFile);
  try {
    const r = await fetch('/api/chat', { method: 'POST', body: fd });
    if (!r.ok) throw new Error('خطأ في الخادم');
    const d = await r.json();
    if (d.error) { showToast(d.error, 'error'); c.pop(); }
    else {
      c[c.length-1].ai = d.response;
      c[c.length-1].rawAi = d.raw || d.response;
      if (d.image_url) c[c.length-1].imageUrl = d.image_url;
      await loadDbChats();
    }
  } catch (err) {
    c[c.length-1].ai = '⚠️ صار خطأ: ' + err.message;
    showToast('تعذر الإرسال', 'error');
  } finally {
    currentFile = null;
    document.getElementById('filePreview').classList.add('hidden');
    document.getElementById('fileInput').value = '';
    saveChats(); renderChat();
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
  fd.append('history', JSON.stringify(c.slice(0, i)));
  try {
    const r = await fetch('/api/chat', { method: 'POST', body: fd });
    const d = await r.json();
    c[i].ai = d.response; c[i].rawAi = d.raw || d.response;
    if (d.image_url) c[i].imageUrl = d.image_url; else delete c[i].imageUrl;
  } catch (err) {
    c[i].ai = '⚠️ صار خطأ: ' + err.message;
  } finally {
    saveChats(); renderChat(); isSending = false;
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
  showToast('✅ تم النسخ', 'success');
}
function saveChats() {
  try { localStorage.setItem('chats', JSON.stringify(chats)); }
  catch(e) { showToast('⚠️ الذاكرة ممتلئة! احذف محادثات قديمة', 'error'); }
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

// ─── 1. Streaming Send ────────────────────────────────────────
async function sendMessageStream() {
  if (isSending) return;
  const inp = document.getElementById('messageInput');
  const t = inp.value.trim();
  if (!t && !currentFile) return;
  // الملفات وطلبات الصور تستخدم endpoint القديم
  if (currentFile) return sendMessage();
  if (t.includes('ارسم') || t.includes('صورة') || t.startsWith('draw')) return sendMessage();
  if (!navigator.onLine) { showToast('📡 لا يوجد اتصال', 'error'); return; }
  isSending = true;
  document.getElementById('sendBtn').disabled = true;
  inp.value = ''; inp.style.height = '52px';
  if (!chats[currentChatId]) chats[currentChatId] = [];
  const c = chats[currentChatId];
  c.push({ user: t, ai: '__typing__' });
  saveChats(); renderChat();
  const fd = new FormData();
  fd.append('message', t);
  fd.append('mode', currentMode);
  fd.append('chat_id', currentChatId);
  fd.append('history', JSON.stringify(c.slice(0, -1)));
  let accumulated = '';
  try {
    const r = await fetch('/api/chat/stream', { method: 'POST', body: fd });
    if (!r.ok || !r.body) throw new Error('no-stream');
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '', started = false;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const data = JSON.parse(line.slice(6));
          if (data.error) throw new Error(data.error);
          if (data.delta) {
            if (!started) { c[c.length-1].ai = ''; started = true; }
            accumulated += data.delta;
            c[c.length-1].ai = escHtml(accumulated).replace(/\n/g,'<br>');
            const last = document.querySelector('.ai-msg:last-of-type');
            if (last) last.innerHTML = c[c.length-1].ai;
          }
          if (data.done) {
            c[c.length-1].ai = data.formatted;
            c[c.length-1].rawAi = data.raw;
            saveChats(); renderChat();
            await loadDbChats();
          }
        } catch(e) { throw e; }
      }
    }
  } catch (err) {
    if (accumulated.length === 0) { c.pop(); saveChats(); return sendMessage(); }
    showToast('⚠️ انقطع البث', 'error');
  } finally {
    isSending = false;
    document.getElementById('sendBtn').disabled = false;
  }
}

// ─── 2. Onboarding ────────────────────────────────────────────
function checkOnboarding() {
  if (!localStorage.getItem('onboarded')) {
    setTimeout(() => {
      const m = document.getElementById('onboardModal');
      if (m) m.classList.add('open');
    }, 400);
  }
}
function finishOnboarding() {
  localStorage.setItem('onboarded', '1');
  const m = document.getElementById('onboardModal');
  if (m) m.classList.remove('open');
}

// ─── 3. Share Chat ────────────────────────────────────────────
async function shareCurrentChat() {
  if (!currentChatId) return;
  try {
    const r = await fetch(`/api/chat/${currentChatId}/share`, { method: 'POST' });
    const d = await r.json();
    if (d.ok && d.url) {
      const inp = document.getElementById('shareUrlInput');
      if (inp) inp.value = d.url;
      const m = document.getElementById('shareModal');
      if (m) m.classList.add('open');
    } else { showToast(d.error || 'تعذر إنشاء الرابط', 'error'); }
  } catch(e) { showToast('خطأ في الاتصال', 'error'); }
}
function copyShareUrl() {
  const inp = document.getElementById('shareUrlInput');
  if (!inp) return;
  navigator.clipboard.writeText(inp.value).then(() => showToast('✅ تم نسخ الرابط', 'success'));
}

// ─── 4. Connection Banner ─────────────────────────────────────
function setupConnection() {
  const banner = document.getElementById('connBanner');
  if (!banner) return;
  const update = () => navigator.onLine ? banner.classList.remove('show') : banner.classList.add('show');
  window.addEventListener('online', update);
  window.addEventListener('offline', update);
  update();
}

// ─── 5. Service Worker (PWA) ──────────────────────────────────
function registerSW() {
  if (!('serviceWorker' in navigator)) return;
  try { if (window.self !== window.top) return; } catch(e) { return; }
  navigator.serviceWorker.register('/sw.js').catch(e => console.warn('SW:', e));
}

// ─── 6. Delete Account ────────────────────────────────────────
function showDeleteAccount() {
  closeSidebar();
  const m = document.getElementById('deleteAccountModal');
  if (m) { m.classList.add('open'); document.getElementById('deleteConfirm').value = ''; }
}
async function confirmDeleteAccount() {
  const val = document.getElementById('deleteConfirm').value.trim();
  if (val !== 'DELETE') { showToast('اكتب DELETE للتأكيد', 'error'); return; }
  try {
    const fd = new FormData();
    fd.append('confirmation', 'DELETE');
    const r = await fetch('/api/account/delete', { method: 'POST', body: fd });
    const d = await r.json();
    if (d.ok) { localStorage.clear(); showToast('✅ تم حذف الحساب', 'success'); setTimeout(() => location.href='/login', 1200); }
    else showToast(d.error || 'تعذر الحذف', 'error');
  } catch(e) { showToast('خطأ في الاتصال', 'error'); }
}

// تفعيل Streaming كـ sendMessage الافتراضية
const _origSend = sendMessage;
window.sendMessage = function() { return sendMessageStream(); };

// ─── Boot ─────────────────────────────────────────────────────
init();
window.addEventListener('load', () => { setupConnection(); registerSW(); checkOnboarding(); });
</script>

<!-- Connection Banner -->
<div id="connBanner" style="display:none;position:fixed;top:0;left:0;right:0;z-index:999;background:rgba(220,50,50,0.92);color:#fff;text-align:center;padding:9px;font-size:13px;font-weight:700;backdrop-filter:blur(8px)">
  📡 لا يوجد اتصال بالإنترنت
</div>
<style>#connBanner{display:none}#connBanner.show{display:block}</style>

<!-- Onboarding Modal -->
<div class="modal" id="onboardModal" onclick="closeModalClick(event)">
  <div class="modal-content" style="max-width:400px">
    <div style="font-size:44px;margin-bottom:14px">🌊</div>
    <h2 style="margin-bottom:12px;font-size:22px">مرحباً في Anas Wadi!</h2>
    <p style="color:var(--text-dim);line-height:1.9;font-size:14px;margin-bottom:20px">
      مساعد ذكاء اصطناعي متكامل — يرسم، يبرمج، يترجم، ويحلل ملفاتك 🚀<br>
      طوّره المهندس <strong>Anas Wadi</strong> من ليبيا 🇱🇾
    </p>
    <button onclick="finishOnboarding()"
      style="width:100%;border-radius:14px;padding:13px;background:linear-gradient(135deg,#00ff94,#00d2ff);color:#000;font-weight:800;font-size:15px;border:none;cursor:pointer;font-family:'Tajawal',sans-serif">
      ابدأ الآن ✨
    </button>
  </div>
</div>

<!-- Share Modal -->
<div class="modal" id="shareModal" onclick="closeModalClick(event)">
  <div class="modal-content">
    <div style="font-size:36px;margin-bottom:12px">🔗</div>
    <h2 style="margin-bottom:10px">شارك المحادثة</h2>
    <p style="color:var(--text-dim);font-size:13px;margin-bottom:16px">رابط للقراءة فقط — بدون تسجيل دخول</p>
    <div style="display:flex;gap:8px;align-items:center">
      <input id="shareUrlInput" type="text" readonly
        style="flex:1;background:var(--surface2);border:1px solid var(--border);color:var(--text);border-radius:10px;padding:10px 14px;font-size:12px;direction:ltr;font-family:monospace">
      <button onclick="copyShareUrl()"
        style="background:linear-gradient(135deg,#00ff94,#00d2ff);border:none;border-radius:10px;padding:10px 16px;color:#000;font-weight:800;cursor:pointer;font-family:'Tajawal',sans-serif;white-space:nowrap">
        نسخ
      </button>
    </div>
  </div>
</div>

<!-- Delete Account Modal -->
<div class="modal" id="deleteAccountModal" onclick="closeModalClick(event)">
  <div class="confirm-modal-content">
    <div style="font-size:36px;margin-bottom:12px">⚠️</div>
    <h3>حذف الحساب نهائياً</h3>
    <p>سيتم حذف حسابك وجميع محادثاتك بشكل لا يمكن التراجع عنه.</p>
    <p style="margin-bottom:14px">اكتب <strong style="color:#ff6b6b">DELETE</strong> للتأكيد:</p>
    <input id="deleteConfirm" type="text" placeholder="DELETE"
      style="width:100%;background:var(--surface2);border:1px solid rgba(255,80,80,0.4);color:var(--text);border-radius:10px;padding:10px 14px;font-size:14px;margin-bottom:16px;text-align:center;letter-spacing:3px;font-family:'Tajawal',sans-serif">
    <div class="confirm-btns">
      <button class="confirm-btn-del" onclick="confirmDeleteAccount()">حذف الحساب</button>
      <button class="confirm-btn-cancel" onclick="document.getElementById('deleteAccountModal').classList.remove('open')">إلغاء</button>
    </div>
  </div>
</div>

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
                                    yield f"data: {json.dumps({'delta': content})}\n\n"
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
                    yield f"data: {json.dumps({'done': True, 'formatted': formatted, 'raw': full_response, 'image_url': image_url})}\n\n"
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
