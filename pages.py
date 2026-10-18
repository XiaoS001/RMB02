"""页面路由 —— 前端必须有页面（审查坑 ③：不是只有 API）。

页面：/login 登录 · /dashboard 首页任务列表 · /annotate 标注页 · /report 报告页
🔴 服务端权限门控：未登录 401 跳登录；无 view_all 权限 403。
"""
from flask import Blueprint, redirect, render_template, url_for

from auth import current_user, login_required


def register_pages_blueprint():
    bp = Blueprint('pages', __name__)

    @bp.context_processor
    def inject_user():
        return {'current_user': current_user()}

    @bp.get('/')
    def index():
        if current_user() is None:
            return redirect(url_for('pages.login_page'))
        return redirect(url_for('pages.dashboard'))

    @bp.get('/login')
    def login_page():
        if current_user() is not None:
            return redirect(url_for('pages.dashboard'))
        return render_template('login.html')

    @bp.get('/dashboard')
    @login_required('view_all')
    def dashboard():
        from dashboard import task_pass_rate
        from models import EvalTask

        tasks = EvalTask.query.order_by(EvalTask.id.desc()).all()
        return render_template('dashboard.html', tasks=[
            {'id': t.id, 'name': t.name, 'status': t.status,
             'pass_rate': task_pass_rate(t.id)}
            for t in tasks
        ])

    @bp.get('/annotate')
    @login_required('view_all')
    def annotate():
        from annotation import agreement_rate

        return render_template('annotate.html', agreement=agreement_rate())

    @bp.get('/report/<int:task_id>')
    @login_required('view_all')
    def report(task_id):
        from annotation import agreement_rate
        from dashboard import annotator_workload, dimension_trend

        return render_template(
            'report.html', task_id=task_id,
            dims=dimension_trend(task_id),
            agreement=agreement_rate(),
            workload=annotator_workload(),
        )

    return bp
