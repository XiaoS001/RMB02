"""pytest 夹具 —— 内存库 + demo 用户。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from auth import ensure_demo_users  # noqa: E402
from models import db  # noqa: E402


@pytest.fixture()
def app():
    app = create_app({
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'TESTING': True,
        'SECRET_KEY': 'test-key',
    })
    with app.app_context():
        db.create_all()
        ensure_demo_users()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, username, password):
    resp = client.post('/api/auth/login', json={
        'username': username, 'password': password,
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp
