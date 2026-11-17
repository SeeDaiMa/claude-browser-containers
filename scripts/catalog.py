"""把 Clash 订阅解析成可选节点。密码只留在权限为 600 的本地文件里。"""
import ipaddress
import json
from pathlib import Path
import socket
import subprocess
import sys
import urllib.parse
import urllib.request


SECRET_FIELDS = ('password', 'uuid', 'private-key', 'public-key', 'short-id')
MAX_SUBSCRIPTION_BYTES = 2 * 1024 * 1024
MAX_SUBSCRIPTION_NODES = 5000
SUBSCRIPTION_TIMEOUT = 30


class CatalogError(ValueError):
    pass


TCP_TYPES = {'http', 'socks5', 'ss', 'vmess', 'vless', 'trojan', 'anytls'}
UDP_ONLY_TYPES = {'hysteria', 'hysteria2', 'tuic', 'wireguard'}
# 供应商节点名可能是 DIRECT、GLOBAL 等 Mihomo 保留名。运行配置只用这个内部名。
RUNTIME_PROXY_NAME = 'cbc-node'


def support_of(node):
    """只放行仍走 TCP 的协议。udp: true 表示节点能转发 UDP，不是上游本身只用 UDP。"""
    kind = str(node.get('type', '')).lower()
    network = str(node.get('network', '') or '').lower()
    if kind in UDP_ONLY_TYPES or network == 'udp':
        return False, '这个节点只走 UDP。防火墙只放行到代理服务器的 TCP，所以不能使用。'
    if kind not in TCP_TYPES:
        return False, f'第一版不支持 {kind or "未知"} 协议。'
    return True, ''


def proxies_from_clash(text):
    """用 YAML safe_load 解析。JSON 也是合法 YAML。不执行订阅里的代码。"""
    try:
        import yaml
    except ImportError as error:
        raise CatalogError('缺少 PyYAML。请先运行 ./scripts/setup.sh。') from error
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise CatalogError('订阅内容无法解析。') from error
    proxies = data.get('proxies') if isinstance(data, dict) else None
    if not isinstance(proxies, list):
        raise CatalogError('订阅里没有 proxies 列表。')
    if len(proxies) > MAX_SUBSCRIPTION_NODES:
        raise CatalogError(f'订阅节点超过 {MAX_SUBSCRIPTION_NODES} 个，请精简后重试。')
    nodes = []
    for item in proxies:
        if not isinstance(item, dict) or not {'name', 'type', 'server', 'port'} <= set(item):
            raise CatalogError('订阅节点缺少名称、类型、服务器或端口。')
        node = dict(item)
        try:
            node['port'] = int(node['port'])
        except (TypeError, ValueError):
            raise CatalogError('订阅节点端口无效。') from None
        if not 1 <= node['port'] <= 65535:
            raise CatalogError('订阅节点端口无效。')
        node['name'] = str(node['name'])
        node['type'] = str(node['type'])
        nodes.append(node)
    if not nodes:
        raise CatalogError('订阅里没有可用节点。')
    return nodes


def fetch_proxies(url):
    # 子进程提供总耗时边界，涵盖 DNS、重定向、慢响应头和 YAML 解析。
    # 地址和节点凭据仅经 stdin/stdout 传递，不进入进程参数或公开错误。
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--fetch'],
                                input=json.dumps({'url': url}), text=True, capture_output=True,
                                timeout=SUBSCRIPTION_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise CatalogError(f'订阅下载超时（上限 {SUBSCRIPTION_TIMEOUT:g} 秒）。') from None
    except OSError:
        raise CatalogError('订阅下载进程无法启动。') from None
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise CatalogError('订阅下载结果无效。') from None
    if result.returncode or not isinstance(payload, dict):
        raise CatalogError('订阅下载或解析失败。')
    if payload.get('error'):
        raise CatalogError(payload['error'])
    if not isinstance(payload.get('nodes'), list):
        raise CatalogError('订阅下载结果无效。')
    return payload['nodes']


class _HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() != 'https':
            raise CatalogError('订阅重定向必须保持 HTTPS。')
        return super().redirect_request(request, fp, code, message, headers, newurl)


def _download_proxies(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'clash-meta'})
    try:
        opener = urllib.request.build_opener(_HTTPSRedirectHandler())
        with opener.open(request, timeout=SUBSCRIPTION_TIMEOUT) as response:
            if urllib.parse.urlsplit(response.geturl()).scheme.lower() != 'https':
                raise CatalogError('订阅下载必须保持 HTTPS。')
            payload = response.read(MAX_SUBSCRIPTION_BYTES + 1)
    except OSError as error:
        raise CatalogError(f'订阅下载失败：{error.__class__.__name__}') from error
    if len(payload) > MAX_SUBSCRIPTION_BYTES:
        raise CatalogError('订阅内容超过 2 MiB，请精简后重试。')
    try:
        text = payload.decode('utf-8')
    except UnicodeError:
        raise CatalogError('订阅内容必须是 UTF-8 文本。') from None
    return proxies_from_clash(text)


