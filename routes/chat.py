import logging
import uuid
from flask import Blueprint, request, jsonify, Response, session, stream_with_context
from security.protector import require_not_limited, is_prompt_injection, sanitize_input
from services.ai_service import stream_ai_response
from services.file_service import extract_text_from_file
from database.db_manager import get_db

logger = logging.getLogger(__name__)
chat_bp = Blueprint('chat', __name__)

def get_conversation_history(chat_id, limit=6):
    """يجيب آخر 6 رسايل من المحادثة عشان السياق"""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT user_message, ai_response
                    FROM conversations
                    WHERE chat_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (chat_id, limit))

                history = []
                for row in cur.fetchall():
                    history.append({"role": "user", "content": row[0]})
                    history.append({"role": "assistant", "content": row[1]})

                return list(reversed(history)) # رجّعهم بالترتيب الصح
    except Exception as e:
        logger.error(f"History error: {e}")
        return []

@chat_bp.route('/api/chat', methods=['POST'])
@require_not_limited # Rate limit من protector.py
def chat():
    # 1. تأكد إن المستخدم مسجل
    if 'email' not in session:
        return jsonify({"error": "سجل دخولك الأول"}), 401

    # 2. خذ البيانات
    user_message = sanitize_input(request.form.get('message', ''))
    mode = sanitize_input(request.form.get('mode', 'fast'))
    chat_id = sanitize_input(request.form.get('chat_id', ''))

    # لو مافيش chat_id، ولّد واحد جديد
    if not chat_id:
        chat_id = str(uuid.uuid4())

    file = request.files.get('file')
    file_content = ""

    # 3. عالج الملف لو فيه
    if file and file.filename:
        file_content = extract_text_from_file(file)
        if file_content.startswith("الملف") or file_content.startswith("صار خطأ"):
            # رسالة خطأ من file_service
            return jsonify({"error": file_content}), 400

    # 4. ادمج الرسالة مع محتوى الملف
    full_message = user_message
    if file_content and not file_content.startswith("[صورة مرفقة]"):
        full_message = f"{user_message}\n\n--- محتوى الملف ---\n{file_content}"

    # 5. حماية من Prompt Injection
    if is_prompt_injection(full_message):
        return jsonify({"error": "رسالتك فيها محتوى ممنوع"}), 400

    if not full_message.strip():
        return jsonify({"error": "اكتب رسالة أو ارفع ملف"}), 400

    # 6. جيب تاريخ المحادثة
    history = get_conversation_history(chat_id)

    # 7. دالة الـ Streaming
    def generate():
        full_ai_response = ""
        try:
            # ابدأ الـ stream
            for chunk in stream_ai_response(full_message, mode, history):
                full_ai_response += chunk
                yield f"data: {chunk}\n\n"

            # بعد ما يكمل، خزّن في القاعدة
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO conversations
                        (chat_id, user_email, user_name, user_message, ai_response, mode)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (chat_id, session['email'], session['name'],
                          user_message, full_ai_response, mode))
                    conn.commit()

            # ارسل chat_id في النهاية عشان الواجهة تحفظه
            yield f"data: [CHAT_ID]{chat_id}[/CHAT_ID]\n\n"
            yield f"data: [DONE]\n\n"

        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield f"data: صار خطأ في السيرفر\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@chat_bp.route('/api/chats', methods=['GET'])
def get_user_chats():
    """يرجع قائمة محادثات المستخدم للـ Sidebar"""
    if 'email' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (chat_id)
                        chat_id, user_message, created_at
                    FROM conversations
                    WHERE user_email = %s
                    ORDER BY chat_id, created_at DESC
                    LIMIT 50
                """, (session['email'],))

                chats = []
                for row in cur.fetchall():
                    chats.append({
                        "chat_id": row[0],
                        "title": row[1][:40] + "..." if len(row[1]) > 40 else row[1],
                        "created_at": row[2].isoformat()
                    })

                return jsonify(chats)
    except Exception as e:
        logger.error(f"Get chats error: {e}")
        return jsonify({"error": "Server error"}), 500
