"""RMB02 数据模型 —— 按任务单第三节十张表设计。

🔴 设计要点（对照任务单 v2）：
1. `Score` 与 `Annotation` **分表**（任务单约束 6）—— 机器分不许覆盖人工标注
2. `Case.status` 有 `valid`/`invalid` —— **invalid 的不参与统计**
3. `Score.run_index` 与 `ModelOutput.run_index` —— 3 遍稳定性靠它区分
4. `Target.contract_source` 记录契约来源（api_md / url_map / ast）
"""
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()


# ============ 角色四档（任务单） ============
ROLE_PLATFORM_ADMIN = 'platform_admin'
ROLE_EVALUATOR = 'evaluator'
ROLE_ANNOTATOR = 'annotator'
ROLE_CLIENT_VIEWER = 'client_viewer'
ALL_ROLES = (
    ROLE_PLATFORM_ADMIN,
    ROLE_EVALUATOR,
    ROLE_ANNOTATOR,
    ROLE_CLIENT_VIEWER,
)

ROLE_LABELS = {
    ROLE_PLATFORM_ADMIN: '平台管理员',
    ROLE_EVALUATOR: '评测工程师',
    ROLE_ANNOTATOR: '标注员',
    ROLE_CLIENT_VIEWER: '客户（只读）',
}

# ============ 评测相关枚举 ============
TARGET_KIND_HTTP = 'http'
TARGET_KIND_OFFLINE = 'offline'

CONTRACT_SOURCE_API_MD = 'api_md'
CONTRACT_SOURCE_URL_MAP = 'url_map'
CONTRACT_SOURCE_AST = 'ast'

CASE_KIND_NORMAL = 'normal'
CASE_KIND_BOUNDARY = 'boundary'
CASE_KIND_ERROR = 'error'
CASE_KIND_AUTHZ = 'authz'
ALL_CASE_KINDS = (CASE_KIND_NORMAL, CASE_KIND_BOUNDARY, CASE_KIND_ERROR, CASE_KIND_AUTHZ)

CASE_STATUS_VALID = 'valid'
CASE_STATUS_INVALID = 'invalid'

INVALID_LAYER_ENDPOINT = 'endpoint'
INVALID_LAYER_FIELD = 'field'
INVALID_LAYER_ASSERT = 'assert'

VERDICT_AGREE = 'agree'
VERDICT_DOUBT = 'doubt'
VERDICT_REJECT = 'reject'
ALL_VERDICTS = (VERDICT_AGREE, VERDICT_DOUBT, VERDICT_REJECT)


class User(db.Model):
    """用户。密码只存 hash（同 RMB01 做法）。"""

    __tablename__ = 'user'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    def __repr__(self):
        return f'<User {self.id} {self.username} {self.role}>'


class Target(db.Model):
    """被测对象。contract_source 记录字段契约从哪来。"""

    __tablename__ = 'target'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    base_url = db.Column(db.String(500), nullable=False)
    kind = db.Column(db.String(20), nullable=False, default=TARGET_KIND_HTTP)
    contract_source = db.Column(db.String(20), nullable=False, default=CONTRACT_SOURCE_API_MD)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    contracts = db.relationship('Contract', back_populates='target', lazy='select')

    def __repr__(self):
        return f'<Target {self.id} {self.name} {self.kind}>'


class Contract(db.Model):
    """从靶子抽出来的接口契约（path + method + 字段表）。"""

    __tablename__ = 'contract'

    id = db.Column(db.Integer, primary_key=True)
    target_id = db.Column(
        db.Integer, db.ForeignKey('target.id'), nullable=False, index=True,
    )
    path = db.Column(db.String(300), nullable=False)
    method = db.Column(db.String(10), nullable=False)
    fields_json = db.Column(db.Text, nullable=False, default='{}')
    source = db.Column(db.String(20), nullable=False, default=CONTRACT_SOURCE_API_MD)
    confidence = db.Column(db.Float, nullable=False, default=1.0)

    target = db.relationship('Target', back_populates='contracts')

    def __repr__(self):
        return f'<Contract {self.id} {self.method} {self.path} src={self.source}>'


class EvalTask(db.Model):
    """一次评测任务。"""

    __tablename__ = 'eval_task'

    id = db.Column(db.Integer, primary_key=True)
    target_id = db.Column(
        db.Integer, db.ForeignKey('target.id'), nullable=False, index=True,
    )
    name = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='created')
    created_by = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    cases = db.relationship('Case', back_populates='task', lazy='select')

    def __repr__(self):
        return f'<EvalTask {self.id} {self.name} {self.status}>'


