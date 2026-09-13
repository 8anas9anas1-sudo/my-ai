"""
ذاكرة عائلية دائمة (RAG): تخزّن مقاطع من وثائق/ملاحظات ترفعها العائلة
كمتجهات (embeddings) بنفس قاعدة postgres عبر امتداد pgvector، وتسترجع
أقرب المقاطع بالمعنى (لا بالكلمة الحرفية) عند كل رسالة نصية جديدة
ليستخدمها Wadi كسياق إضافي — نفس فكرة الملخص طويل المدى بالضبط
(app/memory.py) لكن لمصدر معرفة دائم ومشترك بين كل المحادثات، بدل
تاريخ محادثة واحدة فقط.

⚠️ نموذج التضمين (sentence-transformers) يعمل محلياً على نفس السيرفر —
بدون أي مفتاح API إضافي، مجاني بالكامل. لكنه يُحمَّل بأول استخدام فعلي
فقط (نمط singleton محمي بقفل)، ويستهلك عندها ~500MB رام تقريباً وبضع
ثوانٍ لتحميله. لو شغّلت أكثر من gunicorn worker، كل worker يحمّل نسخته
الخاصة على حدة — على خطة Render محدودة الرام، فضّل worker واحد لو
هذي الميزة مفعّلة (ENABLE_RAG=True بـ config.py).

فشل أي دالة هنا يجب ألا يكسر رد المستخدم أبداً — search_relevant_chunks
تحديداً تُستدعى من مسار المحادثة الحرج مباشرة (routes/api.py)، فهي
مصمَّمة لترجع [] بهدوء عند أي خطأ بدل رفع استثناء.
"""
import re
import threading

from app.extensions import db_pool, log
from app.config import Config

_embedder = None
_embedder_lock = threading.Lock()


def _get_embedder():
    """
    يحمّل نموذج التضمين مرة واحدة فقط لكامل عمر الـ process (نمط
    singleton). القفل + الفحص المزدوج (double-checked locking) يمنعان
    تحميل نسختين لو وصل طلبان بنفس اللحظة تماماً بأول استخدام.
    """
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                from sentence_transformers import SentenceTransformer
                log.info("جاري تحميل نموذج التضمين (أول استخدام فقط، قد يأخذ وقتاً)...")
                _embedder = SentenceTransformer(Config.RAG_EMBEDDING_MODEL)
                log.info("نموذج التضمين جاهز")
    return _embedder


def embed_text(text):
    """يرجع متجه التضمين (قائمة أرقام عشرية) لنص واحد."""
    model = _get_embedder()
    vector = model.encode(text, normalize_embeddings=True)
    return vector.tolist()


def chunk_text(text, max_chars=None, overlap=None):
    """
    يقسّم نصاً طويلاً لمقاطع متداخلة قليلاً (overlap)، بمحاذاة حدود
    الجمل قدر الإمكان بدل قص حرفي عشوائي — نفس فلسفة split_text_for_tts
    بـ ai_service.py، لكن هنا الحجم أكبر بكثير لأن الهدف تضمين دلالي
    لا نطق. التداخل يمنع فقدان سياق يمتد عبر حافة القص بين مقطعين.
    ضمان صلب: أي مقطع ناتج مهما كان لا يتجاوز max_chars أبداً.
    """
    max_chars = max_chars or Config.RAG_CHUNK_MAX_CHARS
    overlap = overlap if overlap is not None else Config.RAG_CHUNK_OVERLAP
    text = (text or '').strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r'(?<=[.!؟?])\s+', text)
    chunks = []
    current = ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        candidate = f"{current} {s}".strip() if current else s
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        tail = current[-overlap:] if (overlap and current) else ""
        current = f"{tail} {s}".strip() if tail else s
        while len(current) > max_chars:
            chunks.append(current[:max_chars])
            current = current[max_chars:]
    if current:
        chunks.append(current)
    return chunks


def add_document(user_email, title, content, source_file=None):
    """
    يقسّم وثيقة كاملة لمقاطع، يولّد تضميناً لكل مقطع، ويخزّنها كلها
    بصف واحد لكل مقطع. يرجع (عدد المقاطع المخزَّنة, رسالة خطأ) —
    العدد صفر مع خطأ None يعني وثيقة فارغة فعلاً بعد التنظيف، وليس
    بالضرورة فشلاً.
    """
    if not db_pool:
        return 0, "قاعدة البيانات غير متاحة"
    if not (title or '').strip():
        return 0, "العنوان مطلوب"

    chunks = chunk_text(content)
    if not chunks:
        return 0, "المحتوى فارغ بعد التنظيف"

    try:
        from pgvector.psycopg import register_vector
    except ImportError:
        return 0, "مكتبة pgvector غير مثبتة (أضفها لـ requirements.txt)"

    try:
        rows_to_insert = [
            (user_email, title.strip(), source_file, idx, chunk, embed_text(chunk))
            for idx, chunk in enumerate(chunks)
        ]
    except Exception as e:
        log.error(f"فشل توليد تضمين لوثيقة '{title}': {e}")
        return 0, f"فشل تجهيز الوثيقة: {e}"

    try:
        with db_pool.connection() as conn:
            register_vector(conn)
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO family_documents
                        (user_email, title, source_file, chunk_index, content, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, rows_to_insert)
            conn.commit()
        log.info(f"تم حفظ وثيقة '{title}' بالذاكرة العائلية ({len(rows_to_insert)} مقطع)")
        return len(rows_to_insert), None
    except Exception as e:
        log.error(f"فشل حفظ وثيقة '{title}' بقاعدة البيانات: {e}")
        return 0, f"فشل الحفظ بقاعدة البيانات: {e}"


