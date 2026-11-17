"""网页和后续任务共用的实例操作。容器动作仍调用 cbc。"""
import copy
from collections import deque
import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time

import cbc
import catalog
import store
import geolocation
import node_probe

_TASKS = {}
_ORDER = []
_QUEUE = queue.Queue()
_NODE_TEST_QUEUE = queue.Queue()
_SUBSCRIPTION_QUEUE = queue.Queue()
_GUARD = threading.RLock()
_ENQUEUE_GUARD = threading.RLock()
_PERSIST_GUARD = threading.Lock()
_SECRETS_GUARD = threading.Lock()
_SECRETS_CACHE = {}
_STATS = {'at': 0.0, 'rows': {}, 'docker_vm_rss_mib': None, 'docker_helper_rss_mib': None, 'error': ''}
_WORKER = None
_NODE_TEST_WORKER = None
_SUBSCRIPTION_WORKER = None
_NETWORK_TASKS = {'test-nodes', 'check-node-quality', 'add-subscription', 'refresh', 'update-subscription'}
_RUNTIME = {'at': 0.0, 'docker': {'ok': False, 'detail': ''}, 'containers': None, 'health': {}, 'ids': {}}
_RUNTIME_GUARD = threading.Lock()
_COLLECTION_GUARD = threading.RLock()
ROLES = ('guard', 'proxy', 'browser')
_KEEP_TASKS = 50
_PHASES = {
    'create': ['检查环境', '保存配置、分配端口和网络', '启动网络保护和代理', '验证代理连接', '启动 Chromium', '等待 Web UI'],
    'start': ['检查环境', '启动网络保护和代理', '验证代理连接', '启动 Chromium', '等待 Web UI'],
    'restart': ['检查环境', '停止实例', '启动网络保护和代理', '验证代理连接', '启动 Chromium', '等待 Web UI'],
    'apply-proxy': ['保存配置'],
    'stop': ['关闭浏览器', '停止实例'],
    'trash': ['关闭浏览器', '停止实例', '移入回收站'],
    'restore': ['恢复实例'],
    'purge': ['彻底删除'],
    'refresh': ['刷新订阅'],
    'add-subscription': ['添加订阅'],
    'update-subscription': ['更新订阅地址'],
    'test-nodes': ['准备测速环境', '测试节点延迟'],
    'check-node-quality': ['准备检测环境', '检测节点出口 IP 质量'],
    'recover': ['检查环境', '恢复原节点连接', '验证代理连接'],
}


def safe_error(error):
    text = str(error).strip() or error.__class__.__name__
    text = re.sub(r'https?://\S+', '订阅地址', text)
    for secret in _known_secrets():
        text = text.replace(secret, '[REDACTED]')
    return text[:300]


def _known_secrets():
    files = {store.LIBRARY, *store.CONFIGS.glob('*/proxy.json'), *(store.PRIVATE / 'nodes').glob('*.json')}
    with _SECRETS_GUARD:
        for path in set(_SECRETS_CACHE) - files:
            del _SECRETS_CACHE[path]
        found = set()
        for path in files:
            try:
                status = path.stat()
                signature = (status.st_dev, status.st_ino, status.st_mtime_ns, status.st_size)
                cached = _SECRETS_CACHE.get(path)
                if cached is None or cached[0] != signature:
                    try:
                        value = json.loads(path.read_text())
                        values = catalog.secret_values(value, min_length=1)
                        if path == store.LIBRARY and isinstance(value, dict):
                            for sub in value.get('subscriptions', []):
                                url = sub.get('url') if isinstance(sub, dict) else None
                                if isinstance(url, str) and url:
                                    values.append(url)
                    except (OSError, ValueError, TypeError):
                        # 损坏文件也按状态缓存，修复或替换后再读。
                        values = []
                    cached = (signature, values)
                    _SECRETS_CACHE[path] = cached
                found.update(cached[1])
            except OSError:
                # 一个损坏文件不能让其他实例的凭据失去脱敏。
                continue
        return sorted(found, key=len, reverse=True)


def _phase_list(kind):
    return [{'name': name, 'state': 'pending'} for name in _PHASES.get(kind, ['执行'])]


