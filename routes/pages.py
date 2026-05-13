import logging
from flask import Blueprint, render_template, session, redirect, url_for, request
from utils.sharing import get_shared_chat

logger = logging.getLogger(__name__)
pages_bp = Blueprint('pages', __name__)

@pages_bp.route('/')
def home():
    """الصفحة الرئيسية - لازم تسجيل دخول"""
    if 'email' not in session:
        return redirect(url_for('auth.login'))
    return render_template('index.html', user_name=session.get('name'))

@pages_bp.route('/share/<share_token>')
def view_shared_chat(share_token):
    """صفحة عرض المحادثة المشاركة"""
    chat_data = get_shared_chat(share_token)

    if not chat_data:
        return render_template('404.html', message="رابط المشاركة غلط أو انحذف"), 404

    return render_template('shared.html', chat=chat_data)

@pages_bp.route('/health')
def health():
    """Render يستخدمها عشان يعرف السيرفر شغال"""
    return {"status": "ok", "service": "Wadi AI"}, 200
