#!/usr/bin/env python3
"""Local-only manager; stdlib only. Credentials are never command arguments."""
import argparse
import datetime
import fcntl
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

import catalog

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ROOT / 'configs'
SELECTION = CONFIGS / 'selection.json'
LABEL = 'io.cbc.project=claude-browser-containers'
TAGS = {'chromium': 'jlesage/chromium:v26.10.1', 'gost': 'gogost/gost:3.3.3'}
UPSTREAM = {
    'chromium': 'jlesage/chromium@sha256:71e2e8468eb00519a1f90cbb82a12651c2541c6f5e85030faf9c8dd2ec2cc831',
    'gost': 'gogost/gost@sha256:f266f957c6e4f8d433ed23202a5745fa4969d08f3f0ef034fb9357345e16ad67',
}
DEFAULTS = dict(BROWSER_MEMORY_MIB='1536', BROWSER_CPUS='1.5', BROWSER_SHM_MIB='512', PROXY_MEMORY_MIB='256', GUARD_MEMORY_MIB='64')
COMMAND_TIMEOUT = 30
IMAGE_PULL_TIMEOUT = 900
IMAGE_BUILD_TIMEOUT = 1800
BACKUP_VERSION = 1
MAX_BACKUP_MANIFEST_BYTES = 8 * 1024 * 1024


class SandboxError(RuntimeError):
    pass


class CountryPolicyError(RuntimeError):
    pass


def run(args, *, check=True, capture=True, timeout=COMMAND_TIMEOUT, **kwargs):
    # 没有超时的外部命令会占住管理锁，其他实例的启停只能一直排队。
    result = subprocess.run([str(a) for a in args], cwd=ROOT, text=True, capture_output=capture, timeout=timeout, **kwargs)
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout or 'Command failed').strip())
    return result


def docker(*args, **kwargs):
    return run(['docker', *args], **kwargs)


def read_json(path):
    return json.loads(Path(path).read_text())


def save_json(path, value, private=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if private:
        path.parent.chmod(0o700)
    temp = path.with_suffix(path.suffix + '.tmp')
    with open(temp, 'w', opener=lambda p, f: os.open(p, f, 0o600 if private else 0o644)) as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    os.replace(temp, path)
    if private:
        path.chmod(0o600)


def validate_name(name):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', name):
        raise ValueError('实例名须为 1–32 位小写字母、数字或连字符，首位为字母。')
    return name


def instances(names=None):
    known = sorted(p.parent.name for p in CONFIGS.glob('*/instance.json'))
    for name in names or []:
        validate_name(name)
        if name not in known:
            raise ValueError(f'实例不存在：{name}')
    return names or known


def settings(name):
    import store
    return store.read_instance(CONFIGS / validate_name(name) / 'instance.json')


def addresses(name):
    i = settings(name)['network_index']
    return {'INTERNAL_SUBNET': f'10.231.{i}.0/29', 'EGRESS_SUBNET': f'10.232.{i}.0/29',
            'BROWSER_IP': f'10.231.{i}.2', 'BRIDGE_IP': f'10.231.{i}.3', 'EGRESS_IP': f'10.232.{i}.3', 'INGRESS_IP': f'10.232.{i}.2'}


def compose(name, *args, check=True, capture=True, timeout=120):
    return docker('compose', '--project-name', f'cbc-{name}', '--env-file', CONFIGS / name / 'runtime/compose.env',
                  '-f', ROOT / 'compose.yaml', *args, check=check, capture=capture, timeout=timeout)


def container(name, role):
    return f'cbc-{name}-{role}'


def active(name):
    result = docker('ps', '-a', '--filter', f'name=^/{container(name, "browser")}$', '--format', '{{.State}}', timeout=8)
    return result.stdout.strip() in ('running', 'paused', 'restarting')


def down(name, timeout=20):
    """停止失败不能当成功；缺少 compose.env 的未启动实例也可以安全清理。"""
    if (CONFIGS / name / 'runtime/compose.env').exists():
        compose(name, 'down', '--timeout', str(timeout))
    remaining = docker('ps', '-a', '--filter', f'label={LABEL}', '--filter', f'label=io.cbc.instance={name}', '--format', '{{.Names}}', timeout=8)
    if remaining.stdout.strip():
        raise RuntimeError('实例组件尚未清理完成，请检查 Docker 和实例运行配置后重试。')


def cleanup_start_failure(name, error):
    if isinstance(error, (SandboxError, CountryPolicyError)):
        # 安全校验失败时先关闭公网通道；浏览器关闭确认不能让它继续联网。
        docker('stop', '--time', '5', container(name, 'proxy'), timeout=15)
    close_browser(name)
    down(name, timeout=10)


def create(name, port):
    import store
    store.require_complete_inventory()
    validate_name(name)
    directory = CONFIGS / name
    if (directory / 'instance.json').exists():
        if settings(name)['port'] != port:
            raise ValueError('实例已存在且端口不同；请先停止并修改 instance.json。')
        print(f'{name} 已存在，保留原配置及数据。')
        return
    if not 1024 <= port <= 65535:
        raise ValueError('端口须在 1024–65535 范围内。')
    if any(settings(n)['port'] == port for n in instances()):
        raise ValueError('该端口已分配给另一个实例。')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', port))
    used = {settings(n)['network_index'] for n in instances()}
    subnets = []
    ids = docker('network', 'ls', '-q').stdout.split()
    if ids:
        for network in json.loads(docker('network', 'inspect', *ids).stdout):
            subnets.extend(ipaddress.ip_network(c['Subnet']) for c in network['IPAM']['Config'] or [] if c.get('Subnet'))
    index = next((i for i in range(1, 255) if i not in used and all(not net.overlaps(ipaddress.ip_network(f'10.{prefix}.{i}.0/29')) for prefix in [231, 232] for net in subnets if net.version == 4)), None)
    if index is None:
        raise ValueError('没有可用的项目子网。')
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    if not (directory / 'proxy.json').exists():
        save_json(directory / 'proxy.json', read_json(CONFIGS / 'proxy.example.json'), private=True)
    data = ROOT / 'data' / name
    data.mkdir(parents=True, exist_ok=True)
    data.chmod(0o700)
    save_json(directory / 'instance.json', {'name': name, 'port': port, 'network_index': index})
    print(f'已创建 {name}：http://127.0.0.1:{port}；代理未配置时只允许离线运行。')


def selection_map():
    if not SELECTION.exists():
        return {}
    SELECTION.chmod(0o600)
    value = read_json(SELECTION)
    if not isinstance(value, dict):
        raise ValueError('selection.json 必须是对象。')
    return value


def selected_label(name):
    value = selection_map().get(name)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get('node', '')
    return ''


def manual_socks(proxy):
    port = int(proxy.get('port', 1080))
    if not 1 <= port <= 65535:
        raise ValueError('代理端口无效。')
    values = [proxy.get(key, '') for key in ('host', 'username', 'password')]
    if not all(isinstance(value, str) for value in values):
        raise ValueError('代理字段须为字符串。')
    configured = all(values)
    if any(values) and not configured:
        raise ValueError('Host、Username、Password 必须全部填写，或全部留空。')
    if not configured:
        return None
    if any(len(proxy[key].encode()) > 255 for key in ('username', 'password')):
        raise ValueError('SOCKS5 用户名、密码不得超过 255 字节。')
    try:
        upstream = [str(ipaddress.IPv4Address(values[0]))]
    except ipaddress.AddressValueError:
        upstream = sorted({item[4][0] for item in socket.getaddrinfo(values[0], port, socket.AF_INET, socket.SOCK_STREAM)})
    if not upstream:
        raise ValueError('上游没有 IPv4 地址。')
    return {'upstream': upstream, 'port': port, 'username': proxy['username'], 'password': proxy['password'], 'label': '手动 SOCKS5'}


def node_upstream(name):
    """节点只能在网页管理台选择；绑定必须带订阅库里的节点 ID。"""
    record = selection_map().get(name)
    if isinstance(record, dict) and record.get('node_id'):
        import store
        node = store.materialize_node(record.get('subscription_id', ''), record['node_id'])
        pinned, address, port = catalog.pin_endpoint(node)
        return {'node': pinned, 'upstream': [address], 'port': port, 'label': node['name']}
    if selected_label(name):
        raise ValueError('节点绑定缺少节点 ID，请在网页管理台重新选择或确认节点。')
    return None


def proxy_plan(name, proxy):
    # 显式 SOCKS5 优先，保证临时验收夹具不会被订阅选择覆盖。
    manual = manual_socks(proxy)
    if manual:
        return manual
    return node_upstream(name)


def gost_config(name, bridge, upstream):
    if upstream and 'node' in upstream:
        address = '127.0.0.1:1080'
        connector = {'type': 'socks5', 'metadata': {'notls': True}}
    elif upstream:
        address = f"{upstream['upstream'][0]}:{upstream['port']}"
        connector = {'type': 'socks5', 'auth': {'username': upstream['username'], 'password': upstream['password']}, 'metadata': {'notls': True}}
    else:
        address = '192.0.2.1:1080'
        connector = {'type': 'socks5', 'auth': {'username': '', 'password': ''}, 'metadata': {'notls': True}}
    return {
        'services': [{'name': name, 'addr': f'{bridge}:8080', 'handler': {'type': 'http', 'chain': 'upstream'}, 'listener': {'type': 'tcp'}}],
        'chains': [{'name': 'upstream', 'hops': [{'name': 'socks', 'nodes': [{'name': 'dedicated', 'addr': address, 'connector': connector, 'dialer': {'type': 'tcp'}}]}]}],
        'log': {'level': 'error', 'format': 'json', 'output': 'stderr'},
    }


def resource_env():
    env = DEFAULTS.copy()
    env_path = ROOT / '.env'
    if env_path.exists():
        env_path.chmod(0o600)
        for line in env_path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            key, value = line.split('=', 1)
            if key.strip() not in DEFAULTS:
                raise ValueError('未知 .env 配置项；代理凭据请放入 proxy.json。')
            env[key.strip()] = value.strip()
    for key, value in env.items():
        if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value) or float(value) <= 0:
            raise ValueError(f'资源配置无效：{key}')
    return env


