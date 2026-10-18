"""认证与授权 —— 四档角色（任务单第三节）。

角色：platform_admin / evaluator / annotator / client_viewer
密码只存 hash（PBKDF2-SHA256，同 RMB01 做法）。
"""
import functools
import hashlib
import hmac
import os

from flask import jsonify, session

import config
from models import ALL_ROLES, User, db


_PBKDF2_ROUNDS = 120_000
_HASH_PREFIX = 'pbkdf2_sha256'


def hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(16).hex()
    salt_bytes = salt.encode('utf-8')
    digest = hashlib.pbkdf2_hmac(
        'sha256', password.encode('utf-8'), salt_bytes, _PBKDF2_ROUNDS,
    )
    return f'{_HASH_PREFIX}${_PBKDF2_ROUNDS}${salt_bytes.hex()}${digest.hex()}'


def verify_password(password, stored_hash):
    try:
        algorithm, rounds, salt_hex, digest_hex = stored_hash.split('$')
        if algorithm != _HASH_PREFIX:
            return False
        expected = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode('utf-8'),
            bytes.fromhex(salt_hex),
            int(rounds),
        ).hex()
        return hmac.compare_digest(expected, digest_hex)
    except (ValueError, AttributeError, TypeError):
        return False


def create_user(username, password, role):
    if not username or not password:
        return None, '用户名与密码不能为空'
    if len(username) < 3:
        return None, '用户名至少 3 个字符'
    if len(password) < 6:
        return None, '密码至少 6 个字符'
    if role not in ALL_ROLES:
        return None, f'角色必须是 {"/".join(ALL_ROLES)} 之一'
    if User.query.filter_by(username=username).first() is not None:
        return None, '用户名已存在'

    user = User(
        username=username,
        password_hash=hash_password(password),
        role=role,
    )
    db.session.add(user)
    db.session.commit()
    return user, None


def authenticate(username, password):
    user = User.query.filter_by(username=username).first()
    if user is None:
        return None, '用户名或密码错误'
    if not verify_password(password, user.password_hash):
        return None, '用户名或密码错误'
    return user, None


def login_required(permission=None):
    """未登录 401 · 无权限 403。"""

    def decorator(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            user_id = session.get('user_id')
            if user_id is None:
                return jsonify({'error': '请先登录'}), 401
            user = db.session.get(User, user_id)
            if user is None:
                session.clear()
                return jsonify({'error': '请先登录'}), 401
            if permission is not None and permission not in PERMISSIONS.get(user.role, set()):
                return jsonify({
                    'error': '无权访问',
                    'required_permission': permission,
                }), 403
            return view(*args, **kwargs)
        return wrapper

    return decorator


def current_user():
    user_id = session.get('user_id')
    if user_id is None:
        return None
    return db.session.get(User, user_id)


# ============ 权限矩阵（能力名） ============
PERMISSIONS = {
    'platform_admin': {
        'manage_targets', 'extract_contract', 'generate_cases',
        'validate_cases', 'run_eval', 'annotate', 'view_all',
    },
    'evaluator': {
        'manage_targets', 'extract_contract', 'generate_cases',
        'validate_cases', 'run_eval', 'view_all',
    },
    'annotator': {'annotate', 'view_all'},
    'client_viewer': {'view_all'},
}


def register_auth_routes(app):
    from flask import request

    @app.post('/api/auth/login')
    def api_login():
        payload = request.get_json(silent=True) or {}
        user, error = authenticate(
            payload.get('username', ''), payload.get('password', ''),
        )
        if error:
            return jsonify({'error': error}), 401
        session['user_id'] = user.id
        return jsonify({
            'username': user.username,
            'role': user.role,
        })

    @app.post('/api/auth/logout')
    def api_logout():
        session.clear()
        return jsonify({'status': 'ok'})

    @app.get('/api/auth/me')
    @login_required()
    def api_me():
        user = current_user()
        return jsonify({
            'username': user.username,
            'role': user.role,
            'permissions': sorted(PERMISSIONS.get(user.role, set())),
        })

    return app


def ensure_demo_users():
    """建 demo 三档账号（幂等）。阶段 5 演示用。"""
    demo = [
        ('eval', 'eval123456', 'evaluator'),
        ('annotator', 'annot123456', 'annotator'),
        ('client', 'client123456', 'client_viewer'),
        ('admin', 'admin123456', 'platform_admin'),
    ]
    for username, password, role in demo:
        if User.query.filter_by(username=username).first() is None:
            db.session.add(User(
                username=username,
                password_hash=hash_password(password),
                role=role,
            ))
    db.session.commit()
