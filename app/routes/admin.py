"""
لوحة تحكم بسيطة للقراءة فقط — أرقام مجمّعة (مستخدمين، رسائل،
توزيع أوضاع)، بدون أي محتوى محادثات فعلي.

⚠️ قاعدة أمان غير قابلة للتفاوض: الوصول افتراضياً "ممنوع" لكل شخص،
حتى لو معه جلسة دخول صالحة، إلا لو بريده الإلكتروني مضاف صراحة بمتغير
البيئة ADMIN_EMAILS. عدم ضبط هذا المتغير = لا أحد يدخل إطلاقاً (fail
closed)، وليس العكس.
"""
from functools import wraps

from flask import Blueprint, render_template, session, abort, request, redirect, url_for, flash

from app.config import Config
from app.extensions import limiter, log
from app.db import get_admin_stats, get_daily_usage_today, create_password_reset
from app.rag import add_document, list_documents, delete_document

bp = Blueprint('admin', __name__, url_prefix='/admin')


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = session.get('user', {})
        email = (user.get('email') or '').strip().lower()
        # 404 لا 403 عمداً — لا نؤكد حتى وجود لوحة تحكم لغير المصرّح لهم
        if not email or not Config.ADMIN_EMAILS or email not in Config.ADMIN_EMAILS:
            abort(404)
        return f(*args, **kwargs)
    return wrapper


@bp.route('')
@admin_required
@limiter.limit(Config.ADMIN_RATE_LIMIT)
def dashboard():
    # get_admin_stats() تحمي كل استعلام على حدة أصلاً، لكن هذا حارس
    # أخير: أي انهيار غير متوقع بهذه الصفحة يعرض حالة فارغة واضحة
    # بدل صفحة 500 خام — نفس فلسفة "افشل بأمان ووضوح" المتّبعة بكل
    # مكان آخر بالمشروع.
    try:
        stats = get_admin_stats()
    except Exception as e:
        from app.extensions import log
        log.error(f"خطأ غير متوقع بلوحة التحكم (تم احتواؤه): {e}")
        stats = {
            'total_users': 0, 'total_chats': 0, 'total_messages': 0,
            'messages_by_mode': [], 'new_users_7d': 0, 'new_users_30d': 0,
            'messages_7d': 0, 'messages_30d': 0, 'daily_volume': [],
            'available': False,
        }

    # نفس فلسفة get_admin_stats: فشل جلب الوثائق لا يجب أن يمنع بقية
    # الصفحة من الظهور — قائمة فارغة أسوأ حالة، ليست صفحة معطوبة.
    try:
        documents = list_documents()
    except Exception as e:
        log.error(f"تعذر جلب قائمة وثائق الذاكرة العائلية (تم احتواؤه): {e}")
        documents = []

    try:
        today_usage = get_daily_usage_today()
    except Exception as e:
        log.error(f"تعذر جلب استهلاك اليوم (تم احتواؤه): {e}")
        today_usage = []

    return render_template('admin.html', stats=stats, documents=documents,
                            rag_enabled=Config.ENABLE_RAG,
                            today_usage=today_usage,
                            daily_limit=Config.DAILY_MESSAGE_LIMIT,
                            quota_enabled=Config.ENABLE_DAILY_QUOTA)


@bp.route('/memory/add', methods=['POST'])
@admin_required
@limiter.limit(Config.ADMIN_RATE_LIMIT)
def memory_add():
    """
    يضيف وثيقة جديدة للذاكرة العائلية المشتركة (تُسترجَع لكل أفراد
    العائلة بغض النظر عن مين رفعها — هي معرفة مشتركة، ليست خاصة
    بالمُضيف). محصورة بالأدمن عمداً حتى الآن لتفادي تلوّث الذاكرة
    المشتركة بإضافات غير مقصودة.
    """
    title = (request.form.get('title') or '').strip()
    content = (request.form.get('content') or '').strip()
    if not title or not content:
        flash('⚠️ العنوان والمحتوى مطلوبان', 'error')
        return redirect(url_for('admin.dashboard'))

    user_email = (session.get('user', {}).get('email') or '').strip().lower()
    count, err = add_document(user_email, title, content)
    if err:
        flash(f'⚠️ تعذرت إضافة الوثيقة: {err}', 'error')
    else:
        flash(f'✅ تمت إضافة "{title}" ({count} مقطع)', 'success')
    return redirect(url_for('admin.dashboard'))


@bp.route('/memory/delete', methods=['POST'])
@admin_required
@limiter.limit(Config.ADMIN_RATE_LIMIT)
def memory_delete():
    title = (request.form.get('title') or '').strip()
    if not title:
        flash('⚠️ لم يُحدَّد عنوان للحذف', 'error')
        return redirect(url_for('admin.dashboard'))

    ok = delete_document(title)
    flash(f'✅ تم حذف "{title}"' if ok else f'⚠️ تعذر حذف "{title}"',
          'success' if ok else 'error')
    return redirect(url_for('admin.dashboard'))


@bp.route('/reset-password', methods=['POST'])
@admin_required
@limiter.limit(Config.ADMIN_RATE_LIMIT)
def generate_reset_link():
    """
    يولّد رابط استرداد لعضو عائلة موجود فعلاً، وتعرضه الصفحة نصاً
    قابلاً للنسخ (flash) — الأدمن يرسله يدوياً (واتساب، شخصياً...) لأنه
    لا بريد إلكتروني بالمشروع حالياً. صالح 24 ساعة، استخدام واحد فقط.
    """
    email = (request.form.get('email') or '').strip().lower()
    if not email:
        flash('⚠️ أدخل البريد الإلكتروني', 'error')
        return redirect(url_for('admin.dashboard'))

    token, err = create_password_reset(email)
    if err:
        flash(f'⚠️ {err}', 'error')
    else:
        reset_url = url_for('auth.reset_password', token=token, _external=True)
        flash(f'✅ رابط استرداد لـ{email} (صالح 24 ساعة، استخدام واحد فقط):|{reset_url}', 'link')
    return redirect(url_for('admin.dashboard'))
