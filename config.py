"""RMB02 配置 —— 收口全部模型相关配置。

🔴 任务单硬约束 2：模型配置四项一起收口（key / base_url / model / timeout）。
RMB01 只收 key 导致 401，这里把四项全部列出。
"""
import os


# ============ 模型配置（四项收口） ============
# 默认指向 AMD 端点（任务单第一节实测 HTTP 200 可用）
DEFAULT_BASE_URL = 'https://developer.amd.com.cn/radeon/api/v1'
DEFAULT_MODEL = 'DeepSeek-V4-Flash'


def model_api_key() -> str:
    """模型 API key。空 = 未配置（走降级路径）。"""
    return os.environ.get('RMB02_API_KEY', '').strip()


def model_base_url() -> str:
    return os.environ.get('RMB02_BASE_URL', DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL


def model_name() -> str:
    return os.environ.get('RMB02_MODEL', DEFAULT_MODEL).strip() or DEFAULT_MODEL


def model_timeout_ms() -> int:
    raw = os.environ.get('RMB02_TIMEOUT_MS', '30000').strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 30000


# ============ 应用配置 ============
SECRET_KEY = os.environ.get('RMB02_SECRET_KEY', 'rmb02-ephemeral-dev-key')

# 数据库（默认 data/rmb02.db；测试用 :memory: 注入）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.environ.get(
    'RMB02_DATABASE_URL',
    f'sqlite:///{os.path.join(BASE_DIR, "data", "rmb02.db")}',
)


def snapshot() -> dict:
    """健康检查用配置快照（不泄漏密钥原文）。"""
    return {
        'service': 'rmb02',
        'ai_key_present': bool(model_api_key()),
        'ai_base_url': model_base_url(),
        'ai_model': model_name(),
        'ai_timeout_ms': model_timeout_ms(),
        'database_driver': 'sqlite',
    }