def search_relevant_chunks(query, top_k=None, min_similarity=None):
    """
    يرجع أقرب المقاطع بالمعنى للاستعلام، من الأقرب للأبعد. pgvector
    يستخدم مسافة cosine (عامل <=>، صفر = تطابق تام)، نحوّلها لتشابه
    (1 - مسافة) ونستبعد أي نتيجة أضعف من الحد الأدنى — يمنع حقن سياق
    غير ذي علاقة بالرد لمجرد وجود صفوف بالجدول.

    مصمَّمة لترجع [] بهدوء عند أي خطأ (لا قاعدة بيانات، لا نموذج تضمين،
    جدول غير موجود...) — تُستدعى من مسار المحادثة الحرج مباشرة، وفشلها
    يجب ألا يكسر أي رد أبداً.
    """
    if not db_pool or not Config.ENABLE_RAG:
        return []
    query = (query or '').strip()
    if not query:
        return []
    top_k = top_k or Config.RAG_TOP_K
    min_similarity = min_similarity if min_similarity is not None else Config.RAG_MIN_SIMILARITY

    try:
        from pgvector.psycopg import register_vector
        query_vector = embed_text(query)
        with db_pool.connection() as conn:
            register_vector(conn)
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT title, content, 1 - (embedding <=> %s) AS similarity
                    FROM family_documents
                    ORDER BY embedding <=> %s
                    LIMIT %s
                """, (query_vector, query_vector, top_k))
                rows = cur.fetchall()
        return [r for r in rows if r['similarity'] >= min_similarity]
    except Exception as e:
        log.error(f"تعذر البحث بالذاكرة العائلية (تم تجاهله بأمان، الرد سيكمل بدون سياق إضافي): {e}")
        return []


def list_documents():
    """يرجع قائمة الوثائق المخزَّنة حالياً (عنوان + عدد مقاطع + تاريخ إضافة) للوحة التحكم."""
    if not db_pool:
        return []
    try:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT title, source_file, user_email, COUNT(*) AS chunks, MIN(created_at) AS added_at
                    FROM family_documents
                    GROUP BY title, source_file, user_email
                    ORDER BY added_at DESC
                """)
                return cur.fetchall()
    except Exception as e:
        log.error(f"فشل جلب قائمة وثائق الذاكرة العائلية: {e}")
        return []


def delete_document(title, source_file, user_email):
    """
    يحذف كل مقاطع وثيقة واحدة بالضبط — بنفس المفتاح المركّب اللي
    list_documents() أعلاه يستخدمه فعلاً لتجميع المقاطع كـ"وثيقة واحدة"
    (title + source_file + user_email معاً، لا العنوان وحده). قبل هذا
    التعديل، وثيقتان تشاركان نفس العنوان بالصدفة (حتى لو من عضوين
    مختلفين بالعائلة، أو من ملفين مختلفين تماماً) كانتا تُحذفان معاً
    بمجرد طلب حذف إحداهما — العنوان وحده لم يكن كافياً للتمييز.

    IS NOT DISTINCT FROM لا = العادي لمقارنة source_file تحديداً، لأنها
    NULLABLE (وثيقة نصية مباشرة بلا ملف مصدر) وNULL = NULL بـSQL نتيجتها
    دائماً NULL (غير معروفة)، لا TRUE — كانت ستفشل بصمت بالضبط بالحالة
    الشائعة (وثيقة بلا ملف مصدر).

    ⚠️ يبقى نظرياً ممكن أن تتطابق وثيقتان بالثلاثة حقول معاً (نفس
    العنوان حرفياً + نفس الملف المصدر + نفس البريد) — حل كامل 100% لهذا
    الاحتمال الأضيق يحتاج عمود document_id حقيقي (UUID يُولَّد مرة واحدة
    بـadd_document ويُخزَّن بكل صف/مقطع تابع له) + migration فعلي على
    الجدول القائم (ALTER TABLE + تعبئة القيم القديمة) — لم أطبّقه هنا
    لأنه يحتاج اختباراً على قاعدة بيانات حقيقية لا أملكها بهذي البيئة،
    وMigration بلا اختبار فعلي خطر أكبر من فائدته لحالة نادرة كهذي.
    """
    if not db_pool:
        return False
    try:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM family_documents
                    WHERE title = %s
                      AND source_file IS NOT DISTINCT FROM %s
                      AND user_email = %s
                """, (title, source_file, user_email))
            conn.commit()
        return True
    except Exception as e:
        log.error(f"فشل حذف وثيقة '{title}': {e}")
        return False
