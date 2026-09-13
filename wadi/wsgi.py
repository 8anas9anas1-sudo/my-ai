"""
نقطة الدخول لـ gunicorn: gunicorn wsgi:app
"""
# لازم يُستدعى قبل "from app import create_app" — استيراد app يجرّ معه
# استيراد Config (config.py) اللي يقرأ os.environ.get(...) وقت تعريف
# الكلاس نفسه (وقت الاستيراد)، لا وقت التشغيل. لو load_dotenv() جاء
# بعد الاستيراد، القيم بـ.env ما توصل لـConfig إطلاقاً. آمن تماماً على
# Render نفسه (لا ملف .env هناك أصلاً) — load_dotenv() بلا ملف موجود
# لا تفعل شيئاً ولا ترفع أي استثناء، والقيم المضبوطة فعلياً كمتغيرات
# بيئة بلوحة Render لا تُستبدَل (override=False افتراضياً).
from dotenv import load_dotenv
load_dotenv()

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
