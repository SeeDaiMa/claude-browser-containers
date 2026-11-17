import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'backend'))

import manager_core
import store
from app.main import app


@pytest.fixture
def subscription(tmp_path, monkeypatch):
    configs = tmp_path / 'configs'
    private = configs / 'private'
    private.mkdir(parents=True)
    for key, value in {
        'ROOT': tmp_path,
        'CONFIGS': configs,
        'PRIVATE': private,
        'LIBRARY': private / 'library.json',
        'EXITS': private / 'exits.json',
        'BACKUPS': configs / 'backups',
    }.items():
        monkeypatch.setattr(store, key, value)
    nodes = [
        {
            'name': '测试节点',
            'type': 'ss',
            'server': '203.0.113.20',
            'port': 443,
            'cipher': 'aes-128-gcm',
            'password': 'fixture-node-secret',
        }
    ]
    url = 'https://sub.example.test/test?token=fixture-sub-token'
    identity = store.add_subscription('旧名称', url, lambda address: nodes)
    directory = configs / 'ifixture'
    directory.mkdir()
    (directory / 'instance.json').write_text(
        json.dumps(
            {
                'name': 'ifixture',
                'id': 'ifixture',
                'display_name': '测试实例',
                'port': 5803,
                'network_index': 9,
                'created_at': None,
                'trashed_at': None,
            }
        )
    )
    node_id = store.public_nodes(identity)[0]['id']
    store.bind_subscription('ifixture', identity, node_id)
    library = store.load_library()
    library['subscriptions'][0].update(updated_at='2026-01-01T00:00:00Z', last_error='保留的刷新错误')
    store._save(store.LIBRARY, library)
    return SimpleNamespace(id=identity, url=url, nodes=nodes, node_id=node_id, configs=configs)


@pytest.fixture
def client(subscription, monkeypatch):
    monkeypatch.setattr(store, 'migrate', lambda: None)
    monkeypatch.setattr(manager_core, 'ensure_worker', lambda: None)

    def forbidden(*args, **kwargs):
        raise AssertionError('改名不能创建后台任务或重启实例')

    monkeypatch.setattr(manager_core, 'enqueue', forbidden)
    with TestClient(app, base_url='http://127.0.0.1:5790') as value:
        yield value


def test_rename_only_changes_name_and_updates_bound_instance(subscription):
    before = store.load_library()['subscriptions'][0]
    cache = store._cache_path(subscription.id)
    cache_before = (cache.read_bytes(), cache.stat().st_mtime_ns)
    selection = subscription.configs / 'selection.json'
    selected_before = selection.read_bytes()
    instance = subscription.configs / 'ifixture' / 'instance.json'
    instance_before = instance.read_bytes()
    result = store.rename_subscription(subscription.id, '  主力线路  ')
    assert result == {'id': subscription.id, 'name': '主力线路'}
    assert store.load_library()['subscriptions'][0] == {**before, 'name': '主力线路'}
    assert (cache.read_bytes(), cache.stat().st_mtime_ns) == cache_before
    assert selection.read_bytes() == selected_before
    assert instance.read_bytes() == instance_before
    proxy = store.proxy_public('ifixture')
    assert proxy['subscription_name'] == '主力线路'
    assert proxy['subscription_id'] == subscription.id and proxy['node_id'] == subscription.node_id


@pytest.mark.parametrize('name', ['', '   ', None, 42, 'a' * 41])
def test_invalid_names_do_not_change_library(subscription, name):
    before = store.LIBRARY.read_bytes()
    with pytest.raises(ValueError, match='订阅名称需要'):
        store.rename_subscription(subscription.id, name)
    assert store.LIBRARY.read_bytes() == before


def test_url_update_keeps_identity_and_matching_binding(subscription, monkeypatch):
    new_url = 'https://sub.example.test/rotated?token=new-fixture-token'
    calls = []

    def fetcher(url):
        calls.append(url)
        return subscription.nodes

    instance_before = (subscription.configs / 'ifixture' / 'instance.json').read_bytes()
    result = store.replace_subscription_url(subscription.id, new_url, fetcher)
    assert result['id'] == subscription.id
    assert 'token' not in json.dumps(result)
    assert calls == [new_url]
    stored = store.load_library()['subscriptions'][0]
    assert stored['id'] == subscription.id and stored['url'] == new_url and stored['name'] == '旧名称'
    proxy = store.proxy_public('ifixture')
    assert proxy['subscription_id'] == subscription.id and proxy['node_id'] == subscription.node_id
    assert 'needs_reconfirm' not in store.selection_map()['ifixture']
    assert (subscription.configs / 'ifixture' / 'instance.json').read_bytes() == instance_before
    before = store.LIBRARY.read_bytes()

    def fail(url):
        raise catalog.CatalogError('订阅下载失败')

    import catalog

    with pytest.raises(ValueError, match='下载失败'):
        store.replace_subscription_url(subscription.id, 'https://sub.example.test/again', fail)
    assert store.LIBRARY.read_bytes() == before


def test_url_update_marks_a_missing_node_for_reconfirm(subscription):
    replacement = [{**subscription.nodes[0], 'password': 'different-fixture-secret'}]
    store.replace_subscription_url(subscription.id, 'https://sub.example.test/other', lambda _url: replacement)
    record = store.selection_map()['ifixture']
    assert record['subscription_id'] == subscription.id
    assert record.get('needs_reconfirm') is True
    assert record.get('node_id') == subscription.node_id


def test_same_name_is_a_noop(subscription, monkeypatch):
    def forbidden(*args):
        raise AssertionError('同名保存不应重写文件')

    monkeypatch.setattr(store, '_save', forbidden)
    assert store.rename_subscription(subscription.id, '旧名称')['name'] == '旧名称'


def test_refresh_keeps_a_name_changed_while_downloading(subscription):
    def fetcher(url):
        assert url == subscription.url
        store.rename_subscription(subscription.id, '刷新期间的新名称')
        return subscription.nodes

    store.refresh_subscription(subscription.id, fetcher)
    assert store.public_subscriptions()[0]['name'] == '刷新期间的新名称'
    assert store.proxy_public('ifixture')['node_id'] == subscription.node_id


def test_patch_returns_only_public_fields(client, subscription):
    response = client.patch(
        f'/api/subscriptions/{subscription.id}', headers={'X-CBC-Request': '1'}, json={'name': '新的订阅名称'}
    )
    assert response.status_code == 200
    assert response.json() == {'id': subscription.id, 'name': '新的订阅名称'}
    assert 'fixture-sub-token' not in response.text and 'fixture-node-secret' not in response.text
    assert store.load_library()['subscriptions'][0]['url'] == subscription.url
    assert client.get('/api/subscriptions').json()['subscriptions'][0]['name'] == '新的订阅名称'


def test_patch_keeps_local_request_protection(client, subscription):
    path = f'/api/subscriptions/{subscription.id}'
    assert client.patch(path, json={'name': '不可保存'}).status_code == 403
    assert (
        client.patch(
            path, headers={'X-CBC-Request': '1', 'Origin': 'https://external.example.test'}, json={'name': '不可保存'}
        ).status_code
        == 403
    )
    assert store.public_subscriptions()[0]['name'] == '旧名称'


def test_patch_rejects_missing_subscription_and_missing_name(client, subscription):
    headers = {'X-CBC-Request': '1'}
    assert client.patch('/api/subscriptions/missing', headers=headers, json={'name': '不存在'}).status_code == 400
    assert client.patch(f'/api/subscriptions/{subscription.id}', headers=headers, json={}).status_code == 400
    assert store.public_subscriptions()[0]['name'] == '旧名称'
