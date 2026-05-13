# ============================================
# Anas Wadi - Production-Ready Flask Application
# Full Refactor with Security, Performance, Scalability & Observability
# ============================================
import os
import re
import json
import time
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
    SESSION_TYPE = os.environ.get("SESSION_TYPE", "redis")
    SESSION_REDIS = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
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
    RATELIMIT_STORAGE_URL = os.environ.get("RATELIMIT_STORAGE_URL", "redis://localhost:6379/3")
    # Security
    BCRYPT_ROUNDS = 12
    PASSWORD_SALT = os.environ.get("PASSWORD_SALT", "production-salt-2026")  # kept for compatibility

# ============================================
# Application Factory
# ============================================
app = Flask(__name__)
app.config.from_object(Config)

# Initialize extensions
Session(app)
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["30 per minute"]
)

# Celery factory
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
db_pool = None
def init_db_pool():
    global db_pool
    if db_pool is None:
        db_url = Config.DATABASE_URL
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        db_pool = ConnectionPool(
            conninfo=db_url,
            min_size=2,
            max_size=10,
            kwargs={"row_factory": dict_row, "sslmode": "require"}
        )
    return db_pool

def get_db_connection():
    pool = init_db_pool()
    return pool.getconn()

def return_db_connection(conn):
    if db_pool and conn:
        db_pool.putconn(conn)

# Database initialization (called once)
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
def hash_password(password: str) -> str:
    """Hash password using bcrypt with configurable rounds."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(Config.BCRYPT_ROUNDS)).decode('utf-8')

def check_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

# ============================================
# Prompt Injection Protection
# ============================================
BANNED_PATTERNS = [
    r'ignore\s*[a-z]*\s*(previous|all)\s*instructions',
    r'(system|user)\s*prompt',
    r'you\s*are\s*now',
    r'jail\s*break',
    r'pretend\s*you',
    r'act\s*as\s*if',
    r'forget\s*your',
    r'reset\s*prompt',
    r'new\s*persona',
    r'deceive',
]

def is_prompt_injection(text: str) -> bool:
    text_lower = text.lower()
    for pattern in BANNED_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True
    # Check for very long repetitive phrases
    if len(text) > 2000 and len(set(text.split())) < 50:
        return True
    return False

# ============================================
# User Authentication (DB operations)
# ============================================
def create_user(email: str, password: str, name: str) -> (bool, str):
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
                return {'email': user['email'], 'name': user['name'], 'onboarding_seen': user['onboarding_seen']}
        return None
    except Exception as e:
        app.logger.error(f"Verify user error: {str(e)}")
        return None
    finally:
        return_db_connection(conn)

def update_onboarding_seen(email: str):
    conn = get_db_connection()
    if not conn: return
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET onboarding_seen = TRUE WHERE email = %s", (email.lower().strip(),))
            conn.commit()
    except Exception as e:
        app.logger.error(f"Onboarding update error: {str(e)}")
    finally:
        return_db_connection(conn)

def delete_user_account(email: str, password: str) -> (bool, str):
    conn = get_db_connection()
    if not conn:
        return False, "Database connection error"
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT password_hash FROM users WHERE email = %s", (email.lower().strip(),))
            row = cur.fetchone()
            if not row or not check_password(password, row['password_hash']):
                return False, "Invalid password"
            cur.execute("DELETE FROM conversations WHERE user_email = %s", (email.lower().strip(),))
            cur.execute("DELETE FROM users WHERE email = %s", (email.lower().strip(),))
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
def save_message(chat_id, user_email, user_name, user_message, ai_response, raw_ai, mode, image_url=None, file_name=None, share_token=None):
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO conversations
                    (chat_id, user_email, user_name, user_message, ai_response, raw_ai, mode, image_url, file_name, share_token)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (chat_id, user_email, user_name, user_message, ai_response, raw_ai, mode, image_url, file_name, share_token))
            conn.commit()
        return True
    except Exception as e:
        app.logger.error(f"Save message error: {str(e)}")
        return False
    finally:
        return_db_connection(conn)

def get_user_chats(user_email):
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (chat_id)
                    chat_id,
                    user_message,
                    created_at
                FROM conversations
                WHERE user_email = %s
                ORDER BY chat_id, created_at ASC
            """, (user_email,))
            rows = cur.fetchall()
        rows.sort(key=lambda x: x['created_at'], reverse=True)
        return rows
    except Exception as e:
        app.logger.error(f"Get user chats error: {str(e)}")
        return []
    finally:
        return_db_connection(conn)

