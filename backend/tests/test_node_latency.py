import io
import json
import queue
import subprocess
import sys
import threading
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'backend'))

import cbc
import manager_core as core
import node_probe
import node_probe_worker as worker
import store
from app.main import app


@pytest.fixture
def library(tmp_path, monkeypatch):
    configs = tmp_path / 'configs'
    private = configs / 'private'
    monkeypatch.setattr(cbc, 'ROOT', tmp_path)
    (tmp_path / 'images.lock.json').write_text(json.dumps({'network': {'reference': 'fixture-network-image'}}))
    for key, value in {
        'ROOT': tmp_path, 'CONFIGS': configs, 'PRIVATE': private,
        'LIBRARY': private / 'library.json', 'EXITS': private / 'exits.json',
        'BACKUPS': configs / 'backups',
    }.items():
        monkeypatch.setattr(store, key, value)
    raw = [
        {'name': '同名节点', 'type': 'ss', 'server': '203.0.113.20', 'port': 443,
         'cipher': 'aes-128-gcm', 'password': 'fixture-secret-one'},
        {'name': '同名节点', 'type': 'ss', 'server': '203.0.113.21', 'port': 443,
         'cipher': 'aes-128-gcm', 'password': 'fixture-secret-two'},
        {'name': 'UDP 节点', 'type': 'hysteria2', 'server': '203.0.113.22', 'port': 443,
         'password': 'fixture-secret-udp'},
    ]
    identity = store.add_subscription('测试订阅', 'https://sub.example.test/?token=fixture-token', lambda _: raw)
    monkeypatch.setattr(core, '_TASKS', {})
    monkeypatch.setattr(core, '_ORDER', [])
    monkeypatch.setattr(core, '_QUEUE', queue.Queue())
    monkeypatch.setattr(core, '_NODE_TEST_QUEUE', queue.Queue())
    return SimpleNamespace(id=identity, nodes=store.load_cached_nodes(identity), raw=raw, configs=configs)


@pytest.fixture
def client(library, monkeypatch):
    monkeypatch.setattr(store, 'migrate', lambda: None)
    monkeypatch.setattr(core, 'ensure_worker', lambda: None)
    with TestClient(app, base_url='http://127.0.0.1:5790') as value:
        yield value


def success(delay=123):
    return {'status': 'ok', 'delay_ms': delay, 'detail': '测试延迟'}


def test_untested_nodes_remain_visible_and_supported_nodes_can_be_selected(library):
    assert len(store.public_nodes(library.id)) == 3
    assert all(node['test'] is None for node in store.public_nodes(library.id))
    assert store.materialize_node(library.id, library.nodes[0]['id'])['password'] == 'fixture-secret-one'
    assert len(node_probe.select_nodes(library.id)) == 2


@pytest.mark.parametrize('selection', [[], 'all', [None], ['missing'], ['../private']])
def test_invalid_selection_is_rejected_before_running_docker(library, selection):
    with pytest.raises(ValueError):
        node_probe.select_nodes(library.id, selection)


def test_unsupported_nodes_and_unknown_subscription_are_rejected(library):
    with pytest.raises(ValueError, match='不支持'):
        node_probe.select_nodes(library.id, [library.nodes[2]['id']])
    with pytest.raises(ValueError, match='订阅不存在'):
        node_probe.select_nodes('../outside')


def test_results_are_public_and_do_not_change_bindings_or_refresh_time(library):
    before = store.LIBRARY.read_bytes()
    store._save(library.configs / 'selection.json', {'ifixture': {'node_id': library.nodes[0]['id']}})
    selection = (library.configs / 'selection.json').read_bytes()
    assert store.save_node_tests(library.id, {library.nodes[0]['id']: success()}) == 1
    rows = store.public_nodes(library.id)
    assert rows[0]['test']['delay_ms'] == 123 and rows[1]['test'] is None
    assert store.public_subscriptions()[0]['nodes_tested_at'] == rows[0]['test']['checked_at']
    assert store.LIBRARY.read_bytes() == before
    assert (library.configs / 'selection.json').read_bytes() == selection
    public = json.dumps(rows)
    assert 'fixture-secret' not in public and 'server' not in public
    assert store._cache_path(library.id).stat().st_mode & 0o777 == 0o600


