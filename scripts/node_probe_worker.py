"""通过 stdin 接收私有节点配置；仅在临时网络容器内运行，stdout 只返回公开测速结果。"""

from concurrent.futures import ThreadPoolExecutor
import ipaddress
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

TEST_URL = 'https://www.gstatic.com/generate_204'
TIMEOUT_MS = 8000
IPPURE_URL = 'https://my.ippure.com/v1/info'
QUALITY_TIMEOUT = 12
MAX_RESPONSE_BYTES = 64 * 1024


def quality_failed(detail):
    return {
        'status': 'failed',
        'ip': None,
        'asn': None,
        'is_residential': None,
        'is_broadcast': None,
        'fraud_score': None,
        'detail': detail,
    }


def normalize_quality(payload):
    """只公开服务商明确返回的字段；缺失布尔值不能当成 False，0 分也是有效分数。"""
    if not isinstance(payload, dict):
        raise ValueError('IP 信息格式无效')
    address = ipaddress.ip_address(payload.get('ip', ''))
    if not address.is_global:
        raise ValueError('没有公网出口 IP')
    score = payload.get('fraudScore')
    score = score if type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 100 else None
    residential = payload.get('isResidential')
    broadcast = payload.get('isBroadcast')
    residential = residential if type(residential) is bool else None
    broadcast = broadcast if type(broadcast) is bool else None
    asn = payload.get('asn')
    asn = asn if type(asn) is int and 0 < asn <= 4294967295 else None
    complete = all(value is not None for value in (score, residential, broadcast))
    return {
        'status': 'ok' if complete else 'partial',
        'ip': str(address),
        'asn': asn,
        'is_residential': residential,
        'is_broadcast': broadcast,
        'fraud_score': score,
        'detail': '经此节点出口查询 IPPure。' if complete else 'IPPure 未提供部分字段；缺失项保持未知。',
    }


def measure_quality(port):
    proxy_url = f'http://127.0.0.1:{port}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url}))
    request = urllib.request.Request(
        IPPURE_URL, headers={'Accept': 'application/json', 'User-Agent': 'CBC-Manager/1.0'}
    )
    try:
        with opener.open(request, timeout=QUALITY_TIMEOUT) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError('响应过大')
        return normalize_quality(json.loads(raw))
    except urllib.error.HTTPError as error:
        detail = 'IPPure 请求受限，请稍后重试。' if error.code == 429 else f'IPPure 暂不可用（HTTP {error.code}）。'
        return quality_failed(detail)
    except (OSError, ValueError, TypeError):
        return quality_failed('经节点查询 IPPure 失败或超时，请稍后重试。')


def failed(detail):
    return {'status': 'failed', 'delay_ms': None, 'detail': detail}


def configuration(proxies, quality=False):
    # 各节点独立接受延迟请求；DNS 不经过列表中的另一个节点。
    config = {
        'mode': 'rule',
        'ipv6': False,
        'allow-lan': False,
        'log-level': 'silent',
        'find-process-mode': 'off',
        'geo-auto-update': False,
        'geodata-mode': False,
        'external-controller': '127.0.0.1:9090',
        'dns': {
            'enable': True,
            'ipv6': False,
            'enhanced-mode': 'redir-host',
            'use-hosts': False,
            'use-system-hosts': False,
            'nameserver': ['https://1.1.1.1/dns-query'],
            'proxy-server-nameserver': ['https://1.1.1.1/dns-query'],
        },
        'proxies': proxies,
        'rules': ['MATCH,REJECT'],
    }
    if quality:
        # 每个入口固定使用一个节点，失败时没有 DIRECT 或其他节点可回退。
        config['listeners'] = [
            {
                'name': f'quality-{index}',
                'type': 'http',
                'listen': '127.0.0.1',
                'port': 18080 + index,
                'proxy': proxy['name'],
            }
            for index, proxy in enumerate(proxies)
        ]
    return config


def main():
    os.umask(0o077)
    payload = json.load(sys.stdin)
    quality = isinstance(payload, dict) and payload.get('mode') == 'quality'
    nodes = payload['nodes'] if quality else payload
    failure = quality_failed if quality else failed
    results, valid = {}, []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix='node-test-') as directory:
        path = Path(directory) / 'mihomo.json'
        for node in nodes:
            # 用 ID 作为临时名称，避免同名节点和 DIRECT/GLOBAL 等保留名称冲突。
            proxy = {**node['config'], 'name': node['id'], 'udp': False}
            path.write_text(json.dumps(configuration([proxy])))
            try:
                checked = subprocess.run(
                    ['mihomo', '-t', '-d', directory, '-f', str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                )
                if checked.returncode:
                    results[node['id']] = failure('节点配置无法通过 Mihomo 校验。')
                    continue
            except subprocess.TimeoutExpired:
                results[node['id']] = failure('节点配置校验超时。')
                continue
            valid.append(proxy)
        if valid:
            path.write_text(json.dumps(configuration(valid, quality)))
            process = subprocess.Popen(
                ['mihomo', '-d', directory, '-f', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            try:
                deadline = time.monotonic() + 5
                while True:
                    try:
                        with opener.open('http://127.0.0.1:9090/version', timeout=0.5):
                            pass
                        if quality:
                            # 控制接口可能先于节点入口就绪，避免首个请求撞上启动窗口。
                            for index in range(len(valid)):
                                with socket.create_connection(('127.0.0.1', 18080 + index), timeout=0.2):
                                    pass
                        break
                    except OSError:
                        if process.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError('测速服务未能启动。') from None
                        time.sleep(0.05)

                def measure(proxy):
                    query = urllib.parse.urlencode({'url': TEST_URL, 'timeout': TIMEOUT_MS, 'expected': '204'})
                    url = (
                        'http://127.0.0.1:9090/proxies/'
                        + urllib.parse.quote(proxy['name'], safe='')
                        + '/delay?'
                        + query
                    )
                    try:
                        with opener.open(url, timeout=TIMEOUT_MS / 1000 + 1) as response:
                            delay = json.load(response).get('delay')
                        if type(delay) is not int or not 0 <= delay <= 65535:
                            raise ValueError('无效延迟')
                        value = {'status': 'ok', 'delay_ms': delay, 'detail': '经此节点访问测试网址的 HTTP 延迟。'}
                    except (OSError, ValueError):
                        value = failed('连接失败或超时（8 秒），可稍后重试。')
                    return proxy['name'], value

                if quality:
                    # 质量接口按节点串行请求，避免与四路延迟测速一样集中访问数据源。
                    for index, proxy in enumerate(valid):
                        if index:
                            time.sleep(2)
                        results[proxy['name']] = measure_quality(18080 + index)
                else:
                    with ThreadPoolExecutor(max_workers=4) as executor:
                        results.update(executor.map(measure, valid))
            except RuntimeError:
                results.update((proxy['name'], failure('检测服务未能启动，请重试。')) for proxy in valid)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    json.dump(results, sys.stdout)


if __name__ == '__main__':
    main()
