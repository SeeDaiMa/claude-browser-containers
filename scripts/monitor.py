"""持续观察实例，只恢复用户要求运行的原节点，不替换节点或放宽网络规则。"""
import hashlib
import secrets
import subprocess
import threading
import time

import cbc
import store

INTERVAL = 10
PROBE_INTERVAL = 600
MAX_ATTEMPTS = 3
RETRY_DELAYS = (30, 120, 300)
STABLE_SECONDS = 300
_STOP = threading.Event()
_THREAD = None
_STATE = {}
_GUARD = threading.RLock()
_SERVICE = {'checked_at': None, 'error': ''}


def start():
    global _THREAD
    with _GUARD:
        if _THREAD and _THREAD.is_alive():
            return
        _STOP.clear()
        _THREAD = threading.Thread(target=_run, name='cbc-health', daemon=True)
        _THREAD.start()


def stop():
    _STOP.set()
    if _THREAD:
        _THREAD.join(timeout=2)


def _run():
    while not _STOP.is_set():
        try:
            tick()
        except Exception:
            # 监测线程继续下一轮，同时向界面报告故障，不输出配置或凭据。
            with _GUARD:
                _SERVICE['error'] = '健康监测暂时失败，将在下一轮重试。'
        _STOP.wait(INTERVAL)


def service_status():
    with _GUARD:
        return {'running': bool(_THREAD and _THREAD.is_alive()), **_SERVICE}


def _state(row):
    slug = row['slug']
    generation = row['raw'].get('run_generation')
    with _GUARD:
        state = _STATE.get(slug)
        if not state or state['generation'] != generation:
            # 首轮和后续复测均错开，不会一次向所有节点发请求。
            offset = int(hashlib.sha256(slug.encode()).hexdigest()[:4], 16) % 120
            state = {'generation': generation, 'next_probe': time.monotonic() + offset, 'component_failures': 0,
                     'probe_failures': 0, 'stable_since': None, 'fault_since': None}
            _STATE[slug] = state
        return state


def public_status(row, runtime, exit_row, proxy=None):
    raw = row.get('raw') or {}
    enabled = bool(raw.get('desired_running')) and not row['trashed_at']
    recovery = raw.get('recovery') or {}
    with _GUARD:
        observed = dict(_STATE.get(row['slug']) or {})
    status, detail = 'idle', '下次成功启动后启用自动恢复。'
    if row['trashed_at'] or raw.get('desired_running') is False:
        detail = '已停止自动恢复，实例不会被自动拉起。'
    elif runtime == 'unknown':
        status, detail = 'unknown', 'Docker 状态未知，恢复操作已暂停。'
    elif proxy and (proxy.get('stale') or proxy.get('mode') == 'unknown'):
        enabled = False
        status, detail = 'warning', '原节点需要确认或配置损坏，自动恢复已暂停。'
    elif recovery.get('exhausted'):
        status, detail = 'failed', recovery.get('detail') or '自动恢复已达上限，请手动检查后启动。'
    elif enabled and (runtime != 'running' or (exit_row or {}).get('status') == 'failed'):
        status, detail = 'warning', recovery.get('detail') or '检测到连接异常，正在观察并准备恢复原节点。'
    elif enabled:
        status, detail = 'healthy', '持续监测中，仅恢复原绑定节点。'
    return {'enabled': enabled, 'state': status, 'detail': detail, 'fault_since': observed.get('fault_since'),
            'recovery_attempts': recovery.get('attempts', 0), 'next_retry_at': recovery.get('next_retry_at'),
            'checked_at': _SERVICE['checked_at']}


def _busy(instance_id):
    import manager_core as core
    with core._GUARD:
        return any(task['instance_id'] == instance_id and task['state'] in ('queued', 'running') for task in core._TASKS.values())


def _eligible(row):
    proxy = store.proxy_public(row['slug'])
    return proxy.get('mode') in ('subscription', 'socks') and not proxy.get('stale')


