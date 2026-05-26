"""应用更新检测接口 /api/query/app-update 的单元测试（Flask test_client）。"""


def test_app_update_missing_params_400(client):
    rv = client.get('/api/query/app-update')
    assert rv.status_code == 400
    data = rv.get_json()
    assert data.get('error')


def test_app_update_with_package_ok(client):
    rv = client.get(
        '/api/query/app-update?package=com.example.pytest&system=liangeos'
    )
    assert rv.status_code == 200
    data = rv.get_json()
    assert data.get('success') is True
    assert 'has_update' in data
    assert 'latest' in data


def test_app_update_four_dims_no_package_ok(client):
    rv = client.get(
        '/api/query/app-update'
        '?system=liangeos&type=ota&vendor=v&device_type=d'
    )
    assert rv.status_code == 200
    data = rv.get_json()
    assert data.get('success') is True
    assert 'has_update' in data
