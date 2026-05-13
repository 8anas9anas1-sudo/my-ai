import os
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

@contextmanager
def get_db():
    """يرجع connection بقاعدة البيانات - بدون Pool عشان Render Free"""
    conn = None
    try:
        conn = psycopg2.connect(
            os.environ.get('DATABASE_URL'),
            cursor_factory=RealDictCursor
        )
        yield conn
        conn.commit()
    except Exception as e:
        logger.error(f"Database error: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

def init_db():
    """ينشئ جدول المستخدمين + المحادثات + المشاركة"""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set")
        return

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # جدول المستخدمين
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        name TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT NOW()
                    );
                """)
                
                # جدول المحادثات
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS conversations (
                        id SERIAL PRIMARY KEY,
                        chat_id TEXT NOT NULL,
                        user_email TEXT NOT NULL,
                        user_name TEXT NOT NULL,
                        user_message TEXT NOT NULL,
                        ai_response TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT NOW()
                    );
                    CREATE INDEX IF NOT EXISTS idx_chat_id ON conversations(chat_id);
                    CREATE INDEX IF NOT EXISTS idx_user_email ON conversations(user_email);
                """)
                
                # جدول روابط المشاركة
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS shared_chats (
                        id SERIAL PRIMARY KEY,
                        share_token TEXT UNIQUE NOT NULL,
                        chat_id TEXT NOT NULL,
                        user_email TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT NOW()
                    );
                    CREATE INDEX IF NOT EXISTS idx_share_token ON shared_chats(share_token);
                """)
        logger.info("✅ Database initialized")
    except Exception as e:
        logger.error(f"DB init error: {e}")
        raise
