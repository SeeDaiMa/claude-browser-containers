"""R1–R9 的隔离回归。只使用临时目录和显式夹具，不访问真实 Docker。"""
import asyncio
import concurrent.futures
import io
import json
import queue
import shutil
import subprocess
import sys
import tarfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import catalog
import cbc
import geolocation
import manager_core as core
import monitor
import store
from app.main import RequestBodyError
from test_reliability import instance, isolated, snapshot, task


def _node(name='audit-node'):
    return {'name': name, 'type': 'ss', 'server': '192.0.2.9', 'port': 443,
            'cipher': 'aes-128-gcm', 'password': 'fixture-only'}


def _subscription():
    identity = store.add_subscription('审查订阅', 'https://audit.example.test/sub', lambda _: [_node()])
    return identity, store.load_cached_nodes(identity)[0]['id']


def test_close_timeout_fails_without_stopping_and_lets_the_next_instance_run(isolated, monkeypatch):
    instance(isolated)
    instance(isolated, 'iother', port=5891, network_index=91)
    seen = []

    def docker(*args, **kwargs):
        assert kwargs.get('timeout'), args
        if args[0] == 'exec' and 'windowquit' in args:
            seen.append(next(arg for arg in args if str(arg).startswith('cbc-')))
            raise subprocess.TimeoutExpired(args, kwargs['timeout'])
        if args[0] == 'ps':
            return SimpleNamespace(returncode=0, stdout='running\n')
        raise AssertionError(f'关闭超时后不应继续执行 {args[0]}')

    class FiniteQueue(queue.Queue):
        def get(self, *args, **kwargs):
            value = super().get(*args, **kwargs)
            if value is None:
                raise SystemExit
            return value

    monkeypatch.setattr(cbc, 'docker', docker)
    monkeypatch.setattr(core, '_QUEUE', FiniteQueue())
    first = core.enqueue('stop', 'ifixture')
    second = core.enqueue('stop', 'iother')
    core._QUEUE.put(None)

    def consume():
        try:
            core._worker(core._QUEUE)
        except SystemExit:
            pass

    worker = threading.Thread(target=consume)
    worker.start()
    worker.join(3)
    assert not worker.is_alive()
    assert seen == ['cbc-ifixture-browser', 'cbc-iother-browser']
    assert core.get_task(first['id'])['state'] == 'failed'
    assert '无法正常关闭' in core.get_task(first['id'])['error']
    assert core.get_task(second['id'])['state'] == 'failed'
    assert (isolated / 'data/ifixture/profile-marker').read_text() == 'preserved'


def test_corrupt_exit_file_keeps_the_instance_list_and_pauses_recovery(isolated, monkeypatch):
    instance(isolated)
    store.EXITS.write_text('{invalid')
    original = store.EXITS.read_text()
    monkeypatch.setattr(core, 'runtime_snapshot', lambda force=False: {'docker': {'ok': False}, 'containers': None, 'health': {}, 'ids': {}})
    listed = core.list_instances()
    assert [row['id'] for row in listed['instances']] == ['ifixture']
    assert listed['config_errors'][-1]['file'] == 'configs/private/exits.json'
    monkeypatch.setattr(core, 'runtime_snapshot', lambda force=False: snapshot())
    monitor.tick()
    assert core._QUEUE.empty()
    assert '暂停自动恢复' in monitor.service_status()['error']
    assert store.EXITS.read_text() == original


@pytest.mark.parametrize('field', ['phase', 'error'])
def test_finished_task_missing_public_field_is_isolated(isolated, field):
    record = {'id': 'taudit', 'kind': 'stop', 'instance_id': 'ifixture', 'state': 'succeeded',
              'phase': '停止实例', 'phases': [], 'error': '', 'result': {}}
    record.pop(field)
    path = store.PRIVATE / 'tasks.json'
    path.write_text(json.dumps([record]))
    core._load_tasks()
    assert core._TASKS == {}
    damaged = list(path.parent.glob('tasks.json.*.damaged'))
    assert len(damaged) == 1 and json.loads(damaged[0].read_text()) == [record]
    assert not path.exists()


