"""偶发网络失败的重试回归：节点域名 DoH 解析和启动/恢复时的出口验证。"""

import io
import json
import ssl
import time
import urllib.error

import pytest

import catalog
import cbc
import manager_core as core
import store
from test_reliability import isolated as isolated, instance, snapshot, task


class FakeOpener:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.urls = []

    def open(self, request, timeout):
        self.urls.append(request.full_url)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(json.dumps(outcome).encode())


def dropped():
    return urllib.error.URLError(ssl.SSLEOFError(8, 'EOF occurred in violation of protocol'))


def answer(*addresses):
    return {'Answer': [{'type': 1, 'data': address} for address in addresses]}


@pytest.fixture
def doh(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, 'sleep', sleeps.append)

    def install(*outcomes):
        opener = FakeOpener(outcomes)
        monkeypatch.setattr(catalog.urllib.request, 'build_opener', lambda *handlers: opener)
        return opener

    return install, sleeps


def test_doh_retries_dropped_connection_on_alternate_endpoint(doh):
    install, sleeps = doh
    opener = install(dropped(), answer('93.184.216.34'))
    assert catalog._doh_ipv4('node.example') == ['93.184.216.34']
    assert [url.split('/dns-query')[0] for url in opener.urls] == ['https://1.1.1.1', 'https://1.0.0.1']
    assert sleeps == [catalog.DOH_RETRY_DELAY]


def test_doh_reports_failure_only_after_every_attempt(doh):
    install, sleeps = doh
    opener = install(*[dropped() for _ in range(catalog.DOH_ATTEMPTS)])
    with pytest.raises(catalog.CatalogError, match='无法解析出公网 IPv4'):
        catalog._doh_ipv4('node.example')
    assert len(opener.urls) == catalog.DOH_ATTEMPTS
    assert opener.urls[0].startswith('https://1.1.1.1/') and opener.urls[1].startswith('https://1.0.0.1/')
    assert len(sleeps) == catalog.DOH_ATTEMPTS - 1


def test_doh_retries_unreadable_response(doh):
    install, _sleeps = doh
    opener = install(ValueError('not json'), answer('93.184.216.34'))
    assert catalog._doh_ipv4('node.example') == ['93.184.216.34']
    assert len(opener.urls) == 2


@pytest.fixture
def exit_check(isolated, monkeypatch):
    instance(isolated)
    store.save_exit('ifixture', 'ok', '192.0.2.1', '', 'GB')
    sleeps = []
    docker_calls = []
    monkeypatch.setattr(cbc.time, 'sleep', sleeps.append)
    monkeypatch.setattr(
        cbc, 'docker', lambda *args, **kwargs: docker_calls.append(args) or type('Done', (), {'returncode': 0})()
    )

    def install(*observations):
        pending = list(observations)
        calls = []

        def probe(slug):
            calls.append(slug)
            return pending.pop(0)

        monkeypatch.setattr(cbc, 'probe_proxy', probe)
        return calls

    return install, sleeps, docker_calls


FAILED = {'status': 'failed', 'ip': None, 'detail': '代理连接失败'}
UNKNOWN = {'status': 'unknown', 'ip': None, 'detail': 'Claude 出口未测得'}


def ok(country):
    return {'status': 'ok', 'ip': '192.0.2.2', 'country_code': country, 'detail': ''}


def test_exit_check_retries_unconfirmed_probe_before_disconnecting(exit_check):
    install, sleeps, docker_calls = exit_check
    calls = install(FAILED, UNKNOWN, ok('GB'))
    assert cbc.verify_exit('ifixture')['status'] == 'ok'
    assert len(calls) == 3
    assert sleeps == [cbc.EXIT_PROBE_RETRY_DELAY] * 2
    assert docker_calls == []
    assert store.load_exits()['ifixture']['ip'] == '192.0.2.2'


def test_exit_check_disconnects_after_every_probe_fails(exit_check):
    install, _sleeps, docker_calls = exit_check
    calls = install(*[FAILED] * cbc.EXIT_PROBE_ATTEMPTS)
    with pytest.raises(cbc.CountryPolicyError, match='代理连接失败'):
        cbc.verify_exit('ifixture')
    assert len(calls) == cbc.EXIT_PROBE_ATTEMPTS
    assert docker_calls == [('stop', '--time', '5', 'cbc-ifixture-proxy')]


def test_exit_check_does_not_retry_confirmed_country_mismatch(exit_check):
    install, sleeps, docker_calls = exit_check
    calls = install(ok('JP'))
    with pytest.raises(cbc.CountryPolicyError, match='JP'):
        cbc.verify_exit('ifixture')
    assert len(calls) == 1
    assert sleeps == []
    assert docker_calls == [('stop', '--time', '5', 'cbc-ifixture-proxy')]


def test_recovery_survives_one_dropped_probe(exit_check, monkeypatch):
    install, _sleeps, docker_calls = exit_check
    install(FAILED, ok('GB'))
    monkeypatch.setattr(core, 'runtime_snapshot', lambda force=False: snapshot())
    monkeypatch.setattr(core, '_preflight_boot', lambda slug: None)
    monkeypatch.setattr(cbc, 'running_browser_identity', lambda slug: {'image': 'image', 'revision': 'revision'})
    monkeypatch.setattr(cbc, 'prepare', lambda *args, **kwargs: True)
    monkeypatch.setattr(cbc, 'compose', lambda *args, **kwargs: None)
    recovery = task('recover', generation='generation')
    core._dispatch(recovery)
    record = store.instance_by_id('ifixture')['raw']['recovery']
    assert record['exhausted'] is False and record['attempts'] == 1
    assert '已恢复' in recovery['result']['detail']
    assert docker_calls == []