def planned_browser(name, cdp=False):
    """计算这次准备会写给浏览器的镜像和修订号，不写文件。"""
    lock = read_json(ROOT / 'images.lock.json')
    env = resource_env()
    addr = addresses(name)
    revision_source = json.dumps({'resources': env, 'addresses': addr, 'images': lock, 'cdp': cdp, 'port': settings(name)['port']}, sort_keys=True).encode()
    revision_source += (ROOT / 'compose.yaml').read_bytes()
    for path in ['chromium-params.sh', 'chromium-policy.json', 'seccomp-chromium.json', 'novnc-clipboard-focus.sh', 'novnc-keyboard-shortcuts.sh', 'novnc-native-cursor.sh', 'novnc-hidpi.sh', 'novnc-ime.sh', 'novnc-ime.js', 'novnc-access.sh', 'novnc-clipboard-image.sh', 'cbc-clipboard-image.nginx.conf', 'cbc-clipboard-image-run.sh', 'bin/cbc-clipboard-image', 'cbc-clipboard-image.c']:
        revision_source += (CONFIGS / path).read_bytes()
    return {
        'image': lock['browser']['reference'],
        'network_image': lock['network']['reference'],
        'revision': hashlib.sha256(revision_source).hexdigest(),
        'resources': env,
        'addresses': addr,
    }


def running_browser_identity(name):
    old = json.loads(docker('inspect', container(name, 'browser')).stdout)[0]
    current = dict(line.split('=', 1) for line in old['Config']['Env'] if '=' in line)
    return {'image': old.get('Image', ''), 'revision': current.get('CBC_BROWSER_REVISION', '')}


def browser_would_change(name, cdp=False):
    if not active(name):
        return False
    running = running_browser_identity(name)
    planned = planned_browser(name, cdp=cdp)
    return running['image'] != planned['image'] or running['revision'] != planned['revision']


