import io
import json
import subprocess
import sys
import urllib.error
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_node_latency import client as client, library as library
import manager_core as core
import node_probe
import node_probe_worker as worker
import store


def payload(**changes):
    return {'ip': '8.8.8.8', 'asn': 15169, 'isResidential': False,
            'isBroadcast': False, 'fraudScore': 0, **changes}


def test_zero_score_and_false_attributes_are_real_values_and_private_fields_are_not_copied():
    result = worker.normalize_quality(payload(userAgent='private-agent', password='private-password'))
    assert result['status'] == 'ok' and result['fraud_score'] == 0
    assert result['is_residential'] is False and result['is_broadcast'] is False
    assert 'private-' not in json.dumps(result)


@pytest.mark.parametrize('score', [None, True, '0', -1, 101, float('nan'), float('inf')])
def test_missing_or_invalid_score_is_unknown(score):
    result = worker.normalize_quality(payload(fraudScore=score))
    assert result['fraud_score'] is None and result['status'] == 'partial'


def test_omitted_or_string_attributes_do_not_become_residential_or_datacenter():
    result = worker.normalize_quality({'ip': '8.8.8.8', 'isResidential': 'false', 'isBroadcast': 0})
    assert result['is_residential'] is None and result['is_broadcast'] is None
    assert result['asn'] is None and result['fraud_score'] is None


@pytest.mark.parametrize('address', ['', None, '127.0.0.1', '10.0.0.1', 'not-an-ip'])
def test_invalid_or_private_exit_is_rejected(address):
    with pytest.raises(ValueError):
        worker.normalize_quality(payload(ip=address))


def test_quality_request_uses_its_assigned_proxy_and_has_bounded_timeout(monkeypatch):
    handlers = []

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == 'https://my.ippure.com/v1/info'
            assert timeout == worker.QUALITY_TIMEOUT
            return closing(io.BytesIO(json.dumps(payload()).encode()))

    def build(handler):
        handlers.append(handler.proxies)
        return Opener()

    monkeypatch.setattr(worker.urllib.request, 'build_opener', build)
    assert worker.measure_quality(18081)['fraud_score'] == 0
    assert handlers == [{'http': 'http://127.0.0.1:18081', 'https': 'http://127.0.0.1:18081'}]


@pytest.mark.parametrize('error', [TimeoutError('private-secret'),
                                 urllib.error.HTTPError(worker.IPPURE_URL, 429, 'private-secret', {}, None)])
def test_failed_request_keeps_fields_unknown_and_hides_diagnostics(monkeypatch, error):
    class Opener:
        def open(self, *args, **kwargs):
            raise error

    monkeypatch.setattr(worker.urllib.request, 'build_opener', lambda *_: Opener())
    result = worker.measure_quality(18080)
    assert result['status'] == 'failed'
    assert result['ip'] is None and result['fraud_score'] is None
    assert 'private-secret' not in json.dumps(result)


def test_results_preserve_latency_and_bindings_and_invalidate_rotated_nodes(library):
    identity = library.nodes[0]['id']
    store.save_node_tests(library.id, {identity: {'status': 'ok', 'delay_ms': 42}})
    store._save(library.configs / 'selection.json', {'instance-a': {'node_id': identity}})
    bindings = (library.configs / 'selection.json').read_bytes()
    library_before = store.LIBRARY.read_bytes()
    assert store.save_node_quality(library.id, {identity: worker.normalize_quality(payload())}) == 1
    row = store.public_nodes(library.id)[0]
    assert row['quality']['fraud_score'] == 0 and row['test']['delay_ms'] == 42
    assert store.public_subscriptions()[0]['nodes_quality_checked_at'] == row['quality']['checked_at']
    assert store.LIBRARY.read_bytes() == library_before
    assert (library.configs / 'selection.json').read_bytes() == bindings
    assert store._cache_path(library.id).stat().st_mode & 0o777 == 0o600
    assert 'fixture-secret' not in json.dumps(store.public_nodes(library.id))
    renamed = [{**library.raw[0], 'name': '改名后的节点'}, *library.raw[1:]]
    store.refresh_subscription(library.id, lambda _: renamed)
    assert store.public_nodes(library.id)[0]['quality'] == row['quality']
    rotated = [{**renamed[0], 'password': 'new-private-secret'}, *renamed[1:]]
    store.refresh_subscription(library.id, lambda _: rotated)
    assert store.public_nodes(library.id)[0]['quality'] is None
    assert store.save_node_quality(library.id, {identity: row['quality']}) == 0
    store.delete_subscription(library.id)
    assert store.save_node_quality(library.id, {identity: row['quality']}) == 0
    assert not store._cache_path(library.id).exists()


