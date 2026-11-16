#!/usr/bin/env python3
"""Explicit temporary test proxies. Restores offline configs in a finally block."""
import datetime
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

import cbc

RESULTS = []
ORIGINAL = {}
FIXTURES = []
TEST_PROXIES = {}
STAMP = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
OUT = cbc.ROOT / 'evidence' / f'acceptance-{STAMP}'
OUT.mkdir(parents=True, exist_ok=True)


def record(name, passed, detail=None, required=True):
    item = {'test': name, 'passed': passed, 'detail': detail, 'required': required}
    RESULTS.append(item)
    cbc.save_json(OUT / 'results.json', RESULTS)
    status = 'PASS' if passed else 'FAIL' if required else 'UNVERIFIED'
    print(f'[{status}] {name}' + (f'：{detail}' if detail else ''), flush=True)
    return passed


def evaluate(name, expression):
    return json.loads(cbc.probe(name, 'eval', expression).stdout)


def page(name, url, expression=None):
    args = ['page', url]
    if expression:
        args.append(expression)
    return json.loads(cbc.probe(name, *args).stdout)


def curl(name, url, timeout=12):
    return cbc.docker('exec', cbc.container(name, 'guard'), 'curl', '-fsS', '--max-time', str(timeout), '--noproxy', '', '--proxy', f"http://{cbc.addresses(name)['BRIDGE_IP']}:8080", url, check=False)


def replace_proxy(name, value):
    cbc.save_json(cbc.CONFIGS / name / 'proxy.json', value, private=True)
    cbc.prepare(name, cdp=True)
    cbc.compose(name, 'up', '-d', '--no-deps', '--force-recreate', '--wait', 'proxy', capture=False)


def setup_fixture(name, marker):
    path = cbc.CONFIGS / name / 'proxy.json'
    original = cbc.read_json(path)
    if any(original.get(k) for k in ['host', 'username', 'password']):
        raise RuntimeError('临时验收拒绝覆盖已填写的代理；请先备份配置并恢复空模板。')
    ORIGINAL[name] = path.read_bytes()
    i = cbc.settings(name)['network_index']
    value = {'host': f'10.232.{i}.4', 'port': 1080, 'username': f'fixture-{marker}', 'password': secrets.token_urlsafe(30), 'expected_ip': ''}
    TEST_PROXIES[name] = value
    cbc.save_json(path, value, private=True)
    cbc.start(name, cdp=True)
    directory = cbc.CONFIGS / name / 'runtime/fixture'
    directory.mkdir(exist_ok=True)
    directory.chmod(0o700)
    cbc.save_json(directory / 'fixture.json', dict(value, marker=marker, download_name=f'cbc-acceptance-{STAMP}-{marker}.txt'), private=True)
    cbc.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-sha256', '-days', '1', '-nodes', '-keyout', directory / 'key.pem', '-out', directory / 'cert.pem', '-subj', '/CN=cbc.test', '-addext', 'subjectAltName=DNS:cbc.test'])
    fixture = f'cbc-fixture-{name}'
    cbc.docker('rm', '-f', fixture, check=False)
    cbc.docker('run', '-d', '--name', fixture, '--label', 'io.cbc.test=true', '--network', f'cbc-{name}-egress', '--ip', value['host'], '--cap-drop', 'ALL', '--cap-add', 'DAC_OVERRIDE', '--security-opt', f'seccomp={cbc.CONFIGS / "seccomp-default.json"}', '--security-opt', 'no-new-privileges=true', '--read-only', '--memory', '96m', '--cpus', '0.25', '--pids-limit', '64', '-v', f'{cbc.ROOT / "scripts/fixture.py"}:/fixture.py:ro', '-v', f'{directory}:/settings:ro', '--entrypoint', 'python3', cbc.read_json(cbc.ROOT / 'images.lock.json')['network']['reference'], '-u', '/fixture.py')
    FIXTURES.append(fixture)
    for _ in range(40):
        if '"kind": "ready"' in cbc.docker('logs', fixture).stdout:
            return
        time.sleep(0.25)
    raise RuntimeError('临时 SOCKS5 服务未就绪。')