def prepare(name, cdp=False, keep_browser=None):
    proxy_path = CONFIGS / name / 'proxy.json'
    proxy_path.chmod(0o600)
    proxy = read_json(proxy_path)
    upstream = proxy_plan(name, proxy)
    addr = addresses(name)
    gost = gost_config(name, addr['BRIDGE_IP'], upstream)
    runtime = CONFIGS / name / 'runtime'
    runtime.mkdir(exist_ok=True)
    runtime.chmod(0o700)
    mihomo_path = runtime / 'mihomo.json'
    mihomo = catalog.mihomo_config(upstream['node']) if upstream and 'node' in upstream else None
    # Compose does not notice changed bind-mounted file contents. A non-secret
    # revision forces only the proxy to restart when its upstream changes.
    old_path = runtime / 'gost.json'
    old_mihomo = read_json(mihomo_path) if mihomo_path.exists() else None
    revision_path = runtime / 'revision'
    changed = not old_path.exists() or read_json(old_path) != gost or old_mihomo != mihomo
    if changed or not revision_path.exists():
        revision_path.write_text(secrets.token_hex(16))
    revision = revision_path.read_text()
    save_json(runtime / 'gost.json', gost, private=True)
    firewall_port = upstream['port'] if upstream else 1080
    firewall_addresses = upstream['upstream'] if upstream else []
    save_json(runtime / 'firewall.json', {'upstream_ipv4': firewall_addresses, 'port': firewall_port}, private=True)
    if mihomo:
        save_json(mihomo_path, mihomo, private=True)
    elif mihomo_path.exists():
        mihomo_path.unlink()
    plan = planned_browser(name, cdp=cdp)
    env = plan['resources'].copy()
    env.update(plan['addresses'], INSTANCE=name, WEB_PORT=str(settings(name)['port']), CHROMIUM_IMAGE=plan['image'], NETWORK_IMAGE=plan['network_image'], CDP_ENABLED='1' if cdp else '0', PROXY_REVISION=revision)
    env['BROWSER_REVISION'] = plan['revision']
    if keep_browser and keep_browser.get('image') and keep_browser.get('revision'):
        # 只切换代理时，沿用正在运行的浏览器镜像和修订号，避免 compose 重建浏览器。
        env['CHROMIUM_IMAGE'] = keep_browser['image']
        env['BROWSER_REVISION'] = keep_browser['revision']
    (runtime / 'compose.env').write_text(''.join(f'{k}={v}\n' for k, v in env.items()))
    (runtime / 'compose.env').chmod(0o600)
    return upstream is not None


def init():
    docker('info', '--format', '{{.ServerVersion}}')
    if not (ROOT / '.env').exists():
        shutil.copyfile(ROOT / '.env.example', ROOT / '.env')
        (ROOT / '.env').chmod(0o600)
    # 不再自动创建示例实例。已经存在的实例留在 configs/ 里，由管理台登记。
    records = {}
    for key, tag in TAGS.items():
        result = docker('image', 'inspect', UPSTREAM[key], check=False, timeout=COMMAND_TIMEOUT)
        if result.returncode:
            docker('pull', '--platform', 'linux/arm64', UPSTREAM[key], capture=False, timeout=IMAGE_PULL_TIMEOUT)
        image = json.loads(docker('image', 'inspect', UPSTREAM[key], timeout=COMMAND_TIMEOUT).stdout)[0]
        if image['Architecture'] != 'arm64':
            raise RuntimeError('拒绝使用非 ARM64 镜像。')
        records[key] = {'tag': tag, 'reference': UPSTREAM[key], 'uncompressed_bytes': image['Size']}
    if not (CONFIGS / 'seccomp-default.json').exists():
        source = 'https://raw.githubusercontent.com/moby/moby/v24.0.6/profiles/seccomp/default.json'
        save_json(CONFIGS / 'seccomp-default.json', json.load(urllib.request.urlopen(source, timeout=30)))
    browser = read_json(CONFIGS / 'seccomp-default.json')
    for entry in browser['syscalls']:
        if 'unshare' in entry['names']:
            entry['names'].remove('unshare')
    # Keep mount/cgroup/UTS/IPC namespace creation denied; permit Chromium's
    # unprivileged user/PID/network sandbox. clone3 retains upstream ENOSYS.
    browser['syscalls'].extend([
        {'names': ['unshare'], 'action': 'SCMP_ACT_ALLOW', 'args': [{'index': 0, 'value': 0xEFFFFFFF, 'valueTwo': 0, 'op': 'SCMP_CMP_MASKED_EQ'}]},
        {'names': ['clone'], 'action': 'SCMP_ACT_ALLOW', 'args': [{'index': 0, 'value': 0x0E020000, 'valueTwo': 0, 'op': 'SCMP_CMP_MASKED_EQ'}]},
    ])
    save_json(CONFIGS / 'seccomp-chromium.json', browser)
    docker('build', '--platform', 'linux/arm64', '--build-arg', f"GOST_IMAGE={records['gost']['reference']}", '-t', 'cbc-network:1', ROOT / 'container', capture=False, timeout=IMAGE_BUILD_TIMEOUT)
    network = json.loads(docker('image', 'inspect', 'cbc-network:1', timeout=COMMAND_TIMEOUT).stdout)[0]
    records['network'] = {'tag': 'cbc-network:1', 'reference': network['Id'], 'uncompressed_bytes': network['Size']}
    docker('build', '--platform', 'linux/arm64', '--build-arg', f"CHROMIUM_BASE={records['chromium']['reference']}", '-f', ROOT / 'container/Dockerfile.browser', '-t', 'cbc-chromium:1', ROOT / 'container', capture=False, timeout=IMAGE_BUILD_TIMEOUT)
    browser_image = json.loads(docker('image', 'inspect', 'cbc-chromium:1', timeout=COMMAND_TIMEOUT).stdout)[0]
    records['browser'] = {'tag': 'cbc-chromium:1', 'reference': browser_image['Id'], 'uncompressed_bytes': browser_image['Size']}
    save_json(ROOT / 'images.lock.json', records)
    for name in instances():
        prepare(name)
    print('初始化完成。未填写代理的实例可安全离线启动。')


