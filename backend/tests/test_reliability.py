import concurrent.futures
import json
import queue
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import cbc
import manager_core as core
import monitor
import store


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    configs = tmp_path / 'configs'
    private = configs / 'private'
    private.mkdir(parents=True)
    for module in (store, cbc):
        monkeypatch.setattr(module, 'ROOT', tmp_path)
        monkeypatch.setattr(module, 'CONFIGS', configs)
    monkeypatch.setattr(cbc, 'SELECTION', configs / 'selection.json')
    monkeypatch.setattr(store, 'PRIVATE', private)
    monkeypatch.setattr(store, 'LIBRARY', private / 'library.json')
    monkeypatch.setattr(store, 'EXITS', private / 'exits.json')
    monkeypatch.setattr(store, 'BACKUPS', configs / 'backups')
    monkeypatch.setattr(core, '_TASKS', {})
    monkeypatch.setattr(core, '_ORDER', [])
    monkeypatch.setattr(core, '_QUEUE', queue.Queue())
    monkeypatch.setattr(core, '_SUBSCRIPTION_QUEUE', queue.Queue())
    monkeypatch.setattr(core, '_LIMIT_CACHE', {})
    monkeypatch.setattr(core, '_STATS', {'at': 0, 'rows': {}, 'error': ''})
    monkeypatch.setattr(
        core, '_RUNTIME', {'at': 0, 'docker': {'ok': False}, 'containers': None, 'health': {}, 'ids': {}}
    )
    monkeypatch.setattr(core, '_host_memory', Mock(return_value={}))
    monkeypatch.setattr(core, '_self_rss', Mock(return_value=12))
    monkeypatch.setattr(monitor, '_STATE', {})
    monkeypatch.setattr(monitor, '_SERVICE', {'checked_at': None, 'error': ''})
    monkeypatch.setattr(monitor, '_STOP', threading.Event())
    monkeypatch.setattr(cbc, 'docker', Mock(side_effect=AssertionError('单元测试禁止访问实际 Docker')))
    return tmp_path


def instance(root, slug='ifixture', **extra):
    raw = {
        'name': slug,
        'id': slug,
        'display_name': '测试实例',
        'port': 5803,
        'network_index': 9,
        'proxy_locked_at': '2026-11-14T00:00:00Z',
        'desired_running': True,
        'run_generation': 'generation',
        **extra,
    }
    store._save(store.CONFIGS / slug / 'instance.json', raw)
    store._write_proxy(slug, {'host': '192.0.2.9', 'port': 1080, 'username': 'fixture', 'password': 'fixture'})
    data = root / 'data' / slug
    data.mkdir(parents=True)
    (data / 'profile-marker').write_text('preserved')
    return store.instance_by_id(slug)


def task(kind, instance_id='ifixture', **payload):
    return {
        'kind': kind,
        'instance_id': instance_id,
        'payload': payload,
        'result': {},
        'phases': core._phase_list(kind),
        'phase': core._PHASES[kind][0],
    }


def snapshot(slug='ifixture', broken=None, health=None):
    containers = {
        cbc.container(slug, role): 'exited' if role == broken else 'running' for role in ('guard', 'proxy', 'browser')
    }
    return {
        'docker': {'ok': True, 'detail': ''},
        'containers': containers,
        'health': health or {},
        'ids': {name: name + '-id' for name in containers},
    }


@pytest.mark.parametrize(
    'bad',
    ['{invalid', '{}', '[]', '{"port":5804}', '{"port":true,"network_index":2}', '{"port":5804,"network_index":0}'],
)
def test_bad_instance_is_reported_without_breaking_healthy_instance(isolated, monkeypatch, bad):
    instance(isolated)
    path = store.CONFIGS / 'ibad' / 'instance.json'
    path.parent.mkdir()
    path.write_text(bad)
    assert store.instance_by_id('ifixture')['port'] == 5803
    with pytest.raises(ValueError, match='已损坏'):
        store.instance_by_id('ibad')
    monkeypatch.setattr(
        core, 'runtime_snapshot', lambda: {'docker': {'ok': False}, 'containers': None, 'health': {}, 'ids': {}}
    )
    result = core.list_instances()
    assert [row['id'] for row in result['instances']] == ['ifixture']
    assert result['config_errors'][0]['id'] == 'ibad'
    with pytest.raises(ValueError, match='无法安全分配'):
        cbc.create('inew', 5804)
    cbc.docker.assert_not_called()
    assert path.read_text() == bad


