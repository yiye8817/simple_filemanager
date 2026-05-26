"""公开 API 文档：HTML 与 JSON。"""
from flask import Blueprint, render_template, jsonify

from app.api_docs_data import API_DOC_META, API_SECTIONS

docs_bp = Blueprint('docs', __name__)


@docs_bp.route('/docs', methods=['GET'])
def api_docs_page():
    """浏览器可读的接口说明（无需登录）。"""
    return render_template(
        'api_docs.html',
        meta=API_DOC_META,
        sections=API_SECTIONS,
    )


@docs_bp.route('/docs.json', methods=['GET'])
def api_docs_json():
    """与文档页相同的结构化数据，便于脚本或第三方工具消费。"""
    payload = {**API_DOC_META, 'sections': API_SECTIONS}
    return jsonify(payload)