def _payload_digest(payload):
    """只保留摘要用于判断重复请求，任务结束后原始 payload（含密码）会被清空。"""
    raw = json.dumps(payload or {}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def enqueue(kind, instance_id='', request_id='', payload=None):
    with _ENQUEUE_GUARD:
        digest = _payload_digest(payload)
        with _GUARD:
            if request_id:
                for task in _TASKS.values():
                    if task.get('request_id') == request_id:
                        same = (
                            task.get('kind') == kind
                            and task.get('requested_instance_id', task.get('instance_id')) == instance_id
                            and task.get('payload_digest') == digest
                        )
                        if not same:
                            raise ValueError('重复的请求标识与原请求不一致。')
                        return public_task(task)
            if kind == 'create' and any(task['kind'] == 'create' and task['state'] in ('queued', 'running') for task in _TASKS.values()):
                raise ValueError('已有一个创建任务正在进行。')
            if kind in ('test-nodes', 'check-node-quality') and any(
                task['kind'] == kind and task.get('subscription_id') == payload['subscription_id']
                and task['state'] in ('queued', 'running') for task in _TASKS.values()
            ):
                raise ValueError('此订阅已有节点检测任务，请等待完成。')
        if kind in ('stop', 'trash'):
            # 用户要求停止后，已排队的自动恢复立即失效；磁盘等待不占用任务读取锁。
            row = store.instance_by_id(instance_id)
            store.set_run_intent(row['slug'], False)
        with _GUARD:
            task_id = 't' + os.urandom(8).hex()
            task = {
                'id': task_id,
                'kind': kind,
                'instance_id': instance_id,
                'requested_instance_id': instance_id,
                'request_id': request_id,
                'payload_digest': digest,
                'state': 'queued',
                'phase': _PHASES.get(kind, ['执行'])[0],
                'phases': _phase_list(kind),
                'error': '',
                'payload': payload or {},
                'result': {},
            }
            _TASKS[task_id] = task
            if kind in ('test-nodes', 'check-node-quality'):
                task['subscription_id'] = payload['subscription_id']
                task['node_ids'] = payload['node_ids']
            elif kind in ('refresh', 'update-subscription'):
                task['subscription_id'] = payload['subscription_id']
            _ORDER.append(task_id)
            _prune_tasks()
        try:
            _persist_tasks()
        except Exception:
            with _GUARD:
                _TASKS.pop(task_id, None)
                _ORDER.remove(task_id)
            raise
        if kind in ('test-nodes', 'check-node-quality'):
            _NODE_TEST_QUEUE.put(task_id)
        elif kind in ('add-subscription', 'refresh', 'update-subscription'):
            _SUBSCRIPTION_QUEUE.put(task_id)
        else:
            _QUEUE.put(task_id)
        return public_task(task)


def _prune_tasks():
    """只保留最近的已结束任务；排队和执行中的任务不删除。调用方持有 _GUARD。"""
    finished = [task_id for task_id in _ORDER if _TASKS.get(task_id, {}).get('state') not in ('queued', 'running')]
    for task_id in finished[:-_KEEP_TASKS] if len(finished) > _KEEP_TASKS else []:
        _ORDER.remove(task_id)
        _TASKS.pop(task_id, None)


# 公开任务直接读取这些字段。加载校验必须要求它们存在，不能再用默认值把缺失伪装成空字符串。
_PUBLIC_TASK_FIELDS = ('id', 'kind', 'instance_id', 'state', 'phase', 'phases', 'error', 'result')


def public_task(task):
    public = {key: task[key] for key in _PUBLIC_TASK_FIELDS}
    if task['kind'] in ('refresh', 'update-subscription'):
        public['subscription_id'] = task.get('payload', {}).get('subscription_id', task.get('subscription_id', ''))
    if task['kind'] in ('test-nodes', 'check-node-quality'):
        public['subscription_id'] = task['subscription_id']
        public['node_ids'] = task['node_ids']
    progress = task.get('progress')
    if isinstance(progress, dict):
        public['progress'] = {
            'completed': progress.get('completed', 0),
            'total': progress.get('total', 0),
            'label': progress.get('label', '') if isinstance(progress.get('label'), str) else '',
        }
    if task.get('cancel_requested'):
        public['cancel_requested'] = True
    return copy.deepcopy(public)


def list_tasks():
    with _GUARD:
        return [public_task(_TASKS[task_id]) for task_id in reversed(_ORDER) if task_id in _TASKS]


def get_task(task_id):
    with _GUARD:
        task = _TASKS.get(task_id)
        if not task:
            raise ValueError('任务不存在。')
        return public_task(task)


def cancel_task(task_id):
    """排队任务立即取消。节点检测若已开始，当前节点结束后不再继续。不停止浏览器。"""
    with _GUARD:
        task = _TASKS.get(task_id)
        if not task:
            raise ValueError('任务不存在。')
        if task['state'] == 'queued':
            task['state'] = 'interrupted'
            task['error'] = '已取消，任务尚未开始。'
            task['cancel_requested'] = True
            task['payload'] = {}
        elif task['state'] == 'running' and task['kind'] in ('test-nodes', 'check-node-quality'):
            task['cancel_requested'] = True
            task['error'] = '正在取消，当前节点结束后停止，已完成结果会保留。'
        else:
            raise ValueError('这个任务不能取消。正在启动或自动恢复的实例请使用停止。')
        if task['state'] == 'interrupted':
            for phase in task['phases']:
                if phase.get('state') == 'current':
                    phase['state'] = 'failed'
    _persist_tasks()
    return public_task(task)


def ensure_worker():
    global _WORKER, _NODE_TEST_WORKER, _SUBSCRIPTION_WORKER
    _load_tasks()
    with _GUARD:
        if not _WORKER or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_worker, name='cbc-manager', daemon=True)
            _WORKER.start()
        if not _NODE_TEST_WORKER or not _NODE_TEST_WORKER.is_alive():
            _NODE_TEST_WORKER = threading.Thread(target=_worker, args=(_NODE_TEST_QUEUE,), name='cbc-node-tests', daemon=True)
            _NODE_TEST_WORKER.start()
        if not _SUBSCRIPTION_WORKER or not _SUBSCRIPTION_WORKER.is_alive():
            _SUBSCRIPTION_WORKER = threading.Thread(target=_worker, args=(_SUBSCRIPTION_QUEUE,), name='cbc-subscriptions', daemon=True)
            _SUBSCRIPTION_WORKER.start()


def _preserve_damaged_record(path):
    store._keep_damaged_copy(path, move=True)


