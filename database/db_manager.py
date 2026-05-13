import os
import logging
import psycopg
from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)
db_pool = None

def init_db():
    """ينشئ جدول المستخدمين + المحادثات + المشاركة"""
    global db_pool
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set")
        return

    try:
        db_pool = ConnectionPool(db_url, min_size=1, max_size=10, timeout=30)
        
        with db_pool.connection() as conn:
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
                conn.commit()
        logger.info("✅ Database initialized with pool")
    except Exception as e:
        logger.error(f"DB init error: {e}")

def get_db():
    """يرجع connection من الـ Pool"""
    if not db_pool:
        raise Exception("Database not initialized")
    return db_pool.connection()
