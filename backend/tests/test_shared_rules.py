"""D1 的兼容性测试：只使用临时配置和合成 Docker 返回值。"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

import cbc
import manager_core as core
import store
from test_reliability import isolated as isolated, instance


@pytest.mark.parametrize('name', ['', 'x' * 41, None, 3])
def test_subscription_name_validation_keeps_the_same_error(isolated, name):
    for operation in (lambda: store.validate_subscription(name, 'https://fixture.test/sub'),
                      lambda: store.rename_subscription('missing', name)):
        with pytest.raises(ValueError, match='订阅名称需要 1–40 个字符'):
            operation()


@pytest.mark.parametrize('slug', ['A', '-a', 'a/b', 'a' * 33])
def test_slug_validation_keeps_each_entry_message(isolated, slug):
    with pytest.raises(ValueError, match='实例名须为'):
        cbc.validate_name(slug)
    with pytest.raises(ValueError, match='实例 ID 无效'):
        with store.instance_operation(slug):
            pass


def test_legacy_and_new_subscription_ids_agree_and_empty_proxy_is_fresh(isolated):
    url = 'https://fixture.test/sub'
    store._save(store.CONFIGS / 'subscription.json', {'url': url})
    store._save(store.CONFIGS / 'nodes.json', [])
    store.migrate()
    migrated = store.load_library()['subscriptions'][0]['id']
    store.delete_subscription(migrated)
    assert store.add_subscription('测试', url, lambda _: []) == migrated
    first = store.read_proxy('missing')
    first['password'] = 'do-not-share'
    assert store.read_proxy('missing')['password'] == ''


def test_damaged_records_keep_their_copy_or_move_semantics(isolated):
    copied, moved = store.PRIVATE / 'copied.json', store.PRIVATE / 'moved.json'
    for path in (copied, moved):
        path.write_text('original')
    store._keep_damaged_copy(copied)
    store._keep_damaged_copy(copied)
    core._preserve_damaged_record(moved)
    assert copied.read_text() == 'original'
    assert not moved.exists()
    assert len(list(store.PRIVATE.glob('copied.json.*.damaged'))) == 2
    for path in store.PRIVATE.glob('*.damaged'):
        assert path.read_text() == 'original'
        assert path.stat().st_mode & 0o777 == 0o600


def test_create_skips_overlapping_subnet_with_one_docker_inventory(isolated, monkeypatch):
    store._save(store.CONFIGS / 'proxy.example.json', {'host': '', 'port': 1080})
    commands = []

    def docker(*args, **kwargs):
        commands.append(args[:2])
        assert args[:2] in [('network', 'ls'), ('network', 'inspect')]
        return SimpleNamespace(stdout='fixture-network' if args[1] == 'ls' else json.dumps([
            {'IPAM': {'Config': [{'Subnet': '10.231.1.0/24'}, {'Subnet': '2001:db8::/32'}]}},
        ]))

    monkeypatch.setattr(cbc, 'docker', docker)
    socket = MagicMock()
    monkeypatch.setattr(cbc.socket, 'socket', lambda: socket)
    cbc.create('inew', 5809)
    assert cbc.settings('inew')['network_index'] == 2
    assert commands == [('network', 'ls'), ('network', 'inspect')]


def test_port_conflict_preserves_create_exception_and_allocator_skips_it(isolated, monkeypatch):
    store._save(store.CONFIGS / 'proxy.example.json', {'host': '', 'port': 1080})
    listener = Mock()
    listener.__enter__ = Mock(return_value=listener)
    listener.__exit__ = Mock(return_value=False)
    listener.bind.side_effect = OSError('fixture port occupied')
    monkeypatch.setattr(cbc.socket, 'socket', lambda: listener)
    with pytest.raises(OSError, match='port occupied'):
        cbc.create('inew', 5809)
    assert not (store.CONFIGS / 'inew/instance.json').exists()
    listener.bind.side_effect = [OSError('occupied'), None]
    assert core._allocate_port() == 5804
    cbc.docker.assert_not_called()


def test_subscription_binding_writes_an_independent_empty_proxy(isolated):
    instance(isolated)
    sid = store.add_subscription('测试', 'https://fixture.test/sub', lambda _: [{
        'name': '节点', 'type': 'ss', 'server': '192.0.2.2', 'port': 443,
        'cipher': 'aes-128-gcm', 'password': 'fixture',
    }])
    store.bind_subscription('ifixture', sid, store.load_cached_nodes(sid)[0]['id'])
    assert store.read_proxy('ifixture') == store.read_proxy('missing')