@pytest.mark.parametrize('broken', [{'last_success': 12}, {'history': 12}])
def test_exit_probe_keeps_unreadable_record_and_can_be_repeated(isolated, monkeypatch, broken):
    instance(isolated)
    original = {'status': 'ok', 'ip': '1.1.1.1', 'previous_ip': '8.8.8.8',
                'changed_at': '2026-11-16T00:00:00Z', 'history': [], **broken}
    store._save(store.EXITS, {'ifixture': original})
    before = store.EXITS.read_text()
    monkeypatch.setattr(core, 'runtime_snapshot', lambda force=False: snapshot())
    monkeypatch.setattr(core, 'probe_proxy', lambda _slug: {
        'status': 'ok', 'ip': '192.0.2.9', 'detail': '', 'country_code': 'AU',
    })
    monkeypatch.setattr(monitor, '_state', lambda _row: {
        'next_probe': 0, 'component_failures': 0, 'probe_failures': 0,
        'stable_since': None, 'fault_since': None,
    })
    saved = store.save_exit('ifixture', 'ok', '192.0.2.9', '', 'AU')
    assert store.exit_record_readable(saved)
    assert saved['last_success']['ip'] == '192.0.2.9'
    copies = list(store.EXITS.parent.glob('exits.json.*.damaged'))
    assert len(copies) == 1 and copies[0].read_text() == before
    monitor.tick()
    assert store.exit_record_readable(store.load_exits()['ifixture'])


def test_incomplete_task_record_is_isolated_without_blocking_startup(isolated):
    path = store.PRIVATE / 'tasks.json'
    original = [{'id': 'taudit'}]
    path.write_text(json.dumps(original))
    core._load_tasks()
    assert core._TASKS == {}
    damaged = list(path.parent.glob('tasks.json.*.damaged'))
    assert len(damaged) == 1 and json.loads(damaged[0].read_text()) == original
    assert not path.exists()


def _exit_record(**overrides):
    record = {
        'status': 'ok', 'ip': '203.0.113.5', 'previous_ip': '203.0.113.1',
        'changed_at': '2026-11-16T00:00:00Z', 'checked_at': '2026-11-16T00:00:00Z', 'history': [],
        'last_success': {'ip': '203.0.113.5', 'country_code': 'AU', 'checked_at': '2026-11-16T00:00:00Z'},
    }
    record.update(overrides)
    return record


@pytest.mark.parametrize('corrupt', [
    {'ip': ['203.0.113.8']},
    {'ip': {'value': '203.0.113.8'}},
    {'ip': 134744072},
    {'ip': 'not-an-ip'},
    {'previous_ip': ['203.0.113.1']},
    {'last_success': {'ip': ['203.0.113.8'], 'country_code': 'AU', 'checked_at': '2026-11-16T00:00:00Z'}},
    {'history': [{'id': 'c1', 'previous_ip': '203.0.113.1', 'ip': ['203.0.113.8'], 'changed_at': '2026-11-16T00:00:00Z'}]},
])
def test_corrupt_exit_ip_keeps_the_other_instances(isolated, monkeypatch, corrupt):
    instance(isolated)
    instance(isolated, 'iother', port=5891, network_index=91)
    store._save(store.EXITS, {'ifixture': _exit_record(**corrupt), 'iother': _exit_record(ip='8.8.8.8', previous_ip='1.1.1.1')})
    original = store.EXITS.read_text()
    seen = []

    def country(value):
        seen.append(value)
        return 'US'

    monkeypatch.setattr(geolocation, 'country_code', country)
    monkeypatch.setattr(core, 'runtime_snapshot', lambda force=False: {
        'docker': {'ok': False}, 'containers': None, 'health': {}, 'ids': {},
    })
    listed = core.list_instances()
    by_id = {row['id']: row for row in listed['instances']}
    assert set(by_id) == {'ifixture', 'iother'}
    assert by_id['ifixture']['exit'] is None
    assert by_id['iother']['exit']['ip'] == '8.8.8.8'
    assert by_id['iother']['exit']['country_code'] == 'US'
    assert seen == ['8.8.8.8']
    assert any(item['id'] == 'ifixture' and item['file'] == 'configs/private/exits.json' for item in listed['config_errors'])
    assert store.EXITS.read_text() == original


