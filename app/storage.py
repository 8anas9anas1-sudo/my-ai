"""
تخزين دائم للصور عبر Supabase Storage — عبر REST API مباشرة بـ
requests (بدون SDK إضافي)، حتى لا نضيف اعتماد ثقيل لميزة اختيارية.

نوعان:
- upload_image(): صورة مرفوعة من المستخدم لتحليلها — باكت خاص،
  نخزّن المسار فقط بقاعدة البيانات ونولّد رابطاً موقّعاً مؤقتاً عند
  كل قراءة (get_signed_url) بدل تخزين رابط ثابت سينتهي.
- persist_generated_image(): صورة ولّدها الذكاء الاصطناعي — نحمّلها
  من pollinations.ai ونعيد رفعها لباكت عام عندنا، حتى لا تعتمد
  المحادثات القديمة على استقرار خدمة خارجية على المدى الطويل.

كل الدوال هنا "أفضل ما يمكن" (best-effort): لو Supabase غير مضبوط أو
فشل الاتصال، ترجع None بهدوء والتطبيق يكمل بالسلوك القديم (بدون تخزين
دائم) — التخزين تحسين اختياري، مو اعتماد أساسي متل قاعدة البيانات.
"""
import uuid

import requests

from app.config import Config
from app.extensions import log


def storage_configured():
    return bool(Config.SUPABASE_URL and Config.SUPABASE_SERVICE_KEY)


def _auth_headers(content_type=None):
    headers = {"Authorization": f"Bearer {Config.SUPABASE_SERVICE_KEY}"}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _safe_ext(content_type):
    if not content_type or '/' not in content_type:
        return 'png'
    ext = content_type.split('/')[-1].split('+')[0].split(';')[0].strip()
    return ext[:5] if ext.isalnum() else 'png'


# ─── صور مرفوعة من المستخدم (باكت خاص) ─────────────────────────
def upload_image(user_email, chat_id, image_bytes, content_type):
    """يرفع صورة لباكت خاص ويرجّع المسار الداخلي (وليس رابطاً مباشراً
    — الرابط المباشر لباكت خاص يحتاج توقيعاً، وينتهي صلاحيته)."""
    if not storage_configured():
        return None
    path = f"{user_email}/{chat_id}/{uuid.uuid4().hex}.{_safe_ext(content_type)}"
    try:
        resp = requests.post(
            f"{Config.SUPABASE_URL}/storage/v1/object/{Config.SUPABASE_UPLOADS_BUCKET}/{path}",
            headers={**_auth_headers(content_type or "application/octet-stream"), "x-upsert": "true"},
            data=image_bytes, timeout=20
        )
        if resp.ok:
            return path
        log.error(f"فشل رفع الصورة لـ Supabase Storage: {resp.status_code} {resp.text[:200]}")
        return None
    except Exception as e:
        log.error(f"خطأ أثناء رفع الصورة المرفوعة: {e}")
        return None


def get_signed_url(path, expires_in=None):
    """يولّد رابط موقّع مؤقت لصورة بباكت خاص. لا نخزّن هذا الرابط بقاعدة
    البيانات أبداً — نولّده من جديد بكل مرة تُقرأ فيها المحادثة."""
    if not path or not storage_configured():
        return None
    try:
        resp = requests.post(
            f"{Config.SUPABASE_URL}/storage/v1/object/sign/{Config.SUPABASE_UPLOADS_BUCKET}/{path}",
            headers=_auth_headers("application/json"),
            json={"expiresIn": expires_in or Config.SIGNED_URL_EXPIRES_IN},
            timeout=10
        )
        if resp.ok:
            signed_path = resp.json().get("signedURL")
            if signed_path:
                return f"{Config.SUPABASE_URL}/storage/v1{signed_path}"
        return None
    except Exception as e:
        log.error(f"خطأ أثناء توليد رابط موقّع: {e}")
        return None


# ─── صور مولّدة بالذكاء الاصطناعي (باكت عام) ────────────────────
def persist_generated_image(source_url, user_email, chat_id):
    """يحمّل الصورة المولّدة من pollinations.ai ويعيد رفعها لباكت عام
    عندنا. لو فشل أي جزء (تنزيل أو رفع)، يرجع None ليُستخدم الرابط
    الأصلي كـ fallback بدل كسر الرد بالكامل."""
    if not storage_configured():
        return None
    try:
        img_resp = requests.get(source_url, timeout=30)
        if not img_resp.ok or not img_resp.content:
            return None
        content_type = img_resp.headers.get('Content-Type', 'image/png')
        path = f"{user_email}/{chat_id}/{uuid.uuid4().hex}.{_safe_ext(content_type)}"
        resp = requests.post(
            f"{Config.SUPABASE_URL}/storage/v1/object/{Config.SUPABASE_GENERATED_BUCKET}/{path}",
            headers={**_auth_headers(content_type), "x-upsert": "true"},
            data=img_resp.content, timeout=20
        )
        if resp.ok:
            return f"{Config.SUPABASE_URL}/storage/v1/object/public/{Config.SUPABASE_GENERATED_BUCKET}/{path}"
        log.error(f"فشل تخزين الصورة المولّدة: {resp.status_code} {resp.text[:200]}")
        return None
    except Exception as e:
        log.error(f"خطأ أثناء تخزين الصورة المولّدة: {e}")
        return None
