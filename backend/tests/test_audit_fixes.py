"""后台故障收尾、持久化和订阅链路回归；只使用临时文件与显式夹具。"""
import io
import json
from pathlib import Path
import queue
import shutil
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import catalog
import cbc
import manager_core as core
import store
from test_reliability import isolated as isolated, instance, task


@pytest.fixture
def workspace(isolated, monkeypatch):
    monkeypatch.setattr(core, '_NODE_TEST_QUEUE', queue.Queue())
    monkeypatch.setattr(core, 'invalidate_runtime', lambda: None)
    return isolated


def node(name='original', password='fixture-only-password'):
    return {'name': name, 'type': 'ss', 'server': '192.0.2.9', 'port': 443,
            'cipher': 'aes-128-gcm', 'password': password}


def subscription():
    identity = store.add_subscription('测试订阅', 'https://audit.example.test/sub', lambda _: [node()])
    return identity, store.load_cached_nodes(identity)[0]['id']


@pytest.mark.parametrize('kind', ['stop', 'trash'])
@pytest.mark.parametrize('failure', ['query', 'down'])
def test_failed_stop_never_succeeds_or_moves_to_trash(workspace, monkeypatch, kind, failure):
    row = instance(workspace)
    runtime = row['path'].parent / 'runtime'
    runtime.mkdir()
    (runtime / 'compose.env').write_text('fixture')

    def docker(*args, **kwargs):
        assert kwargs.get('check', True), '停止相关命令必须检查失败'
        if args[0] == 'compose' or failure == 'query':
            raise RuntimeError('fixture Docker failure')
        return SimpleNamespace(returncode=0, stdout='')

    monkeypatch.setattr(cbc, 'docker', docker)
    with pytest.raises(RuntimeError, match='Docker failure'):
        core._dispatch(task(kind))
    current = store.instance_by_id('ifixture')
    assert not current['trashed_at']
    assert current['raw']['desired_running'] is False
    assert (workspace / 'data/ifixture/profile-marker').read_text() == 'preserved'


@pytest.mark.parametrize('remaining', ['', 'cbc-ifixture-proxy\n'])
def test_down_handles_missing_runtime_without_hiding_leftover_components(workspace, monkeypatch, remaining):
    instance(workspace)
    monkeypatch.setattr(cbc, 'docker', Mock(return_value=SimpleNamespace(stdout=remaining)))
    if remaining:
        with pytest.raises(RuntimeError, match='尚未清理'):
            cbc.down('ifixture')
    else:
        cbc.down('ifixture')
    assert cbc.docker.call_args.args[0] == 'ps'


@pytest.mark.parametrize('state,expected', [('', False), ('exited', False), ('running', True), ('paused', True), ('restarting', True)])
def test_active_distinguishes_absence_from_query_failure(workspace, monkeypatch, state, expected):
    monkeypatch.setattr(cbc, 'docker', Mock(return_value=SimpleNamespace(stdout=state)))
    assert cbc.active('ifixture') is expected
    cbc.docker.side_effect = RuntimeError('Docker unavailable')
    with pytest.raises(RuntimeError, match='unavailable'):
        cbc.active('ifixture')


