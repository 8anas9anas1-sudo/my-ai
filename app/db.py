"""
طبقة الوصول لقاعدة البيانات. كل دالة تستخدم بركة الاتصالات المشتركة
(db_pool) بدل فتح اتصال جديد بكل استعلام.
"""
import psycopg
import secrets

from app.extensions import db_pool, log
from app.config import Config
from app.security import (
    hash_password, is_legacy_sha256_hash,
    verify_legacy_password, verify_bcrypt_password,
)


def init_db():
    if not db_pool:
        log.warning("تعذر إنشاء الجداول - قاعدة البيانات غير متاحة")
        return
    try:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
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
                    CREATE INDEX IF NOT EXISTS idx_chat_user ON conversations(chat_id, user_email);
                    -- عمود جديد لمسار الصورة المرفوعة بمخزن Supabase (تخزين
                    -- دائم). ADD COLUMN IF NOT EXISTS آمن على نشر قائم فعلاً.
                    ALTER TABLE conversations ADD COLUMN IF NOT EXISTS uploaded_image_path TEXT;
                    -- تفكير النموذج الداخلي (reasoning من نماذج gpt-oss أثناء
                    -- البث) — يُحفظ الآن دائماً بدل ما يضيع بعد الجلسة، حتى
                    -- يبقى زر "عرض التفكير" شغّالاً حتى بعد تحديث الصفحة أو
                    -- تبديل محادثة ثم الرجوع لها. NULL للرسائل التي ما مرّت
                    -- بمرحلة تفكير (أو المحفوظة قبل هذا العمود).
                    ALTER TABLE conversations ADD COLUMN IF NOT EXISTS reasoning TEXT;
                """)
                cur.execute("""
                    -- ذاكرة طويلة المدى: ملخص واحد لكل محادثة، يُحدَّث
                    -- تلقائياً كل ما تراكمت رسائل جديدة كافية (انظر app/memory.py)
                    CREATE TABLE IF NOT EXISTS chat_summaries (
                        chat_id TEXT PRIMARY KEY,
                        user_email TEXT NOT NULL,
                        summary_text TEXT NOT NULL,
                        summarized_up_to_id INTEGER NOT NULL,
                        updated_at TIMESTAMP DEFAULT NOW()
                    );
                    CREATE INDEX IF NOT EXISTS idx_summaries_user ON chat_summaries(user_email);
                """)
                cur.execute("""
                    -- حصة الاستخدام اليومية لكل مستخدم — حماية من استنزاف حد
                    -- Groq المجاني المشترك بين كل حسابات العائلة. صف واحد لكل
                    -- (مستخدم, يوم)، يُحدَّث بخطوة ذرية واحدة (انظر
                    -- try_consume_daily_usage أدناه) بدل قراءة-ثم-كتابة بخطوتين
                    -- منفصلتين قد تتسابقان لو وصل طلبان بنفس اللحظة تماماً.
                    CREATE TABLE IF NOT EXISTS daily_usage (
                        user_email TEXT NOT NULL,
                        usage_date DATE NOT NULL,
                        message_count INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (user_email, usage_date)
                    );
                """)
                cur.execute("""
                    -- روابط استرداد كلمة المرور — يولّدها الأدمن يدوياً من
                    -- لوحة التحكم (لا بريد إلكتروني بالمشروع حالياً)، صالحة
                    -- 24 ساعة، استخدام واحد فقط. انظر create_password_reset/
                    -- get_valid_password_reset/consume_password_reset أدناه.
                    CREATE TABLE IF NOT EXISTS password_resets (
                        token TEXT PRIMARY KEY,
                        user_email TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT NOW(),
                        expires_at TIMESTAMP NOT NULL,
                        used_at TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_password_resets_email ON password_resets(user_email);
                """)
            conn.commit()
        log.info("قاعدة البيانات جاهزة")
    except Exception as e:
        log.error(f"خطأ في إنشاء الجداول: {e}")

    # معزول عمداً بمحاولة/استثناء خاصة به: لو امتداد pgvector غير متوفر
    # على مزوّد قاعدة البيانات (نادر مع Supabase، وارد مع Postgres عام
    # بدون الامتداد مُفعَّلاً)، فشل هذا الجزء وحده يجب ألا يمنع الجداول
    # الأساسية أعلاه من إنشائها بنجاح — الذاكرة العائلية ميزة اختيارية،
    # تسجيل الدخول والمحادثات ليست كذلك.
    if Config.ENABLE_RAG and db_pool:
        try:
            with db_pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                    cur.execute(f"""
                        -- الذاكرة العائلية الدائمة (RAG) — انظر app/rag.py.
                        -- طول VECTOR ثابت بأبعاد نموذج التضمين المستخدم
                        -- (RAG_EMBEDDING_MODEL بـ config.py) — تغيير النموذج
                        -- لاحقاً لموديل بأبعاد مختلفة يتطلب إعادة بناء الجدول.
                        CREATE TABLE IF NOT EXISTS family_documents (
                            id SERIAL PRIMARY KEY,
                            user_email TEXT NOT NULL,
                            title TEXT NOT NULL,
                            source_file TEXT,
                            chunk_index INTEGER NOT NULL DEFAULT 0,
                            content TEXT NOT NULL,
                            embedding VECTOR({Config.RAG_EMBEDDING_DIM}) NOT NULL,
                            created_at TIMESTAMP DEFAULT NOW()
                        );
                        CREATE INDEX IF NOT EXISTS idx_family_documents_title ON family_documents(title);
                    """)
                conn.commit()
            log.info("جدول الذاكرة العائلية (RAG) جاهز")
        except Exception as e:
            log.error(f"تعذر تفعيل الذاكرة العائلية (pgvector) — الميزة ستكون معطّلة، "
                      f"بقية التطبيق يعمل طبيعياً: {e}")


# ─── المستخدمون ─────────────────────────────────────────────────
def create_user(email, password, name):
    if not db_pool:
        return False, "تعذر الاتصال بقاعدة البيانات"
    try:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (email, password_hash, name) VALUES (%s, %s, %s)",
                    (email.lower().strip(), hash_password(password), name.strip())
                )
            conn.commit()
        return True, "تم إنشاء الحساب بنجاح"
    except psycopg.errors.UniqueViolation:
        return False, "البريد الإلكتروني مستخدم مسبقاً"
    except Exception as e:
        log.error(f"فشل إنشاء مستخدم: {e}")
        return False, "حدث خطأ غير متوقع، حاول مجدداً"


def verify_user(email, password):
    """
    يتحقق بـ bcrypt. لو المستخدم عنده هاش SHA-256 قديم (من قبل الترقية
    لـ bcrypt)، يتحقق منه بالطريقة القديمة مرة واحدة فقط، ثم يرقّي
    الهاش تلقائياً بالخلفية — بدون أي تعطيل لحسابات المستخدمين الحاليين.
    """
    if not db_pool:
        return None
    email = email.lower().strip()
    try:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT email, name, password_hash FROM users WHERE email = %s",
                    (email,)
                )
                row = cur.fetchone()
                if not row:
                    return None

                stored_hash = row['password_hash']

                if is_legacy_sha256_hash(stored_hash):
                    if not verify_legacy_password(password, stored_hash):
                        return None
                    cur.execute(
                        "UPDATE users SET password_hash = %s WHERE email = %s",
                        (hash_password(password), email)
                    )
                    conn.commit()
                else:
                    if not verify_bcrypt_password(password, stored_hash):
                        return None

            return {'email': row['email'], 'name': row['name']}
    except Exception as e:
        log.error(f"فشل التحقق من المستخدم: {e}")
        return None


# ─── المحادثات ──────────────────────────────────────────────────
def save_message(chat_id, user_email, user_name, user_message, ai_response,
                  raw_ai, mode, image_url=None, file_name=None, uploaded_image_path=None,
                  reasoning=None):
    """يحفظ الرسالة ويرجّع id الصف الجديد (يُستخدم لاحقاً لإعادة توليد دقيقة)."""
    if not db_pool:
        return None
    try:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO conversations
                        (chat_id, user_email, user_name, user_message, ai_response, raw_ai, mode, image_url, file_name, uploaded_image_path, reasoning)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (chat_id, user_email, user_name, user_message, ai_response, raw_ai, mode,
                      image_url, file_name, uploaded_image_path, reasoning))
                new_id = cur.fetchone()['id']
            conn.commit()
        return new_id
    except Exception as e:
        log.error(f"خطأ في حفظ الرسالة: {e}")
        return None


def get_user_chats(user_email, limit=None):
    """
    limit=None (افتراضي): كل المحادثات بلا حد — نفس السلوك القديم
    بالضبط. المسار الفعلي (routes/api.py) يمرّر
    Config.CHAT_LIST_FETCH_LIMIT صراحة؛ القائمة الجانبية لا تحتاج عرض
    آلاف المحادثات المتراكمة عبر شهور دفعة واحدة.

    الفرز بالنتيجة النهائية صار داخل SQL (ORDER BY created_at DESC
    بعد الـsubquery) بدل فرز بايثون بعد الجلب — ضروري الآن لأنه لو
    فرزنا ببايثون بعد قصّ limit بـSQL بترتيب DISTINCT ON الداخلي
    (chat_id، مو created_at)، كنا سنقتطع أقدم المحادثات عشوائياً حسب
    ترتيب chat_id لا الأحدث فعلياً.
    """
    if not db_pool:
        return []
    try:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                query = """
                    SELECT chat_id, user_message, created_at FROM (
                        SELECT DISTINCT ON (chat_id) chat_id, user_message, created_at
                        FROM conversations
                        WHERE user_email = %s
                        ORDER BY chat_id, created_at ASC
                    ) sub
                    ORDER BY created_at DESC
                """
                params = [user_email]
                if limit:
                    query += " LIMIT %s"
                    params.append(limit)
                cur.execute(query, params)
                return cur.fetchall()
    except Exception as e:
        log.error(f"خطأ في جلب قائمة المحادثات: {e}")
        return []


def get_chat_messages(chat_id, user_email, limit=None):
    """
    limit=None (افتراضي): كل رسائل المحادثة بلا حد — هذا السلوك القديم
    بالضبط، وضروري يبقى الافتراضي: get_chat_history_for_context (بناء
    سياق كل رسالة، المسار الحرج) وapp/memory.py (فحص التلخيص) يمرّران
    Config.DB_CONTEXT_FETCH_LIMIT صراحة — سخي عمداً (أكبر بكثير من حد
    السياق الفعلي 12-20 رسالة) حتى ما تنكسر صحة حساب "الرسائل غير
    المُلخَّصة بعد" ولا معالجة before_id (إعادة التوليد) بأي محادثة
    واقعية. مسار العرض بـroutes/api.py يمرّر حداً مختلفاً وأسخى
    (CHAT_DISPLAY_FETCH_LIMIT) لأنه للعرض لا لبناء سياق الموديل.
    """
    if not db_pool:
        return []
    try:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                if limit:
                    # نجيب أحدث limit رسالة بترتيب عكسي (id DESC) ثم
                    # نعيد ترتيبها زمنياً (ASC) بالنتيجة النهائية —
                    # فرق عن ORDER BY ... ASC LIMIT العادي اللي كان
                    # سيرجع أقدم limit رسالة بدل أحدثها.
                    cur.execute("""
                        SELECT * FROM (
                            SELECT id, user_message, ai_response, raw_ai, image_url, file_name,
                                   uploaded_image_path, reasoning, created_at
                            FROM conversations
                            WHERE chat_id = %s AND user_email = %s
                            ORDER BY id DESC
                            LIMIT %s
                        ) sub ORDER BY id ASC
                    """, (chat_id, user_email, limit))
                else:
                    cur.execute("""
                        SELECT id, user_message, ai_response, raw_ai, image_url, file_name,
                               uploaded_image_path, reasoning, created_at
                        FROM conversations
                        WHERE chat_id = %s AND user_email = %s
                        ORDER BY created_at ASC
                    """, (chat_id, user_email))
                return cur.fetchall()
    except Exception as e:
        log.error(f"خطأ في جلب المحادثة: {e}")
        return []


def get_chat_history_for_context(chat_id, user_email, limit, before_id=None):
    """
    يبني سياق المحادثة من قاعدة البيانات مباشرة بدل الثقة بما يرسله
    المتصفح — يمنع حقن ردود ذكاء اصطناعي مزيّفة ضمن التاريخ (ثغرة
    تلاعب بالسياق/جيلبريك مستمر). عند تمرير before_id (حالة إعادة
    التوليد)، يُستثنى الصف المستهدف وما بعده من السياق.

    لو فيه ملخص محفوظ للمحادثة (ذاكرة طويلة المدى — انظر app/memory.py)،
    يُضاف كرسالة سياق إضافية قبل الرسائل الحديثة، والرسائل المشمولة
    بالفعل بالملخص تُستثنى من نافذة "الأحدث" لتفادي التكرار. هذا الجزء
    كله محاط بحماية إضافية: أي فشل بجلب/دمج الملخص يُتجاهَل بهدوء
    ويرجع السلوك للوضع القديم (بدون ملخص) — بناء السياق الأساسي يجب
    أن ينجح دائماً بغض النظر عن حالة ميزة الذاكرة الطويلة.
    """
    rows = get_chat_messages(chat_id, user_email, limit=Config.DB_CONTEXT_FETCH_LIMIT)
    if before_id is not None:
        rows = [r for r in rows if r['id'] < before_id]

    context = []
    try:
        summary = get_chat_summary(chat_id, user_email)
        if summary and summary.get('summary_text'):
            summarized_up_to = summary['summarized_up_to_id']
            # لو نبني سياقاً لإعادة توليد رسالة أقدم من (أو عند) حدود
            # الملخص، الملخص قد يحتوي معلومات "مستقبلية" بالنسبة لتلك
            # النقطة — نتجاهله بهالحالة النادرة بدل تسريب سياق خاطئ.
            if before_id is None or before_id > summarized_up_to:
                context.append({
                    "role": "system",
                    "content": f"ملخص لما سبق من هذه المحادثة (للسياق فقط):\n{summary['summary_text']}"
                })
                rows = [r for r in rows if r['id'] > summarized_up_to]
    except Exception as e:
        log.error(f"تعذر دمج ملخص المحادثة بالسياق (تم تجاهله بأمان): {e}")

    rows = rows[-limit:] if limit else rows
    for r in rows:
        u = (r.get('user_message') or '')[:2000]
        a = (r.get('raw_ai') or r.get('ai_response') or '')[:4000]
        if u and a:
            context.append({"role": "user", "content": u})
            context.append({"role": "assistant", "content": a})
    return context


# ─── ذاكرة طويلة المدى (ملخصات المحادثات) ───────────────────────
def get_chat_summary(chat_id, user_email):
    if not db_pool:
        return None
    try:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT summary_text, summarized_up_to_id FROM chat_summaries "
                    "WHERE chat_id = %s AND user_email = %s",
                    (chat_id, user_email)
                )
                return cur.fetchone()
    except Exception as e:
        log.error(f"خطأ في جلب ملخص المحادثة: {e}")
        return None


def save_chat_summary(chat_id, user_email, summary_text, summarized_up_to_id):
    if not db_pool:
        return False
    try:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO chat_summaries (chat_id, user_email, summary_text, summarized_up_to_id, updated_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (chat_id) DO UPDATE SET
                        summary_text = EXCLUDED.summary_text,
                        summarized_up_to_id = EXCLUDED.summarized_up_to_id,
                        updated_at = NOW()
                """, (chat_id, user_email, summary_text, summarized_up_to_id))
            conn.commit()
        return True
    except Exception as e:
        log.error(f"خطأ في حفظ ملخص المحادثة: {e}")
        return False