def test_refresh_preserves_results_for_same_configuration_but_not_rotated_credentials(library):
    store.save_node_tests(library.id, {library.nodes[0]['id']: success()})
    renamed = [{**library.raw[0], 'name': '新显示名称'}, *library.raw[1:]]
    store.refresh_subscription(library.id, lambda _: renamed)
    assert store.public_nodes(library.id)[0]['test']['delay_ms'] == 123
    rotated = [{**renamed[0], 'password': 'rotated-secret'}, *renamed[1:]]
    store.refresh_subscription(library.id, lambda _: rotated)
    assert store.public_nodes(library.id)[0]['test'] is None
    assert store.save_node_tests(library.id, {library.nodes[0]['id']: success(999)}) == 0
    assert store.public_nodes(library.id)[0]['test'] is None


def test_deleted_subscription_does_not_get_resurrected_by_a_finished_test(library):
    store.delete_subscription(library.id)
    assert store.save_node_tests(library.id, {library.nodes[0]['id']: success()}) == 0
    assert not store._cache_path(library.id).exists()


def test_endpoint_uses_separate_queue_and_deduplicates_subscription(client, library):
    path = f'/api/subscriptions/{library.id}/nodes/test'
    response = client.post(path, headers={'X-CBC-Request': '1'}, json={})
    assert response.status_code == 200
    task = response.json()
    assert task['kind'] == 'test-nodes' and task['subscription_id'] == library.id
    assert set(task['node_ids']) == {node['id'] for node in library.nodes[:2]}
    assert 'fixture-secret' not in response.text and 'fixture-token' not in response.text
    assert core._QUEUE.empty() and core._NODE_TEST_QUEUE.get_nowait() == task['id']
    assert client.post(path, headers={'X-CBC-Request': '1'}, json={}).status_code == 400
    internal = core._TASKS[task['id']]
    internal['payload'] = {}
    assert core.public_task(internal)['node_ids'] == task['node_ids']


def test_endpoint_keeps_local_request_protection_and_rejects_stale_ids(client, library):
    path = f'/api/subscriptions/{library.id}/nodes/test'
    assert client.post(path, json={}).status_code == 403
    assert client.post(path, headers={'X-CBC-Request': '1', 'Origin': 'https://external.example.test'}, json={}).status_code == 403
    assert client.post(path, headers={'X-CBC-Request': '1'}, json={'node_ids': ['missing']}).status_code == 400
    assert not core._TASKS


def test_single_node_selection_keeps_cancelled_result_and_unselected_nodes(library, monkeypatch):
    calls = []

    def measure(nodes, quality=False):
        assert len(nodes) == 1 and quality is False
        calls.append(nodes[0]['id'])
        return {nodes[0]['id']: worker.failed('连接失败或超时')}

    monkeypatch.setattr(node_probe, '_measure', measure)
    seen = []

    def progress(completed, total, node):
        seen.append((completed, total, None if node is None else node['id']))
        return completed == 0

    with pytest.raises(node_probe.ProbeCancelled):
        node_probe.test_subscription(library.id, [library.nodes[0]['id']], on_progress=progress)
    assert calls == [library.nodes[0]['id']]
    assert seen[0][0] == 0 and seen[1][0] == 1
    assert store.public_nodes(library.id)[0]['test']['status'] == 'failed'
    assert store.public_nodes(library.id)[1]['test'] is None


def test_queued_task_can_be_cancelled_before_it_starts(library):
    task = {
        'id': 'tcancel', 'kind': 'test-nodes', 'instance_id': '', 'state': 'queued',
        'phase': '测试节点延迟', 'phases': [{'name': '测试节点延迟', 'state': 'pending'}],
        'error': '', 'result': {}, 'payload': {}, 'subscription_id': library.id,
        'node_ids': [library.nodes[0]['id']],
    }
    core._TASKS[task['id']] = task
    core._ORDER.append(task['id'])
    cancelled = core.cancel_task(task['id'])
    assert cancelled['state'] == 'interrupted'
    assert '尚未开始' in cancelled['error']
    running = {**task, 'id': 'tstart', 'kind': 'start', 'state': 'running', 'subscription_id': '', 'node_ids': []}
    core._TASKS[running['id']] = running
    core._ORDER.append(running['id'])
    with pytest.raises(ValueError, match='不能取消'):
        core.cancel_task(running['id'])


