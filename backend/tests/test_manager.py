import asyncio
import json
import queue
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import catalog
import cbc
import manager_core
import store
from app.main import RequestBodyError, hide_validation_error, hide_body_error


BLOCK = """
proxies:
  - name: alpha
    type: ss
    server: 1.2.3.4
    port: 443
    cipher: aes-128-gcm
    password: secret-alpha
  - name: beta
    type: hysteria2
    server: 5.6.7.8
    port: 443
    password: secret-beta
"""

FLOW = """
proxies:
  - {name: gamma, type: anytls, server: example.com, port: 443, password: secret-gamma, udp: true}
"""

JSON_SUB = json.dumps({'proxies': [{'name': 'delta', 'type': 'vless', 'server': '9.9.9.9', 'port': 443, 'uuid': 'secret-uuid'}]})


def test_yaml_json_and_flow_parse():
    block = catalog.proxies_from_clash(BLOCK)
    flow = catalog.proxies_from_clash(FLOW)
    parsed = catalog.proxies_from_clash(JSON_SUB)
    assert [node['name'] for node in block] == ['alpha', 'beta']
    assert flow[0]['udp'] is True
    assert parsed[0]['uuid'] == 'secret-uuid'
    assert catalog.support_of(block[0])[0] is True
    supported, reason = catalog.support_of(block[1])
    assert supported is False
    assert 'UDP' in reason


