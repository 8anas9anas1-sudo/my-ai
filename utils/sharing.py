import secrets
import logging
from database.db_manager import get_db

logger = logging.getLogger(__name__)

def create_share_link(chat_id, user_email):
    """
    يولد رابط مشاركة عشوائي لمحادثة معينة.
    الرابط ما ينفعش تخمينه أبداً.
    """
    # 1. ولّد توكن عشوائي قوي 32 حرف
    share_token = secrets.token_urlsafe(24) # يطلع زي: xK9mN2pQ8vR5tY7wE3uI6oP1sA

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # 2. احفظ التوكن في قاعدة البيانات
                cur.execute("""
                    INSERT INTO shared_chats (share_token, chat_id, user_email)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (share_token) DO NOTHING
                    RETURNING share_token;
                """, (share_token, chat_id, user_email))

                result = cur.fetchone()
                conn.commit()

                if result:
                    logger.info(f"Share link created for chat {chat_id}")
                    return share_token
                else:
                    # نادر جداً يصير، معناها التوكن تصادف مع واحد موجود
                    return create_share_link(chat_id, user_email) # عاود ولّد

    except Exception as e:
        logger.error(f"Share link error: {e}")
        return None

def get_shared_chat(share_token):
    """
    يجيب المحادثة عن طريق رابط المشاركة.
    لو الرابط غلط يرجع None.
    """
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # 1. جيب chat_id من التوكن
                cur.execute("""
                    SELECT chat_id FROM shared_chats
                    WHERE share_token = %s
                """, (share_token,))
                row = cur.fetchone()

                if not row:
                    return None

                chat_id = row[0]

                # 2. جيب كل رسايل المحادثة هذي
                cur.execute("""
                    SELECT user_name, user_message, ai_response, created_at
                    FROM conversations
                    WHERE chat_id = %s
                    ORDER BY created_at ASC
                """, (chat_id,))

                messages = []
                for row in cur.fetchall():
                    messages.append({
                        "user_name": row[0],
                        "user_message": row[1],
                        "ai_response": row[2],
                        "created_at": row[3].isoformat()
                    })

                return {
                    "chat_id": chat_id,
                    "messages": messages
                }

    except Exception as e:
        logger.error(f"Get shared chat error: {e}")
        return None
