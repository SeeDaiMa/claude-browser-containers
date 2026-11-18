"""资源统计改为后台刷新后的回归：概览不等待 docker stats，结果不过时。"""

import json
import threading
import time
from types import SimpleNamespace

import cbc
import manager_core as core
from test_reliability import isolated as isolated, instance, snapshot


def fake_docker(monkeypatch, *, stats_gate=None, stats_error=None, state='running'):
    containers = list(snapshot('ia')['containers'])
    calls = []

    def docker(*args, **_kwargs):
        calls.append(args[0])
        if args[0] == 'ps':
            output = '\n'.join(
                json.dumps({'Names': name, 'State': state, 'Status': 'Up 1 minute (healthy)', 'ID': name + '-id'})
                for name in containers
            )
        elif args[0] == 'inspect':
            output = '\n'.join(json.dumps({'name': '/' + name, 'memory': 64 * 1024 * 1024}) for name in containers)
        elif args[0] == 'stats':
            if stats_gate is not None:
                assert stats_gate.wait(5)
            if stats_error is not None:
                raise stats_error
            output = '\n'.join(
                json.dumps({'Name': name, 'CPUPerc': '1%', 'MemUsage': '2MiB / 64MiB'}) for name in containers
            )
        else:
            raise AssertionError(args)
        return SimpleNamespace(returncode=0, stdout=output, stderr='')

    monkeypatch.setattr(cbc, 'docker', docker)
    return calls


def usage():
    return core.list_instances()['instances'][0]['usage']['memory_mib']


def test_overview_returns_while_docker_stats_is_still_sampling(isolated, monkeypatch):
    instance(isolated, 'ia')
    gate = threading.Event()
    fake_docker(monkeypatch, stats_gate=gate)
    started = time.monotonic()
    assert usage() is None
    assert time.monotonic() - started < 1
    gate.set()
    core.wait_for_stats_refresh(5)
    assert usage() == 6


def test_only_one_background_refresh_runs_at_a_time(isolated, monkeypatch):
    instance(isolated, 'ia')
    gate = threading.Event()
    calls = fake_docker(monkeypatch, stats_gate=gate)
    for _ in range(3):
        core.list_instances()
    assert calls.count('stats') == 1
    gate.set()
    core.wait_for_stats_refresh(5)


def test_refresh_overtaken_by_lifecycle_change_is_marked_stale(isolated, monkeypatch):
    instance(isolated, 'ia')
    gate = threading.Event()
    calls = fake_docker(monkeypatch, stats_gate=gate)
    core.list_instances()
    core.invalidate_runtime()
    gate.set()
    core.wait_for_stats_refresh(5)
    assert core._STATS['at'] == 0.0
    core.list_instances()
    core.wait_for_stats_refresh(5)
    assert calls.count('stats') == 2
    assert core._STATS['at'] > 0


def test_stopped_containers_never_show_previous_usage(isolated):
    names = list(snapshot('ia')['containers'])
    stats = {'error': '', 'rows': {name: {'cpu': 1.0, 'memory_mib': 2.0} for name in names}}
    running = {name: 'running' for name in names}
    assert core._instance_usage('ia', stats, running)['memory_mib'] == 6
    assert core._instance_usage('ia', stats, {**running, names[0]: 'exited'})['memory_mib'] is None
    assert core._instance_usage('ia', stats, {})['memory_mib'] is None


def test_background_failure_is_recorded_instead_of_raised(isolated, monkeypatch):
    instance(isolated, 'ia')
    fake_docker(monkeypatch, stats_error=RuntimeError('fixture docker failure'))
    core.list_instances()
    core.wait_for_stats_refresh(5)
    assert core._STATS['error'] == '容器资源暂时读不到'
    assert usage() is None
