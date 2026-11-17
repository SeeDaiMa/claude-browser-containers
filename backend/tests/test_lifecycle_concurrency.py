"""生命周期调度回归，只使用临时文件、受控线程和假的 Docker 命令。"""
import concurrent.futures
import fcntl
import os
from pathlib import Path
import queue
import select
import shutil
import subprocess
import sys
import threading
from unittest.mock import Mock

import pytest

import cbc
import manager_core as core
import store
from test_reliability import isolated as isolated, instance, task


class FiniteQueue(queue.Queue):
    def get(self, *args, **kwargs):
        value = super().get(*args, **kwargs)
        if value is None:
            raise SystemExit
        return value


@pytest.fixture
def lifecycle(isolated, monkeypatch):
    work = FiniteQueue()
    monkeypatch.setattr(core, '_QUEUE', work)
    release = threading.Event()

    def consume():
        try:
            core._worker(work)
        except SystemExit:
            pass

    worker = threading.Thread(target=consume)
    worker.start()
    yield release
    release.set()
    work.put(None)
    worker.join(5)
    assert not worker.is_alive(), '测试调度器和实例线程必须全部退出'
    cbc.docker.assert_not_called()


def test_slow_start_does_not_delay_another_instance_stop(isolated, monkeypatch, lifecycle):
    instance(isolated, 'ia')
    instance(isolated, 'ib', port=5804, network_index=10)
    entered, stopped = threading.Event(), threading.Event()
    monkeypatch.setattr(core, '_preflight_boot', lambda _: None)

    def boot(_task, slug, verify):
        assert slug == 'ia'
        entered.set()
        assert lifecycle.wait(3)

    monkeypatch.setattr(core, '_boot', boot)
    monkeypatch.setattr(cbc, 'close_browser', Mock())
    monkeypatch.setattr(cbc, 'down', lambda slug: stopped.set() if slug == 'ib' else None)
    starting = core.enqueue('start', 'ia')
    assert entered.wait(1)
    stopping = core.enqueue('stop', 'ib')
    assert stopped.wait(1), 'B 的停止必须在 A 启动结束前执行'
    assert not lifecycle.is_set()
    assert core.get_task(starting['id'])['state'] == 'running'
    assert store.instance_by_id('ib')['raw']['desired_running'] is False
    lifecycle.set()
    assert stopping['instance_id'] == 'ib'


@pytest.mark.parametrize('first_kind', ['start', 'create'])
def test_same_instance_fifo_aliases_and_cancellation_are_preserved(isolated, monkeypatch, lifecycle, first_kind):
    instance(isolated, 'ia', id='alias-a')
    instance(isolated, 'ib', port=5804, network_index=10)
    entered, stopped, drained = threading.Event(), threading.Event(), threading.Event()
    events = []

    def dispatch(current):
        events.append((current['kind'], current['instance_id']))
        if current['kind'] == first_kind:
            # Creation assigns the instance ID while it already holds its operation lock.
            if first_kind == 'create':
                current['instance_id'] = 'alias-a'
            with store.instance_operation('ia'):
                entered.set()
                assert lifecycle.wait(3)
        elif current['instance_id'] == 'ib':
            stopped.set()
        else:
            drained.set()

    monkeypatch.setattr(core, '_dispatch_locked', dispatch)
    core.enqueue(first_kind, '' if first_kind == 'create' else 'alias-a')
    assert entered.wait(1)
    cancelled = core.enqueue('restart', 'alias-a')
    core.cancel_task(cancelled['id'])
    same = core.enqueue('stop', 'ia')
    core.enqueue('stop', 'ib')
    assert stopped.wait(1), '同实例排队不能挡住另一个实例'
    if first_kind == 'start':
        assert core.get_task(same['id'])['state'] == 'queued'
    assert core.get_task(cancelled['id'])['state'] == 'interrupted'
    lifecycle.set()
    assert drained.wait(1)
    assert events == [(first_kind, '' if first_kind == 'create' else 'alias-a'), ('stop', 'ib'), ('stop', 'ia')]


def test_task_failure_does_not_strand_following_tasks(isolated, monkeypatch, lifecycle):
    instance(isolated, 'ia')
    entered, completed = threading.Event(), threading.Event()

    def dispatch(current):
        if current['kind'] == 'start':
            entered.set()
            assert lifecycle.wait(3)
            raise RuntimeError('fixture start failure')
        completed.set()

    monkeypatch.setattr(core, '_dispatch_locked', dispatch)
    first = core.enqueue('start', 'ia')
    assert entered.wait(1)
    second = core.enqueue('stop', 'ia')
    lifecycle.set()
    assert completed.wait(1)
    assert core.get_task(first['id'])['state'] == 'failed'
    assert core.get_task(second['id'])['kind'] == 'stop'