def test_shared_exit_warning_uses_only_verified_ip(isolated, monkeypatch):
    instance(isolated)
    instance(isolated, 'iother', port=5891, network_index=91)
    instance(isolated, 'ishared', port=5892, network_index=92)
    store._save(store.EXITS, {
        'ifixture': _exit_record(ip=['203.0.113.8']),
        'iother': _exit_record(ip='8.8.8.8', previous_ip='1.1.1.1'),
        'ishared': _exit_record(ip='8.8.8.8', previous_ip='1.1.1.1'),
    })
    monkeypatch.setattr(geolocation, 'country_code', lambda value: 'US' if value == '8.8.8.8' else None)
    monkeypatch.setattr(core, 'runtime_snapshot', lambda force=False: {
        'docker': {'ok': False}, 'containers': None, 'health': {}, 'ids': {},
    })
    listed = core.list_instances()
    assert [row['id'] for row in listed['instances']] == ['ifixture', 'iother', 'ishared']
    assert listed['exit_warnings'] == [{
        'ip': '8.8.8.8',
        'instances': [
            {'id': 'iother', 'name': '测试实例', 'checked_at': '2026-11-16T00:00:00Z'},
            {'id': 'ishared', 'name': '测试实例', 'checked_at': '2026-11-16T00:00:00Z'},
        ],
    }]


def test_corrupt_exit_ip_is_kept_aside_when_observation_is_saved(isolated, monkeypatch):
    instance(isolated)
    store._save(store.EXITS, {'ifixture': _exit_record(ip=['203.0.113.8'])})
    before = store.EXITS.read_text()
    monkeypatch.setattr(core, 'runtime_snapshot', lambda force=False: {
        'docker': {'ok': False}, 'containers': None, 'health': {}, 'ids': {},
    })
    listed = core.list_instances()
    assert listed['instances'][0]['exit'] is None
    monkeypatch.setattr(core, 'runtime_snapshot', lambda force=False: snapshot())
    monkeypatch.setattr(core, 'probe_proxy', lambda _slug: {
        'status': 'ok', 'ip': '192.0.2.9', 'detail': '', 'country_code': 'AU',
    })
    monkeypatch.setattr(monitor, '_state', lambda _row: {
        'next_probe': 0, 'component_failures': 0, 'probe_failures': 0,
        'stable_since': None, 'fault_since': None,
    })
    monitor.tick()
    saved = store.load_exits()['ifixture']
    assert store.exit_record_readable(saved)
    assert saved['ip'] == '192.0.2.9'
    copies = list(store.EXITS.parent.glob('exits.json.*.damaged'))
    assert len(copies) == 1 and copies[0].read_text() == before
    with pytest.raises(ValueError, match='出口 IP 无效'):
        store.save_exit('ifixture', 'ok', {'ip': '192.0.2.10'}, '')
    assert store.load_exits()['ifixture']['ip'] == '192.0.2.9'


def test_bad_nested_exit_record_stays_out_of_the_instance_list(isolated, monkeypatch):
    instance(isolated)
    content = json.dumps({'ifixture': {
        'status': 'ok', 'ip': '1.1.1.1', 'previous_ip': '8.8.8.8',
        'changed_at': '2026-11-16T00:00:00Z', 'last_success': 12,
    }})
    store.EXITS.write_text(content)
    monkeypatch.setattr(core, 'runtime_snapshot', lambda force=False: {
        'docker': {'ok': False}, 'containers': None, 'health': {}, 'ids': {},
    })
    listed = core.list_instances()
    assert [row['id'] for row in listed['instances']] == ['ifixture']
    assert listed['instances'][0]['exit'] is None
    assert any(item['file'] == 'configs/private/exits.json' for item in listed['config_errors'])
    assert store.EXITS.read_text() == content