def test_same_config_keeps_id_when_order_changes(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    nodes = catalog.proxies_from_clash(BLOCK)
    first = store._node_id('sub1', nodes[0])
    second = store._node_id('sub1', catalog.proxies_from_clash(BLOCK)[0])
    assert first == second
    renamed = dict(nodes[0])
    renamed['name'] = 'alpha-renamed'
    assert store._node_id('sub1', renamed) == first
    changed = dict(nodes[0])
    changed['password'] = 'other-secret'
    assert store._node_id('sub1', changed) != first


def test_refresh_failure_keeps_cache_and_public_view_hides_secrets(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    store.add_subscription('测试', 'https://example.test/sub', lambda _url: catalog.proxies_from_clash(BLOCK))
    subscription_id = store.public_subscriptions()[0]['id']
    before = store.load_cached_nodes(subscription_id)

    def fail(_url):
        raise catalog.CatalogError('订阅下载失败：TimeoutError')

    try:
        store.refresh_subscription(subscription_id, fail)
    except ValueError as error:
        assert '保留' in str(error)
    assert store.load_cached_nodes(subscription_id) == before
    public = json.dumps(store.public_nodes(subscription_id), ensure_ascii=False)
    assert 'secret-alpha' not in public
    assert 'server' not in public
    duplicate = catalog.proxies_from_clash(BLOCK + '\n  - name: alpha\n    type: vmess\n    server: 8.8.8.8\n    port: 10086\n    uuid: another-secret\n    alterId: 0\n    cipher: auto\n')
    store._store_nodes(subscription_id, duplicate)
    names = [item['name'] for item in store.public_nodes(subscription_id)]
    assert names.count('alpha') == 2


def test_missing_node_requires_reconfirm(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    (tmp_path / 'configs').mkdir(exist_ok=True)
    slug = 'idemo'
    instance = tmp_path / 'configs' / slug
    instance.mkdir()
    (instance / 'instance.json').write_text(json.dumps({'name': slug, 'port': 5803, 'network_index': 9}))
    store.add_subscription('测试', 'https://example.test/sub', lambda _url: catalog.proxies_from_clash(FLOW))
    subscription_id = store.public_subscriptions()[0]['id']
    node_id = store.public_nodes(subscription_id)[0]['id']
    store.bind_subscription(slug, subscription_id, node_id)
    store._store_nodes(subscription_id, catalog.proxies_from_clash(BLOCK))
    store._mark_missing_bindings(subscription_id, [])
    try:
        store.materialize_node(subscription_id, node_id)
        raise AssertionError('应该要求重新选择')
    except ValueError as error:
        assert '重新选择' in str(error)


def test_validation_error_has_no_input():
    response = asyncio.run(hide_validation_error(None, Exception('password=secret-value')))
    body = response.body.decode()
    assert '请求参数不正确' in body
    assert 'secret-value' not in body


def test_exit_observation_reads_country_from_same_response(monkeypatch):
    calls = []
    monkeypatch.setattr(cbc, 'addresses', lambda name: {'BRIDGE_IP': '10.231.1.3'})

    def docker(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='ip=198.51.100.20\nloc=GB\ncolo=NRT\n')

    monkeypatch.setattr(cbc, 'docker', docker)
    assert cbc.observe_exit_info('fixture') == {'ip': '198.51.100.20', 'country_code': 'GB'}
    assert len(calls) == 1
    assert '--proxy' in calls[0]
    assert calls[0][-1] == 'https://claude.ai/cdn-cgi/trace'


def test_exit_observation_uses_claude_when_other_targets_have_different_exits(monkeypatch):
    monkeypatch.setattr(cbc, 'addresses', lambda name: {'BRIDGE_IP': '10.231.1.3'})

    def docker(*args, **kwargs):
        # 各站可见的出口不同，管理台必须直接采用 Claude 返回的 IP。
        traces = {
            'https://www.cloudflare.com/cdn-cgi/trace': 'ip=198.51.100.10\nloc=HK\n',
            'https://one.one.one.one/cdn-cgi/trace': 'ip=203.0.113.5\nloc=US\n',
            'https://claude.ai/cdn-cgi/trace': 'ip=198.51.100.20\nloc=GB\n',
        }
        assert args[:3] == ('exec', 'cbc-fixture-guard', 'curl')
        assert args[args.index('--proxy') + 1] == 'http://10.231.1.3:8080'
        return SimpleNamespace(returncode=0, stdout=traces[args[-1]])

    monkeypatch.setattr(cbc, 'docker', docker)
    assert cbc.observe_exit_info('fixture') == {'ip': '198.51.100.20', 'country_code': 'GB'}


def test_exit_observation_does_not_substitute_another_site_when_claude_fails(monkeypatch):
    monkeypatch.setattr(cbc, 'addresses', lambda name: {'BRIDGE_IP': '10.231.1.3'})
    calls = []

    def docker(*args, **kwargs):
        calls.append(args[-1])
        if args[-1] == 'https://api.ipify.org':
            return SimpleNamespace(returncode=0, stdout='203.0.113.5')
        return SimpleNamespace(returncode=28, stdout='')

    monkeypatch.setattr(cbc, 'docker', docker)
    assert cbc.observe_exit_info('fixture') == {'ip': None, 'country_code': None}
    assert calls == ['https://claude.ai/cdn-cgi/trace']


def test_exit_observation_rejects_invalid_claude_ip(monkeypatch):
    monkeypatch.setattr(cbc, 'addresses', lambda name: {'BRIDGE_IP': '10.231.1.3'})

    def docker(*args, **kwargs):
        text = '203.0.113.5' if args[-1] == 'https://api.ipify.org' else 'ip=invalid\nloc=JP\n'
        return SimpleNamespace(returncode=0, stdout=text)

    monkeypatch.setattr(cbc, 'docker', docker)
    assert cbc.observe_exit_info('fixture') == {'ip': None, 'country_code': None}


def test_exit_observation_ignores_invalid_country(monkeypatch):
    monkeypatch.setattr(cbc, 'addresses', lambda name: {'BRIDGE_IP': '10.231.1.3'})
    for code in ('XX', 'ZZ', '12', 'not-a-country', '中国'):
        monkeypatch.setattr(cbc, 'docker', lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=f'ip=198.51.100.20\nloc={code}\n'))
        assert cbc.observe_exit_info('fixture') == {'ip': '198.51.100.20', 'country_code': None}


def test_observe_exit_retains_cli_string_contract(monkeypatch):
    monkeypatch.setattr(cbc, 'observe_exit_info', lambda name: {'ip': '198.51.100.20', 'country_code': 'GB'})
    assert cbc.observe_exit('fixture') == '198.51.100.20'


def test_save_exit_keeps_country_with_the_corresponding_ip(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    store.save_exit('fixture', 'ok', '198.51.100.20', '', 'GB')
    latest = store.save_exit('fixture', 'ok', '203.0.113.5', '', 'JP')
    assert store.load_exits()['fixture'] == latest
    assert latest['ip'] == '203.0.113.5' and latest['country_code'] == 'JP'
    unknown = store.save_exit('fixture', 'ok', '203.0.113.6', '')
    assert unknown['country_code'] is None
    failed = store.save_exit('fixture', 'failed', None, '检测失败', 'JP')
    assert failed['country_code'] is None


def test_probe_success_does_not_require_country_metadata(monkeypatch):
    monkeypatch.setattr(cbc, 'addresses', lambda name: {'BRIDGE_IP': '10.231.1.3'})
    monkeypatch.setattr(cbc, 'docker', lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(cbc, 'observe_exit_info', lambda name: {'ip': '203.0.113.5', 'country_code': None})
    result = manager_core.probe_proxy('fixture')
    assert result['status'] == 'ok'
    assert result['ip'] == '203.0.113.5'
    assert result['country_code'] is None


def test_settings_exposes_only_public_configuration(tmp_path, monkeypatch):
    from app import main
    monkeypatch.setattr(main, 'ROOT', tmp_path)
    monkeypatch.setattr(cbc, 'resource_env', lambda: {
        'BROWSER_MEMORY_MIB': '1536', 'BROWSER_CPUS': '1.5',
        'PROXY_MEMORY_MIB': '256', 'GUARD_MEMORY_MIB': '64',
    })
    (tmp_path / 'images.lock.json').write_text(json.dumps({
        'browser': {'tag': 'cbc-chromium:1', 'reference': 'internal-digest'},
        'network': {'tag': 'cbc-network:1'},
    }))
    result = main.settings()
    assert result == {
        'version': '1.0.0',
        'resources': {'browser_memory_mib': 1536.0, 'browser_cpus': 1.5, 'proxy_memory_mib': 256.0, 'guard_memory_mib': 64.0},
        'images': ['cbc-chromium:1', 'cbc-network:1'],
        'data_directory': str(tmp_path / 'data'),
        'config_directory': str(tmp_path / 'configs'),
    }
    assert 'internal-digest' not in json.dumps(result)


def test_refresh_task_exposes_target_without_payload():
    task = {
        'id': 'trefresh', 'kind': 'refresh', 'instance_id': '',
        'state': 'queued', 'phase': '刷新订阅', 'phases': [], 'error': '', 'result': {},
        'payload': {'subscription_id': 'sfixture', 'url': 'private-url', 'password': 'private-password'},
    }
    public = manager_core.public_task(task)
    assert public['subscription_id'] == 'sfixture'
    assert 'payload' not in public
    assert 'private-' not in json.dumps(public)


def test_socks_and_subscription_bindings_replace_each_other(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    (tmp_path / 'configs').mkdir(exist_ok=True)
    slug = 'idemo'
    instance = tmp_path / 'configs' / slug
    instance.mkdir()
    (instance / 'instance.json').write_text(json.dumps({'name': slug, 'port': 5803, 'network_index': 9}))
    store.add_subscription('测试', 'https://example.test/sub', lambda _url: catalog.proxies_from_clash(FLOW))
    subscription_id = store.public_subscriptions()[0]['id']
    node_id = store.public_nodes(subscription_id)[0]['id']
    store.bind_socks(slug, '192.0.2.1', 1080, 'user', 'secret-pass')
    assert store.proxy_public(slug)['mode'] == 'socks'
    assert 'secret-pass' not in json.dumps(store.proxy_public(slug))
    store.bind_subscription(slug, subscription_id, node_id)
    public = store.proxy_public(slug)
    assert public['mode'] == 'subscription'
    assert public['node_id'] == node_id
    assert store.read_proxy(slug)['password'] == ''


def test_duplicate_request_id_returns_the_same_task(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    _isolate_queue(monkeypatch)
    monkeypatch.setattr(manager_core, '_persist_tasks', lambda: None)
    payload = {'subscription_id': 'x', 'password': 'secret-pass'}
    first = manager_core.enqueue('refresh', request_id='same', payload=payload)
    second = manager_core.enqueue('refresh', request_id='same', payload=dict(payload))
    assert first['id'] == second['id']
    assert 'payload' not in first
    assert 'secret-pass' not in json.dumps(first)
    try:
        manager_core.enqueue('refresh', request_id='same', payload={'subscription_id': 'y', 'password': 'other-secret'})
        raise AssertionError('不同内容的重复请求应该拒绝')
    except ValueError as error:
        assert '不一致' in str(error)


def _redirect(tmp_path, monkeypatch):
    configs = tmp_path / 'configs'
    private = configs / 'private'
    private.mkdir(parents=True)
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    monkeypatch.setattr(store, 'CONFIGS', configs)
    monkeypatch.setattr(store, 'PRIVATE', private)
    monkeypatch.setattr(store, 'LIBRARY', private / 'library.json')
    monkeypatch.setattr(store, 'EXITS', private / 'exits.json')
    monkeypatch.setattr(store, 'BACKUPS', configs / 'backups')


def _isolate_queue(monkeypatch):
    monkeypatch.setattr(manager_core, '_QUEUE', queue.Queue())
    monkeypatch.setattr(manager_core, '_TASKS', {})
    monkeypatch.setattr(manager_core, '_ORDER', [])
    monkeypatch.setattr(manager_core, '_SUBSCRIPTION_QUEUE', queue.Queue())


def test_static_route_rejects_escape(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main

    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / 'index.html').write_text('index-page')
    (dist / 'app.js').write_text('alert(1)')
    outside = tmp_path / 'review-canary.txt'
    outside.write_text('CBC_REVIEW_CANARY_NON_SECRET')
    link = dist / 'escape'
    link.symlink_to(outside)
    monkeypatch.setattr(main, 'DIST', dist)
    monkeypatch.setattr(store, 'migrate', lambda: None)
    monkeypatch.setattr(manager_core, 'ensure_worker', lambda: None)
    with TestClient(main.app, base_url='http://127.0.0.1:5790') as client:
        escaped = client.get('/%2e%2e/review-canary.txt')
        linked = client.get('/escape')
        asset = client.get('/app.js')
        missing = client.get('/instance/demo')
    absolute = main.frontend(str(outside))
    assert escaped.status_code == 404
    assert 'CBC_REVIEW_CANARY_NON_SECRET' not in escaped.text
    assert absolute.status_code == 404
    assert outside.read_text() not in absolute.body.decode()
    assert main._static_target('../review-canary.txt') == 'reject'
    assert linked.status_code == 404
    assert 'CBC_REVIEW_CANARY_NON_SECRET' not in linked.text
    assert asset.status_code == 200 and asset.text == 'alert(1)'
    assert missing.status_code == 200 and missing.text == 'index-page'


def test_malformed_json_is_client_error():
    bad = asyncio.run(hide_body_error(None, RequestBodyError('请求格式不正确。', 400)))
    huge = asyncio.run(hide_body_error(None, RequestBodyError('请求过大。', 413)))
    assert bad.status_code == 400 and '请求格式不正确' in bad.body.decode()
    assert huge.status_code == 413 and '请求过大' in huge.body.decode()


def test_malformed_json_route(monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main

    monkeypatch.setattr(store, 'migrate', lambda: None)
    monkeypatch.setattr(manager_core, 'ensure_worker', lambda: None)
    headers = {'Content-Type': 'application/json', 'X-CBC-Request': '1', 'Origin': 'http://127.0.0.1:5790'}
    with TestClient(main.app, base_url='http://127.0.0.1:5790') as client:
        broken = client.post('/api/instances', content=b'{bad json', headers=headers)
        array = client.post('/api/instances', content=b'[]', headers=headers)
        huge = client.post('/api/instances', content=b'x' * 16385, headers=headers)
    assert broken.status_code == 400
    assert array.status_code == 400
    assert huge.status_code == 413
    assert 'x' * 40 not in huge.text


def test_migration_backs_up_each_instance_before_change(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    configs = tmp_path / 'configs'
    for slug, port in (('claude-a', 5801), ('claude-b', 5802)):
        directory = configs / slug
        directory.mkdir()
        (directory / 'instance.json').write_text(json.dumps({'name': slug, 'port': port, 'network_index': port - 5800}))
    store.migrate()
    backups = list((configs / 'backups').rglob('instance.json'))
    assert len(backups) == 2
    originals = {path.relative_to(configs / 'backups').parts[-2]: json.loads(path.read_text()) for path in backups}
    assert set(originals) == {'claude-a', 'claude-b'}
    assert 'display_name' not in originals['claude-a']
    assert 'display_name' not in originals['claude-b']
    assert json.loads((configs / 'claude-a' / 'instance.json').read_text())['port'] == 5801
    assert json.loads((configs / 'claude-b' / 'instance.json').read_text())['display_name'] == 'claude-b'


def _instance(tmp_path, slug='idemo', **extra):
    directory = tmp_path / 'configs' / slug
    directory.mkdir(parents=True, exist_ok=True)
    raw = {'name': slug, 'id': slug, 'display_name': 'demo', 'port': 5803, 'network_index': 9, **extra}
    (directory / 'instance.json').write_text(json.dumps(raw))
    return store.instance_by_id(slug)


def test_locked_instance_rejects_proxy_change(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    row = _instance(tmp_path, proxy_locked_at='2026-11-14T00:00:00+00:00')
    calls = []
    monkeypatch.setattr(manager_core, '_write_binding', lambda *args: calls.append('save'))
    monkeypatch.setattr(cbc, 'compose', lambda *args, **kwargs: calls.append('compose'))
    task = {'phases': manager_core._phase_list('apply-proxy'), 'phase': '保存配置', 'result': {}}
    try:
        manager_core._apply_proxy(task, row, {'mode': 'socks', 'host': '192.0.2.9', 'port': 1080, 'username': 'u', 'password': 'p'})
        raise AssertionError('锁定后的实例不应该能换代理')
    except ValueError as error:
        assert '已锁定' in str(error)
    assert calls == []


def test_locked_instance_rejects_proxy_route(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    _redirect(tmp_path, monkeypatch)
    _isolate_queue(monkeypatch)
    _instance(tmp_path, proxy_locked_at='2026-11-14T00:00:00+00:00')
    monkeypatch.setattr(main.store, 'migrate', lambda: None)
    monkeypatch.setattr(main.manager_core, 'ensure_worker', lambda: None)
    with TestClient(main.app, base_url='http://127.0.0.1:5790') as client:
        response = client.post(
            '/api/instances/idemo/proxy',
            headers={'X-CBC-Request': '1'},
            json={'mode': 'socks', 'host': '192.0.2.9', 'port': 1080, 'username': 'u', 'password': 'p'},
        )
        removed = client.post('/api/instances/idemo/proxy/restore', headers={'X-CBC-Request': '1'})
    assert response.status_code == 400
    assert '已锁定' in response.json()['error']
    assert manager_core._TASKS == {}
    assert removed.status_code != 200


def test_unlocked_instance_only_saves_binding(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    row = _instance(tmp_path, proxy_locked_at=None)
    calls = []
    monkeypatch.setattr(cbc, 'active', lambda slug: False)
    monkeypatch.setattr(manager_core, '_write_binding', lambda slug, payload: calls.append(('save', slug)))
    monkeypatch.setattr(cbc, 'compose', lambda *args, **kwargs: calls.append('compose'))
    monkeypatch.setattr(cbc, 'prepare', lambda *args, **kwargs: calls.append('prepare'))
    task = {'phases': manager_core._phase_list('apply-proxy'), 'phase': '保存配置', 'result': {}}
    manager_core._apply_proxy(task, row, {'mode': 'socks', 'host': '192.0.2.9', 'port': 1080, 'username': 'u', 'password': 'p'})
    assert calls == [('save', 'idemo')]
    assert task['result']['applied'] is False


def test_unlocked_running_instance_must_stop_first(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    row = _instance(tmp_path, proxy_locked_at=None)
    monkeypatch.setattr(cbc, 'active', lambda slug: True)
    monkeypatch.setattr(manager_core, '_write_binding', lambda *args: (_ for _ in ()).throw(AssertionError('不应保存')))
    task = {'phases': manager_core._phase_list('apply-proxy'), 'phase': '保存配置', 'result': {}}
    try:
        manager_core._apply_proxy(task, row, {'mode': 'socks', 'host': '192.0.2.9', 'port': 1080, 'username': 'u', 'password': 'p'})
        raise AssertionError('运行中的实例应先停止')
    except ValueError as error:
        assert '先停止' in str(error)


def test_successful_boot_locks_proxy(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    _instance(tmp_path, proxy_locked_at=None)
    monkeypatch.setattr(cbc, 'settings', lambda slug: {'trashed_at': None})
    monkeypatch.setattr(cbc, 'active', lambda slug: False)
    monkeypatch.setattr(cbc, 'prepare', lambda *args, **kwargs: None)
    monkeypatch.setattr(cbc, 'compose', lambda *args, **kwargs: None)
    monkeypatch.setattr(manager_core, '_require_docker', lambda: None)
    monkeypatch.setattr(manager_core, '_wait_ui', lambda slug: None)
    task = {'phases': manager_core._phase_list('start'), 'phase': '检查环境', 'result': {}}
    manager_core._boot(task, 'idemo', verify=False)
    assert store.proxy_locked(store.instance_by_id('idemo')['raw'])


def test_failed_boot_keeps_proxy_editable(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    _instance(tmp_path, proxy_locked_at=None)
    monkeypatch.setattr(cbc, 'settings', lambda slug: {'trashed_at': None})
    monkeypatch.setattr(cbc, 'active', lambda slug: False)
    monkeypatch.setattr(cbc, 'prepare', lambda *args, **kwargs: None)
    monkeypatch.setattr(cbc, 'compose', lambda *args, **kwargs: None)
    monkeypatch.setattr(manager_core, '_require_docker', lambda: None)
    monkeypatch.setattr(manager_core, 'probe_proxy', lambda slug: {'status': 'failed', 'ip': None, 'detail': 'x'})
    task = {'phases': manager_core._phase_list('start'), 'phase': '检查环境', 'result': {}}
    try:
        manager_core._boot(task, 'idemo', verify=True)
        raise AssertionError('代理失败应该抛出')
    except RuntimeError:
        pass
    assert not store.proxy_locked(store.instance_by_id('idemo')['raw'])


def test_cli_node_selection_is_removed(tmp_path, monkeypatch):
    assert not hasattr(cbc, 'use_node')
    assert not hasattr(cbc, 'load_nodes')
    scripts = Path(cbc.__file__).resolve().parent
    assert not (scripts / 'use-node.sh').exists()
    assert not (scripts / 'nodes.sh').exists()


def test_binding_without_node_id_must_be_reconfirmed_in_web(tmp_path, monkeypatch):
    monkeypatch.setattr(cbc, 'selection_map', lambda: {'claude-x': {'mode': 'global', 'node': 'alpha'}})
    try:
        cbc.node_upstream('claude-x')
        raise AssertionError('缺少节点 ID 的旧记录不能再按名称查旧缓存')
    except ValueError as error:
        assert '网页' in str(error)
    monkeypatch.setattr(cbc, 'selection_map', lambda: {})
    assert cbc.node_upstream('claude-x') is None


def _rotated_subscription(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    _instance(tmp_path, proxy_locked_at='2026-11-14T00:00:00+00:00')
    store.add_subscription('测试', 'https://example.test/sub', lambda _url: catalog.proxies_from_clash(BLOCK))
    subscription_id = store.public_subscriptions()[0]['id']
    alpha = next(node for node in store.public_nodes(subscription_id) if node['name'] == 'alpha')
    store.bind_subscription('idemo', subscription_id, alpha['id'])
    return subscription_id, alpha['id']


def test_reconfirm_binds_same_name_node_after_password_rotation(tmp_path, monkeypatch):
    subscription_id, old_id = _rotated_subscription(tmp_path, monkeypatch)
    rotated = BLOCK.replace('secret-alpha', 'secret-alpha-2')
    store.refresh_subscription(subscription_id, lambda _url: catalog.proxies_from_clash(rotated))
    assert store.proxy_public('idemo')['stale'] is True
    result = manager_core.reconfirm_node('idemo')
    public = store.proxy_public('idemo')
    assert result['node'] == 'alpha'
    assert public['stale'] is False
    assert public['label'] == 'alpha'
    assert public['node_id'] != old_id
    assert 'secret-alpha' not in json.dumps(result)


def test_reconfirm_refuses_when_same_name_missing(tmp_path, monkeypatch):
    subscription_id, old_id = _rotated_subscription(tmp_path, monkeypatch)
    renamed = BLOCK.replace('name: alpha', 'name: alpha-new').replace('secret-alpha', 'secret-alpha-2')
    store.refresh_subscription(subscription_id, lambda _url: catalog.proxies_from_clash(renamed))
    try:
        manager_core.reconfirm_node('idemo')
        raise AssertionError('没有同名节点时不能换成别的节点')
    except ValueError as error:
        assert '同名' in str(error)
    assert store.selection_map()['idemo']['node_id'] == old_id


def test_reconfirm_refuses_when_node_unchanged(tmp_path, monkeypatch):
    _rotated_subscription(tmp_path, monkeypatch)
    try:
        manager_core.reconfirm_node('idemo')
        raise AssertionError('节点没变时不需要重新确认')
    except ValueError as error:
        assert '不需要' in str(error)


def test_migration_locks_started_instances_only(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    configs = tmp_path / 'configs'
    rows = {
        'claude-a': {'name': 'claude-a', 'port': 5801, 'network_index': 1},
        'iused': {'name': 'iused', 'id': 'iused', 'display_name': '用过', 'created_at': '2026-11-13T00:00:00+00:00', 'port': 5803, 'network_index': 3},
        'ifresh': {'name': 'ifresh', 'id': 'ifresh', 'display_name': '新建失败', 'created_at': '2026-11-13T00:00:00+00:00', 'port': 5804, 'network_index': 4},
    }
    for slug, raw in rows.items():
        (configs / slug).mkdir()
        (configs / slug / 'instance.json').write_text(json.dumps(raw))
    (tmp_path / 'data' / 'iused' / '.config').mkdir(parents=True)
    (tmp_path / 'data' / 'ifresh').mkdir(parents=True)
    store.migrate()
    locked = {slug: store.proxy_locked(store.instance_by_id(slug)['raw']) for slug in rows}
    assert locked == {'claude-a': True, 'iused': True, 'ifresh': False}


def test_rename_during_trash_is_not_lost(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    row = _instance(tmp_path)
    monkeypatch.setattr(cbc, 'active', lambda slug: True)
    monkeypatch.setattr(cbc, 'down', lambda *args, **kwargs: None)
    # 关闭浏览器期间用户改名：模拟 API 线程在任务读取 row 之后写入。
    monkeypatch.setattr(cbc, 'close_browser', lambda slug: manager_core.rename_instance('idemo', '新名字'))
    task = {'phases': manager_core._phase_list('trash'), 'phase': '关闭浏览器', 'result': {}}
    manager_core._trash(task, row)
    raw = store.instance_by_id('idemo')['raw']
    assert raw['display_name'] == '新名字'
    assert raw['trashed_at']


def test_purge_forgets_selection_and_exit(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    row = _instance(tmp_path, trashed_at='2026-11-14T00:00:00+00:00')
    store._write_selection({'idemo': {'node': 'alpha'}, 'other': {'node': 'beta'}})
    store.save_exit('idemo', 'ok', '192.0.2.1', '')
    store.save_exit('other', 'ok', '192.0.2.2', '')
    monkeypatch.setattr(cbc, 'active', lambda slug: False)
    monkeypatch.setattr(cbc, 'down', lambda *args, **kwargs: None)
    monkeypatch.setattr(cbc, 'CONFIGS', tmp_path / 'configs')
    monkeypatch.setattr(cbc, 'ROOT', tmp_path)
    task = {'phases': manager_core._phase_list('purge'), 'phase': '彻底删除', 'result': {}}
    manager_core._purge(task, store.instance_by_id('idemo'), 'demo')
    assert set(store.selection_map()) == {'other'}
    assert set(store.load_exits()) == {'other'}
    assert not (tmp_path / 'configs' / 'idemo').exists()


def test_finished_task_drops_secret_payload(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    _isolate_queue(monkeypatch)
    monkeypatch.setattr(manager_core, '_persist_tasks', lambda: None)
    monkeypatch.setattr(cbc, 'ROOT', tmp_path)
    monkeypatch.setattr(manager_core, '_dispatch', lambda task: None)
    payload = {'mode': 'socks', 'password': 'secret-pass', 'display_name': 'x'}
    task = manager_core.enqueue('create', request_id='req-1', payload=payload)
    worker = threading.Thread(target=manager_core._worker, daemon=True)
    worker.start()
    for _ in range(200):
        if manager_core._TASKS[task['id']]['state'] == 'succeeded' and not manager_core._TASKS[task['id']]['payload']:
            break
        threading.Event().wait(0.01)
    internal = manager_core._TASKS[task['id']]
    assert internal['payload'] == {}
    assert 'secret-pass' not in json.dumps(internal)
    # 原 payload 清空后，同一请求重试仍然识别为同一个任务。
    internal['instance_id'] = 'icreated'
    again = manager_core.enqueue('create', request_id='req-1', payload=dict(payload))
    assert again['id'] == task['id']


def test_old_finished_tasks_are_pruned(tmp_path, monkeypatch):
    _isolate_queue(monkeypatch)
    monkeypatch.setattr(manager_core, '_persist_tasks', lambda: None)
    created = [manager_core.enqueue('refresh', payload={'subscription_id': str(index)})['id'] for index in range(60)]
    for task_id in created:
        manager_core._TASKS[task_id]['state'] = 'succeeded'
    manager_core.enqueue('refresh', payload={'subscription_id': 'last'})
    assert len(manager_core._TASKS) == manager_core._KEEP_TASKS + 1
    assert set(manager_core._ORDER) == set(manager_core._TASKS)
    assert created[0] not in manager_core._TASKS


def test_start_closes_before_browser_recreate(monkeypatch):
    monkeypatch.setattr(store, 'lock_proxy', lambda slug: None)
    calls = []
    state = {'running': True}

    def active(_slug):
        return state['running']

    def close(_slug):
        calls.append('close')
        state['running'] = False

    monkeypatch.setattr(cbc, 'settings', lambda slug: {'trashed_at': None})
    monkeypatch.setattr(cbc, 'active', active)
    monkeypatch.setattr(cbc, 'browser_would_change', lambda slug, cdp=False: True)
    monkeypatch.setattr(cbc, 'close_browser', close)
    monkeypatch.setattr(cbc, 'prepare', lambda *args, **kwargs: calls.append('prepare'))
    monkeypatch.setattr(cbc, 'compose', lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(manager_core, '_require_docker', lambda: None)
    monkeypatch.setattr(manager_core, 'probe_proxy', lambda slug: {'status': 'ok', 'ip': '192.0.2.9', 'detail': ''})
    monkeypatch.setattr(manager_core, '_require_same_country', lambda slug, observation: None)
    monkeypatch.setattr(store, 'save_exit', lambda *args: None)
    monkeypatch.setattr(manager_core, '_wait_ui', lambda slug: None)
    task = {'phases': manager_core._phase_list('start'), 'phase': '检查环境', 'result': {}}
    manager_core._boot(task, 'idemo', verify=True)
    assert calls.index('close') < calls.index('prepare')
    browser_ups = [args for args in calls if isinstance(args, tuple) and 'browser' in args]
    assert browser_ups
    assert calls.index('close') < calls.index(browser_ups[0])


def test_unchanged_running_browser_is_not_recreated(monkeypatch):
    monkeypatch.setattr(store, 'lock_proxy', lambda slug: None)
    calls = []
    monkeypatch.setattr(cbc, 'settings', lambda slug: {'trashed_at': None})
    monkeypatch.setattr(cbc, 'active', lambda slug: True)
    monkeypatch.setattr(cbc, 'browser_would_change', lambda slug, cdp=False: False)
    monkeypatch.setattr(cbc, 'close_browser', lambda slug: calls.append('close'))
    monkeypatch.setattr(cbc, 'prepare', lambda *args, **kwargs: calls.append('prepare'))
    monkeypatch.setattr(cbc, 'compose', lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(manager_core, '_require_docker', lambda: None)
    monkeypatch.setattr(manager_core, 'probe_proxy', lambda slug: {'status': 'ok', 'ip': '192.0.2.9', 'detail': ''})
    monkeypatch.setattr(manager_core, '_require_same_country', lambda slug, observation: None)
    monkeypatch.setattr(store, 'save_exit', lambda *args: None)
    monkeypatch.setattr(manager_core, '_wait_ui', lambda slug: None)
    task = {'phases': manager_core._phase_list('start'), 'phase': '检查环境', 'result': {}}
    manager_core._boot(task, 'idemo', verify=True)
    assert 'close' not in calls
    assert all('browser' not in args for args in calls if isinstance(args, tuple))


def test_purge_rejects_instance_not_in_trash():
    row = {'slug': 'idemo', 'display_name': 'demo', 'trashed_at': None}
    task = {'phases': manager_core._phase_list('purge'), 'phase': '彻底删除', 'result': {}}
    try:
        manager_core._purge(task, row, 'demo')
        raise AssertionError('未进回收站的实例不应该被删除')
    except ValueError as error:
        assert '不在回收站' in str(error)


def test_concurrent_writers_do_not_lose_increments(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    path = store.EXITS
    store._save(path, {'n': 0})
    errors = []

    def bump():
        try:
            with store.exclusive(path):
                current = json.loads(path.read_text())
                current['n'] += 1
                store._save(path, current)
        except Exception as error:
            errors.append(type(error).__name__)

    threads = [threading.Thread(target=bump) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert json.loads(path.read_text())['n'] == 8


def test_memory_limit_uses_running_config_not_a_constant(tmp_path, monkeypatch):
    runtime = tmp_path / 'configs' / 'idemo' / 'runtime'
    runtime.mkdir(parents=True)
    (runtime / 'compose.env').write_text('BROWSER_MEMORY_MIB=100\nPROXY_MEMORY_MIB=20\nGUARD_MEMORY_MIB=5\n')
    monkeypatch.setattr(cbc, 'CONFIGS', tmp_path / 'configs')
    manager_core._LIMIT_CACHE.clear()
    assert manager_core._memory_limits('idemo') == {'browser': 100, 'proxy': 20, 'guard': 5}
    monkeypatch.setattr(manager_core, '_compose_limits', lambda slug: {})
    manager_core._LIMIT_CACHE.clear()
    assert manager_core._memory_limits('idemo') == {}


def test_host_memory_separates_vm_and_helpers(monkeypatch):
    class Result:
        stdout = '\n'.join([
            '9200 /Applications/Docker.app/Contents/MacOS/com.docker.virtualization',
            '847152 /System/Library/Frameworks/Virtualization.framework/Versions/A/XPCServices/com.apple.Virtualization.VirtualMachine.xpc/Contents/MacOS/com.apple.Virtualization.VirtualMachine',
            '135184 /Applications/Docker.app/Contents/MacOS/com.docker.backend',
            '5600 /Library/Java/JavaVirtualMachines/zulu-17.jdk/Contents/Home/bin/java',
        ]) + '\n'

    import subprocess
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: Result())
    report = manager_core._host_memory()
    assert report['docker_vm_rss_mib'] == round((9200 + 847152) / 1024, 1)
    assert report['docker_helper_rss_mib'] == round(135184 / 1024, 1)
