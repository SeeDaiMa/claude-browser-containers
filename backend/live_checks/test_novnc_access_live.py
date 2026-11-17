"""只对获准的专用测试实例执行。设置 CBC_TEST_WEB_PORT，不启停任何服务。

本目录不在默认单元测试路径中；显式运行可复现旧配置失败、新配置通过。
"""
import base64
import http.client
import os

import pytest


@pytest.fixture(scope='module')
def port():
    value = int(os.environ['CBC_TEST_WEB_PORT'])
    assert 1024 <= value <= 65535 and value not in (5801, 5802)
    return value


def response(port, host, origin=None, *, websocket=False, path='/', method='GET', body=None):
    headers = {'Host': host}
    if origin is not None:
        headers['Origin'] = origin
    if websocket:
        headers.update({'Upgrade': 'websocket', 'Connection': 'Upgrade',
                        'Sec-WebSocket-Key': base64.b64encode(os.urandom(16)).decode(),
                        'Sec-WebSocket-Version': '13', 'Sec-WebSocket-Protocol': 'binary'})
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=8)
    try:
        connection.request(method, path, body=body, headers=headers)
        reply = connection.getresponse()
        return reply.status
    finally:
        connection.close()


@pytest.mark.parametrize('host', ['127.0.0.1:{port}', 'localhost:{port}'])
def test_direct_page_and_health_check_need_no_origin(port, host):
    assert response(port, host.format(port=port)) == 200


@pytest.mark.parametrize('host', [
    'attacker.example.test:{port}', 'localhost.attacker.example.test:{port}',
    '127.0.0.1.attacker.example.test:{port}', '127.0.0.1:1', 'localhost',
])
def test_rejects_untrusted_http_host(port, host):
    assert response(port, host.format(port=port)) == 403


@pytest.mark.parametrize('origin', [
    'http://127.0.0.1:{port}', 'http://localhost:{port}',
    'http://127.0.0.1:5790', 'http://localhost:5790',
])
def test_allows_instance_and_manager_websocket_origins(port, origin):
    assert response(port, f'127.0.0.1:{port}', origin.format(port=port),
                    websocket=True, path='/websockify') == 101


@pytest.mark.parametrize('origin', [
    None, 'null', 'http://attacker.example.test', 'https://attacker.example.test',
    'http://127.0.0.1:5790.attacker.example.test', 'http://localhost:{port}/',
    'http://127.0.0.1:{port}@attacker.example.test', 'http://127.0.0.1:1',
])
def test_rejects_untrusted_websocket_origin(port, origin):
    assert response(port, f'127.0.0.1:{port}', origin.format(port=port) if origin else None,
                    websocket=True, path='/websockify') == 403


def test_websocket_host_cannot_bypass_origin_check(port):
    assert response(port, f'attacker.example.test:{port}', f'http://127.0.0.1:{port}',
                    websocket=True, path='/websockify') == 403


def test_vnc_location_checks_origin_even_without_upgrade_header(port):
    assert response(port, f'127.0.0.1:{port}', 'http://attacker.example.test',
                    path='/websockify') == 403


NOT_PNG = b'not a png'


@pytest.mark.parametrize('host', ['127.0.0.1:{port}', 'localhost:{port}'])
def test_clipboard_accepts_own_page_and_status_poll(port, host):
    host = host.format(port=port)
    # 非 PNG 内容由剪贴板服务返回 415，说明请求已通过 nginx 的来源检查。
    assert response(port, host, f'http://{host}', path='/cbc-clipboard-image',
                    method='POST', body=NOT_PNG) == 415
    assert response(port, host, path='/cbc-clipboard-image') == 200


@pytest.mark.parametrize('origin', [
    None, 'null', 'http://attacker.example.test', 'http://127.0.0.1:5790',
    'http://localhost:{port}', 'http://127.0.0.1:1',
])
def test_clipboard_rejects_post_from_other_origins(port, origin):
    assert response(port, f'127.0.0.1:{port}', origin.format(port=port) if origin else None,
                    path='/cbc-clipboard-image', method='POST', body=NOT_PNG) == 403
