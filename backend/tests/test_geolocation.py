import sys
from pathlib import Path
from types import SimpleNamespace

import maxminddb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))

import cbc
import geolocation
import manager_core
import store


@pytest.fixture(autouse=True)
def clear_country_cache():
    geolocation.country_code.cache_clear()
    yield
    geolocation.country_code.cache_clear()


def test_local_lookup_uses_country_not_registration_and_caches(monkeypatch):
    calls = []

    def lookup(ip):
        calls.append(ip)
        return {'country': {'iso_code': 'US'}, 'registered_country': {'iso_code': 'JP'}}

    monkeypatch.setattr(geolocation, '_database', lambda: SimpleNamespace(get=lookup))
    assert geolocation.country_code('8.8.8.8') == 'US'
    assert geolocation.country_code('8.8.8.8') == 'US'
    assert calls == ['8.8.8.8']
    assert geolocation.country_code('2001:4860:4860::8888') == 'US'


def test_country_lookup_rejects_non_text_before_caching(monkeypatch):
    calls = []

    def lookup(ip):
        calls.append(ip)
        return {'country': {'iso_code': 'US'}}

    monkeypatch.setattr(geolocation, '_database', lambda: SimpleNamespace(get=lookup))
    for value in (['8.8.8.8'], {'ip': '8.8.8.8'}, 134744072, b'\x08\x08\x08\x08'):
        assert geolocation.country_code(value) is None
    assert calls == []
    assert geolocation.country_code('8.8.8.8') == 'US'
    assert calls == ['8.8.8.8']


def test_local_lookup_skips_nonpublic_and_invalid_addresses(monkeypatch):
    def forbidden():
        raise AssertionError('非公网地址不应查询归属库')

    monkeypatch.setattr(geolocation, '_database', forbidden)
    for ip in (None, '', 'invalid', '127.0.0.1', '192.168.1.1', '198.51.100.10', '2001:db8::1'):
        assert geolocation.country_code(ip) is None


def test_local_lookup_missing_database_is_optional(monkeypatch):
    monkeypatch.setattr(geolocation, '_database', lambda: None)
    assert geolocation.country_code('8.8.8.8') is None


def test_local_lookup_read_error_is_optional(monkeypatch):
    def broken(ip):
        raise maxminddb.InvalidDatabaseError('fixture')

    monkeypatch.setattr(geolocation, '_database', lambda: SimpleNamespace(get=broken))
    assert geolocation.country_code('8.8.8.8') is None


def test_instances_enrich_cached_country_without_probing(monkeypatch):
    saved = {'fixture': {'status': 'ok', 'ip': '8.8.8.8', 'detail': '', 'checked_at': '2026-11-14T00:00:00Z'}}
    monkeypatch.setattr(
        manager_core,
        'runtime_snapshot',
        lambda: {'docker': {'ok': False, 'detail': 'offline'}, 'containers': None, 'health': {}, 'ids': {}},
    )
    monkeypatch.setattr(manager_core, '_host_memory', lambda: {})
    monkeypatch.setattr(manager_core, '_memory_limits', lambda slug: {})
    monkeypatch.setattr(manager_core, '_self_rss', lambda: 12)
    monkeypatch.setattr(
        store,
        'scan_instances',
        lambda: (
            [
                {
                    'slug': 'fixture',
                    'id': 'fixture',
                    'display_name': '测试',
                    'created_at': None,
                    'trashed_at': None,
                    'port': 5803,
                }
            ],
            [],
        ),
    )
    monkeypatch.setattr(store, 'load_exits', lambda: saved)
    monkeypatch.setattr(store, 'proxy_public', lambda slug: {'mode': 'none', 'label': '未配置'})
    monkeypatch.setattr(
        geolocation, '_database', lambda: SimpleNamespace(get=lambda ip: {'country': {'iso_code': 'US'}})
    )

    def forbidden(slug):
        raise AssertionError('列表不能为了国家信息重新检测代理')

    monkeypatch.setattr(cbc, 'probe_proxy', forbidden)
    result = manager_core.list_instances()['instances'][0]['exit']
    assert result['country_code'] == 'US'
    assert result['checked_at'] == saved['fixture']['checked_at']
    assert 'country_code' not in saved['fixture']


def test_proxy_detection_prefers_local_country(monkeypatch):
    monkeypatch.setattr(cbc, 'addresses', lambda name: {'BRIDGE_IP': '10.231.1.3'})
    monkeypatch.setattr(cbc, 'docker', lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(cbc, 'observe_exit_info', lambda name: {'ip': '8.8.8.8', 'country_code': 'JP'})
    monkeypatch.setattr(
        geolocation, '_database', lambda: SimpleNamespace(get=lambda ip: {'country': {'iso_code': 'US'}})
    )
    result = cbc.probe_proxy('fixture')
    assert result['status'] == 'ok'
    assert result['country_code'] == 'US'