def _task_record(task):
    """只接受公开任务能直接读取的记录。缺 phase 或 error 与缺 kind 一样，不能进入内存。"""
    if not isinstance(task, dict):
        return False
    if not isinstance(task.get('id'), str) or not task['id']:
        return False
    for key in ('kind', 'instance_id', 'state', 'phase', 'error'):
        if key not in task or not isinstance(task[key], str):
            return False
    phases = task.get('phases', [])
    if not isinstance(phases, list) or any(not isinstance(phase, dict) for phase in phases):
        return False
    if not isinstance(task.get('result', {}), dict) or not isinstance(task.get('payload', {}), dict):
        return False
    if task['kind'] in ('test-nodes', 'check-node-quality'):
        if not isinstance(task.get('subscription_id'), str) or not isinstance(task.get('node_ids'), list):
            return False
    return True


def _load_tasks():
    path = store.PRIVATE / 'tasks.json'
    if not path.exists():
        return
    with _GUARD:
        if _TASKS:
            return
    try:
        saved = json.loads(path.read_text())
    except (OSError, UnicodeError, ValueError):
        _preserve_damaged_record(path)
        return
    if not isinstance(saved, list):
        _preserve_damaged_record(path)
        return
    usable = []
    skipped = False
    for task in saved:
        # 中断说明只补在仍会执行的任务上；已结束任务缺少 error 时不能靠这个补写蒙混过校验。
        if isinstance(task, dict) and task.get('state') in ('queued', 'running') and 'error' not in task:
            task['error'] = '管理台重启，任务已中断，没有自动重做。'
        if _task_record(task):
            usable.append(task)
        else:
            skipped = True
    if skipped:
        _preserve_damaged_record(path)
    with _GUARD:
        if _TASKS:
            return
        for task in usable:
            if task.get('state') in ('queued', 'running'):
                task['state'] = 'interrupted'
                task['error'] = '管理台重启，任务已中断，没有自动重做。'
            task.setdefault('phases', [])
            task.setdefault('result', {})
            task.setdefault('payload', {})
            _TASKS[task['id']] = task
            _ORDER.append(task['id'])
        _prune_tasks()
    if usable or not skipped:
        _persist_tasks()


def _persist_tasks():
    # 快照和提交按同一顺序串行化；磁盘锁本身不能阻止旧快照覆盖新状态。
    with _PERSIST_GUARD:
        with _GUARD:
            payload = [public_task(task) | {
                'request_id': task.get('request_id', ''),
                'payload_digest': task.get('payload_digest', ''),
                'requested_instance_id': task.get('requested_instance_id', task.get('instance_id', '')),
            } for task in (_TASKS[task_id] for task_id in _ORDER if task_id in _TASKS)]
        store._save(store.PRIVATE / 'tasks.json', payload)


def _worker(task_queue=None):
    task_queue = _QUEUE if task_queue is None else task_queue
    if task_queue is _QUEUE:
        _lifecycle_worker(task_queue)
        return
    while True:
        _run_task(task_queue.get())


def _lifecycle_worker(task_queue):
    # 一个实例一个 FIFO；空队列即退出，长启动不会占用其他实例的停止通道。
    pending = {}
    workers = set()
    guard = threading.Lock()

    def consume(key):
        try:
            while True:
                with guard:
                    if not pending[key]:
                        del pending[key]
                        return
                    task_id = pending[key].popleft()
                _run_task(task_id)
        finally:
            with guard:
                workers.discard(threading.current_thread())

    try:
        while True:
            task_id = task_queue.get()
            with _GUARD:
                task = _TASKS.get(task_id)
                if not task or task['state'] == 'interrupted':
                    continue
                key = task['instance_id']
            if key:
                try:
                    # 历史实例的 ID 和目录名可以不同，仍须进入同一个队列。
                    key = store.instance_by_id(key)['slug']
                except (OSError, ValueError):
                    # 正式执行时保留原有的配置错误响应。
                    pass
            with guard:
                if key in pending:
                    pending[key].append(task_id)
                else:
                    pending[key] = deque([task_id])
                    worker = threading.Thread(target=consume, args=(key,), name=f'cbc-instance-{key or "create"}', daemon=True)
                    workers.add(worker)
                    worker.start()
    finally:
        with guard:
            running = list(workers)
        for worker in running:
            worker.join()


def _run_task(task_id):
    with _GUARD:
        task = _TASKS.get(task_id)
        if not task or task['state'] == 'interrupted':
            return
        task['state'] = 'running'
    try:
        if task['kind'] in _NETWORK_TASKS:
            # 网络下载和节点检测只在提交配置时持有存储锁，不阻塞实例启停。
            _dispatch(task)
        else:
            with cbc.manager_operation():
                _dispatch(task)
        with _GUARD:
            cancelled = bool(task.get('cancel_requested'))
        _finish(task, 'interrupted' if cancelled else 'succeeded',
                '已取消，已完成的节点结果已保留。' if cancelled else '')
    except node_probe.ProbeCancelled as error:
        with _GUARD:
            task['result'] = error.result
        _finish(task, 'interrupted', '已取消，已完成的节点结果已保留。')
    except Exception as error:
        try:
            cancelled = bool(task.get('cancel_requested'))
            _finish(task, 'interrupted' if cancelled else 'failed',
                    '已取消，当前检测未完成，之前的结果已保留。' if cancelled else safe_error(error))
        except Exception:
            with _GUARD:
                task['state'] = 'failed'
                task['error'] = '任务失败，结果没有写入。'
    finally:
        # 任务结束后不再需要原始输入，避免 SOCKS5 密码一直留在内存。
        with _GUARD:
            task['payload'] = {}


