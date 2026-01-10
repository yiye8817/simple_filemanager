from flask import Blueprint, render_template, request, jsonify
from app.models.version_model import FileVersion, db
from sqlalchemy import and_
from app.utils.decorators import login_required
from app.services.ota_formatters import format_ota_response
query_bp = Blueprint('query', __name__)

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