@pytest.mark.parametrize('error', [RuntimeError('Web UI 未就绪'), RuntimeError('Chromium 未就绪'), cbc.SandboxError('Sandbox 无效')])
def test_late_start_failure_cleans_up_started_components(workspace, monkeypatch, error):
    instance(workspace)
    events = []
    running = set()
    monkeypatch.setattr(core, '_preflight_boot', lambda _: None)
    monkeypatch.setattr(core, '_require_docker', lambda: None)
    monkeypatch.setattr(cbc, 'active', lambda _: 'browser' in running)
    monkeypatch.setattr(cbc, 'prepare', lambda *a, **kw: True)
    monkeypatch.setattr(cbc, 'probe_proxy', lambda _: {'status': 'ok', 'ip': '1.1.1.1', 'detail': ''})
    monkeypatch.setattr(cbc, 'require_same_country', lambda *_args: None)
    monkeypatch.setattr(cbc, 'wait_until_ready', Mock(side_effect=error))

    def compose(slug, *args, **kwargs):
        running.update(role for role in ('guard', 'proxy', 'browser') if role in args)

    def close(_):
        events.append('close')
        running.discard('browser')

    def down(*args, **kwargs):
        events.append('down')
        running.clear()

    monkeypatch.setattr(cbc, 'compose', compose)
    monkeypatch.setattr(cbc, 'close_browser', close)
    monkeypatch.setattr(cbc, 'down', down)
    def docker(*args, **kwargs):
        if args[0] == 'logs':
            events.append('logs-' + args[-1].rsplit('-', 1)[-1])
            return SimpleNamespace(stdout='fixture log', stderr='')
        assert args[0] == 'stop'
        events.append('disconnect-proxy')

    monkeypatch.setattr(cbc, 'docker', docker)
    with pytest.raises(type(error), match=str(error)):
        core._dispatch(task('start'))
    assert not running
    assert events[-2:] == ['close', 'down']
    assert events[:3] == ['logs-guard', 'logs-proxy', 'logs-browser']
    if isinstance(error, cbc.SandboxError):
        assert events[3] == 'disconnect-proxy'
    assert store.instance_by_id('ifixture')['raw']['desired_running'] is False
    assert (workspace / 'data/ifixture/profile-marker').read_text() == 'preserved'


@pytest.mark.parametrize('sandbox', [False, True])
def test_failed_cleanup_keeps_close_confirmation_protection(workspace, monkeypatch, sandbox):
    instance(workspace)
    events = []
    error = cbc.SandboxError('Sandbox 无效') if sandbox else RuntimeError('Web UI 未就绪')
    monkeypatch.setattr(core, '_require_docker', lambda: None)
    monkeypatch.setattr(cbc, 'active', lambda _: False)
    monkeypatch.setattr(cbc, 'prepare', lambda *a, **kw: True)
    monkeypatch.setattr(cbc, 'compose', Mock())
    monkeypatch.setattr(cbc, 'wait_until_ready', Mock(side_effect=error))
    monkeypatch.setattr(cbc, 'close_browser', Mock(side_effect=RuntimeError('请处理关闭确认')))
    monkeypatch.setattr(cbc, 'down', Mock())
    monkeypatch.setattr(cbc, 'docker', lambda *a, **kw: events.append(a) or SimpleNamespace(stdout='fixture', stderr=''))
    with pytest.raises(RuntimeError, match='启动收尾未完成.*关闭确认'):
        core._boot(task('start'), 'ifixture', verify=False)
    cbc.down.assert_not_called()
    assert [event[0] for event in events] == ['logs', 'logs', 'logs'] + (['stop'] if sandbox else [])
    if sandbox:
        assert events[3][-1].endswith('-proxy')
    assert (workspace / 'data/ifixture/profile-marker').exists()


@pytest.mark.parametrize('failure', ['data', 'config'])
def test_partial_purge_stays_visible_and_retryable(workspace, monkeypatch, failure):
    row = instance(workspace, trashed_at='2026-11-15T00:00:00Z')
    sid, nid = subscription()
    store.bind_subscription('ifixture', sid, nid)
    runtime = row['path'].parent / 'runtime'
    runtime.mkdir()
    (runtime/'remaining-config').write_text('fixture')
    monkeypatch.setattr(cbc, 'active', lambda _: False)
    monkeypatch.setattr(cbc, 'down', Mock())
    original = shutil.rmtree
    target = workspace/'data/ifixture' if failure == 'data' else runtime

    def fail_once(path, *args, **kwargs):
        if Path(path) == target:
            raise PermissionError('fixture deletion failure')
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(shutil, 'rmtree', fail_once)
        with pytest.raises(PermissionError):
            core._purge(task('purge'), row, row['display_name'])
    current = store.instance_by_id('ifixture')
    assert current['raw']['deleting'] is True and current['trashed_at']
    assert store.selection_map()['ifixture']['subscription_id'] == sid
    with pytest.raises(ValueError, match='不能恢复'):
        core._restore(task('restore'), current)
    core._purge(task('purge'), current, current['display_name'])
    assert not (workspace/'data/ifixture').exists()
    assert not current['path'].parent.exists()
    assert 'ifixture' not in store.selection_map()


