"""noVNC 启动保护的隔离回归，不访问 Docker 或真实实例。"""
import os
import re
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

import cbc
from test_reliability import isolated as isolated, instance


PROJECT = Path(__file__).resolve().parents[2]
GUARD = PROJECT / 'configs/novnc-access.sh'
SITE = 'server {\n\tinclude /opt/base/etc/nginx/include/vnc.conf;\n}\n'
VNC = 'location ~ /websockify$ {\n\twebsockify_pass unix:/tmp/vnc.sock;\n}\n'


def run_guard(root, port='5898', site=SITE, vnc=VNC):
    nginx = root / 'nginx'
    (nginx / 'include').mkdir(parents=True, exist_ok=True)
    if not (nginx / 'default_site.conf').exists():
        (nginx / 'default_site.conf').write_text(site)
        (nginx / 'include/vnc.conf').write_text(vnc)
    script = root / 'access.sh'
    script.write_text(GUARD.read_text().replace('/opt/base/etc/nginx', str(nginx)))
    result = subprocess.run(['sh', str(script)], env={**os.environ, 'CBC_WEB_PORT': port},
                            capture_output=True, text=True)
    return result, (nginx / 'default_site.conf').read_text(), (nginx / 'include/vnc.conf').read_text()


def test_access_guard_changes_browser_revision(isolated):
    instance(isolated)
    cbc.save_json(isolated / 'images.lock.json', {
        'browser': {'reference': 'browser-fixture'}, 'network': {'reference': 'network-fixture'},
    })
    shutil.copy2(PROJECT / 'compose.yaml', isolated / 'compose.yaml')
    sources = '''chromium-params.sh chromium-policy.json seccomp-chromium.json
        novnc-clipboard-focus.sh novnc-keyboard-shortcuts.sh novnc-native-cursor.sh
        novnc-hidpi.sh novnc-ime.sh novnc-ime.js novnc-clipboard-image.sh
        cbc-clipboard-image.nginx.conf cbc-clipboard-image-run.sh
        bin/cbc-clipboard-image cbc-clipboard-image.c'''.split()
    for name in sources:
        target = cbc.CONFIGS / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'fixture source for revision hashing\n')
    guard = cbc.CONFIGS / 'novnc-access.sh'
    guard.write_text('first access policy\n')
    before = cbc.planned_browser('ifixture')['revision']
    guard.write_text('changed access policy\n')
    assert cbc.planned_browser('ifixture')['revision'] != before


def test_compose_passes_instance_port_to_read_only_access_guard():
    browser = yaml.safe_load((PROJECT / 'compose.yaml').read_text())['services']['browser']
    assert browser['environment'].get('CBC_WEB_PORT') == '${WEB_PORT}'
    assert './configs/novnc-access.sh:/etc/cont-init.d/20-local-access.sh:ro' in browser['volumes']


@pytest.mark.parametrize('port', ['', 'no-port', '0', '1023', '65536', '5898; return 200;', '5898\nother'])
def test_invalid_port_cannot_write_nginx_configuration(tmp_path, port):
    result, site, vnc = run_guard(tmp_path, port)
    assert result.returncode != 0
    assert 'CBC_WEB_PORT' in result.stderr
    assert (site, vnc) == (SITE, VNC)


@pytest.mark.parametrize('site,vnc', [('server { # changed\n}\n', VNC), (SITE, 'location /changed {\n}\n')])
def test_unknown_nginx_layout_keeps_both_files(tmp_path, site, vnc):
    result, actual_site, actual_vnc = run_guard(tmp_path, site=site, vnc=vnc)
    assert result.returncode != 0
    assert 'Unsupported noVNC nginx config' in result.stderr
    assert (actual_site, actual_vnc) == (site, vnc)


def test_repeated_initialization_is_idempotent_and_port_changes_fail_closed(tmp_path):
    first, site, vnc = run_guard(tmp_path)
    assert first.returncode == 0, first.stderr
    assert (site, vnc) != (SITE, VNC)
    second, same_site, same_vnc = run_guard(tmp_path)
    assert second.returncode == 0, second.stderr
    assert (same_site, same_vnc) == (site, vnc)
    changed, old_site, old_vnc = run_guard(tmp_path, port='5899')
    assert changed.returncode != 0
    assert (old_site, old_vnc) == (site, vnc)


CLIPBOARD = PROJECT / 'configs/cbc-clipboard-image.nginx.conf'


def clipboard_allows(method, origin, host='127.0.0.1:5898'):
    """按 nginx 拼接方式生成请求串，用配置里的同一条正则判断是否放行。"""
    text = CLIPBOARD.read_text()
    template = re.search(r'set \$cbc_clipboard_request "([^"]+)";', text).group(1)
    pattern = re.search(r'if \(\$cbc_clipboard_request !~\* "([^"]+)"\)', text).group(1)
    request = (template.replace('$request_method', method)
               .replace('$http_origin', origin or '').replace('$http_host', host))
    return re.search(pattern, request, re.IGNORECASE) is not None


def test_clipboard_rule_runs_before_proxying_and_only_returns():
    text = CLIPBOARD.read_text()
    assert text.index('return 403;') < text.index('proxy_pass')
    block = re.search(r'if \(\$cbc_clipboard_request .*\) \{(.*?)\}', text, re.S).group(1)
    assert block.strip() == 'return 403;'


@pytest.mark.parametrize('host', ['127.0.0.1:5898', 'localhost:5898'])
def test_clipboard_accepts_own_page_post_and_status_poll(host):
    assert clipboard_allows('POST', f'http://{host}', host)
    assert clipboard_allows('GET', None, host)


@pytest.mark.parametrize('origin', [
    None, 'null', 'http://attacker.example.test', 'http://127.0.0.1:5790',
    'http://localhost:5898', 'http://127.0.0.1:5898/', 'http://127.0.0.1:58989',
    'http://127.0.0.1:5898 http://127.0.0.1:5898',
])
def test_clipboard_rejects_post_from_other_origins(origin):
    assert not clipboard_allows('POST', origin)


@pytest.mark.parametrize('method', ['PUT', 'DELETE', 'OPTIONS', 'HEAD'])
def test_clipboard_rejects_other_methods(method):
    assert not clipboard_allows(method, 'http://127.0.0.1:5898')