def test_failed_result_is_saved_and_does_not_disable_the_node(library, monkeypatch):
    identity = library.nodes[0]['id']
    monkeypatch.setattr(node_probe, '_measure', lambda nodes: {identity: worker.failed('连接失败或超时')})
    result = node_probe.test_subscription(library.id, [identity])
    assert result['failed'] == 1 and result['succeeded'] == 0
    node = store.public_nodes(library.id)[0]
    assert node['test']['status'] == 'failed' and node['test']['delay_ms'] is None
    assert node['supported'] and store.materialize_node(library.id, identity)


def test_docker_receives_credentials_only_on_stdin_and_removes_its_temporary_container(library, monkeypatch):
    calls = []

    def docker(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps({library.nodes[0]['id']: success(0)}))

    monkeypatch.setattr(cbc, 'docker', docker)
    results = node_probe._measure(library.nodes[:1])
    assert results[library.nodes[0]['id']]['delay_ms'] == 0
    args, options = calls[0]
    assert 'fixture-network-image' in args
    assert 'fixture-secret-one' in options['input']
    assert 'fixture-secret-one' not in ' '.join(args)
    assert not any(value in args for value in ('-p', '--publish', '-v', '--volume', '--mount', '--privileged'))
    name = args[args.index('--name') + 1]
    assert calls[-1][0] == ('rm', '-f', name)


def test_docker_timeout_still_cleans_up_and_hides_private_diagnostics(library, monkeypatch):
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[0] == 'run':
            raise subprocess.TimeoutExpired('private-fixture-command', 1, stderr='fixture-secret-one')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cbc, 'docker', docker)
    with pytest.raises(ValueError, match='测速超时') as caught:
        node_probe._measure(library.nodes[:1])
    assert 'fixture-secret' not in str(caught.value)
    assert calls[-1][:2] == ('rm', '-f')


def test_bad_node_does_not_break_other_nodes_and_duplicate_names_use_ids(monkeypatch):
    nodes = [{'id': 'n1', 'config': {'name': '同名', 'password': 'bad'}},
             {'id': 'n2', 'config': {'name': '同名', 'password': 'good'}},
             {'id': 'n3', 'config': {'name': '同名', 'password': 'timeout'}}]
    stdout = io.StringIO()
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(nodes)))
    monkeypatch.setattr(sys, 'stdout', stdout)

    def validate(args, **kwargs):
        payload = json.loads(Path(args[-1]).read_text())
        return SimpleNamespace(returncode=int(payload['proxies'][0]['password'] == 'bad'))

    class Process:
        def poll(self): return None
        def terminate(self): pass
        def wait(self, **kwargs): pass

    class Opener:
        def open(self, url, **kwargs):
            if '/n3/delay?' in url:
                raise TimeoutError('private-diagnostic')
            return closing(io.BytesIO(b'{"delay":42}'))

    monkeypatch.setattr(subprocess, 'run', validate)
    monkeypatch.setattr(subprocess, 'Popen', lambda *args, **kwargs: Process())
    monkeypatch.setattr(worker.urllib.request, 'build_opener', lambda *args: Opener())
    worker.main()
    results = json.loads(stdout.getvalue())
    assert results['n1']['status'] == 'failed' and results['n3']['status'] == 'failed'
    assert results['n2']['delay_ms'] == 42
    assert 'password' not in stdout.getvalue() and 'private-diagnostic' not in stdout.getvalue()


def test_long_node_test_does_not_block_lifecycle_queue_or_take_global_lock(library, monkeypatch, tmp_path):
    entered, release, normal_done = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(cbc, 'ROOT', tmp_path)
    monkeypatch.setattr(core, '_persist_tasks', lambda: None)

    def dispatch(task):
        if task['kind'] == 'test-nodes':
            assert not (tmp_path / '.manager.lock').exists()
            entered.set()
            release.wait(3)
        else:
            normal_done.set()

    monkeypatch.setattr(core, '_dispatch', dispatch)
    probe = core.enqueue('test-nodes', payload={'subscription_id': library.id, 'node_ids': [library.nodes[0]['id']]})
    threading.Thread(target=core._worker, args=(core._NODE_TEST_QUEUE,), daemon=True).start()
    try:
        assert entered.wait(1)
        core.enqueue('start', 'ifixture')
        threading.Thread(target=core._worker, args=(core._QUEUE,), daemon=True).start()
        assert normal_done.wait(1)
    finally:
        release.set()
        internal = core._TASKS[probe['id']]
        for _ in range(100):
            if internal['state'] == 'succeeded' and not internal['payload']:
                break
            threading.Event().wait(0.01)
    assert internal['state'] == 'succeeded' and not internal['payload']