def test_final_directory_removal_failure_restores_retry_registration(workspace, monkeypatch):
    row = instance(workspace, trashed_at='2026-11-15T00:00:00Z')
    monkeypatch.setattr(cbc, 'active', lambda _: False)
    monkeypatch.setattr(cbc, 'down', Mock())
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'rmdir', Mock(side_effect=PermissionError('directory removal failed')))
        with pytest.raises(PermissionError):
            core._purge(task('purge'), row, row['display_name'])
    current = store.instance_by_id('ifixture')
    assert current['raw']['deleting'] is True
    core._purge(task('purge'), current, current['display_name'])
    assert not current['path'].parent.exists()


def test_request_id_and_digest_survive_reload_without_saving_secrets(workspace):
    payload = {'mode': 'socks', 'password': 'private-fixture-password', 'display_name': '测试'}
    first = core.enqueue('create', request_id='stable-request', payload=payload)
    current = core._TASKS[first['id']]
    current.update(state='succeeded', instance_id='icreated', result={'instance_id': 'icreated'})
    core._persist_tasks()
    raw = (store.PRIVATE/'tasks.json').read_text()
    assert payload['password'] not in raw and 'payload"' not in raw
    core._TASKS.clear()
    core._ORDER.clear()
    core._load_tasks()
    again = core.enqueue('create', request_id='stable-request', payload=payload)
    assert again['id'] == first['id'] and again['state'] == 'succeeded'
    assert not {'request_id', 'payload_digest', 'requested_instance_id', 'payload'} & again.keys()
    with pytest.raises(ValueError, match='不一致'):
        core.enqueue('create', request_id='stable-request', payload={**payload, 'password': 'changed'})


def test_failed_task_persistence_does_not_publish_or_deduplicate_an_orphan(workspace, monkeypatch):
    monkeypatch.setattr(store, '_save', Mock(side_effect=PermissionError('disk unavailable')))
    with pytest.raises(PermissionError):
        core.enqueue('create', request_id='retryable', payload={'display_name': '测试'})
    assert not core._TASKS and not core._ORDER and core._QUEUE.empty()


def test_concurrent_task_persistence_cannot_restore_an_old_state(workspace, monkeypatch):
    public = core.enqueue('refresh', payload={'subscription_id': 'sfixture'})
    current = core._TASKS[public['id']]
    old_entered, new_entered, release = threading.Event(), threading.Event(), threading.Event()
    save, persist = store._save, core._persist_tasks

    def delay_old(path, value):
        if threading.current_thread().name == 'old-snapshot':
            old_entered.set()
            assert release.wait(3)
        return save(path, value)

    def notice_new():
        if threading.current_thread().name == 'new-snapshot':
            new_entered.set()
        persist()

    monkeypatch.setattr(store, '_save', delay_old)
    monkeypatch.setattr(core, '_persist_tasks', notice_new)
    old = threading.Thread(target=core._persist_tasks, name='old-snapshot')
    new = threading.Thread(target=core._finish, args=(current, 'succeeded', ''), name='new-snapshot')
    old.start()
    assert old_entered.wait(1)
    new.start()
    try:
        assert new_entered.wait(1)
    finally:
        release.set()
        old.join(2)
        new.join(2)
    assert not old.is_alive() and not new.is_alive()
    assert json.loads((store.PRIVATE/'tasks.json').read_text())[0]['state'] == 'succeeded'


