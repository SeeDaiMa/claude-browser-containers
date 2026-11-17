"""一次性实例的存储往返和工作区画面。拒绝把 claude-a / claude-b 当目标。"""

import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import time
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRATCH = Path(os.environ.get('CBC_EVIDENCE_DIR', ROOT / 'evidence' / 'live-roundtrip'))
FORBIDDEN = {'claude-a', 'claude-b'}
AB_MUTATIONS = {'start', 'stop', 'restart', 'trash', 'apply-proxy'}


def require_disposable(slug):
    if slug in FORBIDDEN or slug.startswith('claude-'):
        raise RuntimeError(f'拒绝把 {slug} 当作一次性测试实例。')


def test_disposable_roundtrip_refuses_live_instances():
    for name in ('claude-a', 'claude-b'):
        with pytest.raises(RuntimeError):
            require_disposable(name)


def test_disposable_storage_and_workspace(capsys):
    import sys

    sys.path.insert(0, str(ROOT / 'scripts'))
    import cbc
    import manager_core
    import store

    SCRATCH.mkdir(parents=True, exist_ok=True)
    before = _ab_snapshot()
    mutations_before = _ab_mutation_ids()
    (SCRATCH / 'ab-before.json').write_text(
        json.dumps({'containers': before, 'mutation_task_ids': mutations_before}, ensure_ascii=False, indent=2)
    )
    slug = 'i' + secrets.token_hex(4)
    require_disposable(slug)
    marker = secrets.token_hex(8)
    fixture = f'cbc-fixture-{slug}'
    log = []

    def note(line):
        print(line, flush=True)
        log.append(line)

    try:
        _ensure_manager()
        port = manager_core._allocate_port()
        cbc.create(slug, port)
        require_disposable(slug)
        raw = cbc.read_json(cbc.CONFIGS / slug / 'instance.json')
        raw.update({'id': slug, 'display_name': f'往返{marker[:6]}', 'created_at': store._now(), 'trashed_at': None})
        store._save(cbc.CONFIGS / slug / 'instance.json', raw)
        index = raw['network_index']
        password = secrets.token_urlsafe(24)
        proxy = {
            'host': f'10.232.{index}.4',
            'port': 1080,
            'username': f'fixture-{marker[:6]}',
            'password': password,
            'expected_ip': '',
        }
        store._write_proxy(slug, proxy)
        cbc.prepare(slug, cdp=False)
        cbc.compose(slug, 'up', '-d', '--wait', '--wait-timeout', '90', 'guard', 'proxy', capture=True)
        _start_fixture(slug, proxy, marker)
        note(f'PASS 临时代理已就绪 {slug}')
        started = _api_task('start', slug)
        assert started['state'] == 'succeeded', started
        note('PASS 管理台启动成功')
        subprocess.run([str(ROOT / 'scripts' / 'start.sh'), slug, '--cdp'], cwd=ROOT, check=True)
        _write_storage(slug, marker)
        download = ROOT / 'data' / slug / 'Downloads' / f'cbc-roundtrip-{marker}.txt'
        assert download.is_file() and download.read_text() == marker
        note('PASS 已写入 Cookie LocalStorage IndexedDB 和下载文件')
        _workspace_two_passes(slug, marker, note)
        _stop_trash_restore_and_read(slug, marker, note)
        data_dir = ROOT / 'data' / slug
        assert data_dir.is_dir()
        note(f'PASS 数据目录仍是 {slug}')
    finally:
        subprocess.run(['docker', 'rm', '-f', fixture], check=False, capture_output=True)
        _purge_if_present(slug)
        text = '\n'.join(log) + '\n' + capsys.readouterr().out
        (SCRATCH / 'trash-roundtrip.log').write_text(text)
        after = _ab_snapshot()
        mutations_after = _ab_mutation_ids()
        (SCRATCH / 'ab-after.json').write_text(
            json.dumps({'containers': after, 'mutation_task_ids': mutations_after}, ensure_ascii=False, indent=2)
        )
        same = _ab_same(before, after)
        no_new_mutations = mutations_after == mutations_before
        (SCRATCH / 'ab-untouched.txt').write_text(
            'before\n'
            + json.dumps(before, ensure_ascii=False, indent=2)
            + '\nafter\n'
            + json.dumps(after, ensure_ascii=False, indent=2)
            + '\nmutation_task_ids_before\n'
            + json.dumps(mutations_before)
            + '\nmutation_task_ids_after\n'
            + json.dumps(mutations_after)
            + f'\nstarted_at_and_instance_json_same {same}\nno_new_ab_mutations {no_new_mutations}\n'
        )
        assert same, 'claude-a 或 claude-b 的 StartedAt 或 instance.json 变了'
        assert no_new_mutations, '本次运行新增了 claude-a 或 claude-b 的 start/stop/restart/trash/apply-proxy 任务'
        assert not (ROOT / 'configs' / slug).exists()