WRITE = """(async()=>{const marker=MARKER;localStorage.setItem('cbc-marker',marker);document.cookie='cbc-marker='+marker+'; Max-Age=86400; Secure; SameSite=Lax';const db=await new Promise((ok,no)=>{let r=indexedDB.open('cbc-isolation',1);r.onupgradeneeded=()=>r.result.createObjectStore('markers');r.onsuccess=()=>ok(r.result);r.onerror=()=>no(r.error)});await new Promise((ok,no)=>{let t=db.transaction('markers','readwrite');t.objectStore('markers').put(marker,'marker');t.oncomplete=ok;t.onerror=no});db.close();const cache=await caches.open('cbc-isolation');await cache.put('/cbc-marker',new Response(marker));return {marker,cookie:document.cookie,localStorage:localStorage.getItem('cbc-marker'),httpCache:await fetch('/cache.txt').then(r=>r.text())}})()"""
READ = """(async()=>{const db=await new Promise((ok,no)=>{let r=indexedDB.open('cbc-isolation',1);r.onsuccess=()=>ok(r.result);r.onerror=()=>no(r.error)});const indexedDBValue=await new Promise((ok,no)=>{let r=db.transaction('markers').objectStore('markers').get('marker');r.onsuccess=()=>ok(r.result);r.onerror=()=>no(r.error)});db.close();const cache=await caches.open('cbc-isolation');const response=await cache.match('/cbc-marker');return {cookie:document.cookie,localStorage:localStorage.getItem('cbc-marker'),indexedDB:indexedDBValue,cache:response?await response.text():null,httpCache:await fetch('/cache.txt').then(r=>r.text())}})()"""
FETCH = """(async()=>{try{const r=await fetch('https://cbc.test/fault?'+Date.now(),{cache:'no-store',signal:AbortSignal.timeout(5000)});return {success:r.ok}}catch(e){return {success:false,error:e.name}}})()"""
RTC = """(async()=>{const p=new RTCPeerConnection({iceServers:[{urls:['stun:stun.l.google.com:19302','stun:1.1.1.1:3478']}]});const candidates=[];p.onicecandidate=e=>{if(e.candidate)candidates.push(e.candidate.candidate)};p.createDataChannel('cbc');await p.setLocalDescription(await p.createOffer());await new Promise(r=>setTimeout(r,5000));p.close();return candidates})()"""


def storage_matches(value, marker):
    return value.get('localStorage') == marker and value.get('indexedDB') == marker and value.get('cache') == marker and f'cbc-marker={marker}' in value.get('cookie', '') and value.get('httpCache') == marker