def test_renamed_node_can_still_be_reconfirmed_after_credential_rotation(workspace):
    row = instance(workspace)
    sid, nid = subscription()
    store.bind_subscription('ifixture', sid, nid)
    store.refresh_subscription(sid, lambda _: [node(name='renamed')])
    assert store.selection_map()['ifixture']['node'] == 'renamed'
    assert store.selection_map()['ifixture']['node_id'] == nid
    store.refresh_subscription(sid, lambda _: [node(name='renamed', password='rotated-fixture')])
    assert store.proxy_public('ifixture')['stale']
    assert store.reconfirm_same_name('ifixture') == 'renamed'
    assert not store.proxy_public('ifixture')['stale']
    assert store.instance_by_id('ifixture')['raw']['proxy_locked_at'] == row['raw']['proxy_locked_at']


@pytest.mark.parametrize('state', ['corrupt', 'missing', 'trashed'])
def test_all_subscription_references_block_deletion(workspace, state):
    row = instance(workspace, trashed_at='2026-11-15T00:00:00Z' if state == 'trashed' else None)
    sid, nid = subscription()
    store.bind_subscription('ifixture', sid, nid)
    if state == 'corrupt':
        row['path'].write_text('{invalid')
    elif state == 'missing':
        row['path'].unlink()
    before = store._cache_path(sid).read_bytes()
    with pytest.raises(ValueError, match='还有实例使用'):
        store.delete_subscription(sid)
    assert store._cache_path(sid).read_bytes() == before
    assert store.load_library()['subscriptions'][0]['id'] == sid


@pytest.mark.parametrize('kind,key', [('trojan','sni'), ('anytls','sni'), ('http','sni'), ('socks5','sni'), ('vmess','servername'), ('vless','servername')])
@pytest.mark.parametrize('explicit', [None, 'explicit.example.test'])
def test_pinned_tls_nodes_preserve_their_certificate_hostname(monkeypatch, kind, key, explicit):
    monkeypatch.setattr(catalog, '_public_ipv4', lambda *a: ['1.1.1.1'])
    monkeypatch.setattr(catalog, '_tcp_open', lambda *a: True)
    original = {'name': 'TLS fixture', 'type': kind, 'server': 'original.example.test', 'port': 443, 'tls': True}
    if explicit:
        original[key] = explicit
    pinned, address, port = catalog.pin_endpoint(original)
    assert pinned[key] == (explicit or original['server'])
    assert original['server'] == 'original.example.test'
    assert pinned['server'] == address == '1.1.1.1' and port == 443


@pytest.mark.parametrize('content,error', [(b'x'*(2*1024*1024+1), '超过 2 MiB'), (b'\xff', 'UTF-8')])
def test_subscription_response_is_bounded_and_encoding_errors_are_public(monkeypatch, content, error):
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
        def read(self, size=-1):
            assert size == catalog.MAX_SUBSCRIPTION_BYTES + 1
            return super().read(size)
    monkeypatch.setattr(catalog.urllib.request, 'urlopen', lambda *a, **kw: Response(content))
    with pytest.raises(catalog.CatalogError, match=error):
        catalog._download_proxies('https://audit.example.test/sub')


def test_subscription_node_count_is_bounded(monkeypatch):
    monkeypatch.setattr(catalog, 'MAX_SUBSCRIPTION_NODES', 2)
    with pytest.raises(catalog.CatalogError, match='节点超过 2'):
        catalog.proxies_from_clash(json.dumps({'proxies': [node()]*3}))


def test_subscription_worker_has_a_total_timeout_and_keeps_url_off_arguments(monkeypatch):
    url = 'https://audit.example.test/sub?token=private-fixture-token'
    def run(args, **kwargs):
        assert url not in str(args)
        assert json.loads(kwargs['input']) == {'url': url}
        assert kwargs['timeout'] == 30
        raise subprocess.TimeoutExpired(args, 30, output='private-fixture-token')
    monkeypatch.setattr(catalog.subprocess, 'run', run)
    with pytest.raises(catalog.CatalogError, match='下载超时') as caught:
        catalog.fetch_proxies(url)
    assert 'private-fixture-token' not in str(caught.value)


