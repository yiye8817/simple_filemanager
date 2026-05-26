"""按类型列文件、按文件名下载等接口的自动化测试。"""


def _login(client, user_id: int):
    with client.session_transaction() as sess:
        sess['user_id'] = user_id
        sess['user_name'] = 'T'


def test_get_files_by_type_ok(client, app):
    _login(client, app.test_user_id)
    rv = client.get('/api/files/by-type/documents')
    assert rv.status_code == 200
    data = rv.get_json()
    assert data.get('success') is True
    assert data.get('current_path') == 'documents'
    assert data.get('total') == 1
    files = data.get('files') or []
    assert len(files) == 1
    assert files[0].get('name') == 'hello.txt'


def test_get_files_by_type_alias(client, app):
    """与 scripts/test_file_manager_api.py 中 /api/files/type/<plat> 一致。"""
    _login(client, app.test_user_id)
    rv = client.get('/api/files/type/documents')
    assert rv.status_code == 200
    data = rv.get_json()
    assert data.get('current_path') == 'documents'


def test_get_files_by_type_unknown(client, app):
    _login(client, app.test_user_id)
    rv = client.get('/api/files/by-type/not_a_real_type')
    assert rv.status_code == 400
    data = rv.get_json()
    assert data.get('success') is False
    assert 'allowed' in data


def test_get_files_by_type_pagination(client, app):
    _login(client, app.test_user_id)
    rv = client.get('/api/files/by-type/documents?page=1&per_page=10')
    assert rv.status_code == 200
    data = rv.get_json()
    assert data.get('pages') == 1
    assert data.get('page') == 1


def test_download_by_name_ok(client, app):
    _login(client, app.test_user_id)
    rv = client.get('/api/download/by-name?name=hello.txt')
    assert rv.status_code == 200
    assert b'hello-bytes' in rv.data


def test_download_by_name_with_path(client, app):
    _login(client, app.test_user_id)
    rv = client.get('/api/download/by-name?name=hello.txt&path=hello.txt')
    assert rv.status_code == 200


def test_download_by_name_missing_param(client, app):
    _login(client, app.test_user_id)
    rv = client.get('/api/download/by-name')
    assert rv.status_code == 400


def test_download_by_name_not_found(client, app):
    _login(client, app.test_user_id)
    rv = client.get('/api/download/by-name?name=nope.txt')
    assert rv.status_code == 404


def test_all_with_versions_route_not_shadowed(client, app):
    """通配路由不应吞掉 /api/files/all-with-versions。"""
    _login(client, app.test_user_id)
    rv = client.get('/api/files/all-with-versions')
    assert rv.status_code == 200
    data = rv.get_json()
    assert data.get('success') is True
    assert 'files' in data