def _finish(task, state, error):
    with _GUARD:
        task['state'] = state
        task['error'] = error
        for phase in task['phases']:
            if phase['state'] == 'current':
                phase['state'] = 'done' if state == 'succeeded' else 'failed'
        if state == 'succeeded' and task['phases']:
            task['phase'] = task['phases'][-1]['name']
    _persist_tasks()


def _advance(task, name):
    with _GUARD:
        names = [phase['name'] for phase in task['phases']]
        if name not in names:
            return
        index = names.index(name)
        current = names.index(task['phase']) if task['phase'] in names else 0
        if index < current:
            return
        task['phase'] = name
        for position, phase in enumerate(task['phases']):
            if position < index:
                phase['state'] = 'done'
            elif position == index:
                phase['state'] = 'current'


def _dispatch(task):
    if task.get('instance_id'):
        row = store.instance_by_id(task['instance_id'])
        with store.instance_operation(row['slug']):
            try:
                return _dispatch_locked(task)
            finally:
                invalidate_runtime()
    return _dispatch_locked(task)


def _dispatch_locked(task):
    kind = task['kind']
    payload = task['payload']
    if kind == 'create':
        _create(task, payload)
    elif kind == 'start':
        row = store.instance_by_id(task['instance_id'])
        _preflight_boot(row['slug'])
        generation = store.set_run_intent(row['slug'], False)
        _boot(task, row['slug'], verify=True)
        store.mark_running(row['slug'], generation)
    elif kind == 'restart':
        row = store.instance_by_id(task['instance_id'])
        _preflight_boot(row['slug'])
        generation = store.set_run_intent(row['slug'], False)
        _advance(task, '停止实例')
        _stop_running(row['slug'])
        _boot(task, row['slug'], verify=True)
        store.mark_running(row['slug'], generation)
    elif kind == 'stop':
        _stop_task(task, store.instance_by_id(task['instance_id'])['slug'])
    elif kind == 'trash':
        _trash(task, store.instance_by_id(task['instance_id']))
    elif kind == 'restore':
        _restore(task, store.instance_by_id(task['instance_id']))
    elif kind == 'purge':
        _purge(task, store.instance_by_id(task['instance_id']), payload.get('confirm_name', ''))
    elif kind == 'apply-proxy':
        _apply_proxy(task, store.instance_by_id(task['instance_id']), payload)
    elif kind == 'recover':
        import monitor
        monitor.recover(task)
    elif kind == 'refresh':
        _advance(task, '刷新订阅')
        count = store.refresh_subscription(payload['subscription_id'], catalog.fetch_proxies)
        task['result'] = {'node_count': count}
    elif kind == 'add-subscription':
        _advance(task, '添加订阅')
        subscription_id = store.add_subscription(str(payload.get('name', '')), str(payload.get('url', '')), catalog.fetch_proxies)
        task['result'] = {'id': subscription_id}
    elif kind == 'update-subscription':
        _advance(task, '更新订阅地址')
        updated = store.replace_subscription_url(payload['subscription_id'], payload['url'], catalog.fetch_proxies,
                                                 name=payload.get('name'))
        task['result'] = {'id': updated['id'], 'detail': '订阅已更新，原实例绑定已保留；变化的节点需重新确认。'}
    elif kind == 'test-nodes':
        _advance(task, '测试节点延迟')
        task['result'] = node_probe.test_subscription(
            payload['subscription_id'], payload['node_ids'], on_progress=lambda *args: _probe_progress(task, *args),
        )
    elif kind == 'check-node-quality':
        _advance(task, '检测节点出口 IP 质量')
        task['result'] = node_probe.check_quality(
            payload['subscription_id'], payload['node_ids'], on_progress=lambda *args: _probe_progress(task, *args),
        )
    else:
        raise ValueError('未知任务。')


def _probe_progress(task, completed, total, node):
    label = ''
    if node:
        label = node.get('name') if isinstance(node.get('name'), str) else node.get('id', '')
    with _GUARD:
        cancelled = bool(task.get('cancel_requested')) or task.get('state') == 'interrupted'
        task['progress'] = {'completed': completed, 'total': total, 'label': label}
    _persist_tasks()
    return not cancelled


def _require_docker():
    result = cbc.docker('info', '--format', '{{.ServerVersion}}', check=False, timeout=8)
    if result.returncode:
        raise RuntimeError('Docker 不可用。请先启动 Docker Desktop，不要改系统代理或防火墙。')


def _create(task, payload):
    _advance(task, '检查环境')
    display = store.validate_display_name(payload.get('display_name'))
    _validate_binding(payload, resolve=True)
    _require_docker()
    store.require_complete_inventory()
    _advance(task, '保存配置、分配端口和网络')
    slug = 'i' + os.urandom(4).hex()
    with store.instance_operation(slug):
        with store.exclusive():
            # 解析期间订阅可能被删除，因此落盘前在存储锁里再检查引用。
            _validate_binding(payload)
            port = _allocate_port()
            cbc.create(slug, port)
            store.update_instance(slug, lambda raw: raw.update({
                'id': slug,
                'display_name': display,
                'created_at': store._now(),
                'trashed_at': None,
                'proxy_locked_at': None,
            }))
            task['instance_id'] = slug
            _write_binding(slug, payload)
            generation = store.set_run_intent(slug, False)
        try:
            _boot(task, slug, verify=True)
            store.mark_running(slug, generation)
        finally:
            invalidate_runtime()
    task['result'] = {'instance_id': slug}