def single():
    name = 'claude-a'
    setup_fixture(name, 'A')
    public = curl(name, 'https://example.com')
    record('通过认证 SOCKS5 访问普通 HTTPS 网站', public.returncode == 0 and 'Example Domain' in public.stdout, public.stderr.strip() if public.returncode else None)
    local = page(name, 'https://cbc.test/')
    record('Chromium 经 GOST/SOCKS5 加载 HTTPS 页面', local.get('title') == 'CBC fixture A', local)
    value = TEST_PROXIES[name]
    record('浏览器不能绕过 GOST 直连已验证的上游 SOCKS5', cbc.probe(name, 'connect', value['host'], str(value['port']), check=False).returncode != 0)
    written = page(name, 'https://cbc.test/', WRITE.replace('MARKER', json.dumps('A')))
    record('Cookie / LocalStorage / IndexedDB / 缓存写入', written.get('localStorage') == 'A' and 'cbc-marker=A' in written.get('cookie', ''), written)
    cbc.close_browser(name)
    cbc.compose(name, 'up', '-d', '--no-deps', '--wait', 'browser', capture=False)
    time.sleep(4)
    restored = page(name, 'https://cbc.test/', READ)
    record('浏览器重启后持久化数据完整保留', storage_matches(restored, 'A'), restored)
    wrong = dict(TEST_PROXIES[name], password=secrets.token_urlsafe(30))
    replace_proxy(name, wrong)
    failure = evaluate(name, FETCH)
    record('错误密码时浏览器访问失败', not failure['success'], failure)
    logs = cbc.docker('logs', cbc.container(name, 'proxy')).stdout + cbc.docker('logs', cbc.container(name, 'proxy')).stderr
    record('GOST 错误日志未包含代理密码', TEST_PROXIES[name]['password'] not in logs and wrong['password'] not in logs)
    record('错误密码时仍不能直连公网', cbc.probe(name, 'connect', '1.1.1.1', '443', check=False).returncode != 0)
    replace_proxy(name, TEST_PROXIES[name])
    page(name, 'https://cbc.test/')
    cbc.docker('pause', f'cbc-fixture-{name}')
    try:
        failure = evaluate(name, FETCH)
        record('上游代理不响应时浏览器访问失败', not failure['success'], failure)
        record('上游代理不响应时不能直连公网', cbc.probe(name, 'connect', '1.1.1.1', '443', check=False).returncode != 0)
    finally:
        cbc.docker('unpause', f'cbc-fixture-{name}')
    record('单实例网络隔离及 GOST 停止场景', cbc.verify(name))
    # Capture only UDP/DNS: SOCKS5 authentication bytes are excluded.
    capture = subprocess.Popen(['docker', 'exec', cbc.container(name, 'guard'), 'timeout', '12', 'tcpdump', '-Z', 'root', '-ni', 'any', '-w', '/tmp/cbc-dns-webrtc.pcap', 'udp or tcp port 53'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(1)
    dns_code = "import socket\nfor host in ['127.0.0.11','8.8.8.8']:\n s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout(1)\n try:\n  s.sendto(b'\\x12\\x34\\x01\\x00\\x00\\x01\\x00\\x00\\x00\\x00\\x00\\x00\\x03www\\x07example\\x03com\\x00\\x00\\x01\\x00\\x01',(host,53));s.recv(1024);print('UNEXPECTED_DNS_REPLY')\n except OSError:\n  print('DNS_BLOCKED')\n finally:\n  s.close()"
    dns = cbc.docker('exec', cbc.container(name, 'guard'), 'python3', '-c', dns_code)
    record('Docker 内置及公网 UDP DNS 实测被阻断', dns.stdout.count('DNS_BLOCKED') == 2 and 'UNEXPECTED' not in dns.stdout)
    page(name, 'https://cbc.test/')
    candidates = evaluate(name, RTC)
    record('指定 STUN 场景无 WebRTC 地址候选', candidates == [], candidates)
    capture_out, capture_err = capture.communicate(timeout=20)
    (OUT / 'capture.log').write_bytes(capture_out + capture_err)
    # Docker 24's archive endpoint does not expose files in tmpfs. Read inside
    # the namespace, preserving the binary pcap instead of decoding it as text.
    packet_file = subprocess.run(['docker', 'exec', cbc.container(name, 'guard'), 'cat', '/tmp/cbc-dns-webrtc.pcap'], check=True, capture_output=True)
    (OUT / 'dns-webrtc.pcap').write_bytes(packet_file.stdout)
    packets = cbc.docker('exec', cbc.container(name, 'guard'), 'tcpdump', '-n', '-r', '/tmp/cbc-dns-webrtc.pcap')
    record('DNS / WebRTC 期间接口未发出 UDP 或 DNS 包', not packets.stdout.strip())
    logs = cbc.docker('logs', f'cbc-fixture-{name}').stdout
    events = []
    decoder = json.JSONDecoder()
    remaining = logs.lstrip()
    while remaining:
        value, consumed = decoder.raw_decode(remaining)
        events.append(value)
        remaining = remaining[consumed:].lstrip()
    cbc.save_json(OUT / 'fixture-a-events.json', events)
    record('目标域名交给 SOCKS5 上游解析（ATYP=3）', any(e.get('atyp') == 3 and e.get('destination') == 'example.com' for e in events))
    record('临时 SOCKS5 记录到正确与错误认证', any(e.get('accepted') is True for e in events) and any(e.get('accepted') is False for e in events))
    sandbox = page(name, 'chrome://sandbox/')['text']
    (OUT / 'sandbox-a.txt').write_text(sandbox)
    record('Chromium Namespace / seccomp sandbox 启用', 'You are adequately sandboxed.' in sandbox)
    try:
        home = page(name, 'https://claude.ai/')
        time.sleep(5)
        home = evaluate(name, '({title:document.title,text:document.body.innerText,url:location.href})')
        cbc.save_json(OUT / 'claude-homepage.json', home)
        record('Claude 匿名首页可读取（临时测试出口）', 'claude' in home['title'].lower() and 'just a moment' not in home['title'].lower(), {'title': home['title'], 'url': home['url']}, required=False)
    except RuntimeError:
        record('Claude 匿名首页可读取（临时测试出口）', False, '页面未完成加载；不进行验证码或登录操作。', required=False)
    cbc.benchmark('one-claude-home')


def multi():
    setup_fixture('claude-b', 'B')
    before = page('claude-b', 'https://cbc.test/', '({cookie:document.cookie,localStorage:localStorage.getItem("cbc-marker")})')
    record('B 未继承 A 的 Cookie / LocalStorage', before['localStorage'] != 'A' and 'cbc-marker=A' not in before['cookie'], before)
    page('claude-b', 'https://cbc.test/', WRITE.replace('MARKER', json.dumps('B')))
    a = page('claude-a', 'https://cbc.test/', READ)
    b = page('claude-b', 'https://cbc.test/', READ)
    record('两个 Profile / Cookie / IndexedDB / 缓存独立', storage_matches(a, 'A') and storage_matches(b, 'B'), {'A': a, 'B': b})
    for name, marker in [('claude-a', 'A'), ('claude-b', 'B')]:
        page(name, 'https://example.com/')
        filename = f'cbc-acceptance-{STAMP}-{marker}.txt'
        cbc.probe(name, 'download', filename, 'CBC download ' + marker)
        path = cbc.ROOT / 'data' / name / 'Downloads' / filename
        end = time.monotonic() + 30
        while not path.exists() and time.monotonic() < end:
            time.sleep(0.25)
        record(f'{name} 下载到独立持久化目录', path.exists() and path.read_text() == 'CBC download ' + marker)
    for name in ['claude-a', 'claude-b']:
        record(f'{name} 多实例网络及故障检查', cbc.verify(name))
        observed = cbc.observe_exit(name)
        record(f'{name} 临时代理公网出口可测', observed is not None, observed or '临时代理公网请求失败')
        cbc.docker('logs', f'cbc-fixture-{name}')
    process_a = json.loads(cbc.docker('inspect', cbc.container('claude-a', 'browser')).stdout)[0]
    process_b = json.loads(cbc.docker('inspect', cbc.container('claude-b', 'browser')).stdout)[0]
    paths = [next(m['Source'] for m in p['Mounts'] if m['Destination'] == '/config') for p in [process_a, process_b]]
    record('两个浏览器复用同一镜像且挂载不同目录', process_a['Image'] == process_b['Image'] and paths[0] != paths[1])
    cbc.benchmark('two-test-origins')
    cbc.compose('claude-b', 'stop', capture=False)
    record('独立停止 B 后 A 继续运行', cbc.active('claude-a') and not cbc.active('claude-b'))
    cbc.benchmark('after-stop-b')


def cleanup():
    for fixture in FIXTURES:
        logs = cbc.docker('logs', fixture, check=False)
        (OUT / f'{fixture}.log').write_text(logs.stdout + logs.stderr)
        cbc.docker('unpause', fixture, check=False)
        cbc.docker('rm', '-f', fixture, check=False)
    for name, original in ORIGINAL.items():
        path = cbc.CONFIGS / name / 'proxy.json'
        # Restore only our test configuration, not a concurrent user edit.
        current = cbc.read_json(path)
        if current.get('username', '').startswith('fixture-'):
            path.write_bytes(original)
            path.chmod(0o600)
        cbc.stop(name)
        cbc.start(name, cdp=False)


if __name__ == '__main__':
    os.umask(0o077)
    try:
        # Keep the single-browser measurement single, and preserve B's existing
        # profile so the acceptance command is safe to repeat.
        cbc.stop('claude-b')
        single()
        multi()
    finally:
        cleanup()
    print(f'验收记录：{OUT}', flush=True)
    if any(not item['passed'] and item.get('required', True) for item in RESULTS):
        sys.exit(1)
