from flask import Blueprint, app, render_template, request, jsonify, session
from flask_jwt_extended import current_user, jwt_required
from app.models.resource import  Resource
from werkzeug.utils import secure_filename
#解决的引用的db错误
from app import db
import os
from datetime import datetime

from app.utils.decorators import login_required
#路由先直接拷贝，后面再添加用户的管控

resource_bp = Blueprint('resource', __name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp',
                    #   'mkv','apk','mp4',

                      }

def allowed_file(filename):
    
    ret =  '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
    # print(f"allowed_file:{filename},{ret}")
    return ret

@resource_bp.route('/resources', methods=['GET'])
def get_resources():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        category = request.args.get('category', '')
        search = request.args.get('search', '')
        
        query = Resource.query
        
        if category:
            query = query.filter(Resource.category == category)
        
        if search:
            query = query.filter(
                db.or_(
                    Resource.title.contains(search),
                    Resource.tags.contains(search),
                    Resource.author.contains(search)
                )
            )
        
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        resources = [r.to_dict() for r in pagination.items]
        
        return jsonify({
            'resources': resources,
            'total': pagination.total,
            'pages': pagination.pages,
            'current_page': page
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@resource_bp.route('/resources/<int:resource_id>', methods=['GET'])
def get_resource(resource_id):
    try:
        resource = Resource.query.get_or_404(resource_id)
        return jsonify(resource.to_dict())
    except Exception as e:
        return jsonify({'error': str(e)}), 404
update_path="static/uploads_img"
@resource_bp.route('/resources', methods=['POST'])
def create_resource():
    try:
        data = request.form.to_dict()
        
        # 处理海报上传
        poster_url = None
        print(f"create_resource:{ request.files}")
        if 'poster' in request.files:
            file = request.files['poster']
            
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = f"{timestamp}_{filename}"
                if not os.path.exists(update_path):
                    os.mkdir(update_path)
                print(f"create_resource:{update_path}:{request.host_url},{request.base_url}")
                filepath = os.path.join(update_path, filename)
                file.save(filepath)
                poster_url = f"{request.host_url}{update_path}/{filename}"
        
        resource = Resource(
            title=data.get('title'),
            resource_link=data.get('resource_link'),
            poster_url=poster_url or data.get('poster_url'),
            category=data.get('category'),
            tags=data.get('tags'),
            proxy=data.get('proxy'),
            details=data.get('details'),
            author=data.get('author'),
            source=data.get('source'),
            rating=float(data.get('rating', 0)),
            subcategory=data.get('subcategory')
        )
        
        db.session.add(resource)
        db.session.commit()
        
        return jsonify({'message': '资源创建成功', 'resource': resource.to_dict()}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@resource_bp.route('/resources/<int:resource_id>', methods=['PUT'])
def update_resource(resource_id):
    try:
        resource = Resource.query.get_or_404(resource_id)
        data = request.form.to_dict()
        
        # 处理海报上传
        if 'poster' in request.files:
            file = request.files['poster']
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = f"{timestamp}_{filename}"
                filepath = os.path.join('static/uploads', filename)
                file.save(filepath)
                resource.poster_url = f"/static/uploads/{filename}"
        elif 'poster_url' in data:
            resource.poster_url = data.get('poster_url')
        
        # 更新其他字段
        resource.title = data.get('title', resource.title)
        resource.resource_link = data.get('resource_link', resource.resource_link)
        resource.category = data.get('category', resource.category)
        resource.tags = data.get('tags', resource.tags)
        resource.proxy = data.get('proxy', resource.proxy)
        resource.details = data.get('details', resource.details)
        resource.author = data.get('author', resource.author)
        resource.source = data.get('source', resource.source)
        resource.rating = float(data.get('rating', resource.rating))
        resource.subcategory = data.get('subcategory', resource.subcategory)
        
        db.session.commit()
        
        return jsonify({'message': '资源更新成功', 'resource': resource.to_dict()})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@resource_bp.route('/resources/<int:resource_id>', methods=['DELETE'])
def delete_resource(resource_id):
    try:
        resource = Resource.query.get_or_404(resource_id)
        db.session.delete(resource)
        db.session.commit()
        return jsonify({'message': '资源删除成功'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@resource_bp.route('/resources/batch', methods=['POST'])
def batch_import():
    try:
        if 'file' not in request.files:
            return jsonify({'error': '没有上传文件'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': '没有选择文件'}), 400
        
        # 这里可以处理CSV或Excel文件的导入
        # 简化示例，实际需要根据文件类型处理
        import pandas as pd
        
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        elif file.filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(file)
        else:
            return jsonify({'error': '不支持的文件格式'}), 400
        
        success_count = 0
        error_count = 0
        
        for _, row in df.iterrows():
            try:
                resource = Resource(
                    title=row.get('标题', ''),
                    resource_link=row.get('资源链接', ''),
                    poster_url=row.get('资源海报', ''),
                    category=row.get('分类', '其他'),
                    tags=row.get('特征字', ''),
                    proxy=row.get('代理', ''),
                    details=row.get('详细信息', ''),
                    author=row.get('作者', ''),
                    source=row.get('来源', ''),
                    rating=float(row.get('评分', 0)),
                    subcategory=row.get('子类', '')
                )
                db.session.add(resource)
                success_count += 1
            except Exception as e:
                error_count += 1
                continue
        
        db.session.commit()
        
        return jsonify({
            'message': f'批量导入完成，成功: {success_count}，失败: {error_count}',
            'success_count': success_count,
            'error_count': error_count
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@resource_bp.route('/categories', methods=['GET'])
def get_categories():
    try:
        # 获取所有唯一的分类
        categories = db.session.query(Resource.category).distinct().all()
        return jsonify([c[0] for c in categories if c[0]])
    except Exception as e:
        return jsonify({'error': str(e)}), 500
#将网页的接口附到资源路由的最后,拷贝模板文件到创建的目录中来
#拷贝js和css,重命令并修改base.html的引用路径
#暂时考虑内容不分用户，后续再改
@resource_bp.route('/')
# @jwt_required() # 确保只有登录用户才能访问
@login_required # <-- 使用这一行来保护页面
def index():
    # user_name = current_user.username 
    return render_template('resources_manager/index.html', user_name=session.get('user_name'))

@resource_bp.route('/add')
@login_required
def add_resource_page():
    return render_template('resources_manager/add_resource.html',user_name=session.get('user_name'))

@resource_bp.route('/edit/<int:resource_id>')
@login_required
def edit_resource_page(resource_id):
    return render_template('resources_manager/edit_resource.html', resource_id=resource_id,user_name=session.get('user_name'))

@resource_bp.route('/import')
@login_required
def import_page():
    return render_template('resources_manager/import_resources.html',user_name=session.get('user_name'))