def start(name, cdp=False):
    import store
    if settings(name).get('deleting'):
        raise ValueError(f'{name} 已开始彻底删除，只能继续清理，不能启动或重启。')
    if settings(name).get('trashed_at'):
        raise ValueError(f'{name} 在回收站中，请先恢复。')
    generation = store.set_run_intent(name, False)
    if active(name) and browser_would_change(name, cdp=cdp):
        close_browser(name)
    configured = prepare(name, cdp=cdp)
    try:
        if configured:
            compose(name, 'up', '-d', '--wait', '--wait-timeout', '90', 'guard', 'proxy', capture=False)
            verify_exit(name)
            compose(name, 'up', '-d', '--wait', '--wait-timeout', '90', 'browser', capture=False)
        else:
            compose(name, 'up', '-d', '--wait', '--wait-timeout', '90', capture=False)
        wait_until_ready(name)
        if configured:
            detail = f'全局节点“{selected_label(name) or "手动 SOCKS5"}”'
        else:
            detail = '离线保护中，尚未选择节点'
        url = f"http://127.0.0.1:{settings(name)['port']}"
        print(f'{name} 已启动：{url}，{detail}。请运行 check-proxy.sh 验证出口。')
        if configured:
            store.lock_proxy(name)
            store.mark_running(name, generation)
    except Exception as error:
        secrets = catalog.secret_values(read_json(CONFIGS / name / 'proxy.json'))
        mihomo_file = CONFIGS / name / 'runtime/mihomo.json'
        if mihomo_file.exists():
            secrets.extend(catalog.secret_values(read_json(mihomo_file)))
        diagnostic = []
        for role in ['guard', 'proxy', 'browser']:
            try:
                output = docker('logs', '--tail', '80', container(name, role), check=False, timeout=15)
            except subprocess.TimeoutExpired:
                output = subprocess.CompletedProcess(args=(), returncode=1, stdout='', stderr='日志读取超时。')
            message = output.stdout + output.stderr
            for secret in secrets:
                message = message.replace(secret, '[REDACTED]')
            diagnostic.append(f'{role}:\n{message}')
        path = CONFIGS / name / 'runtime/start-failure.log'
        path.write_text('\n'.join(diagnostic))
        path.chmod(0o600)
        print(f'启动诊断已脱敏保存：{path}', file=sys.stderr)
        try:
            cleanup_start_failure(name, error)
        except Exception as cleanup_error:
            raise RuntimeError(f'启动失败，安全收尾未完成：{cleanup_error}') from error
        raise


def close_browser(name):
    try:
        if not active(name):
            return
        result = docker('exec', '--user', '1000', container(name, 'browser'), 'env', 'DISPLAY=:0', 'xdotool', 'search', '--onlyvisible', '--class', 'chromium', 'windowquit', '%@', check=False, timeout=20)
    except subprocess.TimeoutExpired:
        # 关闭命令没有返回时不能改用 docker kill，否则刚写入的 Cookie 可能来不及落盘。
        raise RuntimeError(f'{name} 无法正常关闭 Chromium；为避免丢失刚写入的 Cookie，保留运行状态。') from None
    if result.returncode:
        raise RuntimeError(f'{name} 无法正常关闭 Chromium；为避免丢失刚写入的 Cookie，保留运行状态。')
    end = time.monotonic() + 15
    while True:
        try:
            running = active(name)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f'{name} 无法正常关闭 Chromium；为避免丢失刚写入的 Cookie，保留运行状态。') from None
        if not running:
            return
        if time.monotonic() >= end:
            raise RuntimeError(f'{name} 正常退出超时；请处理浏览器中的关闭确认，再运行停止命令。')
        time.sleep(0.25)


def stop(name):
    import store
    store.set_run_intent(name, False)
    close_browser(name)
    down(name, timeout=15)
    print(f'{name} 已停止；配置与浏览器数据已保留。')


def probe(name, *args, check=True):
    return docker('exec', container(name, 'guard'), 'python3', '/opt/cbc/probe.py', *args, check=check, timeout=45)


