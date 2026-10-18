"""RMB02 应用工厂。

🔴 为什么用工厂而不是模块顶层 `app = Flask(__name__)`：
1. 测试要注入独立的内存库（sqlite:///:memory:），否则测试污染 data/rmb02.db；
2. 避免 import app 就连库（RMB01 的教训：路径在 import 期固化，测试改了环境变量不生效）。
"""
import os

from flask import Flask, jsonify

import config
from models import db


def create_app(overrides=None):
    """应用工厂。overrides 可注入 SQLALCHEMY_DATABASE_URI 等（测试换内存库）。"""
    application = Flask(__name__)
    application.config['SQLALCHEMY_DATABASE_URI'] = config.DATABASE_URL
    application.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    application.config['SECRET_KEY'] = config.SECRET_KEY or 'rmb02-ephemeral-dev-key'
    if overrides:
        application.config.update(overrides)

    db.init_app(application)

    with application.app_context():
        db.create_all()

    _register_routes(application)
    return application


def _register_routes(application):
    @application.get('/healthz')
    def healthz():
        """健康检查：只读配置快照，不触发业务逻辑、不写库。"""
        return jsonify({'status': 'ok', **config.snapshot()})

    # 业务蓝图统一注册（避免 app.py 膨胀）
    from api import register_api_blueprint
    from auth import register_auth_routes
    from pages import register_pages_blueprint

    application.register_blueprint(register_api_blueprint())
    application.register_blueprint(register_pages_blueprint())
    register_auth_routes(application)


app = create_app()


if __name__ == '__main__':
    app.run(debug=True, port=os.environ.get('RMB02_PORT', 5102))
