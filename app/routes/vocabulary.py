from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import func
from app.models import Vocabulary, Phrase, Sentence, PhonicsRelatedWord
from app import db

vocabulary_bp = Blueprint('vocabulary', __name__)

def create_error_response(message, status_code=400):
    return jsonify({'error': message}), status_code

def create_success_response(data, message="Success"):
    return jsonify({
        'status': 'success',
        'message': message,
        'data': data
    })

def log_search(search_params, results_count, user_id=None):
    """记录搜索日志到analytics数据库"""
    try:
        search_log = SearchLog(
            user_id=user_id,
            search_query=str(search_params),
            search_type='vocabulary_search',
            results_count=results_count
        )
        db.session.add(search_log)
        db.session.commit()
    except Exception as e:
        current_app.logger.error(f"记录搜索日志失败: {str(e)}")

@vocabulary_bp.route('/vocabulary/by-frequency-rank', methods=['GET'])
def get_vocabulary_by_frequency_rank():
    """按频率排序查询词汇数据（使用vocabulary数据库）"""
    try:
        start_index = request.args.get('start_index', type=int)
        end_index = request.args.get('end_index', type=int)
        include_relations = request.args.get('include_relations', 'true').lower() == 'true'
        print(f"get_vocabulary_by_frequency_rank:{start_index,end_index,include_relations}")
        if start_index is None or end_index is None:
            return create_error_response('start_index和end_index参数不能为空')
        
        if start_index < 0 or end_index < 0:
            return create_error_response('索引不能为负数')
        
        if start_index > end_index:
            return create_error_response('开始索引不能大于结束索引')
        
        if end_index - start_index > 1000:
            return create_error_response('单次查询数据量不能超过1000条')
        
        # 使用vocabulary数据库进行查询
        total_count = db.session.query(Vocabulary).count()
        
        if start_index >= total_count:
            return create_error_response('开始索引超出数据范围', 404)
        
        actual_end_index = min(end_index, total_count - 1)
        
        vocabulary_list = (
            db.session.query(Vocabulary)
            .order_by(Vocabulary.frequency_rank)
            .offset(start_index)
            .limit(actual_end_index - start_index + 1)
            .all()
        )
        
        response_data = {
            'total_count': total_count,
            'start_index': start_index,
            'end_index': actual_end_index,
            'actual_count': len(vocabulary_list),
            'data': [vocab.to_dict(include_relations) for vocab in vocabulary_list]
        }
        
        return create_success_response(response_data)
        
    except Exception as e:
        current_app.logger.error(f"查询词汇数据错误: {str(e)}")
        return create_error_response(f'数据库查询错误: {str(e)}', 500)

@vocabulary_bp.route('/vocabulary/search', methods=['GET'])
def search_vocabulary():
    """搜索词汇并记录搜索日志"""
    try:
        english = request.args.get('english', '').strip()
        chinese = request.args.get('chinese', '').strip()
        level = request.args.get('level', '').strip()
        category = request.args.get('category', '').strip()
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 50, type=int), 100)
        
        # 构建查询（使用vocabulary数据库）
        query = db.session.query(Vocabulary)
        
        search_params = {}
        if english:
            query = query.filter(Vocabulary.english.contains(english))
            search_params['english'] = english
        if chinese:
            query = query.filter(Vocabulary.chinese.contains(chinese))
            search_params['chinese'] = chinese
        if level:
            query = query.filter(Vocabulary.level == level)
            search_params['level'] = level
        if category:
            query = query.filter(Vocabulary.category == category)
            search_params['category'] = category
        
        # 分页查询
        paginated = query.order_by(Vocabulary.frequency_rank).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        # 记录搜索日志到analytics数据库
        log_search(search_params, paginated.total)
        
        response_data = {
            'total_count': paginated.total,
            'page': page,
            'per_page': per_page,
            'total_pages': paginated.pages,
            'has_next': paginated.has_next,
            'has_prev': paginated.has_prev,
            'data': [vocab.to_dict() for vocab in paginated.items]
        }
        
        return create_success_response(response_data)
        
    except Exception as e:
        current_app.logger.error(f"搜索词汇错误: {str(e)}")
        return create_error_response(f'搜索错误: {str(e)}', 500)
@vocabulary_bp.route('/vocabulary/by-words', methods=['POST'])
def get_vocabulary_by_words():
    """
    根据一串英语单词列表查询对应的所有数据。
    使用POST方法，请求体应为JSON格式，例如:
    {
        "words": ["hello", "world", "python"],
        "include_relations": true
    }
    """
    try:
        # 1. 获取并验证请求数据
        data = request.get_json()
        # print(f"get_vocabulary_by_words:"+data)
        if not data or 'words' not in data:
            return create_error_response('请求体必须是包含 "words" 键的JSON对象')

        words_to_search = data.get('words')
        if not isinstance(words_to_search, list):
            return create_error_response('"words" 的值必须是一个字符串列表')

        # 清理和去重单词列表，并转换成小写以进行不区分大小写的查询
        cleaned_words = list(set([word.strip().lower() for word in words_to_search if isinstance(word, str) and word.strip()]))
        
        if not cleaned_words:
            return create_error_response('单词列表不能为空')

        if len(cleaned_words) > 100:
            return create_error_response('单次查询的单词数量不能超过100个')

        include_relations = data.get('include_relations', True)

        # 2. 查询数据库
        # 使用 func.lower(Vocabulary.english).in_() 来实现不区分大小写的批量查询
        found_vocab_objects = db.session.query(Vocabulary).filter(
            func.lower(Vocabulary.english).in_(cleaned_words)
        ).all()

        # 3. 组织响应数据
        found_data = [vocab.to_dict(include_relations=include_relations) for vocab in found_vocab_objects]
        
        # 找出哪些单词在数据库中找到了
        found_words_set = {vocab.english.lower() for vocab in found_vocab_objects}
        
        # 对比原始请求的单词列表，找出未找到的单词
        not_found_words = [word for word in cleaned_words if word not in found_words_set]
        
        response_data = {
            'found': found_data,
            'not_found': not_found_words,
            'query_count': len(cleaned_words),
            'found_count': len(found_data)
        }
        
        return create_success_response(response_data)

    except Exception as e:
        current_app.logger.error(f"按单词列表查询词汇错误: {str(e)}")
        return create_error_response(f'服务器内部错误: {str(e)}', 500)