def test_quality_endpoint_uses_probe_queue_and_local_request_protection(client, library):
    path = f'/api/subscriptions/{library.id}/nodes/quality'
    assert client.post(path, json={}).status_code == 403
    assert client.post(path, headers={'X-CBC-Request': '1', 'Origin': 'https://elsewhere.test'}, json={}).status_code == 403
    for identities in [['missing'], [library.nodes[2]['id']], []]:
        assert client.post(path, headers={'X-CBC-Request': '1'}, json={'node_ids': identities}).status_code == 400
    assert not core._TASKS
    response = client.post(path, headers={'X-CBC-Request': '1'}, json={'node_ids': [library.nodes[0]['id']]})
    assert response.status_code == 200
    task = response.json()
    assert task['kind'] == 'check-node-quality' and task['subscription_id'] == library.id
    assert task['node_ids'] == [library.nodes[0]['id']]
    assert core._QUEUE.empty() and core._NODE_TEST_QUEUE.get_nowait() == task['id']
    assert client.post(path, headers={'X-CBC-Request': '1'}, json={}).status_code == 400
    assert 'fixture-secret' not in response.text and 'fixture-token' not in response.text


def test_quality_summary_distinguishes_missing_fields_from_connection_failure(library, monkeypatch):
    selected = library.nodes[:2]
    values = {selected[0]['id']: worker.normalize_quality({'ip': '8.8.8.8'}),
              selected[1]['id']: worker.quality_failed('暂不可用')}

    def measure(nodes, quality=False):
        assert quality and len(nodes) == 2
        return values

    monkeypatch.setattr(node_probe, '_measure', measure)
    result = node_probe.check_quality(library.id, [node['id'] for node in selected])
    assert result['partial'] == 1 and result['failed'] == 1 and result['succeeded'] == 0
    assert store.public_nodes(library.id)[0]['quality']['fraud_score'] is None
    assert all(node['supported'] for node in store.public_nodes(library.id)[:2])


def test_docker_credentials_stay_on_stdin_and_results_are_whitelisted(library, monkeypatch):
    identity = library.nodes[0]['id']
    calls = []

    def docker(*args, **kwargs):
        calls.append((args, kwargs))
        value = {**worker.normalize_quality(payload()), 'password': 'unexpected-private-secret'}
        return SimpleNamespace(returncode=0, stdout=json.dumps({identity: value}))

    monkeypatch.setattr(node_probe.cbc, 'docker', docker)
    result = node_probe._measure(library.nodes[:1], quality=True)
    assert result[identity]['fraud_score'] == 0
    assert 'private-secret' not in json.dumps(result)
    arguments, options = calls[0]
    assert 'fixture-network-image' in arguments
    assert 'fixture-secret' not in str(arguments)
    request = json.loads(options['input'])
    assert request['mode'] == 'quality' and request['nodes'][0]['config']['password'] == 'fixture-secret-one'
    assert calls[-1][0][:2] == ('rm', '-f')


def test_quality_worker_does_not_fallback_when_one_node_is_invalid(monkeypatch):
    nodes = [{'id': 'n1', 'config': {'password': 'bad'}}, {'id': 'n2', 'config': {'password': 'good'}}]
    stdout, assigned = io.StringIO(), []
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({'mode': 'quality', 'nodes': nodes})))
    monkeypatch.setattr(sys, 'stdout', stdout)

    def validate(args, **kwargs):
        config = json.loads(Path(args[-1]).read_text())
        return SimpleNamespace(returncode=int(config['proxies'][0]['password'] == 'bad'))

    class Process:
        def poll(self): return None
        def terminate(self): pass
        def wait(self, **kwargs): pass

    class Opener:
        def open(self, *args, **kwargs): return closing(io.BytesIO(b'{}'))

    def popen(args, **kwargs):
        config = json.loads(Path(args[-1]).read_text())
        assert config['listeners'] == [{'name': 'quality-0', 'type': 'http', 'listen': '127.0.0.1', 'port': 18080, 'proxy': 'n2'}]
        assert config['rules'] == ['MATCH,REJECT'] and not any(proxy['name'] == 'n1' for proxy in config['proxies'])
        return Process()

    def measure(port):
        assigned.append(port)
        return worker.normalize_quality(payload())

    monkeypatch.setattr(subprocess, 'run', validate)
    monkeypatch.setattr(subprocess, 'Popen', popen)
    monkeypatch.setattr(worker.urllib.request, 'build_opener', lambda *_: Opener())
    monkeypatch.setattr(worker, 'measure_quality', measure)
    monkeypatch.setattr(worker.socket, 'create_connection', lambda *args, **kwargs: closing(io.BytesIO()))
    worker.main()
    results = json.loads(stdout.getvalue())
    assert results['n1']['status'] == 'failed' and results['n2']['status'] == 'ok'
    assert assigned == [18080] and 'password' not in stdout.getvalue()