class Case(db.Model):
    """一条用例。status=invalid 的不参与统计。"""

    __tablename__ = 'case'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(
        db.Integer, db.ForeignKey('eval_task.id'), nullable=False, index=True,
    )
    path = db.Column(db.String(300), nullable=False)
    method = db.Column(db.String(10), nullable=False)
    body_json = db.Column(db.Text, nullable=True)
    expected = db.Column(db.Text, nullable=True)
    kind = db.Column(db.String(20), nullable=False, default=CASE_KIND_NORMAL)
    status = db.Column(db.String(20), nullable=False, default=CASE_STATUS_VALID)
    invalid_reason = db.Column(db.String(200), nullable=True)
    invalid_layer = db.Column(db.String(20), nullable=True)

    task = db.relationship('EvalTask', back_populates='cases')
    runs = db.relationship('CaseRun', back_populates='case', lazy='select')

    def __repr__(self):
        return f'<Case {self.id} {self.method} {self.path} {self.status}>'


class CaseRun(db.Model):
    """一条用例的真跑结果。"""

    __tablename__ = 'case_run'

    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(
        db.Integer, db.ForeignKey('case.id'), nullable=False, index=True,
    )
    status_code = db.Column(db.Integer, nullable=True)
    response_body = db.Column(db.Text, nullable=True)
    passed = db.Column(db.Boolean, nullable=False, default=False)
    error = db.Column(db.String(500), nullable=True)
    duration_ms = db.Column(db.Integer, nullable=True)

    case = db.relationship('Case', back_populates='runs')

    def __repr__(self):
        return f'<CaseRun {self.id} case={self.case_id} code={self.status_code} passed={self.passed}>'


class ModelOutput(db.Model):
    """被评测的模型输出（run_index 区分 3 遍稳定性）。"""

    __tablename__ = 'model_output'

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(
        db.Integer, db.ForeignKey('eval_task.id'), nullable=False, index=True,
    )
    prompt = db.Column(db.Text, nullable=False)
    output = db.Column(db.Text, nullable=True)
    model = db.Column(db.String(100), nullable=True)
    run_index = db.Column(db.Integer, nullable=False, default=0)

    def __repr__(self):
        return f'<ModelOutput {self.id} task={self.task_id} run={self.run_index}>'


class Score(db.Model):
    """机器打分。必须带 run_index（稳定性按遍比较）。"""

    __tablename__ = 'score'

    id = db.Column(db.Integer, primary_key=True)
    model_output_id = db.Column(
        db.Integer, db.ForeignKey('model_output.id'), nullable=False, index=True,
    )
    accuracy = db.Column(db.Float, nullable=False, default=0)
    relevance = db.Column(db.Float, nullable=False, default=0)
    format = db.Column(db.Float, nullable=False, default=0)
    hallucination = db.Column(db.Float, nullable=True)  # None = N/A（无证据来源）
    total = db.Column(db.Float, nullable=False, default=0)
    run_index = db.Column(db.Integer, nullable=False, default=0)

    def __repr__(self):
        return f'<Score {self.id} out={self.model_output_id} total={self.total}>'


class Annotation(db.Model):
    """人工标注 —— 与 Score 分表，绝不覆盖机器分。"""

    __tablename__ = 'annotation'

    id = db.Column(db.Integer, primary_key=True)
    model_output_id = db.Column(
        db.Integer, db.ForeignKey('model_output.id'), nullable=False, index=True,
    )
    annotator_id = db.Column(
        db.Integer, db.ForeignKey('user.id'), nullable=False,
    )
    verdict = db.Column(db.String(20), nullable=False, default=VERDICT_AGREE)
    note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    def __repr__(self):
        return f'<Annotation {self.id} out={self.model_output_id} {self.verdict}>'


class Settlement(db.Model):
    """标注员按条结算。

    🔴 唯一约束（审查 Bug 1）：同一 (annotator_id, task_id) 只允许一行 ——
    数据库层面防止重复结算多付。业务层 settle_annotator 也先查后拒。
    """

    __tablename__ = 'settlement'
    __table_args__ = (
        db.UniqueConstraint('annotator_id', 'task_id',
                            name='uq_settle_annotator_task'),
    )

    id = db.Column(db.Integer, primary_key=True)
    annotator_id = db.Column(
        db.Integer, db.ForeignKey('user.id'), nullable=False, index=True,
    )
    task_id = db.Column(
        db.Integer, db.ForeignKey('eval_task.id'), nullable=False,
    )
    case_count = db.Column(db.Integer, nullable=False, default=0)
    unit_price = db.Column(db.Float, nullable=False, default=0)
    amount = db.Column(db.Float, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    def __repr__(self):
        return f'<Settlement {self.id} annotator={self.annotator_id} amount={self.amount}>'