def _fetch_worker():
    try:
        nodes = _download_proxies(json.load(sys.stdin)['url'])
        chunks, size = [], 0
        for chunk in json.JSONEncoder(ensure_ascii=False).iterencode({'nodes': nodes}):
            size += len(chunk.encode())
            if size > MAX_SUBSCRIPTION_BYTES * 4:
                raise CatalogError('订阅解析结果过大，请精简后重试。')
            chunks.append(chunk)
        output = ''.join(chunks)
    except CatalogError as error:
        output = json.dumps({'error': str(error)}, ensure_ascii=False)
    except Exception:
        output = json.dumps({'error': '订阅下载或解析失败。'}, ensure_ascii=False)
    sys.stdout.write(output)


def pin_endpoint(node):
    """容器内不能解析域名。在 Mac 上选定一个能握手的公网 IPv4，TLS 名称仍用原来的主机名。

    本机 DNS 可能被 Clash 劫持成 198.18.0.0/15 假地址，容器访问不到。那种结果要丢掉，改用 DoH。
    """
    original = str(node['server'])
    port = int(node['port'])
    addresses = _public_ipv4(original, port)
    reachable = [address for address in addresses if _tcp_open(address, port)]
    if not reachable:
        raise CatalogError(f'节点“{node["name"]}”的服务器端口目前连不上。')
    pinned = dict(node)
    pinned['server'] = reachable[0]
    pinned['udp'] = False
    if not _is_ipv4(original):
        kind = str(pinned.get('type', '')).lower()
        if (kind in ('anytls', 'trojan') or kind in ('http', 'socks5') and pinned.get('tls')) and not pinned.get('sni'):
            pinned['sni'] = original
        if kind in ('vless', 'vmess') and not pinned.get('servername'):
            pinned['servername'] = original
    return pinned, reachable[0], port


def mihomo_config(node):
    # 容器里的系统 DNS 会被防火墙丢掉。网站域名只走所选节点上的 DoH，
    # 不占用特权 53 端口，也不给浏览器开 UDP。
    # 展示名留在订阅缓存里；这里换成固定内部名，避免和 DIRECT、GLOBAL 冲突。
    proxy = dict(node)
    proxy['name'] = RUNTIME_PROXY_NAME
    return {
        'mode': 'global',
        'ipv6': False,
        'allow-lan': False,
        'log-level': 'error',
        'find-process-mode': 'off',
        'geo-auto-update': False,
        'geodata-mode': False,
        'listeners': [{
            'name': 'browser',
            'type': 'socks',
            'listen': '127.0.0.1',
            'port': 1080,
            'udp': False,
        }],
        'dns': {
            'enable': True,
            'listen': '127.0.0.1:1053',
            'ipv6': False,
            'enhanced-mode': 'redir-host',
            'use-hosts': False,
            'use-system-hosts': False,
            'prefer-h3': False,
            'nameserver': ['https://1.1.1.1/dns-query#GLOBAL'],
            'proxy-server-nameserver': ['1.1.1.1'],
        },
        'proxies': [proxy],
        'proxy-groups': [{'name': 'GLOBAL', 'type': 'select', 'proxies': [RUNTIME_PROXY_NAME]}],
        'rules': ['MATCH,GLOBAL'],
    }


def secret_values(value, *, min_length=4):
    found = []

    def walk(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if key in SECRET_FIELDS and isinstance(child, str) and len(child) >= min_length:
                    found.append(child)
                else:
                    walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return found


def _public_ipv4(host, port):
    if _is_ipv4(host):
        candidates = [host]
    else:
        try:
            resolved = {item[4][0] for item in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)}
        except socket.gaierror:
            resolved = set()
        candidates = sorted(address for address in resolved if _usable_ipv4(address))
        if not candidates:
            candidates = _doh_ipv4(host)
    usable = [address for address in candidates if _usable_ipv4(address)]
    if not usable:
        raise CatalogError(f'节点服务器“{host}”没有可从容器连接的公网 IPv4。')
    return usable


def _doh_ipv4(host):
    request = urllib.request.Request(
        'https://1.1.1.1/dns-query?name=' + urllib.parse.quote(host) + '&type=A',
        headers={'Accept': 'application/dns-json'},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=8) as response:
            payload = json.loads(response.read().decode())
    except (OSError, ValueError) as error:
        raise CatalogError(f'节点服务器“{host}”无法解析出公网 IPv4。') from error
    found = []
    for answer in payload.get('Answer') or []:
        data = str(answer.get('data', ''))
        if answer.get('type') == 1 and _usable_ipv4(data):
            found.append(data)
    return sorted(set(found))


def _usable_ipv4(value):
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return False
    # 198.18.0.0/15 是 Clash 假 IP，is_global 在部分 Python 版本里仍可能为真。
    if address in ipaddress.ip_network('198.18.0.0/15'):
        return False
    return bool(address.is_global)


def _tcp_open(address, port):
    try:
        with socket.create_connection((address, port), 4):
            return True
    except OSError:
        return False


def _is_ipv4(value):
    try:
        ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return False
    return True


def dumps(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + '\n'


if __name__ == '__main__' and sys.argv[1:] == ['--fetch']:
    _fetch_worker()
