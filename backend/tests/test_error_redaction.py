import concurrent.futures
import os
from pathlib import Path

import pytest

import manager_core as core
import store
from test_reliability import isolated as isolated, instance


def test_proxy_and_node_secrets_are_removed_when_library_is_damaged(isolated):
    instance(isolated)
    store._write_proxy('ifixture', {'password': 'fixture-socks-password'})
    store.LIBRARY.write_text('{broken')
    store._save(store._cache_path('source'), {'nodes': [{'config': {
        'uuid': 'fixture-node-uuid', 'private-key': 'fixture-private-key',
    }}]})
    text = core.safe_error(ValueError('fixture-socks-password fixture-node-uuid fixture-private-key'))
    assert text == '[REDACTED] [REDACTED] [REDACTED]'


def test_unchanged_secret_files_are_not_reparsed(isolated, monkeypatch):
    instance(isolated)
    store._save(store.LIBRARY, {'subscriptions': [{'url': 'https://fixture.test/sub?token=secret'}]})
    core.safe_error(ValueError('first'))
    original = Path.read_text
    reads = []

    def read(path, *args, **kwargs):
        reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read)
    for _ in range(5):
        assert core.safe_error(ValueError('ordinary error')) == 'ordinary error'
    assert reads == []


def test_secret_rotation_detects_atomic_replacement_even_with_same_size_and_mtime(isolated):
    instance(isolated)
    store._write_proxy('ifixture', {'password': 'fixture-password-old'})
    path = store.CONFIGS / 'ifixture/proxy.json'
    core.safe_error(ValueError('warm cache'))
    original = path.stat()
    store._write_proxy('ifixture', {'password': 'fixture-password-new'})
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert path.stat().st_size == original.st_size
    assert core.safe_error(ValueError('fixture-password-new')) == '[REDACTED]'


def test_secret_cache_is_scoped_to_current_root_and_removes_deleted_files(isolated):
    instance(isolated)
    store._write_proxy('ifixture', {'password': 'fixture-password'})
    assert core.safe_error(ValueError('fixture-password')) == '[REDACTED]'
    (store.CONFIGS / 'ifixture/proxy.json').unlink()
    assert core.safe_error(ValueError('fixture-password')) == 'fixture-password'


def test_longer_overlapping_secrets_are_redacted_first(isolated):
    instance(isolated)
    store._write_proxy('ifixture', {'password': 'fixture-password', 'nested': {'uuid': 'fixture-password-long'}})
    assert core.safe_error(ValueError('fixture-password-long')) == '[REDACTED]'


def test_a_corrupt_file_does_not_prevent_other_files_or_recovering_from_reload(isolated):
    instance(isolated)
    proxy = store.CONFIGS / 'ifixture/proxy.json'
    proxy.write_text('{broken')
    store._save(store.LIBRARY, {'subscriptions': [{'url': 'https://fixture.test/private'}]})
    assert core.safe_error(ValueError('https://fixture.test/private')) == '订阅地址'
    store._write_proxy('ifixture', {'password': 'fixture-recovered-password'})
    assert core.safe_error(ValueError('fixture-recovered-password')) == '[REDACTED]'


def test_cached_redaction_is_safe_with_parallel_requests(isolated):
    instance(isolated)
    store._write_proxy('ifixture', {'password': 'fixture-password'})
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: core.safe_error(ValueError('fixture-password')), range(32)))
    assert results == ['[REDACTED]'] * 32


@pytest.mark.parametrize('password', ['x', 'xy', 'xyz'])
def test_short_but_valid_socks_passwords_are_redacted(isolated, password):
    instance(isolated)
    store._write_proxy('ifixture', {'password': password})
    assert core.safe_error(ValueError(password)) == '[REDACTED]'
