"""接手未完成开发后的功能闭环回归。仅使用临时目录及明确的 Docker/网络夹具。"""
import json
import queue
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import catalog
import cbc
import geolocation
import manager_core as core
import monitor
import node_probe
import store
from app import main
from test_reliability import isolated, instance, snapshot, task


def nodes():
    return [{'name': name, 'type': 'ss', 'server': address, 'port': 443,
             'cipher': 'aes-128-gcm', 'password': 'synthetic-only'}
            for name, address in [('node-a', '8.8.8.8'), ('node-b', '1.1.1.1')]]


def subscription():
    identity = store.add_subscription('原订阅', 'https://fixture.example.test/original', lambda _: nodes())
    return identity, store.load_cached_nodes(identity)


class FiniteQueue(queue.Queue):
    def get(self, *args, **kwargs):
        value = super().get(*args, **kwargs)
        if value is None:
            raise SystemExit
        return value


def test_running_cancellation_keeps_current_result_and_worker_processes_next_task(isolated, monkeypatch):
    identity, cached = subscription()
    work = FiniteQueue()
    monkeypatch.setattr(core, '_NODE_TEST_QUEUE', work)
    first = core.enqueue('test-nodes', payload={'subscription_id': identity, 'node_ids': [row['id'] for row in cached]})
    second = core.enqueue('check-node-quality', payload={'subscription_id': identity, 'node_ids': [cached[0]['id']]})
    measured = []

    def measure(selected, quality=False):
        measured.append((selected[0]['id'], quality))
        if not quality:
            cancelled = core.cancel_task(first['id'])
            assert cancelled['state'] == 'running' and cancelled['cancel_requested']
            with pytest.raises(ValueError, match='已有节点检测'):
                core.enqueue('test-nodes', payload={'subscription_id': identity, 'node_ids': [cached[0]['id']]})
            return {selected[0]['id']: {'status': 'ok', 'delay_ms': 42, 'detail': 'fixture'}}
        return {selected[0]['id']: {'status': 'ok', 'ip': '8.8.8.8', 'detail': 'fixture'}}

    monkeypatch.setattr(node_probe, '_measure', measure)
    work.put(None)
    with pytest.raises(SystemExit):
        core._worker(work)
    cancelled = core.get_task(first['id'])
    assert cancelled['state'] == 'interrupted' and cancelled['result']['completed'] == 1
    assert cancelled['result']['failed'] == 0
    assert core.get_task(second['id'])['state'] == 'succeeded'
    assert measured == [(cached[0]['id'], False), (cached[0]['id'], True)]
    current = store.load_cached_nodes(identity)
    assert current[0]['test']['delay_ms'] == 42 and current[0]['quality']['status'] == 'ok'
    assert current[1]['test'] is None


def test_cancel_at_dispatch_completion_does_not_exit_worker(isolated, monkeypatch):
    identity, cached = subscription()
    work = FiniteQueue()
    monkeypatch.setattr(core, '_NODE_TEST_QUEUE', work)
    first = core.enqueue('test-nodes', payload={'subscription_id': identity, 'node_ids': [cached[0]['id']]})
    second = core.enqueue('check-node-quality', payload={'subscription_id': identity, 'node_ids': [cached[0]['id']]})
    dispatched = []

    def dispatch(current):
        dispatched.append(current['id'])
        if current['id'] == first['id']:
            core.cancel_task(current['id'])

    monkeypatch.setattr(core, '_dispatch', dispatch)
    work.put(None)
    with pytest.raises(SystemExit):
        core._worker(work)
    assert dispatched == [first['id'], second['id']]
    assert core.get_task(first['id'])['state'] == 'interrupted'
    assert core.get_task(second['id'])['state'] == 'succeeded'


def test_quality_progress_keeps_two_second_spacing(isolated, monkeypatch):
    identity, cached = subscription()
    events = []
    monkeypatch.setattr(node_probe.time, 'sleep', lambda seconds: events.append(('wait', seconds)))
    monkeypatch.setattr(node_probe, '_measure', lambda selected, quality=False:
                        events.append(('measure', selected[0]['id'])) or {selected[0]['id']: {'status': 'partial', 'detail': 'fixture'}})
    result = node_probe.check_quality(identity, [row['id'] for row in cached], on_progress=lambda *_: True)
    assert events == [('measure', cached[0]['id']), ('wait', 2), ('measure', cached[1]['id'])]
    assert result['completed'] == 2 and result['partial'] == 2


