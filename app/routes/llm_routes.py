"""LLM 提供商管理：增删改查 + 分类列表 + 页面入口 + 外部 API。"""
import logging

from flask import Blueprint, jsonify, render_template, request, session
from sqlalchemy.exc import IntegrityError

from app import db
from app.models.llm_provider import LLMProvider
from app.utils.decorators import api_key_required, login_required

llm_bp = Blueprint('llm', __name__)
logger = logging.getLogger(__name__)

_EDITABLE_STR_FIELDS = (
    'name', 'category', 'provider', 'base_url', 'api_key', 'default_model', 'notes'
)


def _apply_payload(provider, data):
    for field in _EDITABLE_STR_FIELDS:
        if field in data:
            value = data.get(field)
            if value is None:
                setattr(provider, field, None)
            else:
                setattr(provider, field, str(value).strip())
    if 'reference_links' in data:
        provider.set_reference_links(data.get('reference_links') or [])
    # 分类兜底
    if not (provider.category or '').strip():
        provider.category = '其他'


@llm_bp.route('/llm')
@login_required
def llm_page():
    return render_template('llm/index.html', user_name=session.get('user_name'))


@llm_bp.route('/api/llm', methods=['GET'])
@login_required
def list_llm():
    """获取当前用户全部大模型配置；支持 ?category=xxx 过滤、?mask=1 遮盖 api_key。"""
    user_id = session.get('user_id')
    category = request.args.get('category')
    mask = request.args.get('mask', '0') == '1'
    query = LLMProvider.query.filter_by(user_id=user_id)
    if category:
        query = query.filter(LLMProvider.category == category)
    rows = query.order_by(LLMProvider.category.asc(), LLMProvider.name.asc()).all()
    return jsonify({
        'success': True,
        'items': [p.to_dict(mask_key=mask) for p in rows],
        'total': len(rows),
    })


@llm_bp.route('/api/llm/categories', methods=['GET'])
@login_required
def list_llm_categories():
    user_id = session.get('user_id')
    rows = (
        db.session.query(LLMProvider.category)
        .filter_by(user_id=user_id)
        .distinct()
        .all()
    )
    cats = sorted({(r[0] or '其他') for r in rows})
    return jsonify({'success': True, 'categories': cats})


@llm_bp.route('/api/llm/<int:pid>', methods=['GET'])
@login_required
def get_llm(pid):
    user_id = session.get('user_id')
    mask = request.args.get('mask', '0') == '1'
    p = LLMProvider.query.filter_by(id=pid, user_id=user_id).first()
    if not p:
        return jsonify({'success': False, 'error': '配置不存在'}), 404
    return jsonify({'success': True, 'item': p.to_dict(mask_key=mask)})


@llm_bp.route('/api/llm', methods=['POST'])
@login_required
def create_llm():
    user_id = session.get('user_id')
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'error': '名称不能为空'}), 400
    category = (data.get('category') or '其他').strip() or '其他'
    provider = LLMProvider(user_id=user_id, name=name, category=category)
    _apply_payload(provider, data)
    db.session.add(provider)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({'success': False, 'error': '同名配置已存在'}), 409
    return jsonify({'success': True, 'item': provider.to_dict()})


@llm_bp.route('/api/llm/<int:pid>', methods=['PUT', 'PATCH'])
@login_required
def update_llm(pid):
    user_id = session.get('user_id')
    p = LLMProvider.query.filter_by(id=pid, user_id=user_id).first()
    if not p:
        return jsonify({'success': False, 'error': '配置不存在'}), 404
    data = request.get_json(silent=True) or {}
    _apply_payload(p, data)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({'success': False, 'error': '同名配置已存在'}), 409
    return jsonify({'success': True, 'item': p.to_dict()})


@llm_bp.route('/api/llm/<int:pid>', methods=['DELETE'])
@login_required
def delete_llm(pid):
    user_id = session.get('user_id')
    p = LLMProvider.query.filter_by(id=pid, user_id=user_id).first()
    if not p:
        return jsonify({'success': False, 'error': '配置不存在'}), 404
    db.session.delete(p)
    db.session.commit()
    return jsonify({'success': True})


# ----- 外部 API：通过 X-API-Key 获取全部配置 -----
@llm_bp.route('/api/external/llm', methods=['GET'])
@api_key_required
def external_list_llm():
    user = request.user
    mask = request.args.get('mask', '0') == '1'
    rows = (
        LLMProvider.query.filter_by(user_id=user.id)
        .order_by(LLMProvider.category.asc(), LLMProvider.name.asc())
        .all()
    )
    return jsonify({
        'success': True,
        'items': [p.to_dict(mask_key=mask) for p in rows],
        'total': len(rows),
    })
