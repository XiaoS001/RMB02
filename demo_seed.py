"""演示数据 —— 预置评测集（阶段 5）。

建 demo 靶子（offline）+ 契约 + 生成用例 + 模型输出 + 打分 + 标注 + 结算，
让页面打开就有东西看。幂等：重复跑不重复插（按名字查重）。

用法：
    python demo_seed.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from app import create_app  # noqa: E402
from auth import ensure_demo_users  # noqa: E402
from generator import generate_all  # noqa: E402
from models import (  # noqa: E402
    Annotation,
    Contract,
    EvalTask,
    ModelOutput,
    Score,
    Settlement,
    Target,
    db,
)
from validator import validate_case  # noqa: E402

# demo_target 的契约（与 templates/dashboard.html 里的一致）
DEMO_CONTRACTS = [
    {'method': 'GET', 'path': '/healthz', 'fields': [],
     'source': 'api_md', 'confidence': 1.0},
    {'method': 'POST', 'path': '/api/echo',
     'fields': [{'name': 'name', 'required': True}],
     'source': 'api_md', 'confidence': 1.0},
    {'method': 'GET', 'path': '/api/items/<item_id>', 'fields': [],
     'source': 'url_map', 'confidence': 0.5},
    {'method': 'POST', 'path': '/api/login',
     'fields': [{'name': 'username', 'required': True},
                {'name': 'password', 'required': True}],
     'source': 'api_md', 'confidence': 1.0},
    {'method': 'GET', 'path': '/api/secure/stats', 'fields': [],
     'source': 'url_map', 'confidence': 0.5},
]


def seed():
    app = create_app({'SQLALCHEMY_DATABASE_URI': os.environ.get(
        'RMB02_DATABASE_URL', 'sqlite:///./data/rmb02.db')})
    with app.app_context():
        db.create_all()
        ensure_demo_users()

        # 靶子（幂等）
        target = Target.query.filter_by(name='演示靶子').first()
        if target is None:
            target = Target(name='演示靶子', base_url='offline://demo',
                            kind='offline', contract_source='api_md')
            db.session.add(target)
            db.session.commit()
            db.session.refresh(target)

        # 契约（幂等：先清旧的）
        Contract.query.filter_by(target_id=target.id).delete()
        for c in DEMO_CONTRACTS:
            db.session.add(Contract(target_id=target.id, path=c['path'],
                                    method=c['method'],
                                    fields_json=__import__('json').dumps(
                                        c['fields'], ensure_ascii=False),
                                    source=c['source'],
                                    confidence=c['confidence']))
        db.session.commit()

        # 任务
        task = EvalTask.query.filter_by(name='演示评测任务').first()
        if task is None:
            task = EvalTask(target_id=target.id, name='演示评测任务',
                            status='generated')
            db.session.add(task)
            db.session.commit()
            db.session.refresh(task)

        # 用例（幂等：清旧的重新生成）
        from models import Case
        Case.query.filter_by(task_id=task.id).delete()
        contracts = Contract.query.filter_by(target_id=target.id).all()
        cc = [{'method': c.method, 'path': c.path,
               'fields': __import__('json').loads(c.fields_json or '[]')}
              for c in contracts]
        cases = generate_all(cc)
        for case in cases:
            case.task_id = task.id
            status, layer, reason = validate_case(case, cc)
            case.status = status
            case.invalid_layer = layer
            case.invalid_reason = reason
            db.session.add(case)
        db.session.commit()

        # 模型输出 + 打分（n=5，模拟分数带轻微波动）
        ModelOutput.query.filter_by(task_id=task.id).delete()
        Score.query.filter_by(model_output_id=None).delete()
        outputs = []
        prompts = [
            ('健康检查返回什么', '{"status": "ok", "service": "rmb02"}'),
            ('echo 接口测试', '{"name": "hello"}'),
            ('查询 item 1', '{"id": 1, "name": "alpha"}'),
            ('登录演示', '{"token": "tok-demo-0"}'),
            ('统计接口', '{"items": 2, "tokens": 1}'),
        ]
        for i, (prompt, output) in enumerate(prompts):
            mo = ModelOutput(task_id=task.id, prompt=prompt, output=output,
                             model='local-rule', run_index=0)
            db.session.add(mo)
            db.session.flush()
            db.session.add(Score(model_output_id=mo.id,
                                 accuracy=80 + i * 2, relevance=85,
                                 format=90, hallucination=100,
                                 total=round(80 + i * 2 * 0.4 + 85 * 0.3 + 90 * 0.2 + 100 * 0.1, 1),
                                 run_index=0))
            outputs.append(mo.id)
        db.session.commit()

        # 标注（标注员 id=2 annotator）+ 结算（评测工程师 id=1）
        for out_id in outputs[:4]:
            if not Annotation.query.filter_by(model_output_id=out_id,
                                              annotator_id=2).first():
                db.session.add(Annotation(model_output_id=out_id,
                                          annotator_id=2, verdict='agree'))
        if not Settlement.query.filter_by(annotator_id=2, task_id=task.id).first():
            db.session.add(Settlement(annotator_id=2, task_id=task.id,
                                      case_count=4, unit_price=0.5, amount=2.0))
        db.session.commit()

        # 🔴 控制台 GBK 不编 emoji，用纯文本（经验库铁律：中文/特殊字符落盘再读）
        print(f'[seed] demo ready: target={target.id} task={task.id} '
              f'outputs={len(outputs)} annotations=4 settlement=2.0元')


if __name__ == '__main__':
    seed()