def test_restore_rejects_a_subscription_id_outside_the_cache_directory(isolated, monkeypatch):
    outside = isolated / 'outside-configs-node-cache'
    slug = 'irestoreaudit'
    manifest = {
        'version': cbc.BACKUP_VERSION, 'slug': slug,
        'instance': {'id': slug, 'name': slug, 'display_name': '合成恢复夹具',
                     'port': 5888, 'network_index': 99, 'desired_running': False},
        'proxy': {'host': '', 'port': 1080, 'username': '', 'password': ''},
        'selection': {'subscription_id': str(outside), 'node_id': 'naudit', 'node': 'audit-node'},
        'subscription': {'id': str(outside), 'name': '合成订阅', 'url': 'https://audit.example.test/sub'},
        'nodes': {'nodes': [{'id': 'naudit', 'name': 'audit-node', 'supported': True, 'config': _node(), 'type': 'ss', 'port': 443}]},
    }
    archive = isolated / 'synthetic.tgz'
    payload = json.dumps(manifest).encode()
    with tarfile.open(archive, 'w:gz') as bundle:
        member = tarfile.TarInfo('manifest.json')
        member.size = len(payload)
        bundle.addfile(member, io.BytesIO(payload))
    monkeypatch.setattr(cbc, '_port_free', lambda _port: True)
    monkeypatch.setattr(cbc, 'network_index_conflicts', lambda _index: False)
    with pytest.raises(ValueError, match='编号无效'):
        cbc.restore_instance(archive)
    assert not Path(str(outside) + '.json').exists()
    assert not (store.CONFIGS / slug).exists()
    with pytest.raises(ValueError, match='订阅编号无效'):
        store._cache_path('../outside')
    assert store._cache_path('sfixture').parent == (store.PRIVATE / 'nodes').resolve()


def test_wrong_task_shape_is_kept_and_does_not_block_startup(isolated):
    path = store.PRIVATE / 'tasks.json'
    path.write_text('{"wrong-shape": true}')
    core._load_tasks()
    assert core._TASKS == {}
    damaged = list(path.parent.glob('tasks.json.*.damaged'))
    assert len(damaged) == 1 and damaged[0].read_text() == '{"wrong-shape": true}'
    assert not path.exists()


def test_subscription_backup_restores_the_binding_in_a_clean_project(isolated, monkeypatch):
    instance(isolated, proxy_locked_at='2026-11-16T00:00:00Z', port=58991, network_index=241)
    subscription_id, node_id = _subscription()
    store.bind_subscription('ifixture', subscription_id, node_id)
    monkeypatch.setattr(cbc, 'active', lambda _name: False)
    monkeypatch.setattr(cbc, 'network_index_conflicts', lambda _index: False)
    archive = isolated / 'backups' / 'ifixture.tgz'
    cbc.backup_instance('ifixture', archive)
    assert archive.stat().st_mode & 0o777 == 0o600
    shutil.rmtree(store.CONFIGS / 'ifixture')
    shutil.rmtree(isolated / 'data' / 'ifixture')
    store.LIBRARY.unlink()
    store._cache_path(subscription_id).unlink()
    (store.CONFIGS / 'selection.json').unlink()
    cbc.restore_instance(archive)
    restored = store.instance_by_id('ifixture')
    assert (isolated / 'data/ifixture/profile-marker').read_text() == 'preserved'
    assert store.proxy_locked(restored['raw']) is True
    assert store.proxy_public('ifixture')['mode'] == 'subscription'
    assert store.proxy_public('ifixture')['node_id'] == node_id
    with pytest.raises(ValueError, match='没有变化'):
        store.reconfirm_same_name('ifixture')
    with pytest.raises(ValueError, match='已存在'):
        cbc.restore_instance(archive)


