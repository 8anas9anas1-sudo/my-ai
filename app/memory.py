"""
ذاكرة طويلة المدى: تلخيص تلقائي للمحادثات الطويلة بدل قصّ كل ما هو
أقدم من نافذة السياق عند الوصول لحدها (كان السلوك القديم: أي رسالة
أقدم من آخر 12-20 رسالة تُفقد كلياً بدون أي أثر).

⚠️ قاعدة تصميم غير قابلة للتنازل: هذا الملف لا يجب أبداً أن يؤثر على
رد المستخدم الحالي — لا تأخير، ولا فشل، ولا استثناء ينتشر لأي مكان
آخر. لذلك:
  1. يعمل بخيط خلفي منفصل تماماً، يُطلق بعد حفظ الرد الحالي بنجاح —
     عبر ThreadPoolExecutor محدود (2 خيط أقصى)، لا threading.Thread
     خام بلا حد. السبب: بركة اتصالات قاعدة البيانات صغيرة (10 فقط —
     انظر DB_POOL_MAX_SIZE بconfig.py)، وكل خيط تلخيص يحجز اتصالاً
     منها طول عمره. لو صار تلخيص عدة محادثات بنفس اللحظة (عدة أفراد
     عائلة نشيطين معاً) بخيوط خام بلا حد، ممكن نظرياً تستهلك كل
     البركة وتترك صفراً لطلبات المستخدمين الفعلية. الحد هنا (2) يضمن
     8 اتصالات على الأقل تبقى متاحة دائماً للمسار الحرج.
  2. كل المنطق الداخلي محاط بـ try/except شامل واحد على مستوى المهمة
     بالكامل — أي خطأ غير متوقع يُسجَّل فقط ويموت هناك.
  3. لا كتابة لأي حالة يعتمد عليها الطلب الحالي (session، إلخ) —
     المهمة تقرأ من قاعدة البيانات وتكتب لها فقط، بمعزل كامل عن دورة
     الطلب/الرد التي أطلقتها.
"""
from concurrent.futures import ThreadPoolExecutor

from app.extensions import log
from app.config import Config
from app.db import get_chat_messages, get_chat_summary, save_chat_summary
from app.ai_service import summarize_conversation

# executor واحد مشترك لعمر التطبيق كله (لا يُنشأ من جديد بكل استدعاء) —
# thread_name_prefix يسهّل تمييزها بسجلات/تتبّع العمليات وقت التشخيص.
_summary_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="summarizer")


def maybe_summarize_async(chat_id, user_email):
    """
    نقطة الدخول الوحيدة من routes/api.py. تُطلق الفحص والتلخيص (إن
    احتاج الأمر) بخيط خلفي محدود وترجع فوراً — لا تنتظر أي نتيجة، ولا
    يمكن أن ترفع استثناءً يوقف معالجة الطلب الحالي. لو الـ2 خيط
    مشغولين فعلاً بمهام سابقة، هذي المهمة تنتظر بدَور (queue) داخلي
    للـexecutor نفسه بدل ما تُفتح كخيط إضافي غير محسوب — هذا بالضبط
    الفرق الجوهري عن threading.Thread الخام.
    """
    try:
        _summary_executor.submit(_summarize_if_needed, chat_id, user_email)
    except Exception as e:
        # حتى فشل جدولة المهمة نفسها (نادر جداً) لا يجب أن يكسر شيئاً
        log.error(f"تعذر جدولة تلخيص المحادثة {chat_id}: {e}")


def _summarize_if_needed(chat_id, user_email):
    """يعمل بالكامل داخل خيط منفصل. كل سطر هنا محمي بالـ try/except
    الخارجي — لا نثق حتى بمنطقنا الخاص بنسبة 100%."""
    try:
        if not chat_id or not user_email or user_email == 'anonymous':
            return

        # نفس الحد السخي المستخدم بـget_chat_history_for_context بالضبط
        # (Config.DB_CONTEXT_FETCH_LIMIT) — أكبر بكثير من
        # SUMMARY_TRIGGER_THRESHOLD/SUMMARY_UPDATE_INTERVAL، فحساب
        # "الرسائل غير المُلخَّصة بعد" يبقى صحيحاً بأي محادثة واقعية،
        # مع تفادي جلب آلاف الصفوف لمحادثة تراكمت لها رسائل كتير جداً.
        rows = get_chat_messages(chat_id, user_email, limit=Config.DB_CONTEXT_FETCH_LIMIT)
        if not rows:
            return

        existing = get_chat_summary(chat_id, user_email)
        already_summarized_up_to = existing['summarized_up_to_id'] if existing else 0

        new_rows = [r for r in rows if r['id'] > already_summarized_up_to]
        total = len(rows)

        should_trigger = (
            (existing is None and total >= Config.SUMMARY_TRIGGER_THRESHOLD) or
            (existing is not None and len(new_rows) >= Config.SUMMARY_UPDATE_INTERVAL)
        )
        if not should_trigger:
            return

        # نترك آخر رسالتين خارج التلخيص دائماً — تبقيان "طازجتين" ضمن
        # نافذة السياق العادية، ونلخّص القديم فقط.
        rows_to_summarize = new_rows[:-2] if len(new_rows) > 2 else new_rows
        if not rows_to_summarize:
            return

        conversation_pairs = []
        for r in rows_to_summarize:
            u = (r.get('user_message') or '').strip()
            a = (r.get('raw_ai') or r.get('ai_response') or '').strip()
            if u and a:
                conversation_pairs.append({"user": u, "ai": a})
        if not conversation_pairs:
            return

        new_summary_text = summarize_conversation(
            existing_summary=existing['summary_text'] if existing else None,
            new_messages=conversation_pairs,
        )

        # فحص أمان جوهري: لا نحدّث الملخص المخزَّن إلا لو النتيجة
        # الجديدة نص معقول فعلاً — نمنع استبدال ملخص جيد سابق بنتيجة
        # فارغة أو معطوبة بسبب خطأ أو رد غير متوقع من الموديل. لو
        # رفضناه هنا، نحاول مجدداً تلقائياً بالمرة القادمة (لم نحرّك
        # summarized_up_to_id، فالرسائل نفسها ستُعاد معالجتها).
        if not new_summary_text or len(new_summary_text.strip()) < Config.SUMMARY_MIN_VALID_LENGTH:
            log.warning(f"تلخيص المحادثة {chat_id} رجّع نتيجة غير صالحة — تم تجاهله، سيُعاد المحاولة لاحقاً")
            return

        summarized_up_to_id = rows_to_summarize[-1]['id']
        ok = save_chat_summary(chat_id, user_email, new_summary_text.strip(), summarized_up_to_id)
        if ok:
            log.info(f"تم تحديث ملخص المحادثة {chat_id} (حتى الرسالة #{summarized_up_to_id})")

    except Exception as e:
        # الحارس الأخير: أي استثناء غير متوقع بأي سطر أعلاه يُسجَّل
        # فقط. هذا خيط خلفي معزول تماماً عن رد المستخدم الحالي.
        log.error(f"خطأ أثناء تلخيص المحادثة {chat_id} (تم تجاهله بأمان، لا يؤثر على أي رد): {e}")
