import os
import tempfile

import pytest

from app import create_app, db
from app.models import User, File


@pytest.fixture
def app():
    root = tempfile.mkdtemp()
    main_db = os.path.join(root, 'main.db')
    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{main_db}'
    app.config['SQLALCHEMY_BINDS'] = {
        'vocabulary': f'sqlite:///{os.path.join(root, "vocab.db")}',
        'resources_manager': f'sqlite:///{os.path.join(root, "res.db")}',
        'version_manager': f'sqlite:///{os.path.join(root, "ver.db")}',
    }
    upload = tempfile.mkdtemp(dir=root)
    app.config['UPLOAD_FOLDER'] = upload
    app.config['SECRET_KEY'] = 'test-secret'
    app.config['WTF_CSRF_ENABLED'] = False

    with app.app_context():
        db.create_all()
        for bind_key in (app.config.get('SQLALCHEMY_BINDS') or {}):
            db.create_all(bind_key=bind_key)
        user = User(
            username='tuser',
            email='t@test.local',
            display_name='T',
        )
        user.set_password('pw')
        db.session.add(user)
        db.session.commit()

        # 磁盘上的文件，供下载接口读取
        user_dir = os.path.join(upload, str(user.id))
        os.makedirs(user_dir, exist_ok=True)
        rel = 'hello.txt'
        abs_path = os.path.join(user_dir, rel)
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write('hello-bytes')

        frec = File(
            name='hello.txt',
            path=rel,
            size=11,
            file_type='documents',
            is_directory=False,
            user_id=user.id,
            parent_id=None,
        )
        db.session.add(frec)
        db.session.commit()

        app.test_user_id = user.id
        app.test_file_id = frec.id
        app.test_upload_root = upload

    yield app

    with app.app_context():
        for bind_key in (app.config.get('SQLALCHEMY_BINDS') or {}):
            db.drop_all(bind_key=bind_key)
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()