def _validate_binding(payload, resolve=False):
    if payload.get('mode') == 'subscription':
        subscription_id, node_id = payload.get('subscription_id'), payload.get('node_id')
        if not any(sub['id'] == subscription_id for sub in store.load_library()['subscriptions']):
            raise ValueError('订阅不存在，请重新选择。')
        node = store.materialize_node(subscription_id, node_id)
        if resolve:
            catalog.pin_endpoint(node)
    elif payload.get('mode') == 'socks':
        port = store.validate_socks(payload.get('host'), payload.get('port'), payload.get('username'), payload.get('password'))
        if resolve:
            cbc.manual_socks({**payload, 'port': port})
    else:
        raise ValueError('创建实例时必须选择订阅节点或填写 SOCKS5。')


def _preflight_boot(slug):
    raw = cbc.settings(slug)
    if raw.get('deleting'):
        raise ValueError('这个实例已开始彻底删除，只能继续清理，不能启动或重启。')
    if raw.get('trashed_at'):
        raise ValueError('实例在回收站中，请先恢复。')
    _require_docker()
    if not cbc.proxy_plan(slug, store.read_proxy(slug)):
        raise ValueError('请先配置代理，再启动实例。')
    cbc.planned_browser(slug, cdp=False)


def _write_binding(slug, payload):
    mode = payload.get('mode')
    if mode == 'subscription':
        store.bind_subscription(slug, payload['subscription_id'], payload['node_id'])
    elif mode == 'socks':
        store.bind_socks(slug, payload['host'], payload['port'], payload['username'], payload['password'])
    else:
        raise ValueError('创建实例时必须选择订阅节点或填写 SOCKS5。')


def _allocate_port():
    used = {row['port'] for row in store.require_complete_inventory()}
    for port in range(5803, 5900):
        if port in used:
            continue
        if cbc._port_free(port):
            return port
    raise ValueError('没有可用的本机端口。')


def _boot(task, slug, verify):
    if cbc.settings(slug).get('trashed_at'):
        raise ValueError('实例在回收站中，请先恢复。')
    _advance(task, '检查环境')
    _require_docker()
    # 镜像或浏览器配置变了才重建。重建前必须正常关闭，让 Cookie 先写盘。
    browser_active = cbc.active(slug)
    recreate_browser = (not browser_active) or cbc.browser_would_change(slug, cdp=False, is_active=browser_active)
    if browser_active and recreate_browser:
        cbc.close_browser(slug)
    try:
        _boot_components(task, slug, verify, recreate_browser)
    except Exception as error:
        cbc.save_start_failure_diagnostics(slug)
        try:
            cbc.cleanup_start_failure(slug, error)
        except Exception as cleanup_error:
            raise RuntimeError(f'{safe_error(error)}；启动收尾未完成：{safe_error(cleanup_error)}') from error
        raise


def _boot_components(task, slug, verify, recreate_browser):
    _advance(task, '启动网络保护和代理')
    cbc.prepare(slug, cdp=False)
    cbc.compose(slug, 'up', '-d', '--wait', '--wait-timeout', '90', 'guard', 'proxy', capture=True)
    if verify:
        _advance(task, '验证代理连接')
        cbc.verify_exit(slug)
    _advance(task, '启动 Chromium')
    if recreate_browser:
        cbc.compose(slug, 'up', '-d', '--wait', '--wait-timeout', '90', 'browser', capture=True)
    _advance(task, '等待 Web UI')
    cbc.wait_until_ready(slug)
    # 第一次启动成功后锁定代理，之后不能再更换节点或 SOCKS5。
    store.lock_proxy(slug)


def _stop_running(slug, task=None):
    if task:
        _advance(task, '关闭浏览器')
    cbc.close_browser(slug)
    if task:
        _advance(task, '停止实例')
    cbc.down(slug)


def _stop_task(task, slug):
    store.set_run_intent(slug, False)
    _stop_running(slug, task)


def _trash(task, row):
    _stop_task(task, row['slug'])
    _advance(task, '移入回收站')
    store.update_instance(row['slug'], lambda raw: raw.update({'trashed_at': store._now()}))


def _restore(task, row):
    current = store.instance_by_id(row['id'])
    if current['raw'].get('deleting'):
        raise ValueError('这个实例已开始彻底删除，不能恢复。请重试彻底删除以完成清理。')
    if not current.get('trashed_at'):
        task['result'] = {'detail': '实例不在回收站，没有改动。'}
        return

    def restore(raw):
        if raw.get('deleting'):
            raise ValueError('这个实例已开始彻底删除，不能恢复。请重试彻底删除以完成清理。')
        if not raw.get('trashed_at'):
            return
        raw.update({'trashed_at': None, 'desired_running': False, 'recovery': {}})

    _advance(task, '恢复实例')
    store.update_instance(current['slug'], restore)