def tick():
    import manager_core as core
    snapshot = core.runtime_snapshot()
    with _GUARD:
        _SERVICE.update(checked_at=store._now(), error='' if snapshot['docker']['ok'] else 'Docker 未连接，健康监测和自动恢复已暂停。')
    if not snapshot['docker']['ok']:
        return
    try:
        exits = store.load_exits()
    except store.SharedRecordError:
        with _GUARD:
            _SERVICE['error'] = '出口记录无法读取，已暂停自动恢复。原文件已保留。'
        return
    rows = store._instances_on_disk()
    now = time.monotonic()
    probed_one = False
    for row in rows:
        if row['trashed_at'] or _busy(row['id']):
            continue
        try:
            with store.instance_operation(row['slug'], blocking=False):
                row = store.instance_by_id(row['id'])
                if row['trashed_at'] or _busy(row['id']) or not _eligible(row):
                    continue
                runtime = core._runtime_state(row['slug'], snapshot['containers'], snapshot['health'])
                if 'desired_running' not in row['raw'] and runtime == 'running':
                    # 只接管当前健康运行的旧实例；从不猜测已经停止的旧实例应当启动。
                    def adopt(raw):
                        if 'desired_running' not in raw and not raw.get('trashed_at'):
                            raw.update(desired_running=True, run_generation=secrets.token_hex(8), recovery={})
                    store.update_instance(row['slug'], adopt)
                    row = store.instance_by_id(row['id'])
                state = _state(row)
                desired = bool(row['raw'].get('desired_running'))
                if not desired:
                    continue
                if runtime != 'running':
                    state['component_failures'] += 1
                    state['stable_since'] = None
                else:
                    state['component_failures'] = 0
                    state['stable_since'] = state['stable_since'] or now
                    if now >= state['next_probe'] and not probed_one:
                        probed_one = True
                        observation = core.probe_proxy(row['slug'])
                        exits[row['slug']] = store.save_exit(row['slug'], observation['status'], observation['ip'], observation['detail'], observation.get('country_code'))
                        state['probe_failures'] = state['probe_failures'] + 1 if observation['status'] == 'failed' else 0
                        state['next_probe'] = now + (30 if observation['status'] == 'failed' else PROBE_INTERVAL)
                faulty = runtime != 'running' or (exits.get(row['slug']) or {}).get('status') == 'failed'
                if faulty:
                    state['fault_since'] = state['fault_since'] or store._now()
                    state['stable_since'] = None
                else:
                    state['fault_since'] = None
                    if state['stable_since'] and now - state['stable_since'] >= STABLE_SECONDS and (exits.get(row['slug']) or {}).get('status') == 'ok':
                        if row['raw'].get('recovery'):
                            store.update_instance(row['slug'], lambda raw: raw.update(recovery={}))
                recovery = row['raw'].get('recovery') or {}
                should_recover = state['component_failures'] >= 3 or state['probe_failures'] >= 2
                if should_recover and recovery.get('attempts', 0) >= MAX_ATTEMPTS and not recovery.get('exhausted'):
                    def exhaust(raw):
                        if raw.get('run_generation') == row['raw'].get('run_generation') and raw.get('desired_running'):
                            raw['recovery'] = {**recovery, 'exhausted': True, 'next_retry_at': None,
                                               'detail': '自动恢复已达 3 次上限，请检查原节点后手动启动。'}
                    store.update_instance(row['slug'], exhaust)
                elif should_recover and not _STOP.is_set() and not recovery.get('exhausted') and time.time() >= recovery.get('next_retry_at', 0):
                    core.enqueue('recover', row['id'], payload={'generation': row['raw'].get('run_generation')})
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            # 单个坏代理配置或正在执行的操作不阻止其他实例监测。
            continue
    known = {row['slug'] for row in rows}
    with _GUARD:
        for slug in set(_STATE) - known:
            _STATE.pop(slug, None)


def _current(row, generation):
    raw = row['raw']
    return not row['trashed_at'] and raw.get('desired_running') is True and raw.get('run_generation') == generation


