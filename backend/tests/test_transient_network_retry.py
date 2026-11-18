"""偶发网络失败的回归：DoH 解析重试、沿用上次地址、出口验证重试和恢复失败原因。"""

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


NODE = {'name': '节点 A', 'type': 'trojan', 'server': 'node.example', 'port': 443, 'password': 'secret'}


def resolution_fails(*_args):
    raise catalog.CatalogError('节点服务器“node.example”无法解析出公网 IPv4。')


def test_pin_endpoint_reuses_reachable_known_address_when_resolution_fails(monkeypatch):
    monkeypatch.setattr(catalog, '_public_ipv4', resolution_fails)
    monkeypatch.setattr(catalog, '_tcp_open', lambda address, port: address == '93.184.216.34')
    pinned, address, port = catalog.pin_endpoint(NODE, known_address='93.184.216.34')
    assert (address, port) == ('93.184.216.34', 443)
    assert pinned['server'] == '93.184.216.34' and pinned['sni'] == 'node.example'


@pytest.mark.parametrize('known', [None, '198.18.0.28', '10.0.0.8'])
def test_pin_endpoint_keeps_resolution_error_without_usable_known_address(monkeypatch, known):
    monkeypatch.setattr(catalog, '_public_ipv4', resolution_fails)
    monkeypatch.setattr(catalog, '_tcp_open', lambda *_args: True)
    with pytest.raises(catalog.CatalogError, match='无法解析'):
        catalog.pin_endpoint(NODE, known_address=known)


def test_pin_endpoint_rejects_known_address_that_no_longer_connects(monkeypatch):
    monkeypatch.setattr(catalog, '_public_ipv4', resolution_fails)
    monkeypatch.setattr(catalog, '_tcp_open', lambda *_args: False)
    with pytest.raises(catalog.CatalogError, match='端口目前连不上'):
        catalog.pin_endpoint(NODE, known_address='93.184.216.34')


@pytest.fixture
def bound_node(isolated, monkeypatch):
    node = dict(NODE)
    monkeypatch.setattr(cbc, 'selection_map', lambda: {'ifixture': {'subscription_id': 'sub', 'node_id': 'n1'}})
    monkeypatch.setattr(store, 'materialize_node', lambda _sub, _node: dict(node))
    monkeypatch.setattr(catalog, '_tcp_open', lambda *_args: True)
    return node, isolated / 'configs' / 'ifixture' / 'runtime' / 'endpoint.json'


def test_node_upstream_remembers_and_reuses_last_pinned_address(bound_node, monkeypatch):
    _node, cache = bound_node
    monkeypatch.setattr(catalog, '_public_ipv4', lambda *_args: ['93.184.216.34'])
    assert cbc.node_upstream('ifixture')['upstream'] == ['93.184.216.34']
    assert json.loads(cache.read_text()) == {'server': 'node.example', 'port': 443, 'address': '93.184.216.34'}
    assert cache.stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(catalog, '_public_ipv4', resolution_fails)
    assert cbc.node_upstream('ifixture')['upstream'] == ['93.184.216.34']


@pytest.mark.parametrize('change', [{'server': 'other.example'}, {'port': 8443}])
def test_node_upstream_ignores_address_remembered_for_another_endpoint(bound_node, monkeypatch, change):
    node, _cache = bound_node
    monkeypatch.setattr(catalog, '_public_ipv4', lambda *_args: ['93.184.216.34'])
    cbc.node_upstream('ifixture')
    node.update(change)
    monkeypatch.setattr(catalog, '_public_ipv4', resolution_fails)
    with pytest.raises(catalog.CatalogError, match='无法解析'):
        cbc.node_upstream('ifixture')


def test_node_upstream_ignores_damaged_cache(bound_node, monkeypatch):
    _node, cache = bound_node
    cache.parent.mkdir(parents=True)
    cache.write_text('{broken')
    monkeypatch.setattr(catalog, '_public_ipv4', resolution_fails)
    with pytest.raises(catalog.CatalogError, match='无法解析'):
        cbc.node_upstream('ifixture')


def test_failed_recovery_records_the_redacted_reason(exit_check, monkeypatch):
    monkeypatch.setattr(core, 'runtime_snapshot', lambda force=False: snapshot())
    monkeypatch.setattr(core, '_preflight_boot', lambda slug: None)
    monkeypatch.setattr(cbc, 'running_browser_identity', lambda slug: {'image': 'image', 'revision': 'revision'})

    def prepare_fails(*_args, **_kwargs):
        raise catalog.CatalogError('节点服务器“node.example”无法解析出公网 IPv4。 password=fixture')

    monkeypatch.setattr(cbc, 'prepare', prepare_fails)
    with pytest.raises(RuntimeError) as raised:
        core._dispatch(task('recover', generation='generation'))
    detail = store.instance_by_id('ifixture')['raw']['recovery']['detail']
    assert detail == str(raised.value)
    assert detail.startswith('第 1/3 次恢复未成功（节点服务器“node.example”无法解析出公网 IPv4。')
    assert 'fixture' not in detail
