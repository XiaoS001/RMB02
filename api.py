"""RMB02 业务 API —— 靶子管理 / 契约抽取 / 用例生成 / 任务 / 结算。

🔴 设计：
- 角色四档（platform_admin/evaluator/annotator/client_viewer）
- 权限：evaluator 与 platform_admin 可管理靶子/生成用例；annotator 可标注
- 用例先校验再入库（invalid 不参与统计）
"""
import json

from flask import Blueprint, jsonify, request

from auth import current_user, login_required
from generator import generate_all
from models import (
    CASE_STATUS_VALID,
    Case,
    Contract,
    EvalTask,
    Target,
    db,
)
from validator import validate_case


def register_api_blueprint():
    bp = Blueprint('api', __name__)

    @bp.get('/api/targets')
    @login_required('view_all')
    def list_targets():
        rows = Target.query.order_by(Target.id).all()
        return jsonify([{
            'id': t.id, 'name': t.name, 'base_url': t.base_url,
            'kind': t.kind, 'contract_source': t.contract_source,
        } for t in rows])

    @bp.post('/api/targets')
    @login_required('manage_targets')
    def create_target():
        payload = request.get_json(silent=True) or {}
        name = (payload.get('name') or '').strip()
        base_url = (payload.get('base_url') or '').strip()
        kind = payload.get('kind', 'http')
        contract_source = payload.get('contract_source', 'api_md')
        if not name:
            return jsonify({'error': 'name 不能为空'}), 400
        if not base_url and kind == 'http':
            return jsonify({'error': 'base_url 不能为空'}), 400
        t = Target(
            name=name, base_url=base_url or 'offline://demo',
            kind=kind, contract_source=contract_source,
        )
        db.session.add(t)
        db.session.commit()
        return jsonify({
            'id': t.id, 'name': t.name, 'base_url': t.base_url,
            'kind': t.kind, 'contract_source': t.contract_source,
        }), 201

    @bp.get('/api/targets/<int:target_id>/contracts')
    @login_required('view_all')
    def list_contracts(target_id):
        rows = Contract.query.filter_by(target_id=target_id).order_by(Contract.id).all()
        return jsonify([{
            'id': c.id, 'path': c.path, 'method': c.method,
            'fields': json.loads(c.fields_json or '{}'),
            'source': c.source, 'confidence': c.confidence,
        } for c in rows])

    @bp.post('/api/targets/<int:target_id>/contracts')
    @login_required('extract_contract')
    def save_contracts(target_id):
        """把契约 JSON 批量入库（阶段 1 产出）。"""
        payload = request.get_json(silent=True) or []
        if not isinstance(payload, list):
            return jsonify({'error': '需要契约列表'}), 400
        for c in payload:
            Contract.query.filter_by(
                target_id=target_id, method=c.get('method'), path=c.get('path'),
            ).delete()
            db.session.add(Contract(
                target_id=target_id,
                path=c.get('path'),
                method=c.get('method'),
                fields_json=json.dumps(c.get('fields', []), ensure_ascii=False),
                source=c.get('source', 'api_md'),
                confidence=c.get('confidence', 1.0),
            ))
        db.session.commit()
        return jsonify({'saved': len(payload)}), 201

    @bp.post('/api/targets/<int:target_id>/generate-cases')
    @login_required('generate_cases')
    def generate_cases(target_id):
        """对靶子的全部契约生成用例（未校验前先标 valid，随后校验）。"""
        contracts = Contract.query.filter_by(target_id=target_id).all()
        if not contracts:
            return jsonify({'error': '该靶子还没有契约'}), 400
        task = EvalTask(
            target_id=target_id,
            name=(request.get_json(silent=True) or {}).get('name') or '自动生成用例任务',
            status='generated',
            created_by=current_user().id if current_user() else None,
        )
        db.session.add(task)
        db.session.flush()

        contract_dicts = [{
            'method': c.method, 'path': c.path,
            'fields': json.loads(c.fields_json or '[]'),
        } for c in contracts]

        cases = generate_all(contract_dicts)
        for case in cases:
            case.task_id = task.id
            status, layer, reason = validate_case(case, contract_dicts)
            case.status = status
            case.invalid_layer = layer
            case.invalid_reason = reason
            db.session.add(case)
        db.session.commit()

        valid_n = sum(1 for c in cases if c.status == CASE_STATUS_VALID)
        return jsonify({
            'task_id': task.id,
            'generated': len(cases),
            'valid': valid_n,
            'invalid': len(cases) - valid_n,
        }), 201

    @bp.get('/api/tasks')
    @login_required('view_all')
    def list_tasks():
        rows = EvalTask.query.order_by(EvalTask.id.desc()).all()
        return jsonify([{
            'id': t.id, 'target_id': t.target_id, 'name': t.name,
            'status': t.status, 'created_at': t.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        } for t in rows])

    @bp.get('/api/tasks/<int:task_id>/cases')
    @login_required('view_all')
    def list_cases(task_id):
        rows = Case.query.filter_by(task_id=task_id).order_by(Case.id).all()
        return jsonify([{
            'id': c.id, 'path': c.path, 'method': c.method,
            'body': json.loads(c.body_json) if c.body_json else None,
            'kind': c.kind, 'status': c.status,
            'invalid_reason': c.invalid_reason,
            'invalid_layer': c.invalid_layer,
        } for c in rows])

    @bp.get('/api/tasks/<int:task_id>/stats')
    @login_required('view_all')
    def task_stats(task_id):
        """阶段 2 硬要求：生成总数 / 有效数 / invalid 数与原因分布。"""
        rows = Case.query.filter_by(task_id=task_id).all()
        total = len(rows)
        valid = sum(1 for c in rows if c.status == CASE_STATUS_VALID)
        invalid = total - valid
        by_layer = {}
        by_reason = {}
        for c in rows:
            if c.status != CASE_STATUS_VALID:
                layer = c.invalid_layer or 'unknown'
                reason = c.invalid_reason or 'unknown'
                by_layer[layer] = by_layer.get(layer, 0) + 1
                by_reason[reason] = by_reason.get(reason, 0) + 1
        return jsonify({
            'task_id': task_id,
            'generated': total,
            'valid': valid,
            'invalid': invalid,
            'by_layer': by_layer,
            'by_reason': by_reason,
        })

    # ============ 阶段 4：人工标注 + 一致率 + 结算 ============

    @bp.post('/api/annotations')
    @login_required('annotate')
    def submit_annotation():
        """标注员提交标注（只写 Annotation，不碰 Score）。"""
        from annotation import submit_annotation as _submit

        payload = request.get_json(silent=True) or {}
        user = current_user()
        ann, error = _submit(
            payload.get('model_output_id'),
            user.id,
            payload.get('verdict'),
            payload.get('note'),
        )
        if error:
            return jsonify({'error': error}), 400
        return jsonify({
            'id': ann.id,
            'model_output_id': ann.model_output_id,
            'verdict': ann.verdict,
            'note': ann.note,
        }), 201

    @bp.get('/api/annotations/<int:model_output_id>')
    @login_required('view_all')
    def list_annotations(model_output_id):
        from annotation import get_annotations
        from auth import PERMISSIONS

        user = current_user()
        # 🔴 越权防护（审查验收 #5）：annotator 只能看自己标过的；
        #    evaluator / platform_admin 可看全部
        viewer_id = user.id if user.role == 'annotator' else None
        anns = get_annotations(model_output_id, viewer_id=viewer_id)
        return jsonify([{
            'id': a.id, 'model_output_id': a.model_output_id,
            'annotator_id': a.annotator_id, 'verdict': a.verdict,
            'note': a.note,
            'created_at': a.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        } for a in anns])

    @bp.get('/api/agreement-rate')
    @login_required('view_all')
    def agreement():
        """机器分 vs 人工标注一致率（n 真实 = Annotation 表行数）。"""
        from annotation import agreement_rate

        return jsonify(agreement_rate())

    @bp.post('/api/settlements')
    @login_required('manage_targets')
    def create_settlement():
        """平台管理员给标注员按条结算。"""
        from annotation import settle_annotator

        payload = request.get_json(silent=True) or {}
        settle, error = settle_annotator(
            payload.get('annotator_id'),
            payload.get('task_id'),
            payload.get('unit_price'),
        )
        if error:
            return jsonify({'error': error}), 400
        return jsonify({
            'id': settle.id,
            'annotator_id': settle.annotator_id,
            'task_id': settle.task_id,
            'case_count': settle.case_count,
            'unit_price': settle.unit_price,
            'amount': settle.amount,
        }), 201

    # ============ 阶段 4：看板 ============

    @bp.get('/api/dashboard/tasks')
    @login_required('view_all')
    def dashboard_tasks():
        """任务列表 + 通过率。"""
        from dashboard import task_pass_rate

        tasks = EvalTask.query.order_by(EvalTask.id.desc()).all()
        return jsonify([{
            'id': t.id, 'name': t.name, 'status': t.status,
            'pass_rate': task_pass_rate(t.id),
        } for t in tasks])

    @bp.get('/api/dashboard/tasks/<int:task_id>/dimensions')
    @login_required('view_all')
    def dashboard_dimensions(task_id):
        from dashboard import dimension_trend

        return jsonify(dimension_trend(task_id))

    @bp.get('/api/dashboard/agreement')
    @login_required('view_all')
    def dashboard_agreement():
        from dashboard import annotation_agreement_trend

        return jsonify(annotation_agreement_trend())

    @bp.get('/api/dashboard/workload')
    @login_required('view_all')
    def dashboard_workload():
        from dashboard import annotator_workload

        return jsonify(annotator_workload())

    return bp
