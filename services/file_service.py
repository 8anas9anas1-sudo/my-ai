import logging
import PyPDF2
import io
from PIL import Image
import base64

logger = logging.getLogger(__name__)

# ─── حدود الأمان ────────────────────────────────
MAX_FILE_SIZE = 10 * 1024 # 10MB
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'txt'}
ALLOWED_MIME_TYPES = {
    'application/pdf',
    'image/png',
    'image/jpeg',
    'text/plain'
}

def is_file_safe(file):
    """
    يتحقق من حجم وامتداد ونوع الملف قبل المعالجة
    """
    # 1. تحقق من الحجم
    file.seek(0, 2) # روح لآخر الملف
    size = file.tell()
    file.seek(0) # ارجع للبداية
    if size > MAX_FILE_SIZE:
        logger.warning(f"File too large: {size} bytes")
        return False, "الملف كبير بزيادة. الحد الأقصى 10MB"

    # 2. تحقق من الامتداد
    filename = file.filename.lower()
    if '.' not in filename:
        return False, "الملف بدون امتداد"

    ext = filename.rsplit('.', 1)[1]
    if ext not in ALLOWED_EXTENSIONS:
        return False, f"نوع الملف {ext} مش مدعوم. المسموح: pdf, png, jpg, txt"

    # 3. تحقق من MIME Type الحقيقي
    mime = file.mimetype
    if mime not in ALLOWED_MIME_TYPES:
        return False, f"نوع الملف غير آمن: {mime}"

    return True, "OK"

def extract_text_from_pdf(file_stream):
    """
    يطلع النص من PDF. لو الصفحات واجد ياخذ أول 10 بس.
    """
    try:
        reader = PyPDF2.PdfReader(file_stream)
        text = ""
        pages = len(reader.pages)

        # حد أقصى 10 صفحات عشان ما يعلق السيرفر
        for i in range(min(pages, 10)):
            page_text = reader.pages[i].extract_text()
            if page_text:
                text += page_text + "\n"

        if not text.strip():
            return "الـ PDF هذا فاضي أو صور بس. ما قدرتش نطلع منه نص."

        # نقص النص لو طويل بزيادة
        return text[:15000] # 15 ألف حرف كافي
    except Exception as e:
        logger.error(f"PDF extract error: {e}")
        return "صار خطأ في قراءة الـ PDF. يمكن الملف معطوب."

def process_image_to_base64(file_stream):
    """
    يحول الصورة لـ base64 عشان نرسلها للـ AI
    """
    try:
        img = Image.open(file_stream)
        # صغّر الصورة لو كبيرة عشان ما تصرفش توكن واجد
        if img.width > 1024 or img.height > 1024:
            img.thumbnail((1024, 1024))

        # حولها لـ PNG
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        img_str = base64.b64encode(buffer.getvalue()).decode()
        return f"data:image/png;base64,{img_str}"
    except Exception as e:
        logger.error(f"Image process error: {e}")
        return None

def extract_text_from_file(file):
    """
    الدالة الرئيسية: تستقبل الملف وتقرر كيف تعالجه
    """
    safe, msg = is_file_safe(file)
    if not safe:
        return msg

    mime = file.mimetype

    if mime == 'application/pdf':
        return extract_text_from_pdf(file.stream)

    elif mime == 'text/plain':
        return file.stream.read(15000).decode('utf-8', errors='ignore')

    elif mime in ['image/png', 'image/jpeg']:
        img_base64 = process_image_to_base64(file.stream)
        if img_base64:
            return f"[صورة مرفقة] - اكتب لي شن تبي ندير بالصورة هذي"
        else:
            return "ما قدرتش نقرأ الصورة"

    return "نوع الملف مش مدعوم"
