"""出口探测并行与 IP 质量检测超时余量的回归。"""

import subprocess
import threading
from types import SimpleNamespace

import pytest

import cbc
import node_probe
import node_probe_worker


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.setattr(cbc, 'addresses', lambda _name: {'BRIDGE_IP': '10.231.1.3'})
    started = threading.Event()

    def observe(_name):
        started.set()
        return {'ip': '203.0.113.5', 'country_code': 'JP'}

    monkeypatch.setattr(cbc, 'observe_exit_info', observe)
    return started


def test_public_site_and_claude_exit_are_checked_at_the_same_time(probe, monkeypatch):
    def docker(*_args, **_kwargs):
        # 顺序执行时读取 Claude 出口还没开始，这里会等到超时。
        assert probe.wait(2), '读取 Claude 出口没有与公网访问同时进行'
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cbc, 'docker', docker)
    result = cbc.probe_proxy('fixture')
    assert result['status'] == 'ok' and result['ip'] == '203.0.113.5'


@pytest.mark.parametrize(
    'outcome',
    [SimpleNamespace(returncode=7), subprocess.TimeoutExpired('curl', 20), OSError('docker missing')],
)
def test_public_site_failure_still_fails_even_if_claude_exit_was_read(probe, monkeypatch, outcome):
    def docker(*_args, **_kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(cbc, 'docker', docker)
    result = cbc.probe_proxy('fixture')
    assert result == {'status': 'failed', 'ip': None, 'detail': '代理连接失败'}


def test_quality_batch_timeout_leaves_margin_over_worst_case(monkeypatch):
    nodes = [{'id': f'n{index}', 'config': {}} for index in range(node_probe.PROBE_BATCH_SIZE)]
    seen = {}

    def docker(*args, timeout, **_kwargs):
        if args[0] == 'run':
            seen['timeout'] = timeout
        raise subprocess.TimeoutExpired('docker', timeout)

    monkeypatch.setattr(cbc, 'read_json', lambda _path: {'network': {'reference': 'fixture-image'}})
    monkeypatch.setattr(cbc, 'docker', docker)
    with pytest.raises(ValueError, match='超时'):
        node_probe._measure(nodes, quality=True)
    count = len(nodes)
    # 逐个配置校验 3 秒、mihomo 启动 5 秒、每个节点的 IPPure 请求和节点间 2 秒间隔。
    worst = count * 3 + 5 + count * node_probe_worker.QUALITY_TIMEOUT + (count - 1) * 2
    assert seen['timeout'] - worst >= 30