def test_expanded_yaml_result_cannot_bypass_output_limit(monkeypatch, capsys):
    monkeypatch.setattr(catalog, 'MAX_SUBSCRIPTION_BYTES', 32)
    monkeypatch.setattr(catalog, '_download_proxies', lambda _: [node()]*4)
    monkeypatch.setattr(catalog.sys, 'stdin', io.StringIO('{"url":"https://audit.example.test/sub"}'))
    catalog._fetch_worker()
    result = json.loads(capsys.readouterr().out)
    assert result == {'error': '订阅解析结果过大，请精简后重试。'}


def test_slow_subscription_cannot_block_stop_or_hold_manager_lock(workspace, monkeypatch):
    instance(workspace)
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
    sentinel = object()
    class FiniteQueue(queue.Queue):
        def get(self, *args, **kwargs):
            value = super().get(*args, **kwargs)
            if value is sentinel: raise SystemExit
            return value
    for key in ('_QUEUE', '_SUBSCRIPTION_QUEUE'):
        monkeypatch.setattr(core, key, FiniteQueue())
    def fetch(_):
        assert not (workspace/'.manager.lock').exists()
        entered.set()
        assert release.wait(3)
        return [node()]
    monkeypatch.setattr(catalog, 'fetch_proxies', fetch)
    monkeypatch.setattr(cbc, 'close_browser', lambda _: None)
    monkeypatch.setattr(cbc, 'down', lambda _: stopped.set())
    core.enqueue('add-subscription', payload={'name':'测试', 'url':'https://audit.example.test/sub'})
    core._SUBSCRIPTION_QUEUE.put(sentinel)
    def consume(task_queue):
        try:
            core._worker(task_queue)
        except SystemExit:
            pass
    network = threading.Thread(target=consume, args=(core._SUBSCRIPTION_QUEUE,))
    network.start()
    assert entered.wait(1)
    core.enqueue('stop', 'ifixture')
    core._QUEUE.put(sentinel)
    lifecycle = threading.Thread(target=consume, args=(core._QUEUE,))
    lifecycle.start()
    try:
        assert stopped.wait(1), '停止必须在订阅下载结束之前执行'
    finally:
        release.set()
        network.join(2)
        lifecycle.join(2)
    assert not network.is_alive() and not lifecycle.is_alive()


@pytest.mark.parametrize('url', ['http://audit.example.test/sub', 'ftp://audit.example.test/sub', 'https://'])
def test_invalid_subscription_is_rejected_before_task_acceptance(workspace, monkeypatch, url):
    from fastapi.testclient import TestClient
    import app.main as main
    monkeypatch.setattr(store, 'migrate', lambda: None)
    monkeypatch.setattr(core, 'ensure_worker', lambda: None)
    with TestClient(main.app, base_url='http://127.0.0.1:5790') as client:
        response = client.post('/api/subscriptions', json={'name':'测试', 'url':url}, headers={'X-CBC-Request':'1'})
    assert response.status_code == 400
    assert 'HTTPS' in response.json()['error']
    assert not core._TASKS and core._SUBSCRIPTION_QUEUE.empty()


