"""公开文档接口测试。"""


def test_api_docs_html_ok(client):
    rv = client.get('/api/docs')
    assert rv.status_code == 200
    assert b'File Manager API' in rv.data or 'api_docs' in rv.data.decode('utf-8', errors='replace').lower()
    assert b'/api/docs.json' in rv.data


def test_api_docs_json_ok(client):
    rv = client.get('/api/docs.json')
    assert rv.status_code == 200
    data = rv.get_json()
    assert data.get('title')
    assert isinstance(data.get('sections'), list)
    assert len(data['sections']) >= 1
