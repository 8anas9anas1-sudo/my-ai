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
    """يطابق فقط الملح الافتراضي القديم؛ للحسابات المنشأة قبل الترقية لـ bcrypt.

    ⚠️ لو PASSWORD_SALT غير مضبوط ببيئة الإنتاج، القيمة الافتراضية هنا
    ("anas-wadi-salt-2026") مكتوبة بكود التطبيق نفسه — أي حساب لا يزال
    بهاش SHA-256 قديم محمي فعلياً بملح معروف/عام لا بسرّ حقيقي (يسهّل
    هجوم قاموس لو تسرَّبت قاعدة البيانات بالتحديد بهذي النافذة الزمنية).
    __init__.py يطبع تحذيراً بسجلات الإقلاع لو المتغيّر غير مضبوط — راجعه.
    بما أن الترقية لـbcrypt تلقائية عند أول دخول ناجح (verify_user تحت)،
    الأرجح كل الحسابات النشطة رُقِّيت فعلاً منذ زمن. للتأكد ثم التنظيف
    الكامل (حذف هذي الدالة، verify_legacy_password، وفرعها بـverify_user):
      1) شغّل بمحرر SQL بلوحة Supabase:
         SELECT COUNT(*) FROM users WHERE password_hash ~ '^[0-9a-f]{64}$'
      2) لو النتيجة 0 بالضبط، احذف الثلاثة المذكورة أعلاه بأمان تام —
         لا حساب سيتأثر. لو غير صفر، لا تحذف قبل ما تلك الحسابات تسجّل
         دخول مرة (تُرقّى تلقائياً) أو تُرقّى يدوياً.
    """
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
# ⚠️ لم أغيّر [0] هنا لأن الأدلة الفعلية اللي لقيتها متناقضة حرفياً، لا
# مجرد "غير مؤكدة نظرياً":
#   - رد رسمي من فريق Render (feedback.render.com/features/p/
#     send-the-correct-xforwardedfor): "we set the first IP in the list
#     to the real client IP" — يدعم [0] كما هو.
#   - لكن مستخدمَين مستقلَّين تماماً على community.render.com اختبرا
#     فعلياً (أرسلا X-Forwarded-For مزيَّف بأنفسهما لخدمة حقيقية على
#     Render) ووثّقا أن القيمة المزيَّفة وصلت وبقيت بموضع [0]، وأن
#     Render فقط يُلحق عنوانه هو بآخر القائمة بدل ما يستبدل الهيدر كله.
# تغيير [0] لـ[-1] بلا دليل من تطبيقك أنت تحديداً قد يكون تخميناً خاطئاً
# بنفس درجة إبقاء [0] — الفرق الوحيد بين الخيارين يظهر أصلاً فقط لما
# الهيدر يحتوي أكثر من قيمة (أي محاولة تزييف فعلية)؛ بالاستخدام العادي
# بلا تزييف كلاهما نفس القيمة. تحقّق بنفسك مباشرة بدل الاعتماد على أي
# مصدر (مصدري متضمَّناً): من جهاز آخر (لا من نفس الشبكة)، شغّل
#   curl -H "X-Forwarded-For: 6.6.6.6" https://<رابط تطبيقك>.onrender.com/login
# وشوف قيمة الهيدر الخام بسجلات Render (Logs بالداشبورد) لنفس الطلب —
# لو "6.6.6.6" ظهرت أول القائمة، بدّل [0] لـ[-1] بالسطر تحت. بغض النظر
# عن النتيجة، الحماية الإضافية الحقيقية غير المعتمدة على حسم هذا
# التناقض أصلاً مضافة بـroutes/auth.py (حد بمستوى الحساب نفسه، لا الـIP
# فقط — يوقف استنزاف حساب واحد حتى لو الـIP قابل للتزييف فعلاً).
def get_client_ip() -> str:
    return request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()


# ─── تحقق محتوى الصورة الفعلي عند الرفع (magic bytes) ───────────
# Content-Type بطلب multipart قيمة يُعلنها الطالب نفسه بالكامل — لا
# علاقة لها بمحتوى الملف الفعلي بالبايتات. svg+xml مستبعدة عمداً من
# القائمة المسموحة (تقبل تضمين <script> كجزء طبيعي من صيغتها)، وحتى
# ضمن الصيغ المسموحة، نتحقق من أول بايتات الملف الفعلية تطابق التوقيع
# الحقيقي للصيغة المُعلَنة — يمنع تسمية ملف بامتداد/Content-Type مزيَّف.
ALLOWED_IMAGE_TYPES = {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}

_IMAGE_MAGIC_BYTES = {
    'image/png': (b'\x89PNG\r\n\x1a\n',),
    'image/jpeg': (b'\xff\xd8\xff',),
    'image/gif': (b'GIF87a', b'GIF89a'),
}


def is_valid_image_upload(file_bytes: bytes, content_type: str) -> bool:
    if content_type not in ALLOWED_IMAGE_TYPES:
        return False
    if not file_bytes:
        return False
    if content_type == 'image/webp':
        # صيغة WEBP: 4 بايتات "RIFF"، ثم 4 بايتات لحجم الملف (تختلف بكل
        # ملف، نتجاوزها)، ثم 4 بايتات "WEBP" — التوقيع الوحيد هنا اللي
        # يحتاج تجاوز جزء متغيّر بدل مطابقة بادئة ثابتة كالباقي.
        return file_bytes[:4] == b'RIFF' and file_bytes[8:12] == b'WEBP'
    return any(file_bytes.startswith(sig) for sig in _IMAGE_MAGIC_BYTES.get(content_type, ()))


# ─── تحقق صيغة chat_id ──────────────────────────────────────────
# chat_id يُولَّد بالواجهة حصراً كـ Date.now().toString() (انظر app.js)
# — أي رقم صحيح موجب، لا شيء آخر شرعي. القيمة تدخل لاحقاً مباشرة
# بمسار تخزين خارجي (storage.py: upload_image/persist_generated_image)
# فرفض أي صيغة غريبة هنا (قبل وصولها لذاك المسار) رخيص ومهم.
CHAT_ID_RE = re.compile(r'^\d{1,20}$')


def is_valid_chat_id(chat_id: str) -> bool:
    return bool(chat_id) and bool(CHAT_ID_RE.fullmatch(chat_id))