def delete_chat_from_db(chat_id, user_email):
    if not db_pool:
        return False
    try:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM conversations WHERE chat_id = %s AND user_email = %s",
                    (chat_id, user_email)
                )
                cur.execute(
                    "DELETE FROM chat_summaries WHERE chat_id = %s AND user_email = %s",
                    (chat_id, user_email)
                )
            conn.commit()
        return True
    except Exception as e:
        log.error(f"خطأ في حذف المحادثة: {e}")
        return False


# ─── لوحة التحكم: إحصائيات مجمّعة للقراءة فقط ───────────────────
def get_admin_stats():
    """
    يرجع قاموس إحصائيات للوحة التحكم. كل استعلام محمي بشكل مستقل —
    فشل استعلام واحد (مثلاً الحجم اليومي) لا يُسقط بقية الإحصائيات.
    عند أي فشل، القيمة الافتراضية 0/[] وليس استثناء — الصفحة يجب أن
    تُعرض دائماً، حتى ببيانات جزئية. لا يُقرأ أو يُعرض أي محتوى رسائل
    فعلي هنا عمداً — أرقام وأنماط فقط، لا خصوصية محادثات فردية.
    """
    stats = {
        'total_users': 0, 'total_chats': 0, 'total_messages': 0,
        'messages_by_mode': [], 'new_users_7d': 0, 'new_users_30d': 0,
        'messages_7d': 0, 'messages_30d': 0, 'daily_volume': [],
        'available': False,
    }
    if not db_pool:
        return stats

    try:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute("SELECT COUNT(*) AS c FROM users")
                    stats['total_users'] = cur.fetchone()['c']
                except Exception as e:
                    log.error(f"إحصائيات: فشل عدّ المستخدمين: {e}")

                try:
                    cur.execute("SELECT COUNT(*) AS total, COUNT(DISTINCT chat_id) AS chats FROM conversations")
                    row = cur.fetchone()
                    stats['total_messages'] = row['total']
                    stats['total_chats'] = row['chats']
                except Exception as e:
                    log.error(f"إحصائيات: فشل عدّ الرسائل/المحادثات: {e}")

                try:
                    cur.execute("SELECT mode, COUNT(*) AS cnt FROM conversations GROUP BY mode ORDER BY cnt DESC")
                    stats['messages_by_mode'] = cur.fetchall()
                except Exception as e:
                    log.error(f"إحصائيات: فشل توزيع الأوضاع: {e}")

                try:
                    cur.execute("SELECT COUNT(*) AS c FROM users WHERE created_at >= NOW() - INTERVAL '7 days'")
                    stats['new_users_7d'] = cur.fetchone()['c']
                    cur.execute("SELECT COUNT(*) AS c FROM users WHERE created_at >= NOW() - INTERVAL '30 days'")
                    stats['new_users_30d'] = cur.fetchone()['c']
                except Exception as e:
                    log.error(f"إحصائيات: فشل عدّ المستخدمين الجدد: {e}")

                try:
                    cur.execute("SELECT COUNT(*) AS c FROM conversations WHERE created_at >= NOW() - INTERVAL '7 days'")
                    stats['messages_7d'] = cur.fetchone()['c']
                    cur.execute("SELECT COUNT(*) AS c FROM conversations WHERE created_at >= NOW() - INTERVAL '30 days'")
                    stats['messages_30d'] = cur.fetchone()['c']
                except Exception as e:
                    log.error(f"إحصائيات: فشل عدّ الرسائل الأخيرة: {e}")

                try:
                    cur.execute("""
                        SELECT DATE(created_at) AS day, COUNT(*) AS cnt
                        FROM conversations
                        WHERE created_at >= NOW() - INTERVAL '14 days'
                        GROUP BY DATE(created_at)
                        ORDER BY day
                    """)
                    stats['daily_volume'] = cur.fetchall()
                except Exception as e:
                    log.error(f"إحصائيات: فشل الحجم اليومي: {e}")

        stats['available'] = True
        return stats
    except Exception as e:
        log.error(f"خطأ عام أثناء جلب إحصائيات لوحة التحكم: {e}")
        return stats


