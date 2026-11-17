import gzip
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import cbc
from test_reliability import isolated as isolated


@pytest.fixture
def resources(isolated, monkeypatch):
    (isolated / '.env.example').write_text('BROWSER_CPUS=1.5\n')
    cbc.save_json(cbc.CONFIGS / 'seccomp-default.json', {'syscalls': []})
    (cbc.CONFIGS / 'cbc-clipboard-image.c').write_text('/* synthetic source */')
    database = isolated / 'backend/resources/Country.mmdb'
    database.parent.mkdir(parents=True)
    database.write_bytes(b'fixture database presence only')
    binary = b'fixture mihomo executable'
    packed = gzip.compress(binary)
    monkeypatch.setattr(cbc, 'MIHOMO_GZIP_SHA256', hashlib.sha256(packed).hexdigest(), raising=False)
    monkeypatch.setattr(cbc, 'MIHOMO_BINARY_SHA256', hashlib.sha256(binary).hexdigest(), raising=False)
    download = Mock(side_effect=lambda *a, **kw: io.BytesIO(packed))
    monkeypatch.setattr(cbc.urllib.request, 'urlopen', download)
    commands = []
    state = SimpleNamespace(fail_compile=False)

    def docker(*args, **kwargs):
        commands.append(args)
        if args[0] == 'run':
            if state.fail_compile:
                raise RuntimeError('fixture compiler failure')
            output = next(str(arg).removesuffix(':/out') for arg in args if str(arg).endswith(':/out'))
            (Path(output) / 'cbc-clipboard-image').write_bytes(b'fixture compiled helper')
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        'Architecture': 'arm64',
                        'Id': 'fixture-image',
                        'Size': 123,
                    }
                ]
            ),
        )

    monkeypatch.setattr(cbc, 'docker', docker)
    return SimpleNamespace(
        root=isolated,
        binary=binary,
        packed=packed,
        download=download,
        commands=commands,
        state=state,
        database=database,
        mihomo=isolated / 'container/mihomo',
        helper=cbc.CONFIGS / 'bin/cbc-clipboard-image',
    )


def test_init_prepares_verified_mihomo_and_builds_clipboard_helper(resources):
    cbc.init()
    assert resources.mihomo.read_bytes() == resources.binary
    assert resources.mihomo.stat().st_mode & 0o777 == 0o755
    assert resources.helper.read_bytes() == b'fixture compiled helper'
    assert resources.helper.stat().st_mode & 0o777 == 0o755
    resources.download.assert_called_once()
    commands = resources.commands
    compile_index = next(index for index, args in enumerate(commands) if args[0] == 'run')
    build_index = next(index for index, args in enumerate(commands) if args[0] == 'build')
    assert compile_index < build_index
    compile_args = commands[compile_index]
    assert '--rm' in compile_args and 'linux/arm64' in compile_args
    assert any(str(arg).endswith('/cbc-clipboard-image.c:ro') for arg in compile_args)
    assert 'alpine:3.24' in compile_args


@pytest.mark.parametrize('database', ['missing', 'empty'])
def test_missing_database_stops_init_with_instructions_before_docker(resources, database):
    resources.database.unlink()
    if database == 'empty':
        resources.database.touch()
    with pytest.raises(ValueError, match=r'Country.mmdb.*手动'):
        cbc.init()
    assert resources.commands == []
    resources.download.assert_not_called()


def test_bad_download_never_replaces_existing_mihomo(resources):
    resources.mihomo.parent.mkdir()
    resources.mihomo.write_bytes(b'preserved old executable')
    resources.download.side_effect = lambda *a, **kw: io.BytesIO(b'corrupt download')
    with pytest.raises(ValueError, match='SHA-256'):
        cbc.init()
    assert resources.mihomo.read_bytes() == b'preserved old executable'
    assert not any(args[0] in ('run', 'build') for args in resources.commands)


def test_verified_existing_binary_skips_download(resources):
    resources.mihomo.parent.mkdir()
    resources.mihomo.write_bytes(resources.binary)
    resources.download.side_effect = AssertionError('already verified; do not download again')
    cbc.init()
    resources.download.assert_not_called()
    assert resources.helper.exists()


def test_compile_failure_preserves_old_helper_and_stops_before_image_build(resources):
    resources.helper.parent.mkdir(parents=True)
    resources.helper.write_bytes(b'preserved old helper')
    resources.state.fail_compile = True
    with pytest.raises(RuntimeError, match='compiler failure'):
        cbc.init()
    assert resources.helper.read_bytes() == b'preserved old helper'
    assert not any(args[0] == 'build' for args in resources.commands)


def test_oversized_download_is_rejected(resources, monkeypatch):
    monkeypatch.setattr(cbc, 'MIHOMO_MAX_DOWNLOAD_BYTES', 32, raising=False)
    resources.download.side_effect = lambda *a, **kw: io.BytesIO(b'x' * 33)
    with pytest.raises(ValueError, match='过大'):
        cbc.init()
    assert not resources.mihomo.exists()


def test_decompression_failure_stops_without_replacing_binary(resources, monkeypatch):
    packed = b'not a gzip file'
    monkeypatch.setattr(cbc, 'MIHOMO_GZIP_SHA256', hashlib.sha256(packed).hexdigest(), raising=False)
    resources.download.side_effect = lambda *a, **kw: io.BytesIO(packed)
    with pytest.raises(ValueError, match='解压'):
        cbc.init()
    assert not resources.mihomo.exists()
