from flask import Blueprint, render_template, session

bp = Blueprint('pages', __name__)


@bp.route("/")
def home():
    user = session.get('user', {})
    user_name = user.get('name', 'مستخدم')
    user_initial = user_name[0].upper() if user_name else 'U'
    return render_template('index.html', user_name=user_name, user_initial=user_initial)