# ─── حصة الاستخدام اليومية ──────────────────────────────────────
def try_consume_daily_usage(user_email, daily_limit):
    """
    يحاول استهلاك رسالة واحدة من حصة اليوم لهذا المستخدم بخطوة ذرية
    واحدة (upsert مشروط بـ WHERE على الـ DO UPDATE) — لو وصل طلبان
    لنفس المستخدم بنفس اللحظة تماماً، قاعدة البيانات نفسها تفصل بينهما
    بالتتابع، بعكس قراءة العدّاد ثم زيادته بخطوتين منفصلتين ببايثون
    (قد تقرأ الطلبان نفس القيمة القديمة معاً وتزيدانها كل على حدة).

    يرجع True لو سُمح بالطلب (والعدّاد ازداد فعلاً)، False لو تجاوز
    الحصة (والعدّاد لم يتحرك — لا نعاقب على محاولات مرفوضة). عند أي
    خطأ بالعدّاد نفسه يرجع True عمداً: فشل آلية الحماية يجب ألا يمنع
    رسالة عائلية عادية.
    """
    if not db_pool:
        return True
    if daily_limit <= 0:
        # حالة حدّية اكتُشفت بالاختبار: WHERE بالـ SQL أدناه ينطبق فقط
        # على مسار التحديث (DO UPDATE)، فلو الحد صفر أو أقل، أول إدخال
        # (INSERT) بالسجل الجديد كان سينجح رغم ذلك بتجاوز الشرط كلياً.
        # نقطعها هنا بايثون قبل أي استعلام — أوضح وأصح من تعقيد الـ SQL.
        return False
    try:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO daily_usage (user_email, usage_date, message_count)
                    VALUES (%s, CURRENT_DATE, 1)
                    ON CONFLICT (user_email, usage_date) DO UPDATE
                        SET message_count = daily_usage.message_count + 1
                        WHERE daily_usage.message_count < %s
                    RETURNING message_count
                """, (user_email, daily_limit))
                row = cur.fetchone()
            conn.commit()
        return row is not None
    except Exception as e:
        log.error(f"خطأ بفحص/تحديث حصة الاستخدام اليومية لـ{user_email}: {e}")
        return True


def get_daily_usage_today():
    """يرجع استهلاك اليوم لكل مستخدم (للعرض بلوحة التحكم) — [] بهدوء عند أي خطأ."""
    if not db_pool:
        return []
    try:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT user_email, message_count
                    FROM daily_usage
                    WHERE usage_date = CURRENT_DATE
                    ORDER BY message_count DESC
                """)
                return cur.fetchall()
    except Exception as e:
        log.error(f"فشل جلب استهلاك اليوم: {e}")
        return []