def get_chat_messages(chat_id, user_email):
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

def delete_chat_from_db(chat_id, user_email):
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

def create_share_token(chat_id, user_email):
    token = secrets.token_urlsafe(16)
    conn = get_db_connection()
    if not conn: return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE conversations SET share_token = %s WHERE chat_id = %s AND user_email = %s AND share_token IS NULL",
                (token, chat_id, user_email)
            )
            conn.commit()
        return token
    except Exception as e:
        app.logger.error(f"Share token error: {str(e)}")
        return None
    finally:
        return_db_connection(conn)

def get_shared_chat_by_token(token):
    conn = get_db_connection()
    if not conn: return None
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

def get_system_prompt(mode, user_message):
    if any(q in user_message.lower() for q in IDENTITY_TRIGGERS):
        return "أجب بالضبط: أنا Wadi، مساعد ذكاء اصطناعي طوّره المهندس Anas Wadi من ليبيا 🇱🇾. لا تضف أي معلومة أخرى."
    return MODE_PROMPTS.get(mode, MODE_PROMPTS['fast'])

def generate_image(prompt):
    clean_prompt = prompt.strip()
    encoded = requests.utils.quote(clean_prompt)
    primary_url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1024&height=1024&model=flux&enhance=true&nologo=true"
        f"&seed={hash(clean_prompt) % 99999}"
    )
    fallback_url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1024&height=768&nologo=true"
    )
    return primary_url, fallback_url

def format_response(text):
    import mistune
    md = mistune.create_markdown()
    html = md(text)
    allowed_tags = ['h2','h3','h4','p','strong','em','ul','ol','li','code','pre','br','hr','a']
    return bleach.clean(html, tags=allowed_tags, attributes={'pre': ['data-lang'], 'code': ['class'], 'a': ['href']}, strip=True)

# ============================================
# File Handling
# ============================================
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS

def extract_pdf_text(file):
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(file.read()))
        text = ""
        for page in reader.pages[:20]:
            t = page.extract_text()
            if t:
                text += t + "\n"
        return text[:15000]
    except Exception as e:
        return f"Error reading PDF: {str(e)}"

def extract_text_from_txt(file):
    try:
        return file.read().decode('utf-8')[:15000]
    except:
        return ""
        