def test_recovery_country_mismatch_disconnects_proxy_and_pauses_retries(isolated, monkeypatch):
    row = instance(isolated)
    store._save(store.CONFIGS / 'ifixture/runtime/firewall.json', {'upstream_ipv4': ['8.8.8.8']})
    store.save_exit('ifixture', 'ok', '8.8.4.4', '', 'US')
    before = store.read_proxy('ifixture')
    events = []
    monkeypatch.setattr(core, '_preflight_boot', lambda _: None)
    monkeypatch.setattr(core, 'runtime_snapshot', lambda force=False: snapshot())
    monkeypatch.setattr(cbc, 'running_browser_identity', lambda _: {'image': 'fixture', 'revision': 'fixture'})
    monkeypatch.setattr(cbc, 'prepare', lambda *args, **kwargs: None)
    monkeypatch.setattr(cbc, 'compose', lambda *args, **kwargs: events.append('proxy-up'))
    monkeypatch.setattr(cbc, 'docker', lambda *args, **kwargs: events.append(args) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(core, 'probe_proxy', lambda _: {'status': 'ok', 'ip': '1.1.1.1', 'country_code': 'JP', 'detail': ''})
    monkeypatch.setattr(geolocation, 'country_code', lambda value: 'US' if value == '8.8.8.8' else None)
    with pytest.raises(RuntimeError, match='已断开本实例代理'):
        monitor.recover(task('recover', generation=row['raw']['run_generation']))
    assert ('stop', '--time', '5', 'cbc-ifixture-proxy') in events
    assert store.instance_by_id('ifixture')['raw']['recovery']['exhausted'] is True
    assert store.load_exits()['ifixture']['status'] == 'failed'
    assert store.read_proxy('ifixture') == before
    assert (isolated / 'data/ifixture/profile-marker').read_text() == 'preserved'


def test_country_stop_failure_does_not_claim_disconnection(isolated, monkeypatch):
    instance(isolated)
    monkeypatch.setattr(geolocation, 'country_code', lambda _: None)
    monkeypatch.setattr(cbc, 'docker', lambda *args, **kwargs: SimpleNamespace(returncode=1))
    with pytest.raises(cbc.CountryPolicyError, match='无法确认代理已断开'):
        cbc.require_same_country('ifixture', {'status': 'unknown'})
    assert store.load_exits()['ifixture']['status'] == 'failed'


def test_country_rejection_disconnects_even_when_exit_storage_is_damaged(isolated, monkeypatch):
    instance(isolated)
    store.EXITS.write_text('{invalid')
    events = []
    monkeypatch.setattr(geolocation, 'country_code', lambda _: None)
    monkeypatch.setattr(cbc, 'docker', lambda *args, **kwargs: events.append(args) or SimpleNamespace(returncode=0))
    with pytest.raises(cbc.CountryPolicyError, match='已断开本实例代理'):
        cbc.require_same_country('ifixture', {'status': 'unknown'})
    assert events == [('stop', '--time', '5', 'cbc-ifixture-proxy')]
    assert store.EXITS.read_text() == '{invalid'


def test_cli_country_failure_never_starts_browser(isolated, monkeypatch):
    instance(isolated)
    store._save(store.CONFIGS / 'ifixture/runtime/firewall.json', {'upstream_ipv4': ['8.8.8.8']})
    store.save_exit('ifixture', 'ok', '8.8.4.4', '', 'US')
    commands = []
    monkeypatch.setattr(cbc, 'active', lambda _: False)
    monkeypatch.setattr(cbc, 'prepare', lambda *args, **kwargs: True)
    monkeypatch.setattr(cbc, 'compose', lambda *args, **kwargs: commands.append(args))
    monkeypatch.setattr(cbc, 'observe_exit_info', lambda _: {'ip': '1.1.1.1', 'country_code': 'JP'})
    monkeypatch.setattr(geolocation, 'country_code', lambda value: 'US' if value == '8.8.8.8' else 'JP')
    monkeypatch.setattr(cbc, 'docker', lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='', stderr=''))
    monkeypatch.setattr(cbc, 'down', lambda *args, **kwargs: None)
    with pytest.raises(cbc.CountryPolicyError):
        cbc.start('ifixture')
    assert commands and all('browser' not in command for command in commands)
    assert 'guard' in commands[0] and 'proxy' in commands[0]
    assert store.instance_by_id('ifixture')['raw']['desired_running'] is False


