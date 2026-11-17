import io
from urllib.request import Request

import pytest

import catalog


@pytest.mark.parametrize('target', ['http://fixture.test/sub', 'ftp://fixture.test/sub', '//fixture.test/sub'])
def test_redirect_cannot_downgrade_or_change_protocol(target):
    handler = getattr(catalog, '_HTTPSRedirectHandler', catalog.urllib.request.HTTPRedirectHandler)()
    with pytest.raises(catalog.CatalogError, match='HTTPS'):
        handler.redirect_request(Request('https://fixture.test/sub'), None, 302, 'Found', {}, target)


def test_https_redirect_preserves_normal_request_headers():
    handler = getattr(catalog, '_HTTPSRedirectHandler', catalog.urllib.request.HTTPRedirectHandler)()
    result = handler.redirect_request(Request('https://fixture.test/sub', headers={'User-Agent': 'clash-meta'}),
                                      None, 302, 'Found', {}, 'https://cdn.fixture.test/sub')
    assert result.full_url == 'https://cdn.fixture.test/sub'
    assert result.get_header('User-agent') == 'clash-meta'


def test_response_url_is_checked_before_reading_content(monkeypatch):
    class Response(io.BytesIO):
        def geturl(self):
            return 'http://fixture.test/unsafe'

        def read(self, *args):
            raise AssertionError('不得读取非 HTTPS 订阅内容')

    class Opener:
        def open(self, *args, **kwargs):
            return Response(b'fixture')

    monkeypatch.setattr(catalog.urllib.request, 'build_opener', lambda *a: Opener())
    monkeypatch.setattr(catalog.urllib.request, 'urlopen', lambda *a, **kw: Response(b'fixture'))
    with pytest.raises(catalog.CatalogError, match='HTTPS'):
        catalog._download_proxies('https://fixture.test/sub')