def _purge(task, row, confirm_name):
    _advance(task, '彻底删除')
    if confirm_name != row['display_name']:
        raise ValueError('确认名称不一致，没有删除。')
    if not row.get('trashed_at'):
        raise ValueError('实例不在回收站，没有删除。')
    if cbc.active(row['slug']):
        raise ValueError('实例仍在运行。请先停止，再彻底删除。')
    cbc.down(row['slug'])
    cbc.erase_instance(row['slug'])


def ensure_proxy_editable(row):
    """代理在实例第一次启动成功后锁定；锁定后只能重新确认同名节点。"""
    if store.proxy_locked(row['raw']):
        raise ValueError('这个实例已经启动过，代理已锁定，不能更换节点或 SOCKS5。')


def _apply_proxy(task, row, payload):
    """只给从未启动成功的实例保存代理，下次启动生效，不触碰运行中的容器。"""
    ensure_proxy_editable(row)
    if cbc.active(row['slug']):
        raise ValueError('实例正在运行，请先停止再修改代理。')
    _advance(task, '保存配置')
    _write_binding(row['slug'], payload)
    task['result'] = {'applied': False, 'detail': '已保存，下次启动生效。'}


def reconfirm_node(instance_id):
    row = store.instance_by_id(instance_id)
    with store.instance_operation(row['slug'], blocking=False):
        name = store.reconfirm_same_name(row['slug'])
    return {'ok': True, 'node': name, 'detail': '已重新确认同名节点，下次启动生效。'}


def container_map():
    return runtime_snapshot()['containers']


def runtime_snapshot(force=False):
    """一次 docker ps 同时读取连接状态、容器状态和健康状态，多页面共用两秒缓存。"""
    global _RUNTIME
    with _RUNTIME_GUARD:
        now = time.monotonic()
        if not force and now - _RUNTIME['at'] < 2:
            return _RUNTIME
        snapshot = {'at': now, 'docker': {'ok': False, 'detail': 'Docker 暂时无法连接。'}, 'containers': None, 'health': {}, 'ids': {}}
        try:
            result = cbc.docker('ps', '-a', '--filter', f'label={cbc.LABEL}', '--format', '{{json .}}', check=False, timeout=8)
            if result.returncode == 0:
                found, health, ids = {}, {}, {}
                for line in result.stdout.splitlines():
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    name = item.get('Names', '').lstrip('/')
                    found[name] = item.get('State', '')
                    ids[name] = item.get('ID', '')
                    status = item.get('Status', '')
                    health[name] = 'unhealthy' if '(unhealthy)' in status else 'healthy' if '(healthy)' in status else 'starting' if '(health: starting)' in status else None
                snapshot.update(docker={'ok': True, 'detail': ''}, containers=found, health=health, ids=ids)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            # Docker 异常仍返回实例元数据；不把命令输出带进网页。
            pass
        snapshot['at'] = time.monotonic()
        _RUNTIME = snapshot
        return snapshot


def invalidate_runtime():
    with _RUNTIME_GUARD:
        _RUNTIME['at'] = 0.0
    with _COLLECTION_GUARD:
        _STATS['at'] = 0.0
        _LIMIT_CACHE.clear()


def _runtime_state(slug, containers, health=None):
    if containers is None:
        return 'unknown'
    states = [containers.get(cbc.container(slug, role)) for role in ROLES]
    running = [state == 'running' for state in states]
    if all(running):
        if any((health or {}).get(cbc.container(slug, role)) in ('unhealthy', 'starting') for role in ROLES):
            return 'degraded'
        return 'running'
    if any(running):
        return 'degraded'
    return 'stopped'


def _stats(mapped=None):
    now = time.monotonic()
    if now - _STATS['at'] < 10:
        return _STATS
    if mapped is None:
        mapped = container_map() or {}
    names = [name for name, state in mapped.items() if name.startswith('cbc-') and state == 'running']
    if not names:
        _STATS.update({'at': now, 'rows': {}, 'error': '', 'manager_rss_mib': _self_rss(), **_host_memory()})
        return _STATS
    rows = {}
    error = ''
    try:
        result = cbc.docker('stats', '--no-stream', '--format', '{{json .}}', *names, check=False, timeout=8)
        if result.returncode:
            error = '容器资源暂时读不到'
        else:
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                name = item.get('Name', '').lstrip('/')
                rows[name] = {'cpu': _cpu(item.get('CPUPerc', '')), 'memory_mib': _memory(item.get('MemUsage', ''))}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        error = '容器资源暂时读不到'
    _STATS.update({'at': now, 'rows': rows, 'error': error, 'manager_rss_mib': _self_rss(), **_host_memory()})
    return _STATS


def _cpu(value):
    text = str(value).replace('%', '').strip()
    try:
        return float(text)
    except ValueError:
        return None


def _memory(value):
    left = str(value).split('/')[0].strip()
    match = re.fullmatch(r'([0-9.]+)\s*([KMGTP]i?B)', left)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2)
    scale = {'B': 1 / 1048576, 'KiB': 1 / 1024, 'MiB': 1, 'GiB': 1024, 'TiB': 1024 * 1024, 'KB': 1 / 1024, 'MB': 1, 'GB': 1024}
    factor = scale.get(unit)
    if factor is None:
        return None
    return round(number * factor, 1)


_VM_MARKERS = ('com.docker.virtualization', 'com.apple.Virtualization.VirtualMachine', 'hyperkit', 'qemu-system')