# ─── استرداد كلمة المرور (بمساعدة الأدمن، بلا بريد إلكتروني) ──────
def create_password_reset(user_email):
    """
    يولّد رابط استرداد جديد — فقط لو المستخدم موجود فعلاً بالجدول
    (حتى ما نولّد رابطاً وهمياً لبريد غير مسجَّل). صالح 24 ساعة،
    استخدام واحد فقط (انظر consume_password_reset). يرجع (token, خطأ)
    — خطأ None يعني نجاح.
    """
    if not db_pool:
        return None, "قاعدة البيانات غير متاحة"
    user_email = (user_email or '').strip().lower()
    if not user_email:
        return None, "البريد الإلكتروني مطلوب"
    try:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM users WHERE email = %s", (user_email,))
                if not cur.fetchone():
                    return None, "لا يوجد مستخدم بهذا البريد"
                token = secrets.token_urlsafe(32)
                cur.execute("""
                    INSERT INTO password_resets (token, user_email, expires_at)
                    VALUES (%s, %s, NOW() + INTERVAL '24 hours')
                """, (token, user_email))
            conn.commit()
        return token, None
    except Exception as e:
        log.error(f"فشل توليد رابط استرداد لـ{user_email}: {e}")
        return None, "حدث خطأ غير متوقع"