def test_restore_drops_run_intent_so_monitor_does_not_start_it(isolated, monkeypatch):
    instance(isolated)
    store.update_instance('ifixture', lambda raw: raw.update(
        run_generation='archived-generation', recovery={'attempts': 2},
    ))
    monkeypatch.setattr(cbc, 'active', lambda _name: False)
    monkeypatch.setattr(cbc, 'network_index_conflicts', lambda _index: False)
    archive = isolated / 'ifixture.tgz'
    cbc.backup_instance('ifixture', archive)
    with tarfile.open(archive) as bundle:
        manifest = json.load(bundle.extractfile('manifest.json'))
    assert manifest['instance']['desired_running'] is True
    shutil.rmtree(store.CONFIGS / 'ifixture')
    shutil.rmtree(isolated / 'data' / 'ifixture')
    cbc.restore_instance(archive)
    restored = store.instance_by_id('ifixture')
    assert restored['raw']['desired_running'] is False
    assert restored['raw']['recovery'] == {}
    assert restored['raw']['run_generation'] not in ('', 'archived-generation')
    assert restored['raw']['proxy_locked_at'] == '2026-11-14T00:00:00Z'
    assert (isolated / 'data/ifixture/profile-marker').read_text() == 'preserved'
    queued = []
    monkeypatch.setattr(core, 'enqueue', lambda *args, **kwargs: queued.append(args) or {'id': 'trecover'})
    monkeypatch.setattr(core, 'runtime_snapshot', lambda force=False: {
        'docker': {'ok': True, 'detail': ''}, 'containers': {}, 'health': {}, 'ids': {},
    })
    for _ in range(3):
        monitor.tick()
    assert queued == []


def test_cli_delete_data_can_retry_after_profile_removal_fails(isolated, monkeypatch):
    instance(isolated)
    subscription_id, node_id = _subscription()
    store.bind_subscription('ifixture', subscription_id, node_id)
    monkeypatch.setattr(cbc, 'stop', lambda _name: None)
    monkeypatch.setattr(sys, 'argv', ['cbc', 'remove-instance', 'ifixture', '--delete-data'])
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    answers = iter(['ifixture', 'ifixture'])
    monkeypatch.setattr('builtins.input', lambda _prompt: next(answers))
    original = shutil.rmtree
    failed = {'count': 0}

    def fail_profile_once(path, *args, **kwargs):
        if Path(path) == isolated / 'data' / 'ifixture' and failed['count'] == 0:
            failed['count'] += 1
            raise PermissionError('fixture deletion failure')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(shutil, 'rmtree', fail_profile_once)
    with pytest.raises(PermissionError, match='fixture deletion failure'):
        cbc.main()
    current = store.instance_by_id('ifixture')
    assert current['raw']['deleting'] is True
    assert current['raw']['desired_running'] is False
    assert (isolated / 'data/ifixture/profile-marker').read_text() == 'preserved'
    assert store.selection_map()['ifixture']['node_id'] == node_id
    cbc.main()
    with pytest.raises(ValueError, match='实例不存在'):
        store.instance_by_id('ifixture')
    assert not (isolated / 'data' / 'ifixture').exists()
    assert 'ifixture' not in store.selection_map()


def test_deleting_instance_cannot_start_or_restart(isolated):
    instance(isolated)
    store.update_instance('ifixture', lambda raw: raw.update(deleting=True, desired_running=False))
    for kind in ('start', 'restart'):
        with pytest.raises(ValueError, match='只能继续清理'):
            core._dispatch(task(kind))
    with pytest.raises(ValueError, match='只能继续清理'):
        cbc.start('ifixture')
    assert store.instance_by_id('ifixture')['raw']['deleting'] is True