def observe_exit_info(name):
    """以 Claude 看到的出口为准；其他检测站的 IP 不能替代它。"""
    addr = addresses(name)
    try:
        result = docker('exec', container(name, 'guard'), 'curl', '-fsS', '--max-time', '10', '--noproxy', '', '--proxy', f"http://{addr['BRIDGE_IP']}:8080", 'https://claude.ai/cdn-cgi/trace', check=False, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return {'ip': None, 'country_code': None}
    if result.returncode:
        return {'ip': None, 'country_code': None}
    fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    try:
        value = str(ipaddress.ip_address(fields.get('ip', '').strip()))
    except ValueError:
        return {'ip': None, 'country_code': None}
    code = fields.get('loc', '').strip().upper()
    country_code = code if len(code) == 2 and code.isascii() and code.isalpha() and code not in ('XX', 'ZZ') else None
    return {'ip': value, 'country_code': country_code}


def observe_exit(name):
    """保留 CLI 原有的 IP 字符串返回值。"""
    return observe_exit_info(name)['ip']


def _country(value):
    code = value.strip().upper() if isinstance(value, str) else ''
    return code if len(code) == 2 and code.isascii() and code.isalpha() and code not in ('XX', 'ZZ') else None


def baseline_exit_country(name):
    """该实例最近一次成功时的 Claude 出口国家。

    返回 (国家代码或 None, 记录是否可读)。从未成功过时返回 (None, True)。
    """
    import geolocation
    import store
    try:
        record = store.load_exits().get(name)
    except (OSError, ValueError):  # SharedRecordError 是 ValueError 的子类
        return None, False
    if record is None:
        return None, True
    if not isinstance(record, dict) or not store.exit_record_readable(record):
        return None, False
    last = record.get('last_success')
    if not isinstance(last, dict):
        return None, True
    return _country(last.get('country_code')) or _country(geolocation.country_code(last.get('ip'))), True


def require_same_country(name, observation):
    """只比较 Claude 出口，入口位置不参与判断（中转节点的入口和出口本就不在同一国家）。

    放行条件：本次 Claude 出口国家可以确认，并且与该实例上次成功时的出口国家相同。
    从未成功过的实例没有历史出口，只要求出口国家可以确认。
    不满足时立即关闭本实例代理，保留原绑定。
    """
    exit_country = _country(observation.get('country_code')) if observation.get('status') == 'ok' else None
    baseline, readable = baseline_exit_country(name)
    if exit_country and readable and (baseline is None or baseline == exit_country):
        return
    if not exit_country:
        detail = 'Claude 出口国家无法确认。原绑定已保留，请检查原节点或新建实例。'
    elif not readable:
        detail = '出口记录无法读取，无法确认出口是否与上次一致。原绑定和原记录已保留，请检查出口记录。'
    else:
        detail = f'Claude 出口国家（{exit_country}）与上次成功时（{baseline}）不同。原绑定已保留，请检查原节点或新建实例。'
    if observation.get('status') == 'failed':
        detail = '代理连接失败。' + detail
    try:
        stopped = docker('stop', '--time', '5', container(name, 'proxy'), check=False, timeout=15)
        if stopped.returncode:
            raise RuntimeError('代理停止失败')
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        detail += ' 无法确认代理已断开，请检查 Docker。'
    else:
        detail += ' 已断开本实例代理。'
    try:
        import store
        store.save_exit(name, 'failed', None, detail)
    except (OSError, ValueError):
        # 网络拒绝优先；损坏记录不能掩盖拒绝原因，也不能被空记录覆盖。
        pass
    raise CountryPolicyError(detail)


def probe_proxy(name):
    """先确认本实例代理能访问公网，再读取 Claude 看到的出口 IP 和国家。"""
    import geolocation
    address = addresses(name)
    proxy = f"http://{address['BRIDGE_IP']}:8080"
    try:
        page = docker(
            'exec', container(name, 'guard'), 'curl', '-fsS', '--max-time', '15', '--noproxy', '',
            '--proxy', proxy, 'https://example.com', '-o', '/dev/null', check=False, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {'status': 'failed', 'ip': None, 'detail': '代理连接失败'}
    if page.returncode != 0:
        return {'status': 'failed', 'ip': None, 'detail': '代理连接失败'}
    observed = observe_exit_info(name)
    if observed['ip']:
        country = geolocation.country_code(observed['ip']) or observed.get('country_code')
        return {'status': 'ok', **observed, 'country_code': country, 'detail': ''}
    return {'status': 'unknown', 'ip': None, 'detail': 'Claude 出口未测得'}


def verify_exit(name):
    """网页启动、命令行启动和自动恢复共用的出口检查，通过后记录这次出口。

    出口国家不符合要求时，require_same_country 会断开本实例代理并抛出 CountryPolicyError。
    """
    import store
    observation = probe_proxy(name)
    require_same_country(name, observation)
    store.save_exit(name, observation['status'], observation['ip'], observation['detail'],
                    observation.get('country_code'))
    if observation['status'] == 'failed':
        raise RuntimeError('代理连接失败。实例配置已保留，可以修改代理后再启动。')
    return observation


def wait_until_ready(name):
    """等待 Web UI 可以打开、Chromium 已开始渲染；发现关闭 Sandbox 立即拒绝。"""
    deadline = time.monotonic() + 60
    url = f"http://127.0.0.1:{settings(name)['port']}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while True:
        try:
            if opener.open(url, timeout=2).status == 200:
                break
        except OSError:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError('Web UI 在 60 秒内未就绪。')
        time.sleep(1)
    deadline = time.monotonic() + 30
    while True:
        try:
            processes = docker('exec', container(name, 'browser'), 'ps', '-eo', 'args',
                               check=False, timeout=5).stdout.splitlines()
        except subprocess.TimeoutExpired:
            processes = []
        chromium = [line for line in processes if line.startswith('/usr/lib/chromium/chromium ')]
        if any('--no-sandbox' in line for line in chromium):
            raise SandboxError('检测到 Chromium 关闭 Sandbox，拒绝继续运行。')
        if chromium and any('--type=renderer' in line for line in chromium):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('Chromium 没有在 30 秒内就绪。')
        time.sleep(0.5)


def check_proxy(name):
    proxy = read_json(CONFIGS / name / 'proxy.json')
    label = selected_label(name)
    if not proxy['host'] and not label:
        print(f'{name}：未选择节点，也未填写 SOCKS5；公网代理验收未执行。')
        return None
    value = observe_exit(name)
    if not value:
        print(f'{name}：代理出口请求失败，无有效出口 IP。')
        return None
    expected = proxy.get('expected_ip', '')
    source = label or '手动 SOCKS5'
    print(f'{name}：节点“{source}”出口 {value}；' + ('与 expected_ip 一致。' if expected and value == expected else '未设置 expected_ip，无法核对预期。' if not expected else '与 expected_ip 不一致！'))
    return value if not expected or value == expected else None


def verify(name):
    if not active(name):
        raise RuntimeError(f'{name} 尚未运行。')
    findings = []
    def record(label, passed):
        findings.append({'test': label, 'passed': passed})
        print(f'[{"PASS" if passed else "FAIL"}] {name} {label}')
    addr = addresses(name)
    record('本实例 GOST 可连接', probe(name, 'connect', addr['BRIDGE_IP'], '8080', check=False).returncode == 0)
    for host, port in [('1.1.1.1', '443'), ('8.8.8.8', '53'), ('127.0.0.11', '53'), ('192.168.65.2', '80'), ('2606:4700:4700::1111', '443')]:
        record(f'直连 {host}:{port} 被阻断', probe(name, 'connect', host, port, check=False).returncode != 0)
    for other in instances():
        if other != name and active(other):
            record(f'跨实例 {other} GOST 被阻断', probe(name, 'connect', addresses(other)['BRIDGE_IP'], '8080', check=False).returncode != 0)
            result = docker('exec', container(name, 'proxy'), 'python3', '/opt/cbc/probe.py', 'connect', addresses(other)['BRIDGE_IP'], '8080', check=False)
            record(f'GOST 也不能连接 {other} GOST', result.returncode != 0)
    result = docker('exec', container(name, 'proxy'), 'python3', '/opt/cbc/probe.py', 'connect', '1.1.1.1', '443', check=False)
    record('GOST 不能连接上游白名单之外的公网', result.returncode != 0)
    info = json.loads(docker('inspect', container(name, 'browser')).stdout)[0]
    record('浏览器无 NET_ADMIN / SYS_ADMIN / privileged', not info['HostConfig']['Privileged'] and not set(info['HostConfig']['CapAdd'] or []) & {'NET_ADMIN', 'SYS_ADMIN'})
    rules = docker('exec', container(name, 'guard'), 'iptables-save').stdout
    record('OUTPUT 默认拒绝且只有 TCP 例外', ':OUTPUT DROP' in rules and '-p udp' not in rules)
    record('Docker DNS 在 raw/OUTPUT 阻断', '-A OUTPUT -d 127.0.0.11/32 -j DROP' in rules)
    ipv6 = docker('exec', container(name, 'guard'), 'ip6tables-save').stdout
    record('IPv6 OUTPUT 默认拒绝', ':OUTPUT DROP' in ipv6)
    binds = json.loads(docker('inspect', container(name, 'guard')).stdout)[0]['HostConfig']['PortBindings']
    record('Web UI 仅绑定 127.0.0.1', all(b['HostIp'] == '127.0.0.1' for group in binds.values() for b in group))
    stopped = False
    try:
        docker('stop', '--time', '5', container(name, 'proxy'))
        stopped = True
        record('停止 GOST 后仍无法直连公网', probe(name, 'connect', '1.1.1.1', '443', check=False).returncode != 0)
        result = docker('exec', container(name, 'guard'), 'curl', '-fsS', '--max-time', '5', '--noproxy', '', '--proxy', f"http://{addr['BRIDGE_IP']}:8080", 'https://example.com', check=False)
        record('停止 GOST 后代理请求失败', result.returncode != 0)
        if 'CDP_ENABLED=1' in (CONFIGS / name / 'runtime/compose.env').read_text():
            expression = "(async()=>{try{await fetch('https://example.com/cbc-check?'+Date.now(),{cache:'no-store',signal:AbortSignal.timeout(4000)});return true}catch(e){return false}})()"
            actual = probe(name, 'eval', expression, check=False)
            record('停止 GOST 后实际 Chromium 请求失败', actual.returncode == 0 and actual.stdout.strip() == 'false')
    finally:
        if stopped:
            docker('start', container(name, 'proxy'))
            # docker start returns before GOST initializes its firewall/listener.
            # Do not report recovery while a subsequent proxy check can still race.
            end = time.monotonic() + 15
            while True:
                health = docker('inspect', container(name, 'proxy'), '--format', '{{.State.Health.Status}}', check=False)
                if health.returncode == 0 and health.stdout.strip() == 'healthy':
                    break
                if time.monotonic() >= end:
                    raise RuntimeError(f'{name} GOST 未能在故障测试后恢复健康。')
                time.sleep(0.25)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    save_json(ROOT / 'evidence' / f'isolation-{name}-{stamp}.json', findings)
    return all(f['passed'] for f in findings)


def benchmark(label):
    if not re.fullmatch(r'[a-zA-Z0-9-]+', label):
        raise ValueError('测量标签只能包含字母、数字、连字符。')
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = {'label': label, 'time_utc': stamp}
    ids = docker('ps', '-q', '--filter', f'label={LABEL}').stdout.split()
    output['containers'] = [json.loads(line) for line in docker('stats', '--no-stream', '--format', '{{json .}}', *ids).stdout.splitlines()] if ids else []
    output['docker_system_df'] = docker('system', 'df').stdout
    output['docker_info'] = json.loads(docker('info', '--format', '{"server":"{{.ServerVersion}}","vm_memory_limit":{{.MemTotal}},"vcpus":{{.NCPU}}}').stdout)
    output['macos_memory_pressure'] = run(['memory_pressure', '-Q']).stdout
    process = run(['ps', '-axo', 'pid,rss,pcpu,comm'], check=False)
    output['virtualization_processes'] = [line for line in process.stdout.splitlines() if any(x in line.lower() for x in ['com.docker', '/applications/docker.app/', 'virtualization.virtualmachine', 'orbstack'])]
    output['data_du_kib'] = run(['du', '-sk', ROOT / 'data'], timeout=120).stdout
    output['project_du_kib'] = run(['du', '-sk', ROOT], timeout=120).stdout
    if (ROOT / 'images.lock.json').exists():
        output['images'] = read_json(ROOT / 'images.lock.json')
    save_json(ROOT / 'evidence' / f'benchmark-{label}-{stamp}.json', output)
    print(json.dumps(output, indent=2, ensure_ascii=False))


def network_index_conflicts(index):
    """实例使用的两段 /29 是否和现有 Docker 网络重叠。"""
    ids = docker('network', 'ls', '-q', timeout=15).stdout.split()
    if not ids:
        return False
    own = [ipaddress.ip_network(f'10.{prefix}.{index}.0/29') for prefix in (231, 232)]
    for network in json.loads(docker('network', 'inspect', *ids, timeout=15).stdout):
        for config in network.get('IPAM', {}).get('Config') or []:
            subnet = config.get('Subnet')
            if not subnet:
                continue
            net = ipaddress.ip_network(subnet)
            if net.version == 4 and any(net.overlaps(item) for item in own):
                return True
    return False


def _port_free(port):
    with socket.socket() as listener:
        try:
            listener.bind(('127.0.0.1', port))
        except OSError:
            return False
    return True


def _restore_snapshot(path, content):
    import store
    store._restore_snapshot(path, content)


def backup_instance(name, destination):
    """打包一个实例的浏览器数据、代理和它实际绑定的订阅。"""
    import store
    name = validate_name(name)
    if not (CONFIGS / name / 'instance.json').exists():
        raise ValueError(f'实例不存在：{name}')
    if active(name):
        raise ValueError(f'{name} 仍在运行。请先停止，再备份，避免复制正在写入的浏览器数据。')
    with store.exclusive():
        selection = store.selection_map().get(name)
        if selection is not None and not isinstance(selection, dict):
            raise ValueError('实例的节点绑定无法读取，没有创建不完整的备份。')
        subscription = None
        nodes = None
        if isinstance(selection, dict) and selection.get('subscription_id'):
            subscription_id = selection['subscription_id']
            subscription = next((item for item in store.load_library()['subscriptions'] if item['id'] == subscription_id), None)
            if not subscription:
                raise ValueError('实例绑定的订阅不在订阅库中，没有创建不完整的备份。')
            cache = store._cache_path(subscription_id)
            if not cache.exists():
                raise ValueError('实例绑定的节点缓存不存在，没有创建不完整的备份。')
            try:
                nodes = json.loads(cache.read_text())
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise ValueError('节点缓存无法读取，没有创建不完整的备份。') from error
            node_rows = nodes.get('nodes') if isinstance(nodes, dict) else None
            if not isinstance(node_rows, list) or not any(
                isinstance(item, dict) and item.get('id') == selection.get('node_id') for item in node_rows
            ):
                raise ValueError('节点缓存里没有实例绑定的节点，没有创建不完整的备份。')
        manifest = {
            'version': BACKUP_VERSION,
            'slug': name,
            'instance': read_json(CONFIGS / name / 'instance.json'),
            'proxy': read_json(CONFIGS / name / 'proxy.json'),
            'selection': selection if isinstance(selection, dict) else None,
            'subscription': subscription,
            'nodes': nodes,
        }
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + '.tmp')
    data = ROOT / 'data' / name
    try:
        with tarfile.open(temp, 'w:gz') as archive:
            payload = json.dumps(manifest, ensure_ascii=False).encode()
            info = tarfile.TarInfo('manifest.json')
            info.size = len(payload)
            info.mtime = int(time.time())
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(payload))
            if data.exists():
                for path in sorted(data.rglob('*')):
                    if path.is_symlink():
                        raise ValueError('数据目录包含符号链接，没有备份。')
                    archive.add(path, arcname=f'data/{name}/{path.relative_to(data).as_posix()}', recursive=False)
        temp.chmod(0o600)
        os.replace(temp, destination)
        destination.chmod(0o600)
    except Exception:
        if temp.exists():
            temp.unlink()
        raise
    print(f'已备份 {name}：{destination}。归档含浏览器数据和该实例的代理绑定，请按私密文件保存。')