def recover(task):
    import manager_core as core
    row = store.instance_by_id(task['instance_id'])
    slug = row['slug']
    generation = task['payload'].get('generation')
    if not _current(row, generation):
        task['result'] = {'detail': '运行意图已变化，旧恢复任务已取消。'}
        return
    recovery = row['raw'].get('recovery') or {}
    if recovery.get('attempts', 0) >= MAX_ATTEMPTS or not _eligible(row):
        task['result'] = {'detail': '恢复已停止，请手动检查原节点。'}
        return
    attempt = recovery.get('attempts', 0) + 1
    result = {'attempts': attempt, 'next_retry_at': time.time() + RETRY_DELAYS[attempt - 1], 'exhausted': False,
              'detail': f'正在进行第 {attempt}/{MAX_ATTEMPTS} 次原节点恢复。'}
    def record(raw):
        if raw.get('run_generation') == generation and raw.get('desired_running'):
            raw['recovery'] = dict(result)
    store.update_instance(slug, record)
    try:
        core._advance(task, '检查环境')
        core._preflight_boot(slug)
        if not _current(store.instance_by_id(row['id']), generation):
            task['result'] = {'detail': '用户已停止实例，恢复已取消。'}
            return
        snapshot = core.runtime_snapshot(force=True)
        if not snapshot['docker']['ok'] or snapshot['containers'] is None:
            raise RuntimeError('Docker 暂时无法连接。')
        core._advance(task, '恢复原节点连接')
        intact = all(snapshot['containers'].get(cbc.container(slug, role)) == 'running' and snapshot['health'].get(cbc.container(slug, role)) not in ('unhealthy', 'starting') for role in ('guard', 'browser'))
        if intact:
            # 浏览器和 guard 保持原容器，只重建代理并加载重新解析后的窄白名单。
            identity = cbc.running_browser_identity(slug)
            cbc.prepare(slug, cdp=False, keep_browser=identity)
            if not _current(store.instance_by_id(row['id']), generation):
                task['result'] = {'detail': '用户已停止实例，恢复已取消。'}
                return
            cbc.compose(slug, 'up', '-d', '--no-deps', '--force-recreate', '--wait', '--wait-timeout', '90', 'proxy', timeout=120)
            core._advance(task, '验证代理连接')
            observation = core.probe_proxy(slug)
            core._require_same_country(slug, observation)
            store.save_exit(slug, observation['status'], observation['ip'], observation['detail'], observation.get('country_code'))
            if observation['status'] == 'failed':
                raise RuntimeError('原节点连接仍然失败。')
        else:
            # guard 的网络空间不能独立重建；先正常关闭浏览器，再协调重启整个实例。
            core._stop_running(slug)
            if not _current(store.instance_by_id(row['id']), generation):
                task['result'] = {'detail': '用户已停止实例，恢复已取消。'}
                return
            core._boot(task, slug, verify=True)
        result['detail'] = f'原节点已恢复（第 {attempt}/{MAX_ATTEMPTS} 次），继续观察稳定性。'
        state = _state(row)
        state.update(component_failures=0, probe_failures=0, stable_since=time.monotonic(), fault_since=None, next_probe=time.monotonic() + 30)
        task['result'] = {'detail': result['detail']}
    except Exception as error:
        if isinstance(error, cbc.CountryPolicyError):
            result['exhausted'] = True
            result['detail'] = str(error)
        else:
            result['exhausted'] = attempt >= MAX_ATTEMPTS
            result['detail'] = '自动恢复已达 3 次上限，请检查原节点后手动启动。' if result['exhausted'] else f'第 {attempt}/{MAX_ATTEMPTS} 次恢复未成功，将等待后重试原节点。'
        raise RuntimeError(result['detail']) from None
    finally:
        # 等待从本次恢复结束后计算，耗时的连接失败不能吃掉重试间隔。
        result['next_retry_at'] = None if result['exhausted'] else time.time() + RETRY_DELAYS[attempt - 1]
        store.update_instance(slug, record)