@pytest.mark.parametrize(
    'change',
    [
        {'host': ''},
        {'host': 'https://node.example'},
        {'host': 'node.example:1080'},
        {'host': 'bad host'},
        {'port': 0},
        {'port': True},
        {'port': 'not-a-number'},
        {'port': None},
        {'username': ''},
        {'password': 'x' * 256},
        {'mode': 'missing'},
    ],
)
def test_invalid_creation_has_no_instance_side_effect(isolated, monkeypatch, change):
    create = Mock()
    monkeypatch.setattr(cbc, 'create', create)
    payload = {
        'display_name': '测试',
        'mode': 'socks',
        'host': '192.0.2.9',
        'port': 1080,
        'username': 'u',
        'password': 'p',
        **change,
    }
    with pytest.raises(ValueError):
        core._create(task('create', ''), payload)
    create.assert_not_called()
    assert store._instances_on_disk() == []
    assert not (isolated / 'data').exists()


def test_missing_subscription_node_is_rejected_before_create(isolated, monkeypatch):
    store._save(store.LIBRARY, {'subscriptions': [{'id': 'sfixture'}]})
    create = Mock()
    monkeypatch.setattr(cbc, 'create', create)
    with pytest.raises(ValueError, match='节点已消失'):
        core._create(
            task('create', ''),
            {'display_name': '测试', 'mode': 'subscription', 'subscription_id': 'sfixture', 'node_id': 'missing'},
        )
    create.assert_not_called()


def test_valid_creation_retains_config_when_network_boot_fails(isolated, monkeypatch):
    monkeypatch.setattr(core, '_require_docker', lambda: None)
    monkeypatch.setattr(core, '_allocate_port', lambda: 5803)

    def create(slug, port):
        instance(isolated, slug, proxy_locked_at=None, desired_running=False, port=port)

    monkeypatch.setattr(cbc, 'create', create)
    monkeypatch.setattr(core, '_boot', Mock(side_effect=RuntimeError('代理连接失败')))
    monkeypatch.setattr(cbc, 'compose', Mock())
    current = task('create', '')
    with pytest.raises(RuntimeError, match='代理连接失败'):
        core._create(
            current,
            {
                'display_name': '测试',
                'mode': 'socks',
                'host': '192.0.2.9',
                'port': 1080,
                'username': 'u',
                'password': 'p',
            },
        )
    rows = store._instances_on_disk()
    assert len(rows) == 1 and rows[0]['id'] == current['instance_id']
    assert not store.proxy_locked(rows[0]['raw'])
    assert rows[0]['raw']['desired_running'] is False


@pytest.mark.parametrize('failure', ['trash', 'docker', 'node'])
def test_restart_preflights_before_stop(isolated, monkeypatch, failure):
    instance(isolated, trashed_at='2026-11-14T00:00:00Z' if failure == 'trash' else None)
    stop = Mock()
    monkeypatch.setattr(core, '_stop_running', stop)
    monkeypatch.setattr(
        core, '_require_docker', Mock(side_effect=RuntimeError('Docker 不可用') if failure == 'docker' else None)
    )
    monkeypatch.setattr(cbc, 'proxy_plan', Mock(side_effect=ValueError('节点不可用') if failure == 'node' else None))
    with pytest.raises((ValueError, RuntimeError)):
        core._dispatch(task('restart'))
    stop.assert_not_called()


def test_same_instance_probe_and_reload_cannot_overlap_or_resurrect_deleted_exit(isolated, monkeypatch):
    instance(isolated)
    monkeypatch.setattr(core, 'container_map', lambda: snapshot()['containers'])
    started, release, removal_started = threading.Event(), threading.Event(), threading.Event()

    def probe(_slug):
        started.set()
        assert release.wait(5)
        return {'status': 'ok', 'ip': '192.0.2.1', 'detail': ''}

    monkeypatch.setattr(cbc, 'probe_proxy', probe)
    monkeypatch.setattr(cbc, 'active', lambda _slug: False)
    monkeypatch.setattr(cbc, 'down', Mock())

    def remove():
        removal_started.set()
        core._dispatch(task('trash'))
        core._dispatch(task('purge', confirm_name='测试实例'))

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        checking = pool.submit(core.check_exit, 'ifixture')
        assert started.wait(5)
        with pytest.raises(ValueError, match='其他操作'):
            core.check_exit('ifixture')
        with pytest.raises(ValueError, match='其他操作'):
            core.reload_browser_page('ifixture')
        removing = pool.submit(remove)
        assert removal_started.wait(5)
        release.set()
        checking.result(timeout=5)
        removing.result(timeout=5)
    assert 'ifixture' not in store.load_exits()
    assert not (store.CONFIGS / 'ifixture').exists()
    assert not (isolated / 'data' / 'ifixture').exists()


