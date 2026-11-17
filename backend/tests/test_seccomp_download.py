import hashlib
import io
from unittest.mock import Mock

import pytest

import cbc
from test_init_resources import resources as resources
from test_reliability import isolated as isolated


def prepare_download(resources, monkeypatch, payload):
    path = cbc.CONFIGS / 'seccomp-default.json'
    path.unlink()
    monkeypatch.setattr(cbc, '_prepare_mihomo', lambda: None)
    monkeypatch.setattr(cbc, '_prepare_clipboard_helper', lambda: None)
    monkeypatch.setattr(cbc, 'SECCOMP_SHA256', hashlib.sha256(payload).hexdigest(), raising=False)
    download = Mock(side_effect=lambda *a, **kw: io.BytesIO(payload))
    monkeypatch.setattr(cbc.urllib.request, 'urlopen', download)
    return path, download


def test_verified_seccomp_download_is_saved_before_build(resources, monkeypatch):
    path, download = prepare_download(resources, monkeypatch, b'{"syscalls": []}')
    cbc.init()
    assert cbc.read_json(path) == {'syscalls': []}
    download.assert_called_once()
    assert any(args[0] == 'build' for args in resources.commands)


def test_seccomp_hash_mismatch_stops_without_writing_or_building(resources, monkeypatch):
    path, _ = prepare_download(resources, monkeypatch, b'{"syscalls": []}')
    monkeypatch.setattr(cbc, 'SECCOMP_SHA256', '0' * 64, raising=False)
    with pytest.raises(ValueError, match='seccomp.*SHA-256'):
        cbc.init()
    assert not path.exists()
    assert not (cbc.CONFIGS / 'seccomp-chromium.json').exists()
    assert not any(args[0] == 'build' for args in resources.commands)


def test_existing_seccomp_is_preserved_without_downloading(resources, monkeypatch):
    path = cbc.CONFIGS / 'seccomp-default.json'
    before = path.read_bytes()
    resources.mihomo.parent.mkdir()
    resources.mihomo.write_bytes(resources.binary)
    cbc.init()
    assert path.read_bytes() == before
    resources.download.assert_not_called()


def test_invalid_verified_json_is_not_written(resources, monkeypatch):
    path, _ = prepare_download(resources, monkeypatch, b'not JSON')
    with pytest.raises(ValueError):
        cbc.init()
    assert not path.exists()