def test_exclusive_maintenance_lock_still_blocks_lifecycle(isolated, monkeypatch, lifecycle):
    instance(isolated, 'ia')
    entered = threading.Event()
    monkeypatch.setattr(core, '_dispatch_locked', lambda _: entered.set())
    with open(isolated / '.manager.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        core.enqueue('stop', 'ia')
        assert not entered.wait(0.1)
    assert entered.wait(1)


def test_cli_status_can_share_the_manager_lock(isolated):
    # Run the real CLI entry in a disposable source/config tree and replace Docker.
    source = Path(cbc.__file__).parent
    scripts = isolated / 'scripts'
    scripts.mkdir()
    for name in ('cbc.py', 'store.py', 'catalog.py'):
        shutil.copy2(source / name, scripts / name)
    instance(isolated, 'ia')
    commands = isolated / 'commands'
    commands.mkdir()
    fake = commands / 'docker'
    fake.write_text('''#!/bin/sh
[ "$1" = ps ] || exit 2
echo running
''')
    fake.chmod(0o755)
    env = {**os.environ, 'PATH': str(commands) + os.pathsep + os.environ.get('PATH', '')}
    process = None
    try:
        with open(isolated / '.manager.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            driver = '''import runpy, sys
from pathlib import Path
source = Path(sys.argv[1])
sys.path.insert(0, str(source.parent))
import cbc
print('CLI_READY', flush=True)
sys.argv = sys.argv[1:]
runpy.run_path(str(source), run_name='__main__')
'''
            process = subprocess.Popen([sys.executable, '-B', '-c', driver, str(scripts / 'cbc.py'), 'status', 'ia'],
                                       env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            assert select.select([process.stdout], [], [], 5)[0], 'CLI 未完成模块加载'
            assert process.stdout.readline() == 'CLI_READY\n'
            stdout, stderr = process.communicate(timeout=5)
            assert process.returncode == 0, stderr
            assert '运行中' in stdout
    finally:
        if process is not None:
            process.communicate(timeout=5)


def test_creation_port_allocation_is_inside_the_storage_transaction(isolated, monkeypatch):
    entered, allocated = threading.Event(), threading.Event()
    monkeypatch.setattr(core, '_advance', lambda *_: None)
    monkeypatch.setattr(core, '_validate_binding', lambda *a, **kw: None)
    monkeypatch.setattr(core, '_require_docker', entered.set)
    monkeypatch.setattr(core, '_allocate_port', lambda: allocated.set() or 5808)
    monkeypatch.setattr(cbc, 'create', lambda slug, port: instance(isolated, slug, port=port, desired_running=False))
    monkeypatch.setattr(core, '_write_binding', lambda *a: None)
    monkeypatch.setattr(core, '_boot', lambda *a, **kw: None)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        with store.exclusive():
            creating = pool.submit(core._create, task('create', ''), {'display_name': 'fixture'})
            assert entered.wait(1)
            assert not allocated.wait(0.1), '分配端口前必须已经获得存储锁'
        creating.result(timeout=3)
    assert allocated.is_set()
    assert len(store._instances_on_disk()) == 1
    cbc.docker.assert_not_called()


def test_only_one_creation_can_be_accepted_at_a_time(isolated, monkeypatch, lifecycle):
    entered = threading.Event()

    def create(_task):
        entered.set()
        assert lifecycle.wait(3)

    monkeypatch.setattr(core, '_dispatch_locked', create)
    first = core.enqueue('create', payload={'display_name': 'first'})
    assert entered.wait(1)
    with pytest.raises(ValueError, match='已有一个创建任务'):
        core.enqueue('create', payload={'display_name': 'second'})
    assert core.get_task(first['id'])['state'] == 'running'


def test_short_task_bursts_drain_without_losing_work(isolated, monkeypatch, lifecycle):
    instance(isolated, 'ia')
    completed = threading.Event()
    seen = []

    def dispatch(current):
        seen.append(current['id'])
        if len(seen) == 64:
            completed.set()

    monkeypatch.setattr(core, '_dispatch_locked', dispatch)
    expected = [core.enqueue('restore', 'ia')['id'] for _ in range(64)]
    assert completed.wait(3)
    assert seen == expected
