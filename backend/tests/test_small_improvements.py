import concurrent.futures
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

import cbc
import manager_core as core
import store
from test_reliability import isolated as isolated, instance


def test_malformed_resource_line_reports_location_without_echoing_value(isolated):
    (isolated / '.env').write_text('# comment\nBROWSER_CPUS=2\nprivate-invalid-value\n')
    with pytest.raises(ValueError, match=r'第 3 行.*KEY=VALUE') as error:
        cbc.resource_env()
    assert 'private-invalid-value' not in str(error.value)


def test_reads_do_not_repeat_permission_writes_and_startup_repairs_legacy_files(isolated, monkeypatch):
    paths = [
        store.LIBRARY,
        store.EXITS,
        store.PRIVATE / 'tasks.json',
        store.CONFIGS / 'selection.json',
        store._cache_path('fixture'),
    ]
    for path, text in zip(paths, ['{"subscriptions": []}', '{}', '[]', '{}', '{"nodes": []}'], strict=True):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o644)
    # Track just the read paths; lock-directory and write permissions remain separate.
    original = Path.chmod
    calls = []

    def record(path, mode, *args, **kwargs):
        calls.append(path)
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'chmod', record)
    for _ in range(2):
        assert store.load_library() == {'subscriptions': []}
        assert store.load_cached_nodes('fixture') == []
        assert store.selection_map() == {}
    assert not any(path in calls for path in paths)
    store.migrate()
    for path in paths:
        assert path.stat().st_mode & 0o777 == 0o600
    store._save(store.EXITS, {})
    assert store.EXITS.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('kind', ['stop', 'trash'])
def test_stop_intent_io_does_not_hold_task_read_lock(isolated, monkeypatch, kind):
    instance(isolated)
    entered, release = threading.Event(), threading.Event()
    original = store.set_run_intent

    def write(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(store, 'set_run_intent', write)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(core.enqueue, kind, 'ifixture', request_id='same-request')
        try:
            assert entered.wait(1)
            assert pool.submit(core.list_tasks).result(timeout=1) == []
            assert not pending.done()
        finally:
            release.set()
        result = pending.result(timeout=3)
    assert store.instance_by_id('ifixture')['raw']['desired_running'] is False
    monkeypatch.setattr(store, 'set_run_intent', Mock(side_effect=AssertionError('重复请求不能再次写运行意图')))
    assert core.enqueue(kind, 'ifixture', request_id='same-request')['id'] == result['id']
    with pytest.raises(ValueError, match='不一致'):
        core.enqueue(kind, 'missing', request_id='same-request')