def test_url_update_is_background_and_keeps_binding(isolated, monkeypatch):
    identity, cached = subscription()
    instance(isolated)
    store.bind_subscription('ifixture', identity, cached[0]['id'])
    before_instance = (store.CONFIGS / 'ifixture/instance.json').read_bytes()
    before_library = store.LIBRARY.read_bytes()
    monkeypatch.setattr(store, 'migrate', lambda: None)
    monkeypatch.setattr(core, 'ensure_worker', lambda: None)
    fetch = Mock(side_effect=AssertionError('HTTP 接受任务时不能下载订阅'))
    monkeypatch.setattr(catalog, 'fetch_proxies', fetch)
    new_url = 'https://fixture.example.test/updated?token=synthetic-only'
    with TestClient(main.app, base_url='http://127.0.0.1:5790') as client:
        response = client.patch(f'/api/subscriptions/{identity}', headers={'X-CBC-Request': '1'},
                                json={'name': '新名称', 'url': new_url})
        assert response.status_code == 200
        accepted = response.json()
        assert accepted['kind'] == 'update-subscription' and accepted['state'] == 'queued'
        assert accepted['subscription_id'] == identity and new_url not in response.text
        assert client.get('/api/tasks').status_code == 200
    assert store.LIBRARY.read_bytes() == before_library
    fetch.assert_not_called()
    monkeypatch.setattr(catalog, 'fetch_proxies', lambda url: nodes())
    core._dispatch(core._TASKS[accepted['id']])
    updated = store.load_library()['subscriptions'][0]
    assert updated['id'] == identity and updated['name'] == '新名称' and updated['url'] == new_url
    assert store.proxy_public('ifixture')['node_id'] == cached[0]['id']
    assert (store.CONFIGS / 'ifixture/instance.json').read_bytes() == before_instance


def test_url_update_rolls_back_address_cache_and_binding_on_commit_failure(isolated, monkeypatch):
    identity, cached = subscription()
    instance(isolated)
    store.bind_subscription('ifixture', identity, cached[0]['id'])
    paths = (store.LIBRARY, store._cache_path(identity), store.CONFIGS / 'selection.json')
    before = {path: path.read_bytes() for path in paths}
    save = store._save

    def fail_library(path, value):
        if path == store.LIBRARY:
            raise OSError('合成的提交失败')
        return save(path, value)

    monkeypatch.setattr(store, '_save', fail_library)
    replacement = [{**node, 'password': 'rotated-synthetic-only'} for node in nodes()]
    with pytest.raises(OSError, match='提交失败'):
        store.replace_subscription_url(identity, 'https://fixture.example.test/rotated', lambda _: replacement, name='新名称')
    assert all(path.read_bytes() == original for path, original in before.items())


def test_updated_url_is_unique_and_old_address_can_be_added_without_overwriting(isolated):
    identity, _ = subscription()
    new_url = 'https://fixture.example.test/moved'
    store.replace_subscription_url(identity, new_url, lambda _: nodes())
    with pytest.raises(ValueError, match='已经添加过'):
        store.add_subscription('重复地址', new_url, lambda _: nodes())
    old_identity = store.add_subscription('旧地址另存', 'https://fixture.example.test/original', lambda _: nodes())
    assert old_identity != identity
    current = {row['id']: row for row in store.load_library()['subscriptions']}
    assert current[identity]['url'] == new_url
    assert current[old_identity]['url'] == 'https://fixture.example.test/original'


@pytest.mark.parametrize('body', [{'name': '', 'url': 'https://fixture.example.test/updated'},
                                  {'name': '合法名称', 'url': 'http://fixture.example.test/updated'},
                                  {'name': '合法名称', 'url': None}])
def test_invalid_url_update_is_not_enqueued(isolated, monkeypatch, body):
    identity, _ = subscription()
    before = store.LIBRARY.read_bytes()
    monkeypatch.setattr(store, 'migrate', lambda: None)
    monkeypatch.setattr(core, 'ensure_worker', lambda: None)
    with TestClient(main.app, base_url='http://127.0.0.1:5790') as client:
        response = client.patch(f'/api/subscriptions/{identity}', headers={'X-CBC-Request': '1'}, json=body)
    assert response.status_code == 400
    assert core.list_tasks() == [] and store.LIBRARY.read_bytes() == before
