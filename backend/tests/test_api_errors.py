import concurrent.futures
import threading
from pathlib import Path

import pytest

import manager_core as core
import store
from test_reliability import isolated as isolated


@pytest.fixture
def client(isolated, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setattr(store, 'migrate', lambda: None)
    monkeypatch.setattr(core, 'ensure_worker', lambda: None)
    with TestClient(app, base_url='http://127.0.0.1:5790', raise_server_exceptions=False) as result:
        yield result


def test_node_validation_uses_the_standard_bad_request_response(client):
    response = client.get('/api/subscriptions/invalid.id/nodes')
    assert response.status_code == 400
    assert response.json() == {'error': '订阅编号无效。'}


def test_missing_task_remains_not_found_and_body_errors_keep_their_status(client):
    assert client.get('/api/tasks/missing').status_code == 404
    headers = {'X-CBC-Request': '1'}
    response = client.patch('/api/instances/missing', headers=headers, content='{broken')
    assert response.status_code == 400
    assert response.json() == {'error': '请求格式不正确。'}
    assert client.patch('/api/instances/missing', headers=headers, content='x' * 16385).status_code == 413
    assert client.patch('/api/instances/missing', json={}).status_code == 403


def test_error_redaction_does_not_stall_other_requests(client, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def safe_error(_error):
        entered.set()
        assert release.wait(3)
        return 'redacted fixture'

    monkeypatch.setattr(core, 'safe_error', safe_error)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        failed = pool.submit(client.get, '/api/subscriptions/invalid.id/nodes')
        try:
            assert entered.wait(1)
            overview = pool.submit(client.get, '/api/tasks').result(timeout=1)
            assert overview.status_code == 200
            assert not failed.done()
        finally:
            release.set()
        result = failed.result(timeout=3)
    assert result.status_code == 400
    assert result.json() == {'error': 'redacted fixture'}