def test_entry_and_exit_must_share_a_confirmed_country(isolated, monkeypatch):
    instance(isolated)
    runtime = store.CONFIGS / 'ifixture' / 'runtime'
    runtime.mkdir()
    (runtime / 'firewall.json').write_text(json.dumps({'upstream_ipv4': ['8.8.8.8'], 'port': 443}))

    def country(value):
        return 'US' if value == '8.8.8.8' else None

    monkeypatch.setattr(geolocation, 'country_code', country)
    stops = []
    monkeypatch.setattr(cbc, 'docker', lambda *args, **kwargs: stops.append(args) or SimpleNamespace(returncode=0))
    core._require_same_country('ifixture', {'status': 'ok', 'country_code': 'US', 'ip': '203.0.113.8'})
    assert stops == []
    with pytest.raises(cbc.CountryPolicyError, match='国家不同'):
        core._require_same_country('ifixture', {'status': 'ok', 'country_code': 'JP', 'ip': '203.0.113.9'})
    with pytest.raises(cbc.CountryPolicyError, match='国家不同'):
        core._require_same_country('ifixture', {'status': 'unknown', 'country_code': None, 'ip': None})
    assert all(args == ('stop', '--time', '5', 'cbc-ifixture-proxy') for args in stops)
    assert len(stops) == 2


def test_cli_remove_without_delete_data_keeps_the_profile(isolated, monkeypatch):
    instance(isolated)
    monkeypatch.setattr(cbc, 'stop', lambda _name: None)
    monkeypatch.setattr(sys, 'argv', ['cbc', 'remove-instance', 'ifixture'])
    cbc.main()
    assert not (store.CONFIGS / 'ifixture').exists()
    assert (isolated / 'data/ifixture/profile-marker').read_text() == 'preserved'


def test_restore_refuses_a_conflicting_port_without_writing(isolated, monkeypatch):
    instance(isolated, 'ia', port=58992, network_index=242, proxy_locked_at=None)
    instance(isolated, 'ib', port=58993, network_index=243, proxy_locked_at=None)
    monkeypatch.setattr(cbc, 'active', lambda _name: False)
    monkeypatch.setattr(cbc, 'network_index_conflicts', lambda _index: False)
    archive = isolated / 'ib.tgz'
    cbc.backup_instance('ib', archive)
    shutil.rmtree(store.CONFIGS / 'ib')
    shutil.rmtree(isolated / 'data' / 'ib')
    store.update_instance('ia', lambda raw: raw.update(port=58993))
    with pytest.raises(ValueError, match='端口或网络编号'):
        cbc.restore_instance(archive)
    assert not (store.CONFIGS / 'ib').exists()


def test_delete_during_bind_cannot_leave_a_dangling_subscription(isolated, monkeypatch):
    instance(isolated, proxy_locked_at=None)
    subscription_id, node_id = _subscription()
    read, resume = threading.Event(), threading.Event()
    original = store.materialize_node

    def pause_after_read(*args):
        value = original(*args)
        read.set()
        assert resume.wait(3)
        return value

    monkeypatch.setattr(store, 'materialize_node', pause_after_read)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        binding = pool.submit(store.bind_subscription, 'ifixture', subscription_id, node_id)
        assert read.wait(3)
        deleting = pool.submit(store.delete_subscription, subscription_id)
        resume.set()
        assert binding.result(3)
        with pytest.raises(ValueError, match='还有实例使用'):
            deleting.result(3)
    assert store.selection_map()['ifixture']['subscription_id'] == subscription_id
    assert len(store.load_library()['subscriptions']) == 1


def test_failed_node_cache_rolls_back_the_new_subscription(isolated, monkeypatch):
    original = store._save

    def fail_cache(path, value):
        if path.parent == store.PRIVATE / 'nodes':
            raise OSError('合成的缓存写入失败')
        return original(path, value)

    monkeypatch.setattr(store, '_save', fail_cache)
    with pytest.raises(OSError, match='缓存写入失败'):
        store.add_subscription('审查订阅', 'https://audit.example.test/sub', lambda _: [_node()])
    assert store.public_subscriptions() == []
    monkeypatch.setattr(store, '_save', original)
    identity = store.add_subscription('审查订阅', 'https://audit.example.test/sub', lambda _: [_node()])
    assert store.public_subscriptions()[0]['id'] == identity
    assert store.public_subscriptions()[0]['node_count'] == 1
    store._cache_path(identity).unlink()
    assert store.public_subscriptions()[0]['node_count'] == 0
    again = store.add_subscription('审查订阅', 'https://audit.example.test/sub', lambda _: [_node()])
    assert again == identity and store.public_subscriptions()[0]['node_count'] == 1