def test_locks_do_not_block_a_different_instance(isolated, monkeypatch):
    instance(isolated, 'ia')
    instance(isolated, 'ib', port=5804, network_index=10)
    monkeypatch.setattr(core, 'container_map', lambda: snapshot('ib')['containers'])
    monkeypatch.setattr(cbc, 'probe_proxy', lambda _slug: {'status': 'ok', 'ip': '192.0.2.2', 'detail': ''})
    with store.instance_operation('ia'):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(core.check_exit, 'ib').result(timeout=3)['ip'] == '192.0.2.2'


def test_exit_failure_keeps_last_success_and_change_notification(isolated):
    store.save_exit('ifixture', 'ok', '192.0.2.1', '', 'US')
    changed = store.save_exit('ifixture', 'ok', '192.0.2.2', '', 'JP')
    assert changed['previous_ip'] == '192.0.2.1' and changed['changed_at']
    failed = store.save_exit('ifixture', 'unknown', None, 'Claude 出口未测得')
    assert failed['ip'] is None and failed['country_code'] is None
    assert failed['last_success']['ip'] == '192.0.2.2'
    assert failed['last_success']['country_code'] == 'JP'
    assert failed['changed_at'] == changed['changed_at']


def test_exit_history_tracks_transitions_and_preserves_dismissal(isolated, monkeypatch):
    monkeypatch.setattr(store, '_now', lambda: '2026-11-15T04:00:00+00:00')
    first = store.save_exit('ifixture', 'ok', '192.0.2.1', '', 'JP')
    assert first['history'] == [] and 'change_id' not in first
    changed = store.save_exit('ifixture', 'ok', '192.0.2.2', '', 'JP')
    assert changed['status'] == 'ok'
    assert [(entry['previous_ip'], entry['ip']) for entry in changed['history']] == [('192.0.2.1', '192.0.2.2')]
    dismissed = store.dismiss_exit_change('ifixture', changed['change_id'])
    assert dismissed['dismissed_change_id'] == changed['change_id']
    assert dismissed['history'] == changed['history']
    failed = store.save_exit('ifixture', 'failed', None, '连接失败')
    assert failed['history'] == changed['history']
    assert failed['dismissed_change_id'] == changed['change_id']
    repeated = store.save_exit('ifixture', 'ok', '192.0.2.2', '', 'JP')
    assert repeated['history'] == changed['history']
    assert repeated['dismissed_change_id'] == repeated['change_id']
    returned = store.save_exit('ifixture', 'ok', '192.0.2.1', '', 'JP')
    # 同一秒内变化、甚至回到旧 IP，也必须形成新的提醒。
    assert returned['changed_at'] == changed['changed_at']
    assert returned['change_id'] != returned['dismissed_change_id']
    assert [(entry['previous_ip'], entry['ip']) for entry in returned['history']] == [
        ('192.0.2.1', '192.0.2.2'),
        ('192.0.2.2', '192.0.2.1'),
    ]
    assert store.load_exits()['ifixture'] == returned


def test_stale_dismissal_cannot_hide_a_new_change_or_another_instance(isolated):
    for slug in ('ia', 'ib'):
        store.save_exit(slug, 'ok', '192.0.2.1', '')
        store.save_exit(slug, 'ok', '192.0.2.2', '')
    old_id = store.load_exits()['ia']['change_id']
    latest = store.save_exit('ia', 'ok', '192.0.2.3', '')
    for slug, change_id in [('ia', old_id), ('ib', latest['change_id']), ('ia', None), ('ia', '')]:
        before = store.EXITS.read_bytes()
        with pytest.raises(ValueError, match='记录已更新'):
            store.dismiss_exit_change(slug, change_id)
        assert store.EXITS.read_bytes() == before
    store.dismiss_exit_change('ia', latest['change_id'])
    assert 'dismissed_change_id' not in store.load_exits()['ib']


