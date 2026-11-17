"""网页节点检测按批执行，Docker、网络和等待均使用明确的隔离夹具。"""
import json
import io
import sys
from contextlib import closing
from types import SimpleNamespace

import pytest

import cbc
import manager_core as core
import node_probe
import node_probe_worker as worker
import store
from test_node_latency import library as library


def many_nodes(library, count):
    raw = [{**library.raw[0], 'name': f'fixture-node-{index}', 'server': f'203.0.113.{30 + index}'}
           for index in range(count)]
    store.refresh_subscription(library.id, lambda _: raw)
    return store.load_cached_nodes(library.id)


def fake_docker(monkeypatch, batches, quality=False):
    def docker(*args, **kwargs):
        if args[0] == 'rm':
            return SimpleNamespace(returncode=0)
        assert args[0] == 'run'
        payload = json.loads(kwargs['input'])
        nodes = payload['nodes'] if quality else payload
        if quality:
            assert payload['mode'] == 'quality'
        batches.append([node['id'] for node in nodes])
        result = ({'status': 'ok', 'ip': '8.8.8.8', 'asn': 15169, 'fraud_score': 0,
                   'is_residential': False, 'is_broadcast': False}
                  if quality else {'status': 'ok', 'delay_ms': 42, 'detail': 'fixture'})
        return SimpleNamespace(returncode=0, stdout=json.dumps({node['id']: result for node in nodes}))

    monkeypatch.setattr(cbc, 'docker', docker)


def test_web_latency_uses_three_containers_and_six_task_writes_for_twenty_nodes(library, monkeypatch):
    nodes = many_nodes(library, 20)
    batches, writes = [], []
    fake_docker(monkeypatch, batches)
    task = {'state': 'running'}
    monkeypatch.setattr(core, '_persist_tasks', lambda: writes.append(dict(task['progress'])))
    result = node_probe.test_subscription(library.id, [node['id'] for node in nodes],
                                          on_progress=lambda *args: core._probe_progress(task, *args))
    assert [len(batch) for batch in batches] == [8, 8, 4]
    assert [row['completed'] for row in writes] == [0, 8, 8, 16, 16, 20]
    assert result['completed'] == result['succeeded'] == 20
    assert all(node['test']['delay_ms'] == 42 for node in store.public_nodes(library.id))


def test_cancel_keeps_completed_batch_and_does_not_start_the_next_container(library, monkeypatch):
    nodes = many_nodes(library, 20)
    batches = []
    fake_docker(monkeypatch, batches)
    with pytest.raises(node_probe.ProbeCancelled) as caught:
        node_probe.test_subscription(library.id, [node['id'] for node in nodes],
                                      on_progress=lambda completed, *_: completed < 8)
    assert [len(batch) for batch in batches] == [8]
    assert caught.value.result['completed'] == caught.value.result['succeeded'] == 8
    current = store.public_nodes(library.id)
    assert all(node['test']['delay_ms'] == 42 for node in current[:8])
    assert all(node['test'] is None for node in current[8:])


def test_quality_shares_a_container_per_batch_and_keeps_boundary_spacing(library, monkeypatch):
    nodes = many_nodes(library, 10)
    batches, waits = [], []
    fake_docker(monkeypatch, batches, quality=True)
    monkeypatch.setattr(node_probe.time, 'sleep', waits.append)
    result = node_probe.check_quality(library.id, [node['id'] for node in nodes], on_progress=lambda *_: True)
    assert [len(batch) for batch in batches] == [8, 2]
    assert waits == [2]
    assert result['completed'] == result['succeeded'] == 10
    assert all(node['quality']['fraud_score'] == 0 for node in store.public_nodes(library.id))


def test_cancel_before_first_batch_runs_no_container(library, monkeypatch):
    nodes = many_nodes(library, 10)
    batches = []
    fake_docker(monkeypatch, batches)
    with pytest.raises(node_probe.ProbeCancelled) as caught:
        node_probe.test_subscription(library.id, [node['id'] for node in nodes], on_progress=lambda *_: False)
    assert caught.value.result['completed'] == 0
    assert batches == []


def test_quality_worker_reuses_engine_and_keeps_two_second_node_spacing(library, monkeypatch):
    selected = [{'id': node['id'], 'config': node['config']} for node in library.nodes[:2]]
    output, launches, requests, waits = io.StringIO(), [], [], []
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({'mode': 'quality', 'nodes': selected})))
    monkeypatch.setattr(sys, 'stdout', output)
    monkeypatch.setattr(worker.subprocess, 'run', lambda *_args, **_kwargs: SimpleNamespace(returncode=0))
    process = SimpleNamespace(poll=lambda: None, terminate=lambda: None, wait=lambda **_kwargs: None)
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *_args, **_kwargs: launches.append(True) or process)
    opener = SimpleNamespace(open=lambda *_args, **_kwargs: closing(io.BytesIO(b'{}')))
    monkeypatch.setattr(worker.urllib.request, 'build_opener', lambda *_args: opener)
    monkeypatch.setattr(worker.socket, 'create_connection', lambda *_args, **_kwargs: closing(io.BytesIO()))
    monkeypatch.setattr(worker.time, 'sleep', waits.append)
    monkeypatch.setattr(worker, 'measure_quality', lambda port: requests.append(port) or worker.quality_failed('fixture'))
    worker.main()
    assert launches == [True]
    assert requests == [18080, 18081] and waits == [2]
    assert set(json.loads(output.getvalue())) == {node['id'] for node in selected}