def get_valid_password_reset(token):
    """يرجع صف password_resets لو التوكن صالح فعلاً (موجود، غير مستخدَم، غير منتهي)، وإلا None."""
    if not db_pool or not token:
        return None
    try:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT token, user_email FROM password_resets
                    WHERE token = %s AND used_at IS NULL AND expires_at > NOW()
                """, (token,))
                return cur.fetchone()
    except Exception as e:
        log.error(f"خطأ بفحص رابط استرداد: {e}")
        return None


def consume_password_reset(token, new_password):
    """
    يحدّث كلمة مرور صاحب التوكن ويعلّمه "مستخدَم" بخطوة ذرية واحدة —
    الشرط (غير مستخدَم وغير منتهٍ) داخل نفس UPDATE يمنع استخدام نفس
    الرابط مرتين حتى لو وصل طلبان بنفس اللحظة تماماً (بالضبط نفس فكرة
    try_consume_daily_usage أعلاه). يرجع True/False فقط — الرسائل
    المفصَّلة مسؤولية الطبقة اللي فوق.
    """
    if not db_pool:
        return False
    try:
        with db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE password_resets SET used_at = NOW()
                    WHERE token = %s AND used_at IS NULL AND expires_at > NOW()
                    RETURNING user_email
                """, (token,))
                row = cur.fetchone()
                if not row:
                    return False
                cur.execute(
                    "UPDATE users SET password_hash = %s WHERE email = %s",
                    (hash_password(new_password), row['user_email'])
                )
            conn.commit()
        return True
    except Exception as e:
        log.error(f"فشل تنفيذ استرداد كلمة المرور: {e}")
        return False
