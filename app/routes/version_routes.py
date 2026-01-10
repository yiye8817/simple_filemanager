import os
from flask import Blueprint, current_app, request, jsonify, session
from app.services.version_service import VersionService
from app.services.file_service import FileService
from app.utils.async_tasks import calculate_checksum_async, calculate_md5_async
from app.models.version_model import db, FileVersion
import json
from app.models.file import update_file_version
from app.utils.decorators import login_required

version_bp = Blueprint('version', __name__)

@version_bp.route('/create', methods=['POST'])
@login_required # <-- 使用这一行来保护页面
def create_version():
    """创建新版本"""
    try:
        data = request.json
        user_id = session.get('user_id')
        # 验证必需字段
        # required_fields = ['type', 'vendor', 'device_type', 'file_path']
        # for field in required_fields:
        #     if not data.get(field):
        #         return jsonify({'success': False, 'error': f'缺少必需字段: {field}'}), 400
        print(f"create_version:{data.get('version')},file_id:"+str(data.get('file_id'))+
              ","+data.get('file_path')+',size:'+str(data.get('file_size'))
              +str(data.get('checksum_type')))
        data['user_id'] = user_id
        file_id = data.get('file_id')
        # 获取或生成版本号，如果没有版本号就自动生成1个
        if not data.get('version'):
            data['version'] = VersionService.get_next_version(
                data.get('system', 'liangeos'),
                data['type'],
                data['vendor'],
                data['device_type']
            )
        else:
            # 获取旧版本进行备份
            old_version = FileVersion.query.filter_by(
                system=data.get('system', 'liangeos'),
                type=data['type'],
                vendor=data['vendor'],
                device_type=data['device_type'],
                is_latest=True,
                status='active'
            ).first()
            print(f"find:{old_version}")
            # if old_version :
            #如果上传的版本号和最新的版本号是一致的
            if not old_version or old_version.version == data.get('version'):
                data['version'] = VersionService.get_next_version(
                data.get('system', 'liangeos'),
                data['type'],
                data['vendor'],
                data['device_type']
                )
        #上传文件就把文件给覆盖了，所以不能在这里备份
        # if old_version:
        #     # 备份旧文件
        #     backup_path = FileService.backup_file(old_version.file_path, old_version.version)
        #     print(f"文件已备份到: {backup_path}")
        
        
        # 创建新版本
        version = VersionService.create_version(data)
        update_file_version(file_id,version.version)
        checksum_type = version.checksum_type
        
        # 异步计算MD5
        if data.get('file_path'):
             fp = os.path.join(current_app.config['UPLOAD_FOLDER'], str(user_id),data['file_path'])
             if os.path.exists(fp):
                calculate_checksum_async(version.id, fp,checksum_type)
        
        return jsonify({
            'success': True,
            'data': version.to_dict()
        })
    
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
@version_bp.route('/query_by_file_id', methods=['POST'])
def get_by_file_id():
    """获取版本详情"""
    try:
        data = request.json
        fid = data.get("file_id")
        print(f"get_by_file_id:{fid}")
        version = FileVersion.query.filter_by(file_id=fid,is_latest=True,).first()
        if version and version.status == 'active':
            return jsonify({
                'success': True,
                'data': version.to_dict()
            })
        else:
            # print(version)
            return jsonify({
                'success': True,
                'data': {
                     'version': '0.0.0'
                    #  'file_size':
                }
            })
    except Exception as e:
        print(e)
        return jsonify({
             'success': True,
                'data': {
                     'version': '0.0.0'
                    #  'file_size':
                }
        })
@version_bp.route('/auto-version', methods=['POST'])
def get_auto_version():
    """获取自动生成的版本号"""
    try:
        data = request.json
        
        # 验证必需字段
        # required_fields = ['type', 'vendor', 'device_type']
        # for field in required_fields:
        #     if not data.get(field):
        #         return jsonify({'success': False, 'error': f'缺少必需字段: {field}'}), 400
        print(f"auto-version<={data}")
        version = VersionService.get_next_version(
            data.get('system', 'liangeos'),
            data['type'],
            data['vendor'],
            data['device_type']
        )
        
        return jsonify({
            'success': True,
            'version': version
        })
    except Exception as e:
        print("default version:0.0.0")
        return jsonify({
            'success': True,
            'version': "0.0.0"
        })
    # , 500

@version_bp.route('/<int:id>', methods=['GET'])
def get_version(id):
    """获取版本详情"""
    try:
        version = FileVersion.query.get(id)
        if version and version.status == 'active':
            return jsonify({
                'success': True,
                'data': version.to_dict()
            })
        else:
            return jsonify({
                'success': False,
                'error': '版本不存在'
            }), 404
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@version_bp.route('/update/<int:id>', methods=['PUT'])
def update_version(id):
    """更新版本信息"""
    try:
        version = FileVersion.query.get(id)
        if not version:
            return jsonify({'success': False, 'error': '版本不存在'}), 404
        
        data = request.json
        
        # 更新允许修改的字段
        updateable_fields = ['upgrade_desc', 'version_desc', 'extra_fields']
        for field in updateable_fields:
            if field in data:
                if field == 'extra_fields':
                    setattr(version, field, json.dumps(data[field]))
                else:
                    setattr(version, field, data[field])
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'data': version.to_dict()
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500