@pytest.mark.parametrize('status', ['ok', 'unknown'])
def test_legacy_exit_change_is_visible_then_persisted_without_inventing_history(isolated, status):
    legacy = {
        'status': status,
        'ip': '192.0.2.2' if status == 'ok' else None,
        'previous_ip': '192.0.2.1',
        'changed_at': '2026-11-14T01:00:00+00:00',
        'last_success': {'ip': '192.0.2.2', 'country_code': 'JP', 'checked_at': '2026-11-14T02:00:00+00:00'},
    }
    store._save(store.EXITS, {'ifixture': legacy})
    before = store.EXITS.read_bytes()
    loaded = store.load_exits()['ifixture']
    assert loaded['change_id'] == store.load_exits()['ifixture']['change_id']
    assert loaded['history'] == [
        {'id': loaded['change_id'], 'previous_ip': '192.0.2.1', 'ip': '192.0.2.2', 'changed_at': legacy['changed_at']}
    ]
    assert store.EXITS.read_bytes() == before
    store.dismiss_exit_change('ifixture', loaded['change_id'])
    persisted = json.loads(store.EXITS.read_text())['ifixture']
    assert persisted['history'] == loaded['history']
    assert persisted['dismissed_change_id'] == loaded['change_id']


def test_exit_dismiss_api_preserves_locked_binding_and_requires_local_request(isolated, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    instance(isolated, desired_running=False)
    store.save_exit('ifixture', 'ok', '192.0.2.1', '')
    changed = store.save_exit('ifixture', 'ok', '192.0.2.2', '')
    instance_before = (store.CONFIGS / 'ifixture' / 'instance.json').read_bytes()
    proxy_before = (store.CONFIGS / 'ifixture' / 'proxy.json').read_bytes()
    monkeypatch.setattr(store, 'migrate', lambda: None)
    monkeypatch.setattr(core, 'ensure_worker', lambda: None)
    headers = {'X-CBC-Request': '1', 'Origin': 'http://127.0.0.1:5790'}
    with TestClient(main.app, base_url='http://127.0.0.1:5790') as client:
        path = '/api/instances/ifixture/exit/dismiss'
        assert client.post(path, json={'change_id': changed['change_id']}).status_code == 403
        assert client.post(path, json={}, headers=headers).status_code == 400
        assert client.post(path, json={'change_id': 'old-change'}, headers=headers).status_code == 400
        response = client.post(path, json={'change_id': changed['change_id']}, headers=headers)
        assert response.status_code == 200
        assert response.json()['dismissed_change_id'] == changed['change_id']
        assert response.json()['history'] == changed['history']
        assert client.post(path, json={'change_id': changed['change_id']}, headers=headers).status_code == 200
        assert (
            client.post(
                '/api/instances/missing/exit/dismiss', json={'change_id': changed['change_id']}, headers=headers
            ).status_code
            == 400
        )
    assert (store.CONFIGS / 'ifixture' / 'instance.json').read_bytes() == instance_before
    assert (store.CONFIGS / 'ifixture' / 'proxy.json').read_bytes() == proxy_before
    cbc.docker.assert_not_called()


def test_monitor_allows_exit_change_without_recovery_or_node_change(isolated, monkeypatch):
    row = instance(isolated)
    store.save_exit('ifixture', 'ok', '192.0.2.1', '')
    before = (store.CONFIGS / 'ifixture' / 'instance.json').read_bytes()
    monkeypatch.setattr(core, 'runtime_snapshot', lambda: snapshot())
    monkeypatch.setattr(cbc, 'probe_proxy', lambda _slug: {'status': 'ok', 'ip': '192.0.2.2', 'detail': ''})
    queued = Mock()
    monkeypatch.setattr(core, 'enqueue', queued)
    monitor._state(row)['next_probe'] = 0
    monitor.tick()
    changed = store.load_exits()['ifixture']
    assert changed['history'][0]['ip'] == '192.0.2.2'
    assert monitor.public_status(row, 'running', changed)['state'] == 'healthy'
    queued.assert_not_called()
    assert (store.CONFIGS / 'ifixture' / 'instance.json').read_bytes() == before
    cbc.docker.assert_not_called()


def test_overview_batches_commands_and_shares_cache(isolated, monkeypatch):
    instance(isolated, 'ia')
    instance(isolated, 'ib', port=5804, network_index=10)
    commands = []
    containers = [name for slug in ('ia', 'ib') for name in snapshot(slug)['containers']]

    def docker(*args, **_kwargs):
        commands.append(args[0])
        if args[0] == 'ps':
            output = '\n'.join(
                json.dumps({'Names': name, 'State': 'running', 'Status': 'Up 1 minute (healthy)', 'ID': name + '-id'})
                for name in containers
            )
        elif args[0] == 'stats':
            output = '\n'.join(
                json.dumps({'Name': name, 'CPUPerc': '1%', 'MemUsage': '2MiB / 64MiB'}) for name in containers
            )
        elif args[0] == 'inspect':
            output = '\n'.join(json.dumps({'name': '/' + name, 'memory': 64 * 1024 * 1024}) for name in containers)
        else:
            raise AssertionError(args)
        return SimpleNamespace(returncode=0, stdout=output, stderr='')

    monkeypatch.setattr(cbc, 'docker', docker)
    result = core.list_instances()
    assert commands == ['ps', 'inspect', 'stats']
    assert result['instances'][0]['usage']['memory_limit_mib'] == 192
    assert result['instances'][0]['usage']['memory_mib'] == 6
    core.list_instances()
    assert commands == ['ps', 'inspect', 'stats']
    core._host_memory.assert_called_once()
    core._self_rss.assert_called_once()


def test_unhealthy_container_is_visible_as_degraded(isolated, monkeypatch):
    instance(isolated)
    snap = snapshot(health={cbc.container('ifixture', 'proxy'): 'unhealthy'})
    monkeypatch.setattr(core, 'runtime_snapshot', lambda: snap)
    monkeypatch.setattr(core, '_refresh_limits', lambda *_args: None)
    monkeypatch.setattr(core, '_stats', lambda *_args: {'rows': {}, 'error': ''})
    result = core.list_instances()['instances'][0]
    assert result['runtime'] == 'degraded'
    assert next(row for row in result['diagnostics'] if row['role'] == 'proxy')['state'] == 'unhealthy'


def test_monitor_waits_for_repeated_component_failure(isolated, monkeypatch):
    instance(isolated)
    monkeypatch.setattr(core, 'runtime_snapshot', lambda: snapshot(broken='proxy'))
    queued = Mock()
    monkeypatch.setattr(core, 'enqueue', queued)
    monitor.tick()
    monitor.tick()
    queued.assert_not_called()
    monitor.tick()
    queued.assert_called_once_with('recover', 'ifixture', payload={'generation': 'generation'})


@pytest.mark.parametrize(
    'raw',
    [
        {'desired_running': False},
        {'trashed_at': '2026-11-14T00:00:00Z'},
        {'desired_running': False, 'recovery': {'attempts': 1}},
    ],
)
def test_monitor_never_restarts_manually_stopped_or_trashed_instances(isolated, monkeypatch, raw):
    instance(isolated, **raw)
    monkeypatch.setattr(core, 'runtime_snapshot', lambda: snapshot(broken='proxy'))
    queued = Mock()
    monkeypatch.setattr(core, 'enqueue', queued)
    for _ in range(5):
        monitor.tick()
    queued.assert_not_called()


def test_monitor_adopts_only_healthy_running_legacy_instances(isolated, monkeypatch):
    row = instance(isolated)
    store.update_instance('ifixture', lambda raw: raw.pop('desired_running'))
    monkeypatch.setattr(core, 'runtime_snapshot', lambda: snapshot(broken='proxy'))
    monitor.tick()
    assert 'desired_running' not in store.instance_by_id(row['id'])['raw']
    monkeypatch.setattr(core, 'runtime_snapshot', lambda: snapshot())
    monitor.tick()
    assert store.instance_by_id(row['id'])['raw']['desired_running'] is True


def test_monitor_honors_retry_delay_and_maximum_after_recurrence(isolated, monkeypatch):
    instance(isolated, recovery={'attempts': 1, 'next_retry_at': 10**12})
    monkeypatch.setattr(core, 'runtime_snapshot', lambda: snapshot(broken='proxy'))
    queued = Mock()
    monkeypatch.setattr(core, 'enqueue', queued)
    for _ in range(4):
        monitor.tick()
    queued.assert_not_called()
    store.update_instance('ifixture', lambda raw: raw.update(recovery={'attempts': 3, 'next_retry_at': 0}))
    monitor.tick()
    assert store.instance_by_id('ifixture')['raw']['recovery']['exhausted'] is True
    queued.assert_not_called()


def test_manual_stop_cancels_an_already_queued_recovery(isolated, monkeypatch):
    instance(isolated)
    pending = task('recover', generation='generation')
    core.enqueue('stop', 'ifixture')
    core._dispatch(pending)
    assert '取消' in pending['result']['detail']
    assert store.instance_by_id('ifixture')['raw']['desired_running'] is False
    cbc.docker.assert_not_called()


def test_proxy_only_recovery_preserves_browser_and_guard(isolated, monkeypatch):
    instance(isolated)
    monkeypatch.setattr(core, '_preflight_boot', Mock())
    monkeypatch.setattr(core, 'runtime_snapshot', Mock(return_value=snapshot(broken='proxy')))
    monkeypatch.setattr(cbc, 'running_browser_identity', lambda _slug: {'image': 'same', 'revision': 'same'})
    prepare, compose, stop = Mock(), Mock(), Mock()
    monkeypatch.setattr(cbc, 'prepare', prepare)
    monkeypatch.setattr(cbc, 'compose', compose)
    monkeypatch.setattr(core, '_stop_running', stop)
    monkeypatch.setattr(
        cbc, 'probe_proxy', lambda _slug: {'status': 'ok', 'ip': '192.0.2.1', 'country_code': 'US', 'detail': ''}
    )
    monkeypatch.setattr(cbc, 'require_same_country', lambda _slug, _observation: None)
    core._dispatch(task('recover', generation='generation'))
    stop.assert_not_called()
    assert compose.call_args.args[-1] == 'proxy'
    assert '--no-deps' in compose.call_args.args and '--force-recreate' in compose.call_args.args
    assert prepare.call_args.kwargs['keep_browser'] == {'image': 'same', 'revision': 'same'}
    raw = store.instance_by_id('ifixture')['raw']
    assert raw['run_generation'] == 'generation' and raw['desired_running'] is True
    assert raw['recovery']['attempts'] == 1
    assert (isolated / 'data' / 'ifixture' / 'profile-marker').read_text() == 'preserved'


def test_guard_failure_uses_coordinated_restart(isolated, monkeypatch):
    instance(isolated)
    monkeypatch.setattr(core, '_preflight_boot', Mock())
    monkeypatch.setattr(core, 'runtime_snapshot', Mock(return_value=snapshot(broken='guard')))
    actions = []
    monkeypatch.setattr(core, '_stop_running', lambda slug: actions.append(('stop', slug)))
    monkeypatch.setattr(core, '_boot', lambda _task, slug, verify: actions.append(('boot', slug)))
    core._dispatch(task('recover', generation='generation'))
    assert actions == [('stop', 'ifixture'), ('boot', 'ifixture')]


def test_stop_during_recovery_does_not_rearm_running_intent(isolated, monkeypatch):
    instance(isolated)
    monkeypatch.setattr(core, '_preflight_boot', Mock())
    monkeypatch.setattr(core, 'runtime_snapshot', Mock(return_value=snapshot(broken='guard')))
    monkeypatch.setattr(core, '_stop_running', lambda slug: store.set_run_intent(slug, False))
    boot = Mock()
    monkeypatch.setattr(core, '_boot', boot)
    core._dispatch(task('recover', generation='generation'))
    boot.assert_not_called()
    raw = store.instance_by_id('ifixture')['raw']
    assert raw['desired_running'] is False and raw['recovery'] == {}


def test_failed_recovery_is_bounded_and_persisted(isolated, monkeypatch):
    instance(isolated)
    preflight = Mock(side_effect=RuntimeError('fixture failure'))
    monkeypatch.setattr(core, '_preflight_boot', preflight)
    for count in range(1, 4):
        with pytest.raises(RuntimeError):
            core._dispatch(task('recover', generation='generation'))
        assert store.instance_by_id('ifixture')['raw']['recovery']['attempts'] == count
    core._dispatch(task('recover', generation='generation'))
    assert preflight.call_count == 3
    assert store.instance_by_id('ifixture')['raw']['recovery']['exhausted'] is True


def test_periodic_probe_is_spaced_and_unknown_does_not_trigger_recovery(isolated, monkeypatch):
    row = instance(isolated)
    store.save_exit('ifixture', 'ok', '192.0.2.1', '')
    monkeypatch.setattr(core, 'runtime_snapshot', lambda: snapshot())
    probe = Mock(return_value={'status': 'unknown', 'ip': None, 'detail': 'Claude 出口未测得'})
    monkeypatch.setattr(cbc, 'probe_proxy', probe)
    queued = Mock()
    monkeypatch.setattr(core, 'enqueue', queued)
    monitor._state(row)['next_probe'] = 0
    monitor.tick()
    monitor.tick()
    probe.assert_called_once()
    queued.assert_not_called()
    assert store.load_exits()['ifixture']['last_success']['ip'] == '192.0.2.1'


def test_reprepare_updates_proxy_target_and_firewall_together(isolated, monkeypatch):
    instance(isolated)
    store._write_proxy('ifixture', {'host': 'node.example', 'port': 1080, 'username': 'u', 'password': 'p'})
    monkeypatch.setattr(
        cbc,
        'planned_browser',
        lambda *_args, **_kwargs: {
            'image': 'browser',
            'network_image': 'network',
            'revision': 'unchanged',
            'resources': dict(cbc.DEFAULTS),
            'addresses': cbc.addresses('ifixture'),
        },
    )
    address = ['192.0.2.1']
    monkeypatch.setattr(cbc.socket, 'getaddrinfo', lambda *_args: [(None, None, None, None, (address[0], 1080))])
    cbc.prepare('ifixture')
    runtime = store.CONFIGS / 'ifixture' / 'runtime'
    old_revision = (runtime / 'revision').read_text()
    address[0] = '192.0.2.2'
    cbc.prepare('ifixture')
    assert json.loads((runtime / 'firewall.json').read_text()) == {'upstream_ipv4': ['192.0.2.2'], 'port': 1080}
    config = json.loads((runtime / 'gost.json').read_text())
    assert config['chains'][0]['hops'][0]['nodes'][0]['addr'] == '192.0.2.2:1080'
    assert (runtime / 'revision').read_text() != old_revision
    assert 'BROWSER_REVISION=unchanged' in (runtime / 'compose.env').read_text()
    assert store.read_proxy('ifixture')['host'] == 'node.example'


def test_docker_outage_pauses_monitor_without_changing_intent(isolated, monkeypatch):
    instance(isolated)
    monkeypatch.setattr(
        core, 'runtime_snapshot', lambda: {'docker': {'ok': False}, 'containers': None, 'health': {}, 'ids': {}}
    )
    queued = Mock()
    monkeypatch.setattr(core, 'enqueue', queued)
    monitor.tick()
    queued.assert_not_called()
    assert store.instance_by_id('ifixture')['raw']['desired_running'] is True
    assert '暂停' in monitor.service_status()['error']


def test_attempts_reset_only_after_stable_success(isolated, monkeypatch):
    row = instance(isolated, recovery={'attempts': 2, 'next_retry_at': 0})
    store.save_exit('ifixture', 'ok', '192.0.2.1', '')
    monkeypatch.setattr(core, 'runtime_snapshot', lambda: snapshot())
    state = monitor._state(row)
    now = monitor.time.monotonic()
    state.update(stable_since=now - 100, next_probe=now + 1000)
    monitor.tick()
    assert store.instance_by_id('ifixture')['raw']['recovery']['attempts'] == 2
    state['stable_since'] = now - monitor.STABLE_SECONDS - 1
    monitor.tick()
    assert store.instance_by_id('ifixture')['raw']['recovery'] == {}


def test_two_connection_failures_enqueue_original_generation_recovery(isolated, monkeypatch):
    row = instance(isolated)
    monkeypatch.setattr(core, 'runtime_snapshot', lambda: snapshot())
    monkeypatch.setattr(cbc, 'probe_proxy', lambda _slug: {'status': 'failed', 'ip': None, 'detail': '代理连接失败'})
    state = monitor._state(row)
    state['next_probe'] = 0
    monitor.tick()
    assert core._QUEUE.empty()
    state['next_probe'] = 0
    monitor.tick()
    pending = core._TASKS[core._QUEUE.get_nowait()]
    assert pending['kind'] == 'recover' and pending['payload']['generation'] == 'generation'


def test_stale_node_shows_paused_recovery(isolated):
    row = instance(isolated)
    status = monitor.public_status(row, 'running', None, {'mode': 'subscription', 'stale': True})
    assert status['enabled'] is False and status['state'] == 'warning'
    assert '已暂停' in status['detail']


def test_retry_delay_starts_when_slow_attempt_finishes(isolated, monkeypatch):
    instance(isolated)
    clock = [100.0]
    monkeypatch.setattr(monitor.time, 'time', lambda: clock[0])

    def slow_failure(_slug):
        clock[0] = 500.0
        raise RuntimeError('fixture timeout')

    monkeypatch.setattr(core, '_preflight_boot', slow_failure)
    with pytest.raises(RuntimeError):
        core._dispatch(task('recover', generation='generation'))
    retry = store.instance_by_id('ifixture')['raw']['recovery']
    assert retry['next_retry_at'] == 500.0 + monitor.RETRY_DELAYS[0]


def test_storage_lock_wait_does_not_stall_overview_requests(isolated, monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from fastapi.testclient import TestClient
    from app import main

    instance(isolated, desired_running=False)
    monkeypatch.setattr(store, 'migrate', lambda: None)
    monkeypatch.setattr(core, 'ensure_worker', lambda: None)
    monkeypatch.setattr(
        core,
        'runtime_snapshot',
        lambda: {
            'docker': {'ok': False},
            'containers': None,
            'health': {},
            'ids': {},
        },
    )
    entered = threading.Event()
    rename_instance = core.rename_instance

    def rename(instance_id, display_name):
        entered.set()
        return rename_instance(instance_id, display_name)

    monkeypatch.setattr(core, 'rename_instance', rename)
    with TestClient(main.app, base_url='http://127.0.0.1:5790') as client:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            # The caller thread owns the real flock while the ASGI request tries to write.
            with store.exclusive():
                writing = pool.submit(
                    client.patch,
                    '/api/instances/ifixture',
                    headers={'X-CBC-Request': '1'},
                    json={'display_name': '新名称'},
                )
                assert entered.wait(3)
                reading = pool.submit(client.get, '/api/instances')
                try:
                    response = reading.result(timeout=1)
                except concurrent.futures.TimeoutError:
                    pytest.fail('等待存储锁的写请求阻塞了实例轮询')
                assert response.status_code == 200
                assert response.json()['instances'][0]['display_name'] == '测试实例'
                assert not writing.done()
            result = writing.result(timeout=3)
            assert result.status_code == 200
            assert result.json()['display_name'] == '新名称'
    assert store.instance_by_id('ifixture')['display_name'] == '新名称'
    cbc.docker.assert_not_called()


def test_monitor_scans_inventory_once_per_tick(isolated, monkeypatch):
    for number in range(3):
        instance(isolated, f'ifixture{number}', port=5810 + number, network_index=20 + number)
    monkeypatch.setattr(core, 'runtime_snapshot', lambda: snapshot())
    scan = Mock(wraps=store.scan_instances)
    monkeypatch.setattr(store, 'scan_instances', scan)
    monitor.tick()
    assert scan.call_count == 1
    cbc.docker.assert_not_called()


def test_monitor_probe_allows_rename_without_losing_new_name(isolated, monkeypatch):
    row = instance(isolated)
    monitor._state(row)['next_probe'] = 0
    monkeypatch.setattr(core, 'runtime_snapshot', lambda: snapshot())
    entered, release = threading.Event(), threading.Event()

    def probe(_slug):
        entered.set()
        assert release.wait(3)
        return {'status': 'ok', 'ip': '192.0.2.20', 'detail': ''}

    monkeypatch.setattr(cbc, 'probe_proxy', probe)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        checking = pool.submit(monitor.tick)
        assert entered.wait(3)
        try:
            assert core.rename_instance(row['id'], '探测期间改名')['display_name'] == '探测期间改名'
        finally:
            release.set()
        checking.result(timeout=3)
    assert store.instance_by_id(row['id'])['display_name'] == '探测期间改名'
    assert store.load_exits()[row['slug']]['ip'] == '192.0.2.20'
    cbc.docker.assert_not_called()


@pytest.mark.parametrize('change', ['stop', 'generation', 'trash', 'delete'])
def test_monitor_discards_probe_result_after_instance_changes(isolated, monkeypatch, change):
    row = instance(isolated)
    monitor._state(row)['next_probe'] = 0
    monkeypatch.setattr(core, 'runtime_snapshot', lambda: snapshot())
    queued = Mock()
    monkeypatch.setattr(core, 'enqueue', queued)

    def probe(_slug):
        if change == 'delete':
            (store.CONFIGS / row['slug'] / 'instance.json').unlink()
        elif change == 'stop':
            store.set_run_intent(row['slug'], False)
        elif change == 'generation':
            store.set_run_intent(row['slug'], True)
        else:
            store.update_instance(row['slug'], lambda raw: raw.update(trashed_at=store._now()))
        return {'status': 'failed', 'ip': None, 'detail': '旧探测结果'}

    monkeypatch.setattr(cbc, 'probe_proxy', probe)
    monitor.tick()
    assert row['slug'] not in store.load_exits()
    queued.assert_not_called()
    cbc.docker.assert_not_called()
