import os
import logging
from flask import Flask
from flask_cors import CORS
from dotenv import load_dotenv
from datetime import timedelta

# استورد كل الـ Blueprints
from routes.auth import auth_bp
from routes.chat import chat_bp
from routes.pages import pages_bp
from database.db_manager import init_db_pool, close_db_pool

# شغل الـ .env
load_dotenv()

# ─── إعداد الـ Logging ──────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ─── إنشاء التطبيق ──────────────────────────────
app = Flask(__name__)

# ─── الإعدادات ──────────────────────────────────
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 # 10MB للملفات

# CORS للـ API بس
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ─── سجل الـ Blueprints ─────────────────────────
app.register_blueprint(auth_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(pages_bp)

# ─── تشغيل/إيقاف Pool قاعدة البيانات ────────────
@app.before_request
def before_request():
    init_db_pool()

@app.teardown_appcontext
def teardown(exception=None):
    close_db_pool()

# ─── شغل السيرفر ────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting Wadi AI on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
