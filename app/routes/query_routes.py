from flask import Blueprint, render_template, request, jsonify
from app.models.version_model import FileVersion, db
from sqlalchemy import and_
from app.utils.decorators import login_required
from app.services.ota_formatters import format_ota_response
from app.services.app_update_service import (
    find_latest_for_app,
    compare_with_client,
    latest_to_payload,
)

query_bp = Blueprint('query', __name__)


def _app_update_request_params():
    if request.method == 'POST' and request.is_json:
        return request.get_json(silent=True) or {}
    if request.method == 'POST':
        return request.form.to_dict()
    return request.args.to_dict()

@query_bp.route('/l', methods=['GET'])
def get_latest():
    """查询最新版本"""
    system = request.args.get('system', 'liangeos')
    type = request.args.get('type')
    vendor = request.args.get('vendor')
    device_type = request.args.get('dtype')
    
    filters = [FileVersion.status == 'active', FileVersion.is_latest == True]
    
    if system:
        filters.append(FileVersion.system == system)
    if type:
        filters.append(FileVersion.type == type)
    if vendor:
        filters.append(FileVersion.vendor == vendor)
    if device_type:
        filters.append(FileVersion.device_type == device_type)
    
    # versions = FileVersion.query.filter(and_(*filters)).all()
    version = FileVersion.query.filter(and_(*filters)).first()
    #添加每个不同的系统升级返回规则
    #
    #
    response_data = format_ota_response(system,version)


    return jsonify(response_data)

@query_bp.route('/specific', methods=['GET'])
def get_specific():
    """查询特定版本"""
    version_number = request.args.get('version')
    system = request.args.get('system', 'liangeos')
    type = request.args.get('type')
    vendor = request.args.get('vendor')
    device_type = request.args.get('device_type')
    
    filters = [FileVersion.status == 'active']
    
    if version_number:
        filters.append(FileVersion.version == version_number)
    if system:
        filters.append(FileVersion.system == system)
    if type:
        filters.append(FileVersion.type == type)
    if vendor:
        filters.append(FileVersion.vendor == vendor)
    if device_type:
        filters.append(FileVersion.device_type == device_type)
    
    version = FileVersion.query.filter(and_(*filters)).first()
    
    if version:
        return jsonify({
            'success': True,
            'data': version.to_dict()
        })
    else:
        return jsonify({
            'success': False,
            'message': 'Version not found'
        }), 404

@query_bp.route('/all', methods=['GET'])
def get_all():
    """查询所有版本"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    
    # 构建过滤条件
    filters = [FileVersion.status == 'active']
    
    system = request.args.get('system')
    type = request.args.get('type')
    vendor = request.args.get('vendor')
    device_type = request.args.get('device_type')
    
    if system:
        filters.append(FileVersion.system == system)
    if type:
        filters.append(FileVersion.type == type)
    if vendor:
        filters.append(FileVersion.vendor == vendor)
    if device_type:
        filters.append(FileVersion.device_type == device_type)
    
    # 分页查询
    pagination = FileVersion.query.filter(
        and_(*filters)
    ).order_by(
        FileVersion.timestamp.desc()
    ).paginate(page=page, per_page=per_page, error_out=False)
    
    return jsonify({
        'success': True,
        'data': [v.to_dict() for v in pagination.items],
        'total': pagination.total,
        'pages': pagination.pages,
        'current_page': page
    })


@query_bp.route('/latest', methods=['GET'])
def get_latest_for_ui():
    """版本查询管理页「查询最新」：返回与 query.js 一致的 { success, data: [] }。"""
    filters = [FileVersion.status == 'active', FileVersion.is_latest == True]

    system = request.args.get('system')
    type_ = request.args.get('type')
    vendor = request.args.get('vendor')
    device_type = request.args.get('device_type')

    if system:
        filters.append(FileVersion.system == system)
    if type_:
        filters.append(FileVersion.type == type_)
    if vendor:
        filters.append(FileVersion.vendor == vendor)
    if device_type:
        filters.append(FileVersion.device_type == device_type)

    version = FileVersion.query.filter(and_(*filters)).order_by(FileVersion.timestamp.desc()).first()
    if not version:
        return jsonify({'success': True, 'data': []})
    return jsonify({'success': True, 'data': [version.to_dict()]})


@query_bp.route('/app-update', methods=['GET', 'POST'])
def check_app_update():
    """
    检测应用是否有更新，并返回最新版下载信息。

    参数（GET 查询串或 POST JSON/表单）：
    - package 或 android_package：Android 包名（推荐，与保存版本时 extra_fields 中 android_package 一致）
    - system, type, vendor, device_type：渠道维度；未提供 package 时四项均必填
    - current_version / version_name：当前安装版本名（可选）
    - current_version_code / version_code：当前 versionCode（可选，整数优先于版本名比较）

    未提供当前版本时，视为首次检查，若有服务端记录则 has_update 为 true。
    """
    data = _app_update_request_params()
    package = data.get('package') or data.get('android_package')
    system = data.get('system')
    type_ = data.get('type')
    vendor = data.get('vendor')
    device_type = data.get('device_type')
    current_version = data.get('current_version') or data.get('version_name')
    current_version_code = data.get('current_version_code')
    if current_version_code is None:
        current_version_code = data.get('version_code')

    if not (package and str(package).strip()) and not all(
        [system, type_, vendor, device_type]
    ):
        return jsonify({
            'success': False,
            'error': '请提供 package（或 android_package），或同时提供 system、type、vendor、device_type',
        }), 400

    latest = find_latest_for_app(package, system, type_, vendor, device_type)
    if not latest:
        return jsonify({
            'success': True,
            'has_update': False,
            'message': '未找到匹配的已发布版本',
            'latest': None,
        })

    has_update, reason = compare_with_client(
        latest, current_version, current_version_code
    )
    payload = latest_to_payload(latest)

    return jsonify({
        'success': True,
        'has_update': has_update,
        'compare_reason': reason,
        'latest': payload,
    })


@query_bp.route('/delete/<int:id>', methods=['DELETE'])
def delete_version(id):
    """删除版本（软删除）"""
    version = FileVersion.query.get(id)
    if version:
        version.status = 'deleted'
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': 'Version not found'}), 404

#前端路由query.html
@query_bp.route('/query.html')
@login_required # <-- 使用这一行来保护页面
# def index():
def page():
    return render_template('version_manager/query.html')