def _ab_snapshot():
    import hashlib

    rows = {}
    for name in FORBIDDEN:
        started = subprocess.check_output(
            ['docker', 'inspect', '-f', '{{.State.StartedAt}}', f'cbc-{name}-browser'], text=True
        ).strip()
        du = subprocess.check_output(['du', '-sk', str(ROOT / 'data' / name)], text=True).split()[0]
        config = hashlib.sha256((ROOT / 'configs' / name / 'instance.json').read_bytes()).hexdigest()
        rows[name] = {'started': started, 'data_kib': du, 'instance_json_sha256': config}
    return rows


def _child_of(parent, pid):
    try:
        ppid = subprocess.check_output(['ps', '-o', 'ppid=', '-p', str(pid)], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return False
    return ppid == str(parent)


def _ab_mutation_ids():
    path = ROOT / 'configs' / 'private' / 'tasks.json'
    if not path.exists():
        return []
    tasks = json.loads(path.read_text())
    return [task['id'] for task in tasks if task.get('instance_id') in FORBIDDEN and task.get('kind') in AB_MUTATIONS]


def _ab_same(before, after):
    for name in FORBIDDEN:
        if before[name]['started'] != after[name]['started']:
            return False
        if before[name]['instance_json_sha256'] != after[name]['instance_json_sha256']:
            return False
    return True


def _ensure_manager():
    import socket

    probe = socket.socket()
    probe.settimeout(0.4)
    up = probe.connect_ex(('127.0.0.1', 5790)) == 0
    probe.close()
    if not up:
        subprocess.Popen([str(ROOT / 'scripts' / 'start-manager.sh')], cwd=ROOT)
        for _ in range(40):
            time.sleep(0.25)
            probe = socket.socket()
            probe.settimeout(0.4)
            up = probe.connect_ex(('127.0.0.1', 5790)) == 0
            probe.close()
            if up:
                return
        raise RuntimeError('管理台没有在 5790 监听。')


def _start_fixture(slug, proxy, marker):
    import sys

    sys.path.insert(0, str(ROOT / 'scripts'))
    import cbc

    directory = cbc.CONFIGS / slug / 'runtime' / 'fixture'
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    cbc.save_json(
        directory / 'fixture.json',
        dict(proxy, marker=marker, download_name=f'cbc-roundtrip-{marker}.txt'),
        private=True,
    )
    cbc.run(
        [
            'openssl',
            'req',
            '-x509',
            '-newkey',
            'rsa:2048',
            '-sha256',
            '-days',
            '1',
            '-nodes',
            '-keyout',
            directory / 'key.pem',
            '-out',
            directory / 'cert.pem',
            '-subj',
            '/CN=cbc.test',
            '-addext',
            'subjectAltName=DNS:cbc.test',
        ]
    )
    image = cbc.read_json(ROOT / 'images.lock.json')['network']['reference']
    cbc.docker('rm', '-f', f'cbc-fixture-{slug}', check=False)
    cbc.docker(
        'run',
        '-d',
        '--name',
        f'cbc-fixture-{slug}',
        '--label',
        'io.cbc.test=true',
        '--network',
        f'cbc-{slug}-egress',
        '--ip',
        proxy['host'],
        '--cap-drop',
        'ALL',
        '--cap-add',
        'DAC_OVERRIDE',
        '--security-opt',
        f'seccomp={cbc.CONFIGS / "seccomp-default.json"}',
        '--security-opt',
        'no-new-privileges=true',
        '--read-only',
        '--memory',
        '96m',
        '--cpus',
        '0.25',
        '--pids-limit',
        '64',
        '-v',
        f'{ROOT / "scripts/fixture.py"}:/fixture.py:ro',
        '-v',
        f'{directory}:/settings:ro',
        '--entrypoint',
        'python3',
        image,
        '-u',
        '/fixture.py',
    )
    for _ in range(40):
        if '"kind": "ready"' in cbc.docker('logs', f'cbc-fixture-{slug}', check=False).stdout:
            return
        time.sleep(0.25)
    raise RuntimeError('临时 SOCKS5 没有就绪。')


def _api(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {}
    if method != 'GET':
        headers = {'Content-Type': 'application/json', 'X-CBC-Request': '1', 'Origin': 'http://127.0.0.1:5790'}
    request = urllib.request.Request('http://127.0.0.1:5790' + path, data=data, headers=headers, method=method)
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=30) as response:
        return json.loads(response.read().decode())


def _api_task(action, slug, body=None):
    task = _api('POST', f'/api/instances/{slug}/{action}', body or {})
    deadline = time.time() + 180
    while time.time() < deadline:
        current = _api('GET', f'/api/tasks/{task["id"]}')
        if current['state'] in ('succeeded', 'failed', 'interrupted'):
            return current
        time.sleep(1)
    raise RuntimeError('任务超时。')


def _write_storage(slug, marker):
    import sys

    sys.path.insert(0, str(ROOT / 'scripts'))
    import cbc

    write = """(async()=>{const marker=MARKER;localStorage.setItem('cbc-marker',marker);document.cookie='cbc-marker='+marker+'; Max-Age=86400; Secure; SameSite=Lax';const db=await new Promise((ok,no)=>{let r=indexedDB.open('cbc-isolation',1);r.onupgradeneeded=()=>r.result.createObjectStore('markers');r.onsuccess=()=>ok(r.result);r.onerror=()=>no(r.error)});await new Promise((ok,no)=>{let t=db.transaction('markers','readwrite');t.objectStore('markers').put(marker,'marker');t.oncomplete=ok;t.onerror=no});db.close();return {cookie:document.cookie,localStorage:localStorage.getItem('cbc-marker')}})()"""
    written = json.loads(
        cbc.probe(slug, 'page', 'https://cbc.test/', write.replace('MARKER', json.dumps(marker))).stdout
    )
    assert written['localStorage'] == marker
    assert f'cbc-marker={marker}' in written['cookie']
    cbc.probe(slug, 'download', f'cbc-roundtrip-{marker}.txt', marker)
    deadline = time.time() + 10
    path = ROOT / 'data' / slug / 'Downloads' / f'cbc-roundtrip-{marker}.txt'
    while time.time() < deadline and not path.exists():
        time.sleep(0.25)


def _read_storage(slug):
    import sys

    sys.path.insert(0, str(ROOT / 'scripts'))
    import cbc

    read = """(async()=>{const db=await new Promise((ok,no)=>{let r=indexedDB.open('cbc-isolation',1);r.onsuccess=()=>ok(r.result);r.onerror=()=>no(r.error)});const indexedDBValue=await new Promise((ok,no)=>{let r=db.transaction('markers').objectStore('markers').get('marker');r.onsuccess=()=>ok(r.result);r.onerror=()=>no(r.error)});db.close();return {cookie:document.cookie,localStorage:localStorage.getItem('cbc-marker'),indexedDB:indexedDBValue}})()"""
    return json.loads(cbc.probe(slug, 'page', 'https://cbc.test/', read).stdout)


def _workspace_two_passes(slug, marker, note):
    import sys

    sys.path.insert(0, str(ROOT / 'scripts'))
    import cbc

    arm = """(()=>{const input=document.createElement('input');input.id='cbc-keys';input.style.cssText='position:fixed;inset:0;font-size:48px;z-index:2147483647';document.body.append(input);input.focus();window.__cbcClicks=0;window.__cbcKeys='';document.addEventListener('pointerdown',()=>{window.__cbcClicks+=1},true);document.addEventListener('keydown',(event)=>{window.__cbcKeys+=event.key},true);return true})()"""
    assert json.loads(cbc.probe(slug, 'eval', arm).stdout) is True
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        (SCRATCH / 'workspace-launch-error.txt').write_text(str(error))
        pytest.skip(f'浏览器自动化环境不可用：{error}')
    results = []
    chrome_proc = None
    playwright = None
    try:
        playwright = sync_playwright().start()
        profile = SCRATCH / 'chrome-profile'
        if profile.exists():
            shutil.rmtree(profile)
        profile.mkdir(parents=True)
        chrome_bin = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
        if not Path(chrome_bin).exists():
            raise FileNotFoundError(chrome_bin)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            debug_port = listener.getsockname()[1]
        chrome_proc = subprocess.Popen(
            [
                chrome_bin,
                f'--remote-debugging-port={debug_port}',
                f'--user-data-dir={profile}',
                '--no-first-run',
                '--new-window',
                'about:blank',
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.time() + 15
        version = None
        while time.time() < deadline:
            try:
                version = json.loads(
                    urllib.request.urlopen(f'http://127.0.0.1:{debug_port}/json/version', timeout=0.5).read().decode()
                )
                listeners = subprocess.check_output(
                    ['lsof', '-nP', f'-iTCP:{debug_port}', '-sTCP:LISTEN', '-t'], text=True
                )
                if str(chrome_proc.pid) in listeners.split() or any(
                    _child_of(chrome_proc.pid, int(pid)) for pid in listeners.split() if pid.strip()
                ):
                    break
                version = None
            except (OSError, json.JSONDecodeError, subprocess.CalledProcessError):
                time.sleep(0.2)
        if not version:
            raise RuntimeError('本机 Chrome 调试端口没有由本次启动的进程打开。')
        browser = playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{debug_port}')
        for index in (1, 2):
            results.append(_one_workspace_pass(browser.contexts[0], slug, index))
    except FileNotFoundError as error:
        (SCRATCH / 'workspace-launch-error.txt').write_text(f'{type(error).__name__}: {error}')
        pytest.skip(f'浏览器自动化环境不可用：{error}')
    finally:
        if chrome_proc and chrome_proc.poll() is None:
            try:
                os.killpg(chrome_proc.pid, 15)
                chrome_proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                chrome_proc.kill()
        if playwright:
            playwright.stop()

    def succeeded(row):
        observed = row['observed']
        typed = (observed.get('keys') or '') + (observed.get('downs') or '')
        return (
            row['same_iframe']
            and 'frame-wrap' in row['fullscreen']
            and observed.get('clicks', 0) >= 1
            and 'z' in typed.lower()
        )

    assert succeeded(results[0]) and succeeded(results[1])
    note('PASS 两遍工作区键盘、鼠标、尺寸和全屏一致')
    (SCRATCH / 'workspace-input.log').write_text(json.dumps(results, ensure_ascii=False, indent=2))


def _one_workspace_pass(context, slug, index):
    import sys

    sys.path.insert(0, str(ROOT / 'scripts'))
    import cbc

    cbc.probe(
        slug,
        'eval',
        "(()=>{const input=document.getElementById('cbc-keys'); if(input){input.value=''; input.focus();} window.__cbcClicks=0; window.__cbcKeys=''; return true})()",
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.set_viewport_size({'width': 1200, 'height': 800})
    page.goto(f'http://127.0.0.1:5790/instance/{slug}', wait_until='domcontentloaded')
    page.wait_for_selector('iframe')
    page.evaluate('() => { window.__frame = document.querySelector("iframe"); }')
    time.sleep(2.5)
    same = page.evaluate('() => window.__frame === document.querySelector("iframe")')
    assert same
    assert page.locator('iframe').count() == 1
    src = page.locator('iframe').get_attribute('src')
    assert src.startswith('http://127.0.0.1:')
    box = page.locator('.frame-wrap').bounding_box()
    work = page.locator('.workspace-page').bounding_box()
    assert box['width'] >= work['width'] - 80
    assert box['height'] >= 320
    page.set_viewport_size({'width': 1000, 'height': 700})
    resized = page.locator('.frame-wrap').bounding_box()
    assert resized['width'] < box['width'] - 50
    subprocess.run(['osascript', '-e', 'tell application "Google Chrome" to activate'], check=False)
    page.bring_to_front()
    page.wait_for_timeout(300)
    page.get_by_role('button', name='全屏').click()
    page.wait_for_timeout(500)
    fullscreen = page.evaluate('() => document.fullscreenElement && document.fullscreenElement.className')
    assert fullscreen and 'frame-wrap' in fullscreen
    page.keyboard.press('Escape')
    frame = page.frame_locator('iframe')
    canvas = frame.locator('canvas').first
    canvas.wait_for(timeout=20000)
    box = canvas.bounding_box()
    observed = {'keys': '', 'clicks': 0}
    for _ in range(3):
        canvas.click(position={'x': box['width'] / 2, 'y': box['height'] / 2})
        page.wait_for_timeout(200)
        canvas.press('z')
        page.wait_for_timeout(300)
        observed = json.loads(
            cbc.probe(
                slug,
                'eval',
                '({keys:(document.getElementById("cbc-keys")||{}).value||"", downs:window.__cbcKeys||"", clicks:window.__cbcClicks})',
            ).stdout
        )
        typed = (observed.get('keys') or '') + (observed.get('downs') or '')
        if observed.get('clicks', 0) >= 1 and 'z' in typed.lower():
            break
    page.screenshot(path=str(SCRATCH / ('workspace.png' if index == 2 else 'workspace-1.png')))
    page.goto('http://127.0.0.1:5790/')
    page.wait_for_timeout(500)
    running = subprocess_running(slug)
    assert running
    assert observed['clicks'] >= 1 or 'Z' in (observed['keys'] or '') or 'z' in (observed['keys'] or '')
    return {
        'src': src,
        'same_iframe': same,
        'fullscreen': fullscreen,
        'observed': observed,
        'still_running_after_leave': running,
    }


def subprocess_running(slug):
    result = subprocess.run(
        ['docker', 'inspect', '-f', '{{.State.Running}}', f'cbc-{slug}-browser'], text=True, capture_output=True
    )
    return result.stdout.strip() == 'true'


def _stop_trash_restore_and_read(slug, marker, note):
    import sys

    sys.path.insert(0, str(ROOT / 'scripts'))
    import cbc

    cbc.probe(slug, 'eval', "(()=>{window.onbeforeunload=()=>'stay';return true})()")
    dialog = _api_task('stop', slug)
    if dialog['state'] == 'failed' and '关闭确认' in dialog['error'] and subprocess_running(slug):
        note('PASS 关闭确认出现时停止失败且进程仍在')
        # 确认框挡住页面脚本。用已有的 xdotool 关掉确认框，再清掉拦截。
        cbc.docker(
            'exec',
            '--user',
            '1000',
            cbc.container(slug, 'browser'),
            'env',
            'DISPLAY=:0',
            'xdotool',
            'key',
            'Escape',
            check=False,
        )
        time.sleep(0.5)
        cbc.probe(slug, 'eval', '(()=>{window.onbeforeunload=null;return true})()')
        dialog = _api_task('stop', slug)
    assert dialog['state'] == 'succeeded', dialog
    note('PASS 管理台正常停止')
    trashed = _api_task('trash', slug)
    assert trashed['state'] == 'succeeded', trashed
    restored = _api_task('restore', slug)
    assert restored['state'] == 'succeeded', restored
    assert not subprocess_running(slug)
    started = _api_task('start', slug)
    assert started['state'] == 'succeeded', started
    subprocess.run([str(ROOT / 'scripts' / 'start.sh'), slug, '--cdp'], cwd=ROOT, check=True)
    read = _read_storage(slug)
    assert read['localStorage'] == marker
    assert read['indexedDB'] == marker
    assert f'cbc-marker={marker}' in read['cookie']
    download = ROOT / 'data' / slug / 'Downloads' / f'cbc-roundtrip-{marker}.txt'
    assert download.read_text() == marker
    note('PASS 回收站往返后 Cookie LocalStorage IndexedDB 和下载文件仍在')


def _purge_if_present(slug):
    if slug in FORBIDDEN or not slug.startswith('i'):
        return
    import sys

    sys.path.insert(0, str(ROOT / 'scripts'))
    import cbc

    if not (cbc.CONFIGS / slug / 'instance.json').exists():
        return
    try:
        display = cbc.read_json(cbc.CONFIGS / slug / 'instance.json').get('display_name', slug)
        if subprocess_running(slug):
            subprocess.run([str(ROOT / 'scripts' / 'stop.sh'), slug], cwd=ROOT, check=False)
        _api_task('purge', slug, {'confirm_name': display})
    except Exception:
        subprocess.run(
            [
                'docker',
                'compose',
                '--project-name',
                f'cbc-{slug}',
                '--env-file',
                str(cbc.CONFIGS / slug / 'runtime' / 'compose.env'),
                '-f',
                str(ROOT / 'compose.yaml'),
                'down',
                '--timeout',
                '10',
            ],
            check=False,
        )
        for role in ('browser', 'proxy', 'guard'):
            subprocess.run(['docker', 'rm', '-f', f'cbc-{slug}-{role}'], check=False, capture_output=True)
        shutil.rmtree(cbc.CONFIGS / slug, ignore_errors=True)
        shutil.rmtree(ROOT / 'data' / slug, ignore_errors=True)