def _install_restored_instance(slug, instance_body, proxy, selection, subscription, nodes, extracted):
    import store
    config_dir = CONFIGS / slug
    data_dir = ROOT / 'data' / slug
    subscription_id = selection.get('subscription_id') if isinstance(selection, dict) else None
    cache_path = store._cache_path(subscription_id) if subscription_id else None
    selection_path = CONFIGS / 'selection.json'
    with store.exclusive():
        if (config_dir / 'instance.json').exists() or data_dir.exists():
            raise ValueError(f'{slug} 已存在，没有覆盖。请先处理现有实例后再恢复。')
        snapshots = {
            'selection': selection_path.read_bytes() if selection_path.exists() else None,
            'library': store.LIBRARY.read_bytes() if store.LIBRARY.exists() else None,
            'nodes': cache_path.read_bytes() if cache_path and cache_path.exists() else None,
        }
        created = []
        try:
            data_dir.mkdir(parents=True, exist_ok=False)
            data_dir.chmod(0o700)
            created.append(data_dir)
            if extracted.exists():
                for path in sorted(extracted.rglob('*')):
                    target = data_dir / path.relative_to(extracted)
                    if path.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        target.chmod(0o700)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(path, target)
                        target.chmod(0o600)
            config_dir.mkdir(parents=True, exist_ok=False)
            config_dir.chmod(0o700)
            created.append(config_dir)
            store._save(config_dir / 'instance.json', instance_body)
            store._write_proxy(slug, proxy)
            if subscription_id and snapshots['nodes'] is None:
                store._save(cache_path, nodes)
            if subscription_id:
                library = store.load_library() if store.library_exists() else store._empty_library()
                if not any(item['id'] == subscription_id for item in library['subscriptions']):
                    library['subscriptions'].append(subscription)
                    store._save(store.LIBRARY, library)
            if isinstance(selection, dict):
                current = store.selection_map()
                current[slug] = selection
                store._write_selection(current)
            if subscription_id:
                store.materialize_node(subscription_id, selection['node_id'])
        except Exception:
            _restore_snapshot(selection_path, snapshots['selection'])
            _restore_snapshot(store.LIBRARY, snapshots['library'])
            if cache_path is not None:
                _restore_snapshot(cache_path, snapshots['nodes'])
            for path in reversed(created):
                shutil.rmtree(path, ignore_errors=True)
            raise