# ============================================
# CSRF Protection (custom)
# ============================================
def generate_csrf_token():
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
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://image.pollinations.ai blob:; connect-src 'self' https://api.groq.com;"
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
        .form-group {
            margin-bottom: 20px;
        }
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
        .link a:hover {
            text-decoration: underline;
        }
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
        /* SIDEBAR */
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
        .sidebar-header .user-info {
            flex: 1;
        }
        .sidebar-header .user-info h3 {
            font-size: 1rem;
            font-weight: 600;
        }
        .sidebar-header .user-info p {
            font-size: 0.75rem;
            color: var(--muted);
        }
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
        .new-chat-btn:hover {
            background: rgba(255,255,255,0.06);
        }
        .chat-list {
            flex: 1;
            overflow-y: auto;
            padding: 8px;
        }
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
        .chat-item.active, .chat-item:hover {
            background: rgba(255,255,255,0.05);
        }
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
        /* MAIN */
        .main {
            flex: 1;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }
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
            to { opacity: 1; transform: translateY(0); }
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
        .loader {
            display: none;
            text-align: center;
            color: var(--muted);
            padding: 10px;
        }
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
            30% { transform: translateY(-8px); }
        }
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
                <input type="file" id="fileImage" accept="image/*" style="display:none" onchange="handleFileSelect(this, 'image')">
                <input type="file" id="fileFile" accept=".txt,.pdf" style="display:none" onchange="handleFileSelect(this, 'file')">
                <input type="file" id="filePdf" accept=".pdf" style="display:none" onchange="handleFileSelect(this, 'pdf')">
            </div>

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
    <head><meta charset="UTF-8"><title>محادثة مشتركة - Anas Wadi</title><link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@300;400;700&display=swap" rel="stylesheet"><style>
    body{background:#050510;color:#e8eaf6;font-family:'Tajawal',sans-serif;max-width:800px;margin:0 auto;padding:20px;}
    .user-msg{background:linear-gradient(135deg,#00ff94,#00d2ff);color:#000;padding:12px 18px;border-radius:20px 20px 6px 20px;margin:10px 0;max-width:80%;margin-right:auto;}
    .ai-msg{background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);padding:14px 18px;border-radius:20px 20px 20px 6px;margin:10px 0;max-width:85%;}
    .footer{text-align:center;margin-top:30px;font-size:12px;color:gray;}
    </style></head>
    <body><h2 style="text-align:center">✨ محادثة مشتركة من Anas Wadi</h2>
    {% for m in messages %}
        <div class="user-msg">{{ m.user_message }}</div>
        <div class="ai-msg">{{ m.ai_response|safe }}</div>
    {% endfor %}
    <div class="footer">تمت المشاركة بواسطة مستخدم Anas Wadi | <a href="/" style="color:#00d2ff">جرب المساعد بنفسك</a></div>
    </body></html>
    """, messages=messages)

@app.route("/api/user/delete", methods=["POST"])
@login_required
@csrf_required
def api_delete_user():
    email = session['user']['email']
    password = request.form.get('password', '')
    if not email or not password:
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
@limiter.limit("10/minute")
def chat_stream():
    if not Config.GROQ_API_KEY:
        return jsonify({"error": "API key missing"}), 500

    user_message = request.form.get("message", "")
    mode = request.form.get("mode", "fast")
    chat_id = request.form.get("chat_id", "")
    history_raw = request.form.get("history", "[]")
    file = request.files.get("file")

    user_info = session['user']
    user_email = user_info['email']
    user_name = user_info['name']

    # Security check
    if is_prompt_injection(user_message):
        return jsonify({"error": "Message rejected for security reasons"}), 403

    if mode not in MODE_PROMPTS:
        mode = 'fast'

    # Build message list
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
                messages.append({"role": "user", "content": u})
                messages.append({"role": "assistant", "content": a})
    except Exception:
        pass

    # Handle file upload
    file_name = None
    image_url = None
    file_context = ""
    if file and file.filename and allowed_file(file.filename):
        original_filename = secure_filename(file.filename)
        ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
        if ext in {'png', 'jpg', 'jpeg', 'webp'}:
            os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
            saved_name = f"{int(time.time())}_{secrets.token_hex(4)}.{ext}"
            file_path = os.path.join(Config.UPLOAD_FOLDER, saved_name)
            file.save(file_path)
            image_url = f"/uploads/{saved_name}"
            file_name = original_filename
        elif ext == 'pdf':
            file_content = extract_pdf_text(file)
            file_context = f"PDF content ({original_filename}):\n{file_content}\n\n"
            file_name = original_filename
        elif ext == 'txt':
            file_content = extract_text_from_txt(file)
            file_context = f"File content ({original_filename}):\n{file_content}\n\n"
            file_name = original_filename

    final_user_message = (file_context + user_message).strip() or "Hello"
    messages.append({"role": "user", "content": final_user_message})

    model_map = {
        'thinker':'qwen/qwen3-32b', 'coder':'qwen/qwen3-32b',
        'writer':'llama-3.3-70b-versatile', 'creative':'llama-3.3-70b-versatile',
        'fast':'llama-3.1-8b-instant', 'funny':'llama-3.1-8b-instant'
    }
    model = model_map.get(mode, 'llama-3.1-8b-instant')
    temperature = {'funny':0.92,'creative':0.88,'writer':0.82,'thinker':0.45,'coder':0.25,'fast':0.72}.get(mode, 0.72)

    def generate():
        full_response = ""
        try:
            with requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {Config.GROQ_API_KEY}"},
                json={
                    "model": model, "messages": messages, "max_tokens": max_tokens,
                    "temperature": temperature, "top_p": 0.92, "stream": True
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
                                chunk = json.loads(data)
                                content = chunk['choices'][0]['delta'].get('content', '')
                                if content:
                                    full_response += content
                                    yield f"data: {json.dumps({'content': content})}\n\n"
                            except:
                                pass
        except Exception as e:
            app.logger.error(f"Stream error: {str(e)}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            # Save message after stream ends (even if connection dropped)
            if chat_id and full_response:
                try:
                    save_message(chat_id, user_email, user_name,
                                 user_message or "(file)", format_response(full_response),
                                 full_response, mode, image_url, file_name)
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
    mode = request.form.get("mode", "fast")
    chat_id = request.form.get("chat_id", "")
    history_raw = request.form.get("history", "[]")
    file = request.files.get("file")

    user_info = session['user']
    user_email = user_info['email']
    user_name = user_info['name']

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
                messages.append({"role": "user", "content": u})
                messages.append({"role": "assistant", "content": a})
    except:
        pass

    file_name = None
    image_url = None
    file_context = ""
    if file and file.filename and allowed_file(file.filename):
        original_filename = secure_filename(file.filename)
        ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
        if ext in {'png', 'jpg', 'jpeg', 'webp'}:
            os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
            saved_name = f"{int(time.time())}_{secrets.token_hex(4)}.{ext}"
            file_path = os.path.join(Config.UPLOAD_FOLDER, saved_name)
            file.save(file_path)
            image_url = f"/uploads/{saved_name}"
            file_name = original_filename
        elif ext == 'pdf':
            file_content = extract_pdf_text(file)
            file_context = f"PDF content ({original_filename}):\n{file_content}\n\n"
            file_name = original_filename
        elif ext == 'txt':
            file_content = extract_text_from_txt(file)
            file_context = f"File content ({original_filename}):\n{file_content}\n\n"
            file_name = original_filename

    final_user_message = (file_context + user_message).strip() or "Hello"
    messages.append({"role": "user", "content": final_user_message})

    model_map = {
        'thinker':'qwen/qwen3-32b', 'coder':'qwen/qwen3-32b',
        'writer':'llama-3.3-70b-versatile', 'creative':'llama-3.3-70b-versatile',
        'fast':'llama-3.1-8b-instant', 'funny':'llama-3.1-8b-instant'
    }
    model = model_map.get(mode, 'llama-3.1-8b-instant')
    temperature = {'funny':0.92,'creative':0.88,'writer':0.82,'thinker':0.45,'coder':0.25,'fast':0.72}.get(mode, 0.72)

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {Config.GROQ_API_KEY}"},
            json={
                "model": model, "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature, "top_p": 0.92
            },
            timeout=60
        )
        if resp.status_code == 200:
            data = resp.json()
            raw = data['choices'][0]['message']['content']
            formatted = format_response(raw)
            image_gen_url = None
            if mode == 'creative' and ('رسم' in user_message or 'صورة' in user_message):
                eng_prompt = re.sub(r'ارسم|صورة|توليد', '', user_message).strip()
                primary, _ = generate_image(eng_prompt)
                image_gen_url = primary
            if chat_id:
                save_message(chat_id, user_email, user_name, user_message, formatted, raw, mode, image_url or image_gen_url, file_name)
            return jsonify({
                "response": formatted,
                "raw": raw,
                "image_url": image_url or image_gen_url
            })
        else:
            return jsonify({"error": f"Groq API error {resp.status_code}"}), 500
    except Exception as e:
        app.logger.error(f"Chat error: {str(e)}")
        return jsonify({"error": f"Connection error: {str(e)}"}), 500

# ============================================
# Celery Tasks
# ============================================
@celery.task
def process_heavy_request(messages, model, temperature, max_tokens):
    """Handle heavy AI processing in background."""
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
            return resp.json()['choices'][0]['message']['content']
        else:
            return None
    except Exception as e:
        app.logger.error(f"Celery task error: {str(e)}")
        return None

# ============================================
# Static Files & Service Worker
# ============================================
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(Config.UPLOAD_FOLDER, filename)

@app.route('/sw.js')
def service_worker():
    return Response("""
    const CACHE_NAME = 'anas-wadi-v1';
    const urlsToCache = [
        '/',
        '/static/offline.html'
    ];
    self.addEventListener('install', event => {
        event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(urlsToCache)));
    });
    self.addEventListener('fetch', event => {
        event.respondWith(
            fetch(event.request).catch(() => caches.match(event.request).then(response => response || caches.match('/')))
        );
    });
    """, mimetype='application/javascript')

@app.route('/manifest.json')
def manifest():
    return Response("""
    {
        "name": "Anas Wadi AI",
        "short_name": "AnasWadi",
        "start_url": "/",
        "display": "standalone",
        "theme_color": "#050510",
        "background_color": "#050510",
        "icons": []
    }
    """, mimetype='application/manifest+json')

# ============================================
# Error Handlers
# ============================================
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
