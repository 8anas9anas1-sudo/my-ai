"""
الإضافات المشتركة (logging، بركة اتصالات قاعدة البيانات، Flask-Limiter)
تُنشأ هنا بدون app مرتبط، وتُربط بالتطبيق داخل create_app()
(نمط app factory) — يسمح باستيرادها من أي ملف بدون استيراد دائري.
"""
import logging
from flask_limiter import Limiter
from flask_wtf.csrf import CSRFProtect
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

from app.config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("anas_wadi")


def _get_client_ip():
    # يستورد من security.py محلياً لتفادي استيراد دائري عند تحميل الوحدة
    from app.security import get_client_ip
    return get_client_ip()


limiter = Limiter(
    key_func=_get_client_ip,
    storage_uri=Config.REDIS_URL or "memory://",
    default_limits=[],
)

# حماية CSRF على مستوى التطبيق كله — أي POST/PUT/PATCH/DELETE بلا رمز
# صالح يُرفض تلقائياً قبل ما يوصل أي دالة مسار. انظر csrf_error_handler
# بـ __init__.py للرد المخصَّص حسب نوع المسار عند الرفض.
csrf = CSRFProtect()

if not Config.REDIS_URL:
    log.warning("REDIS_URL غير مضبوط — تحديد المعدل سيعمل بالذاكرة فقط "
                "(غير موثوق مع أكثر من gunicorn worker في الإنتاج)")


def _normalize_db_url(url):
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


db_pool = None
if Config.DATABASE_URL:
    try:
        db_pool = ConnectionPool(
            conninfo=_normalize_db_url(Config.DATABASE_URL),
            min_size=Config.DB_POOL_MIN_SIZE,
            max_size=Config.DB_POOL_MAX_SIZE,
            kwargs={"row_factory": dict_row, "sslmode": "require"},
        )
        log.info("تم إنشاء بركة اتصالات قاعدة البيانات")
    except Exception as e:
        log.error(f"فشل إنشاء بركة اتصالات قاعدة البيانات: {e}")
        db_pool = None
else:
    log.warning("DATABASE_URL غير مضبوط — قاعدة البيانات غير متاحة")