@pytest.mark.parametrize('entry', ['web', 'cli'])
def test_start_failure_saves_redacted_diagnostics_before_cleanup(workspace, monkeypatch, entry):
    instance(workspace)
    password, token = 'fixture-socks-password', 'fixture-mihomo-token'
    store._write_proxy('ifixture', {'host': '192.0.2.9', 'port': 1080, 'username': 'fixture-user', 'password': password})
    runtime = store.CONFIGS / 'ifixture' / 'runtime'
    store._save(runtime / 'mihomo.json', {'proxies': [{'password': token}]})
    path = runtime / 'start-failure.log'
    roles = []
    monkeypatch.setattr(core, '_require_docker', lambda: None)
    monkeypatch.setattr(cbc, 'active', lambda _: False)
    monkeypatch.setattr(cbc, 'prepare', lambda *a, **kw: True)
    monkeypatch.setattr(cbc, 'compose', Mock())
    monkeypatch.setattr(cbc, 'verify_exit', Mock(side_effect=RuntimeError('fixture boot failure')))

    def docker(*args, **kwargs):
        assert args[:3] == ('logs', '--tail', '80')
        assert kwargs['timeout'] == 15 and kwargs['check'] is False
        roles.append(args[3])
        return SimpleNamespace(stdout=f'fixture-user:{password}', stderr=f' {token} diagnostic')

    def cleanup(*_args):
        text = path.read_text()
        assert password not in text and token not in text
        assert '[REDACTED]' in text and 'diagnostic' in text
        assert path.stat().st_mode & 0o777 == 0o600

    monkeypatch.setattr(cbc, 'docker', docker)
    cleaning = Mock(side_effect=cleanup)
    monkeypatch.setattr(cbc, 'cleanup_start_failure', cleaning)
    with pytest.raises(RuntimeError, match='fixture boot failure'):
        if entry == 'web':
            core._boot(task('start'), 'ifixture', verify=True)
        else:
            cbc.start('ifixture')
    assert roles == [cbc.container('ifixture', role) for role in ('guard', 'proxy', 'browser')]
    cleaning.assert_called_once()


@pytest.mark.parametrize('entry', ['web', 'cli'])
@pytest.mark.parametrize('failure', ['logs', 'write'])
def test_diagnostic_failures_do_not_skip_start_cleanup(workspace, monkeypatch, entry, failure):
    instance(workspace)
    runtime = store.CONFIGS / 'ifixture' / 'runtime'
    runtime.mkdir()
    if failure == 'write':
        (runtime / 'start-failure.log').mkdir()
    monkeypatch.setattr(core, '_require_docker', lambda: None)
    monkeypatch.setattr(cbc, 'active', lambda _: False)
    monkeypatch.setattr(cbc, 'prepare', lambda *a, **kw: True)
    monkeypatch.setattr(cbc, 'compose', Mock())
    monkeypatch.setattr(cbc, 'verify_exit', Mock(side_effect=RuntimeError('original boot failure')))
    monkeypatch.setattr(cbc, 'docker', Mock(side_effect=OSError('log read unavailable')) if failure == 'logs'
                        else Mock(return_value=SimpleNamespace(stdout='fixture log', stderr='')))
    cleanup = Mock()
    monkeypatch.setattr(cbc, 'cleanup_start_failure', cleanup)
    with pytest.raises(RuntimeError, match='original boot failure'):
        if entry == 'web':
            core._boot(task('start'), 'ifixture', verify=True)
        else:
            cbc.start('ifixture')
    cleanup.assert_called_once()


@pytest.mark.parametrize('running,changed', [(False, False), (True, False), (True, True)])
def test_boot_reuses_one_browser_state_query(workspace, monkeypatch, running, changed):
    instance(workspace)
    commands = []
    identity = {'image': 'fixture-image', 'revision': 'before'}
    monkeypatch.setattr(core, '_require_docker', lambda: None)
    monkeypatch.setattr(cbc, 'running_browser_identity', lambda _: identity)
    monkeypatch.setattr(cbc, 'planned_browser', lambda *a, **kw: {**identity, 'revision': 'after' if changed else 'before'})
    monkeypatch.setattr(cbc, 'prepare', lambda *a, **kw: None)
    compose = Mock()
    monkeypatch.setattr(cbc, 'compose', compose)
    close = Mock()
    monkeypatch.setattr(cbc, 'close_browser', close)
    monkeypatch.setattr(cbc, 'wait_until_ready', lambda _: None)

    def docker(*args, **kwargs):
        assert args[0] == 'ps'
        commands.append(args)
        return SimpleNamespace(stdout='running' if running else '')

    monkeypatch.setattr(cbc, 'docker', docker)
    core._boot(task('start'), 'ifixture', verify=False)
    assert len(commands) == 1
    assert close.call_count == int(running and changed)
    assert any('browser' in call.args for call in compose.call_args_list) == (not running or changed)
