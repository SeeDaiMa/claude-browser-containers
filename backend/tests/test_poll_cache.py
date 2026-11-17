"""轮询接口的节点摘要缓存：文件不变时不重复解析，任何写入后立即反映新内容。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))

import catalog
import store

NODES = """
proxies:
  - {name: alpha, type: ss, server: 1.2.3.4, port: 443, cipher: aes-128-gcm, password: secret-alpha}
  - {name: beta, type: ss, server: 5.6.7.8, port: 443, cipher: aes-128-gcm, password: secret-beta}
"""


@pytest.fixture
def subscription(tmp_path, monkeypatch):
    configs = tmp_path / 'configs'
    private = configs / 'private'
    private.mkdir(parents=True)
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    monkeypatch.setattr(store, 'CONFIGS', configs)
    monkeypatch.setattr(store, 'PRIVATE', private)
    monkeypatch.setattr(store, 'LIBRARY', private / 'library.json')
    monkeypatch.setattr(store, 'EXITS', private / 'exits.json')
    monkeypatch.setattr(store, 'BACKUPS', configs / 'backups')
    nodes = catalog.proxies_from_clash(NODES)
    subscription_id = store.add_subscription('测试', 'https://example.test/sub', lambda _url: nodes)
    return subscription_id, nodes


def test_unchanged_cache_is_not_parsed_again(subscription, monkeypatch):
    subscription_id, _nodes = subscription
    assert store.public_subscriptions()[0]['node_count'] == 2
    calls = []
    original = store.load_cached_nodes
    monkeypatch.setattr(store, 'load_cached_nodes', lambda sid: calls.append(sid) or original(sid))
    for _ in range(3):
        assert store.public_subscriptions()[0]['node_count'] == 2
    assert calls == []


def test_saved_test_results_show_up_immediately(subscription):
    subscription_id, _nodes = subscription
    assert store.public_subscriptions()[0]['nodes_tested_at'] is None
    node_id = store.load_cached_nodes(subscription_id)[0]['id']
    store.save_node_tests(subscription_id, {node_id: {'status': 'ok', 'delay_ms': 80, 'detail': ''}})
    saved = store.load_cached_nodes(subscription_id)[0]['test']
    assert store.public_subscriptions()[0]['nodes_tested_at'] == saved['checked_at']


def test_binding_turns_stale_after_refresh_drops_the_node(subscription):
    subscription_id, nodes = subscription
    node_id = store.load_cached_nodes(subscription_id)[0]['id']
    store.bind_subscription('ifixture', subscription_id, node_id)
    assert store.proxy_public('ifixture')['stale'] is False
    store.refresh_subscription(subscription_id, lambda _url: nodes[1:])
    assert store.public_subscriptions()[0]['node_count'] == 1
    assert store.proxy_public('ifixture')['stale'] is True


def test_missing_cache_reports_no_nodes(subscription):
    subscription_id, _nodes = subscription
    assert store.public_subscriptions()[0]['node_count'] == 2
    store._cache_path(subscription_id).unlink()
    assert store.public_subscriptions()[0]['node_count'] == 0