def erase_instance(slug):
    """彻底删除实例文件。登记留到数据和共享引用都清理完，失败后仍可重试。"""
    import store
    slug = validate_name(slug)
    config_dir = CONFIGS / slug
    data_dir = ROOT / 'data' / slug
    raw = store.update_instance(slug, lambda body: body.update(deleting=True, desired_running=False))
    if data_dir.exists():
        shutil.rmtree(data_dir)
    for path in config_dir.iterdir():
        if path.name == 'instance.json':
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    store.forget_instance(slug)
    metadata = config_dir / 'instance.json'
    metadata.unlink()
    try:
        config_dir.rmdir()
    except OSError:
        store._save(metadata, raw)
        raise


def restore_instance(archive_path):
    """把备份恢复到当前项目。不启动实例，也不覆盖已有实例。"""
    import store
    archive_path = Path(archive_path)
    if not archive_path.is_file():
        raise ValueError('找不到备份归档。')
    with tempfile.TemporaryDirectory(prefix='cbc-restore-') as directory:
        root = Path(directory)
        with tarfile.open(archive_path, 'r:gz') as archive:
            members = []
            for member in archive.getmembers():
                pure = Path(member.name)
                if member.issym() or member.islnk() or member.isdev() or pure.is_absolute() or '..' in pure.parts:
                    raise ValueError('归档路径不安全，没有恢复。')
                if member.name != 'manifest.json' and not member.name.startswith('data/'):
                    raise ValueError('归档包含意外内容，没有恢复。')
                if member.name == 'manifest.json' and member.size > MAX_BACKUP_MANIFEST_BYTES:
                    raise ValueError('备份清单过大，没有恢复。')
                members.append(member)
            archive.extractall(root, members=members, filter='data')
        try:
            manifest = json.loads((root / 'manifest.json').read_text())
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError('备份清单无法读取，没有恢复。') from error
        if not isinstance(manifest, dict) or manifest.get('version') != BACKUP_VERSION:
            raise ValueError('无法识别这个备份，没有恢复。')
        slug = manifest.get('slug')
        if not isinstance(slug, str):
            raise ValueError('无法识别这个备份，没有恢复。')
        slug = validate_name(slug)
        instance_body = manifest.get('instance')
        proxy = manifest.get('proxy')
        selection = manifest.get('selection')
        subscription = manifest.get('subscription')
        nodes = manifest.get('nodes')
        if not isinstance(instance_body, dict) or not isinstance(proxy, dict):
            raise ValueError('备份里的实例或代理配置无效，没有恢复。')
        if selection is not None and not isinstance(selection, dict):
            raise ValueError('备份里的节点绑定无效，没有恢复。')
        if instance_body.get('id') not in (None, slug) or instance_body.get('name') not in (None, slug):
            raise ValueError('备份里的实例名和清单不一致，没有恢复。')
        preview = root / 'instance.json'
        save_json(preview, instance_body)
        parsed = store.read_instance(preview)
        port, index = parsed['port'], parsed['network_index']
        if (CONFIGS / slug / 'instance.json').exists() or (ROOT / 'data' / slug).exists():
            raise ValueError(f'{slug} 已存在，没有覆盖。请先处理现有实例后再恢复。')
        rows = store.require_complete_inventory()
        if any(row['port'] == port or row['network_index'] == index for row in rows):
            raise ValueError('端口或网络编号已被其他实例使用，没有恢复。')
        if not _port_free(port):
            raise ValueError('这个端口当前无法绑定，没有恢复。')
        if network_index_conflicts(index):
            raise ValueError('实例的子网和现有 Docker 网络冲突，没有恢复。')
        subscription_id = selection.get('subscription_id') if isinstance(selection, dict) else ''
        node_id = selection.get('node_id') if isinstance(selection, dict) else ''
        if subscription_id:
            if (
                not isinstance(subscription_id, str) or not re.fullmatch(r'sub[0-9a-f]{12}', subscription_id)
                or not isinstance(node_id, str) or not re.fullmatch(r'n[0-9a-f]{20}', node_id)
            ):
                raise ValueError('备份里的订阅或节点编号无效，没有恢复。')
            node_list = nodes.get('nodes') if isinstance(nodes, dict) else None
            if not isinstance(subscription, dict) or subscription.get('id') != subscription_id:
                raise ValueError('备份缺少这个实例的订阅信息，没有恢复。')
            store.validate_subscription(subscription.get('name'), subscription.get('url'))
            if not isinstance(node_list, list) or not any(
                isinstance(item, dict) and item.get('id') == selection.get('node_id') for item in node_list
            ):
                raise ValueError('备份缺少实例绑定的节点，没有恢复。')
            if store.library_exists():
                current = next((item for item in store.load_library()['subscriptions'] if item['id'] == subscription_id), None)
                if current and current.get('url') != subscription.get('url'):
                    raise ValueError('本地已有同一个订阅编号，但地址不同，没有覆盖。')
            cache = store._cache_path(subscription_id)
            if cache.exists() and not any(item.get('id') == selection.get('node_id') for item in store.load_cached_nodes(subscription_id)):
                raise ValueError('本地节点缓存和备份不一致，没有覆盖。')
        extracted = root / 'data' / slug
        if extracted.exists() and any(path.is_symlink() for path in extracted.rglob('*')):
            raise ValueError('归档数据包含符号链接，没有恢复。')
        # 备份可能保留“应当运行”，例如异常退出后尚未由管理台停止。恢复后必须等用户确认代理再手动启动。
        instance_body = dict(instance_body)
        instance_body['desired_running'] = False
        instance_body['run_generation'] = secrets.token_hex(8)
        instance_body['recovery'] = {}
        _install_restored_instance(slug, instance_body, proxy, selection, subscription, nodes, extracted)
    print(f'已恢复 {slug}。实例没有启动；确认代理后运行 ./scripts/start.sh {slug}。')