def _host_memory():
    """分开虚拟机和 Docker 辅助进程。读不到就留空，不用别的数字代替。"""
    try:
        output = subprocess.run(['ps', '-axo', 'rss=,comm='], text=True, capture_output=True, check=False).stdout
    except OSError:
        return {'docker_vm_rss_mib': None, 'docker_helper_rss_mib': None}
    vm = 0
    helper = 0
    vm_seen = False
    helper_seen = False
    for line in output.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        rss, command = parts
        try:
            kib = int(rss)
        except ValueError:
            continue
        if any(marker in command for marker in _VM_MARKERS):
            vm += kib
            vm_seen = True
            continue
        if 'com.docker.' in command or '/Docker.app/' in command or command in ('docker', 'docker-compose'):
            helper += kib
            helper_seen = True
    return {
        'docker_vm_rss_mib': round(vm / 1024, 1) if vm_seen else None,
        'docker_helper_rss_mib': round(helper / 1024, 1) if helper_seen else None,
    }


def _compose_limits(slug):
    path = cbc.CONFIGS / slug / 'runtime' / 'compose.env'
    if not path.exists():
        return {}
    keys = {'BROWSER_MEMORY_MIB': 'browser', 'PROXY_MEMORY_MIB': 'proxy', 'GUARD_MEMORY_MIB': 'guard'}
    found = {}
    try:
        lines = path.read_text().splitlines()
    except (OSError, ValueError):
        return {}
    for line in lines:
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        key, value = line.split('=', 1)
        if key in keys and value.isdigit() and int(value) > 0:
            found[keys[key]] = int(value)
    return found


_LIMIT_CACHE = {}


def _refresh_limits(items, snapshot):
    """只批量检查有容器的实例。容器 ID 变化立即更新，其余上限缓存一分钟。"""
    now = time.monotonic()
    needed = []
    for item in items:
        slug = item['slug']
        signature = tuple(snapshot['ids'].get(cbc.container(slug, role)) for role in reversed(ROLES))
        cached = _LIMIT_CACHE.get(slug)
        if not cached or now - cached[0] >= 60 or cached[2] != signature:
            needed.append((slug, signature))
    names = [cbc.container(slug, role) for slug, _signature in needed for role in reversed(ROLES) if cbc.container(slug, role) in (snapshot['containers'] or {})]
    observed = {}
    if names and snapshot['docker']['ok']:
        try:
            result = cbc.docker('inspect', '--format', '{"name":{{json .Name}},"memory":{{.HostConfig.Memory}}}', *names, check=False, timeout=8)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if line.strip():
                        record = json.loads(line)
                        if type(record.get('memory')) is int and record['memory'] > 0:
                            observed[record['name'].lstrip('/')] = record['memory'] // (1024 * 1024)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    for slug, signature in needed:
        limits = _compose_limits(slug)
        for role in reversed(ROLES):
            value = observed.get(cbc.container(slug, role))
            if value is not None:
                limits[role] = value
        _LIMIT_CACHE[slug] = (now, limits, signature)
    known = {item['slug'] for item in items}
    for slug in set(_LIMIT_CACHE) - known:
        _LIMIT_CACHE.pop(slug, None)


def _memory_limits(slug):
    cached = _LIMIT_CACHE.get(slug)
    if cached:
        return cached[1]
    return _compose_limits(slug)


def _instance_usage(slug, stats):
    names = [cbc.container(slug, role) for role in reversed(ROLES)]
    limits = _memory_limits(slug)
    memory = 0.0
    cpu = 0.0
    complete = not stats['error']
    for name in names:
        row = stats['rows'].get(name)
        if not row or row['memory_mib'] is None or row['cpu'] is None:
            complete = False
            break
        memory += row['memory_mib']
        cpu += row['cpu']
    return {
        'memory_mib': round(memory, 1) if complete else None,
        'cpu_percent': round(cpu, 2) if complete else None,
        'memory_limit_mib': sum(limits.values()) if len(limits) == 3 else None,
        'limits': limits,
    }


def list_instances():
    with _COLLECTION_GUARD:
        return _list_instances()