def test_reserved_node_names_do_not_become_internal_proxy_names():
    for name in ('ordinary', 'DIRECT', 'GLOBAL', 'REJECT'):
        node = _node(name)
        assert catalog.support_of(node)[0] is True
        config = catalog.mihomo_config(node)
        assert config['proxies'][0]['name'] == catalog.RUNTIME_PROXY_NAME
        assert config['proxy-groups'][0]['proxies'] == [catalog.RUNTIME_PROXY_NAME]
        assert config['proxy-groups'][0]['name'] == 'GLOBAL'


def test_offline_cli_start_does_not_lock_an_empty_proxy(isolated, monkeypatch):
    instance(isolated, proxy_locked_at=None)
    store._write_proxy('ifixture', {'host': '', 'port': 1080, 'username': '', 'password': ''})
    processes = '/usr/lib/chromium/chromium --type=renderer\n'
    monkeypatch.setattr(cbc, 'active', lambda _name: False)
    monkeypatch.setattr(cbc, 'prepare', lambda *_args, **_kwargs: False)
    monkeypatch.setattr(cbc, 'compose', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cbc, 'docker', lambda *_args, **_kwargs: SimpleNamespace(stdout=processes, returncode=0))
    opener = SimpleNamespace(open=lambda *_args, **_kwargs: SimpleNamespace(status=200))
    monkeypatch.setattr(cbc.urllib.request, 'build_opener', lambda *_args, **_kwargs: opener)
    cbc.start('ifixture')
    assert store.proxy_public('ifixture')['mode'] == 'none'
    assert store.proxy_locked(store.instance_by_id('ifixture')['raw']) is False
    core.ensure_proxy_editable(store.instance_by_id('ifixture'))


def test_restore_of_an_active_instance_keeps_recovery(isolated):
    row = instance(isolated)
    core._restore(task('restore'), row)
    current = store.instance_by_id('ifixture')
    assert current['raw']['desired_running'] is True
    assert current['raw']['run_generation'] == row['raw']['run_generation']
    assert not current['trashed_at']
    store.update_instance('ifixture', lambda raw: raw.update(trashed_at='2026-11-16T00:00:00Z'))
    core._restore(task('restore'), store.instance_by_id('ifixture'))
    restored = store.instance_by_id('ifixture')
    assert restored['trashed_at'] is None
    assert restored['raw']['desired_running'] is False


def test_request_body_stops_early_and_rejects_bad_encoding(isolated, monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main

    consumed = {'bytes': 0, 'chunks': 0}

    async def receive():
        consumed['bytes'] += 16384
        consumed['chunks'] += 1
        return {'type': 'http.request', 'body': b'x' * 16384, 'more_body': consumed['chunks'] < 64}

    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': [(b'content-length', b'1048576')]}, receive)
    with pytest.raises(RequestBodyError) as caught:
        asyncio.run(main._body(request))
    assert caught.value.status == 413
    assert consumed['chunks'] == 2 and consumed['bytes'] == 32768

    monkeypatch.setattr(store, 'migrate', lambda: None)
    monkeypatch.setattr(core, 'ensure_worker', lambda: None)
    headers = {'Content-Type': 'application/json', 'X-CBC-Request': '1', 'Origin': 'http://127.0.0.1:5790'}
    with TestClient(main.app, base_url='http://127.0.0.1:5790') as client:
        invalid = client.post('/api/subscriptions', content=b'{invalid', headers=headers)
        encoded = client.post('/api/subscriptions', content=b'{"name":"\xff"}', headers=headers)
    assert invalid.status_code == 400
    assert encoded.status_code == 400