def main():
    parser = argparse.ArgumentParser(description='Claude Browser Containers 本地管理')
    parser.add_argument('action', choices=['init', 'start', 'stop', 'restart', 'status', 'create-instance', 'remove-instance', 'check-proxy', 'verify-isolation', 'benchmark', 'backup-instance', 'restore-instance'])
    parser.add_argument('names', nargs='*')
    parser.add_argument('--delete-data', action='store_true', help='明确删除数据；默认保留')
    parser.add_argument('--cdp', action='store_true', help='临时启用仅容器本地可达的验收 CDP')
    args = parser.parse_args()
    if args.action == 'benchmark':
        benchmark(args.names[0] if args.names else 'snapshot')
        return
    if args.action == 'init':
        init()
        return
    if args.action == 'create-instance':
        if len(args.names) != 2:
            parser.error('用法：create-instance.sh claude-c 5803')
        create(args.names[0], int(args.names[1]))
        if (ROOT / 'images.lock.json').exists():
            prepare(args.names[0])
        return
    if args.action == 'backup-instance':
        if len(args.names) != 2:
            parser.error('用法：backup-instance.sh 实例名 归档路径')
        backup_instance(args.names[0], args.names[1])
        return
    if args.action == 'restore-instance':
        if len(args.names) != 1:
            parser.error('用法：restore-instance.sh 归档路径')
        restore_instance(args.names[0])
        return
    if args.action == 'remove-instance' and not args.names:
        raise ValueError('删除必须显式指定实例名。')
    success = True
    exits = {}
    import store
    for name in instances(args.names):
        with store.instance_operation(name):
            if args.action == 'start':
                start(name, cdp=args.cdp)
            elif args.action == 'stop':
                stop(name)
            elif args.action == 'restart':
                if settings(name).get('deleting'):
                    raise ValueError('这个实例已开始彻底删除，只能继续清理，不能启动或重启。')
                if settings(name).get('trashed_at'):
                    raise ValueError('实例在回收站中，请先恢复。')
                docker('info', '--format', '{{.ServerVersion}}', timeout=8)
                proxy_plan(name, read_json(CONFIGS / name / 'proxy.json'))
                planned_browser(name, cdp=args.cdp)
                stop(name)
                start(name, cdp=args.cdp)
            elif args.action == 'status':
                proxy = read_json(CONFIGS / name / 'proxy.json')
                label = selected_label(name)
                state = f'全局节点“{label}”' if label and not proxy['host'] else ('已填写 SOCKS5' if proxy['host'] else '离线保护（未选择节点）')
                print(f'{name}: {"运行中" if active(name) else "已停止"} http://127.0.0.1:{settings(name)["port"]} {state}')
            elif args.action == 'remove-instance':
                print('将删除持久化数据。' if args.delete_data else '将保留 Profile、Cookie、缓存和下载。')
                if args.delete_data and not (sys.stdin.isatty() and input(f'输入 {name} 确认永久删除数据：') == name):
                    raise ValueError('未确认，不删除数据。')
                stop(name)
                if args.delete_data:
                    erase_instance(name)
                else:
                    store.forget_instance(name)
                    shutil.rmtree(CONFIGS / name)
                print(f'{name} 已删除。')
            elif args.action == 'check-proxy':
                observed = check_proxy(name)
                if observed:
                    exits[name] = observed
                success = bool(observed) and success
            elif args.action == 'verify-isolation':
                success = verify(name) and success
    if len(exits) > 1:
        independent = len(set(exits.values())) == len(exits)
        print('多个实例出口 IP 不同。' if independent else '检查失败：多个实例出口 IP 相同。')
        success = independent and success
    if not success:
        sys.exit(1)


if __name__ == '__main__':
    os.umask(0o077)
    # 选择页长期占用进程。空闲时不持有管理锁，避免挡住 start.sh。
    if len(sys.argv) > 1 and sys.argv[1] == 'panel':
        from panel import serve
        try:
            serve()
        except (ValueError, RuntimeError, OSError) as error:
            print(f'错误：{error}', file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    try:
        with open(ROOT / '.manager.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            main()
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        print(f'错误：{error}', file=sys.stderr)
        sys.exit(1)