def _list_instances():
    import monitor
    snapshot = runtime_snapshot()
    report, containers = snapshot['docker'], snapshot['containers']
    items, config_errors = store.scan_instances()
    _refresh_limits(items, snapshot)
    stats = _stats(containers or {})
    try:
        exits = store.load_exits()
    except store.SharedRecordError as error:
        exits = {}
        config_errors.append({'id': '出口记录', 'file': 'configs/private/exits.json', 'detail': str(error)})
    rows = []
    for item in items:
        if report['ok']:
            usage = _instance_usage(item['slug'], stats)
        else:
            limits = _memory_limits(item['slug'])
            usage = {'memory_mib': None, 'cpu_percent': None, 'memory_limit_mib': sum(limits.values()) if len(limits) == 3 else None, 'limits': limits}
        exit_row = exits.get(item['slug'])
        if exit_row is not None and not store.exit_record_readable(exit_row):
            config_errors.append({'id': item['id'], 'file': 'configs/private/exits.json', 'detail': '这个实例的出口记录格式不正确，原文件已保留。'})
            exit_row = None
        verified_ip = store.exit_address((exit_row or {}).get('ip'))
        if verified_ip:
            country = geolocation.country_code(verified_ip) or exit_row.get('country_code')
            exit_row = {**exit_row, 'country_code': country}
        try:
            proxy = store.proxy_public(item['slug'])
        except (OSError, ValueError, TypeError, KeyError):
            proxy = {'mode': 'unknown', 'label': '代理配置损坏', 'stale': True}
            config_errors.append({'id': item['id'], 'file': f"configs/{item['slug']}/proxy.json", 'detail': '代理配置无法读取，原文件已保留。'})
        runtime = _runtime_state(item['slug'], containers, snapshot['health'])
        rows.append({
            'id': item['id'],
            'display_name': item['display_name'],
            'created_at': item['created_at'],
            'historical': item['created_at'] is None,
            'trashed_at': item['trashed_at'],
            'deleting': bool((item.get('raw') or {}).get('deleting')),
            'proxy_locked': store.proxy_locked(item.get('raw') or {}),
            'port': item['port'],
            'url': f"http://127.0.0.1:{item['port']}",
            'runtime': runtime,
            'proxy': proxy,
            'exit': exit_row,
            'usage': usage,
            'diagnostics': _diagnostics(item['slug'], containers, snapshot['health']),
            'monitor': monitor.public_status(item, runtime, exit_row, proxy),
        })
    running = sum(1 for row in rows if not row['trashed_at'] and row['runtime'] == 'running')
    visible = [row for row in rows if not row['trashed_at']]
    memory_values = [row['usage']['memory_mib'] for row in visible if row['runtime'] == 'running']
    memory_total = round(sum(memory_values), 1) if memory_values and all(value is not None for value in memory_values) else None
    warnings = _exit_warnings(rows)
    return {
        'docker': report,
        'summary': {
            'total': len(visible),
            'running': running if report['ok'] else None,
            'memory_total_mib': memory_total,
            'docker_vm_rss_mib': stats.get('docker_vm_rss_mib'),
            'docker_helper_rss_mib': stats.get('docker_helper_rss_mib'),
            'manager_rss_mib': stats.get('manager_rss_mib'),
        },
        'instances': visible,
        'trash': [row for row in rows if row['trashed_at']],
        'exit_warnings': warnings,
        'config_errors': config_errors,
        'monitor': monitor.service_status(),
    }


def _exit_warnings(rows):
    groups = {}
    for row in rows:
        exit_row = row.get('exit') or {}
        ip = exit_row.get('ip')
        if exit_row.get('status') == 'ok' and store.exit_address(ip) and not row.get('trashed_at'):
            groups.setdefault(ip, []).append({
                'id': row['id'], 'name': row['display_name'], 'checked_at': exit_row.get('checked_at'),
            })
    return [{'ip': ip, 'instances': items} for ip, items in groups.items() if len(items) > 1]


def _diagnostics(slug, containers, health=None):
    if containers is None:
        return [{'role': role, 'state': 'unknown'} for role in ROLES]
    rows = []
    for role in ROLES:
        name = cbc.container(slug, role)
        state = containers.get(name) or 'absent'
        rows.append({'role': role, 'state': (health or {}).get(name) if state == 'running' and (health or {}).get(name) in ('unhealthy', 'starting') else state})
    return rows


def _self_rss():
    try:
        output = subprocess.run(['ps', '-o', 'rss=', '-p', str(os.getpid())], text=True, capture_output=True, check=False).stdout
        return round(int(output.strip()) / 1024, 1)
    except (OSError, ValueError):
        return None


def rename_instance(instance_id, display_name):
    row = store.instance_by_id(instance_id)
    cleaned = store.validate_display_name(display_name)

    def rename(raw):
        raw['display_name'] = cleaned
        raw.setdefault('id', row['slug'])

    with store.instance_operation(row['slug'], blocking=False):
        store.update_instance(row['slug'], rename)
    return {'id': row['id'], 'display_name': cleaned}


def reload_browser_page(instance_id):
    row = store.instance_by_id(instance_id)
    with store.instance_operation(row['slug'], blocking=False):
        row = store.instance_by_id(instance_id)
        return _reload_browser_page(row)


def _reload_browser_page(row):
    if row['trashed_at']:
        raise ValueError('实例在回收站中，请先恢复。')
    if not cbc.active(row['slug']):
        raise ValueError('实例没有在运行。')
    try:
        result = cbc.docker('exec', '--user', '1000', cbc.container(row['slug'], 'browser'), 'env', 'DISPLAY=:0', 'xdotool', 'search', '--onlyvisible', '--class', 'chromium', 'windowfocus', 'key', 'F5', check=False, timeout=10)
    except subprocess.TimeoutExpired:
        raise RuntimeError('没能刷新容器里的网页。可以改用“重连画面”。') from None
    if result.returncode:
        raise RuntimeError('没能刷新容器里的网页。可以改用“重连画面”。')
    return {'ok': True}


def check_exit(instance_id):
    row = store.instance_by_id(instance_id)
    with store.instance_operation(row['slug'], blocking=False):
        row = store.instance_by_id(instance_id)
        if row['trashed_at'] or _runtime_state(row['slug'], container_map()) != 'running':
            raise ValueError('实例没有在运行，不能检测出口。')
        probed = cbc.probe_proxy(row['slug'])
        return store.save_exit(row['slug'], probed['status'], probed['ip'], probed['detail'], probed.get('country_code'))


def dismiss_exit_change(instance_id, change_id):
    row = store.instance_by_id(instance_id)
    with store.instance_operation(row['slug'], blocking=False):
        row = store.instance_by_id(instance_id)
        return store.dismiss_exit_change(row['slug'], change_id)
