from flask import Blueprint, render_template, session

bp = Blueprint('pages', __name__)


@bp.route("/ping")
def ping():
    # نقطة فحص خفيفة لخدمات keep-alive (GitHub Actions/UptimeRobot/...):
    # لا تلمس الجلسة ولا قاعدة البيانات ولا أي مزوّد AI — همّها الوحيد
    # إبقاء الخدمة صاحية على Render Free (spin-down بعد 15د خمول)
    # بأقل كلفة ممكنة على كل نبضة.
    return "ok", 200


@bp.route("/")
def home():
    user = session.get('user', {})
    user_name = user.get('name', 'مستخدم')
    user_initial = user_name[0].upper() if user_name else 'U'
    return render_template('index.html', user_name=user_name, user_initial=user_initial)
