"""订阅库、节点 ID 和实例元数据。不负责启动容器。"""
import datetime
import fcntl
import hashlib
import ipaddress
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
import secrets
import shutil
import threading
import urllib.parse

import catalog

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ROOT / 'configs'
PRIVATE = CONFIGS / 'private'
LIBRARY = PRIVATE / 'library.json'
EXITS = PRIVATE / 'exits.json'
BACKUPS = CONFIGS / 'backups'
INSTANCE_SLUG_PATTERN = r'[a-z][a-z0-9-]{0,31}'


class SharedRecordError(ValueError):
    """共享记录无法安全读取。调用方不得据此改写原文件或拉起实例。"""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


_LOCK_DEPTH = threading.local()
_OPERATIONS = {}
_OPERATIONS_GUARD = threading.Lock()
_OPERATION_DEPTH = threading.local()


@contextmanager
def instance_operation(slug, blocking=True):
    """同一个实例的检测和生命周期操作互斥；不同实例不占用同一把长操作锁。"""
    if not isinstance(slug, str) or not re.fullmatch(INSTANCE_SLUG_PATTERN, slug):
        raise ValueError('实例 ID 无效。')
    with _OPERATIONS_GUARD:
        lock = _OPERATIONS.setdefault(slug, threading.RLock())
    if not lock.acquire(blocking=blocking):
        raise ValueError('实例正在处理其他操作，请稍后重试。')
    held = getattr(_OPERATION_DEPTH, 'held', set())
    try:
        if slug in held:
            yield
            return
        directory = PRIVATE / 'operations'
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(directory / f'{slug}.lock', 'a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError:
                raise ValueError('实例正在处理其他操作，请稍后重试。') from None
            _OPERATION_DEPTH.held = held | {slug}
            try:
                yield
            finally:
                _OPERATION_DEPTH.held = held
                fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        lock.release()


@contextmanager
def exclusive():
    """同一进程的线程和不同进程，共用 configs 的一把锁覆盖完整读—改—写。"""
    depth = getattr(_LOCK_DEPTH, 'depth', 0)
    if depth:
        _LOCK_DEPTH.depth = depth + 1
        try:
            yield
        finally:
            _LOCK_DEPTH.depth -= 1
        return
    PRIVATE.mkdir(parents=True, exist_ok=True)
    PRIVATE.chmod(0o700)
    lock_path = PRIVATE / '.store.lock'
    with open(lock_path, 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        _LOCK_DEPTH.depth = 1
        try:
            yield
        finally:
            _LOCK_DEPTH.depth = 0
            fcntl.flock(handle, fcntl.LOCK_UN)


def _save(path, value):
    payload = (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    _write_private(path, payload)


def _write_private(path, payload):
    path = Path(path)
    with exclusive():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        temp = path.parent / f'.{path.name}.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(4)}.tmp'
        try:
            with open(temp, 'wb', opener=lambda name, flag: os.open(name, flag, 0o600)) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()
        path.chmod(0o600)


def _restore_snapshot(path, content):
    with exclusive():
        if content is None:
            Path(path).unlink(missing_ok=True)
        else:
            _write_private(path, content)


def _read(path, default):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text())


def library_exists():
    return LIBRARY.exists()


def _empty_library():
    return {'version': 1, 'subscriptions': []}


def load_library():
    value = _read(LIBRARY, _empty_library())
    if not isinstance(value, dict) or not isinstance(value.get('subscriptions'), list):
        raise ValueError('订阅库格式不正确。')
    return value


def _node_id(subscription_id, node):
    material = {key: node[key] for key in sorted(node) if key != 'name'}
    raw = json.dumps({'subscription': subscription_id, 'node': material}, sort_keys=True, ensure_ascii=False, default=str)
    return 'n' + hashlib.sha256(raw.encode()).hexdigest()[:20]


def _cache_path(subscription_id):
    """节点缓存文件名只能是一段安全编号，解析后必须仍在 nodes 目录内。"""
    if not isinstance(subscription_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', subscription_id):
        raise ValueError('订阅编号无效。')
    directory = (PRIVATE / 'nodes').resolve()
    path = (directory / f'{subscription_id}.json').resolve()
    if path.parent != directory:
        raise ValueError('订阅编号无效。')
    return path


def load_cached_nodes(subscription_id):
    payload = _read(_cache_path(subscription_id), {'nodes': []})
    nodes = payload.get('nodes') if isinstance(payload, dict) else []
    return nodes if isinstance(nodes, list) else []


_NODE_SUMMARIES = {}
_NODE_SUMMARIES_GUARD = threading.Lock()


def _node_summary(subscription_id):
    """轮询只需要节点数量、编号和最近检测时间，不必每次解析完整节点缓存。

    缓存按文件的 inode、修改时间和大小失效。节点缓存都经 os.replace 整体替换，
    任何写入都会换掉 inode，因此不会读到旧摘要。返回值只读，调用方不得修改。
    """
    path = _cache_path(subscription_id)
    try:
        stat = path.stat()
    except FileNotFoundError:
        with _NODE_SUMMARIES_GUARD:
            _NODE_SUMMARIES.pop(path, None)
        return {'count': 0, 'ids': frozenset(), 'tested_at': None, 'quality_checked_at': None}
    signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
    with _NODE_SUMMARIES_GUARD:
        cached = _NODE_SUMMARIES.get(path)
    if cached and cached[0] == signature:
        return cached[1]
    # 先取文件状态再读内容：若期间文件被替换，下次状态不一致会重新读取，不会长期留下旧摘要。
    nodes = load_cached_nodes(subscription_id)
    summary = {
        'count': len(nodes),
        'ids': frozenset(item.get('id') for item in nodes if isinstance(item, dict)),
        'tested_at': max(
            ((node.get('test') or {}).get('checked_at', '') for node in nodes), default=''
        ) or None,
        'quality_checked_at': max(
            ((node.get('quality') or {}).get('checked_at', '') for node in nodes), default=''
        ) or None,
    }
    with _NODE_SUMMARIES_GUARD:
        _NODE_SUMMARIES[path] = (signature, summary)
    return summary


def _store_nodes(subscription_id, raw_nodes):
    previous = {item['id']: item for item in load_cached_nodes(subscription_id)}
    stored = []
    seen = set()
    for node in raw_nodes:
        supported, reason = catalog.support_of(node)
        identity = _node_id(subscription_id, node)
        if identity in seen:
            continue
        seen.add(identity)
        stored.append({
            'id': identity,
            'name': node['name'],
            'type': node['type'],
            'port': int(node['port']),
            'supported': supported,
            'reason': reason,
            'config': node,
            'test': previous.get(identity, {}).get('test'),
            'quality': previous.get(identity, {}).get('quality'),
        })
    _save(_cache_path(subscription_id), {'nodes': stored})
    return stored


def materialize_node(subscription_id, node_id):
    for item in load_cached_nodes(subscription_id):
        if item.get('id') == node_id:
            if not item.get('supported', True):
                raise ValueError(item.get('reason') or '这个节点不能使用。')
            config = dict(item.get('config') or {})
            if not config:
                raise ValueError('所选节点已消失或配置已变化，请重新选择。')
            return config
    raise ValueError('所选节点已消失或配置已变化，请重新选择。')


def read_instance(path):
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError('实例配置必须是 JSON 对象。')
    if type(data.get('port')) is not int or not 1024 <= data['port'] <= 65535:
        raise ValueError('实例端口缺失或无效。')
    if type(data.get('network_index')) is not int or not 1 <= data['network_index'] <= 254:
        raise ValueError('实例网络编号缺失或无效。')
    for key in ('id', 'display_name'):
        if data.get(key) is not None and not isinstance(data[key], str):
            raise ValueError('实例名称或 ID 格式无效。')
    if 'desired_running' in data and type(data['desired_running']) is not bool:
        raise ValueError('实例运行意图格式无效。')
    if data.get('recovery') is not None and not isinstance(data['recovery'], dict):
        raise ValueError('实例恢复记录格式无效。')
    return data


def scan_instances():
    rows = []
    errors = []
    for path in sorted(CONFIGS.glob('*/instance.json')):
        slug = path.parent.name
        try:
            if not re.fullmatch(INSTANCE_SLUG_PATTERN, slug):
                raise ValueError('实例目录名无效。')
            data = read_instance(path)
        except (OSError, ValueError) as error:
            detail = str(error) if type(error) is ValueError else '实例配置无法读取或 JSON 已损坏。'
            errors.append({'id': slug, 'file': f'configs/{slug}/instance.json', 'detail': detail})
            continue
        rows.append({
            'id': data.get('id') or slug,
            'slug': slug,
            'display_name': data.get('display_name') or slug,
            'created_at': data.get('created_at') if 'created_at' in data else None,
            'trashed_at': data.get('trashed_at') or None,
            'port': data['port'],
            'network_index': data['network_index'],
            'path': path,
            'raw': data,
        })
    return rows, errors


def _instances_on_disk():
    return scan_instances()[0]


def require_complete_inventory():
    rows, errors = scan_instances()
    if errors:
        raise ValueError('有实例配置损坏，无法安全分配端口和子网。请先修复：' + '、'.join(item['id'] for item in errors))
    return rows


def instance_by_id(instance_id):
    rows, errors = scan_instances()
    for row in rows:
        if row['id'] == instance_id or row['slug'] == instance_id:
            return row
    if any(item['id'] == instance_id for item in errors):
        raise ValueError('这个实例的配置已损坏，请先修复，原文件和浏览器数据已保留。')
    raise ValueError('实例不存在。')


def update_instance(slug, mutate):
    """在锁内重新读取 instance.json 再修改，避免覆盖其他线程或进程刚写入的字段。"""
    path = CONFIGS / slug / 'instance.json'
    with exclusive():
        if not path.exists():
            raise ValueError('实例不存在。')
        raw = json.loads(path.read_text())
        mutate(raw)
        _save(path, raw)
        return raw


def proxy_locked(raw):
    """实例第一次启动成功后锁定代理，防止同一个浏览器 Profile 换出口。"""
    return bool(raw.get('proxy_locked_at'))


def lock_proxy(slug):
    def mark(raw):
        if not raw.get('proxy_locked_at'):
            raw['proxy_locked_at'] = _now()

    update_instance(slug, mark)


def _has_profile_data(slug):
    """浏览器运行过就会在数据目录写入 Profile。"""
    directory = ROOT / 'data' / slug
    return directory.is_dir() and any(directory.iterdir())


def selection_map():
    path = CONFIGS / 'selection.json'
    if not path.exists():
        return {}
    value = _read(path, {})
    return value if isinstance(value, dict) else {}


def _write_selection(value):
    _save(CONFIGS / 'selection.json', value)


def _proxy_path(slug):
    return CONFIGS / slug / 'proxy.json'


def _empty_proxy():
    return {'host': '', 'port': 1080, 'username': '', 'password': '', 'expected_ip': ''}


def read_proxy(slug):
    path = _proxy_path(slug)
    if not path.exists():
        return _empty_proxy()
    path.chmod(0o600)
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('代理配置必须是 JSON 对象。')
    return value


def _write_proxy(slug, proxy):
    _save(_proxy_path(slug), proxy)


def proxy_public(slug):
    proxy = read_proxy(slug)
    manual = bool(proxy.get('host'))
    record = selection_map().get(slug) or {}
    if manual:
        return {
            'mode': 'socks',
            'label': '手动 SOCKS5',
            'host': proxy.get('host', ''),
            'port': proxy.get('port', 1080),
            'username': proxy.get('username', ''),
            'password_set': bool(proxy.get('password')),
            'stale': False,
        }
    node_id = record.get('node_id') if isinstance(record, dict) else ''
    subscription_id = record.get('subscription_id') if isinstance(record, dict) else ''
    label = record.get('node', '') if isinstance(record, dict) else (record if isinstance(record, str) else '')
    stale = bool(isinstance(record, dict) and record.get('needs_reconfirm'))
    subscription_name = ''
    if subscription_id:
        for sub in load_library()['subscriptions']:
            if sub['id'] == subscription_id:
                subscription_name = sub.get('name', '')
                break
        if node_id and node_id not in _node_summary(subscription_id)['ids']:
            stale = True
            label = label or '节点需要重新确认'
    if not manual and not label and not node_id:
        return {'mode': 'none', 'label': '未配置', 'stale': False, 'password_set': False}
    return {
        'mode': 'subscription',
        'label': label,
        'node_id': node_id or '',
        'subscription_id': subscription_id or '',
        'subscription_name': subscription_name,
        'stale': stale,
        'password_set': False,
    }


def node_users():
    users = {}
    selection = selection_map()
    for row in _instances_on_disk():
        record = selection.get(row['slug']) or {}
        node_id = record.get('node_id') if isinstance(record, dict) else ''
        if not node_id:
            continue
        users.setdefault(node_id, []).append({'id': row['id'], 'name': row['display_name'], 'trashed': bool(row['trashed_at'])})
    return users


def public_nodes(subscription_id):
    users = node_users()
    rows = []
    for item in load_cached_nodes(subscription_id):
        rows.append({
            'id': item['id'],
            'name': item['name'],
            'type': item['type'],
            'port': item['port'],
            'supported': item.get('supported', True),
            'reason': item.get('reason', ''),
            'used_by': users.get(item['id'], []),
            'test': item.get('test'),
            'quality': item.get('quality'),
        })
    return rows


def public_subscriptions():
    rows = []
    for sub in load_library()['subscriptions']:
        summary = _node_summary(sub['id'])
        rows.append({
            'id': sub['id'],
            'name': sub.get('name', ''),
            'node_count': summary['count'],
            'nodes_tested_at': summary['tested_at'],
            'nodes_quality_checked_at': summary['quality_checked_at'],
            'updated_at': sub.get('updated_at'),
            'last_error': sub.get('last_error', ''),
        })
    return rows


def save_node_tests(subscription_id, results):
    """只更新仍存在的相同配置节点；并发刷新、删除订阅时不恢复旧节点。"""
    return _save_node_results(subscription_id, results, 'test')


def save_node_quality(subscription_id, results):
    return _save_node_results(subscription_id, results, 'quality')


def _save_node_results(subscription_id, results, field):
    with exclusive():
        if not any(sub['id'] == subscription_id for sub in load_library()['subscriptions']):
            return 0
        nodes = load_cached_nodes(subscription_id)
        # 前端以此时间戳判断节点缓存版本，同秒内的批次也必须能区分。
        checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        saved = 0
        for node in nodes:
            if node['id'] in results:
                node[field] = {**results[node['id']], 'checked_at': checked_at}
                saved += 1
        if saved:
            _save(_cache_path(subscription_id), {'nodes': nodes})
        return saved


def _backup(paths):
    """复制修改前的原件。按 configs 内的相对路径分开放，避免多个 instance.json 互相覆盖。"""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    directory = BACKUPS / f'migrate-{stamp}'
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    root = CONFIGS.resolve()
    for path in paths:
        path = Path(path)
        if not path.exists():
            continue
        relative = path.resolve().relative_to(root)
        if any(part in ('..', '') for part in relative.parts):
            raise ValueError('备份路径不正确。')
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        target.chmod(0o600)
    return directory


def _migrate_read_permissions():
    paths = [LIBRARY, EXITS, PRIVATE / 'tasks.json', CONFIGS / 'selection.json',
             CONFIGS / 'subscription.json', CONFIGS / 'nodes.json']
    paths.extend((PRIVATE / 'nodes').glob('*.json'))
    for path in paths:
        if path.exists():
            path.chmod(0o600)


def migrate():
    """登记已有实例和单订阅缓存。先备份原件，不启动、不重启容器。"""
    with exclusive():
        PRIVATE.mkdir(parents=True, exist_ok=True)
        PRIVATE.chmod(0o700)
        _migrate_read_permissions()
        updates = []
        for row in _instances_on_disk():
            raw = dict(row['raw'])
            changed = False
            if 'id' not in raw:
                raw['id'] = row['slug']
                changed = True
            if 'display_name' not in raw:
                raw['display_name'] = row['slug']
                changed = True
            if 'created_at' not in raw:
                raw['created_at'] = None
                changed = True
            if 'proxy_locked_at' not in raw:
                # 历史实例或已经写过 Profile 的实例视为启动过，直接锁定。
                started = raw.get('created_at') is None or _has_profile_data(row['slug'])
                raw['proxy_locked_at'] = _now() if started else None
                changed = True
            if changed:
                updates.append((row['path'], raw))
        subscription = CONFIGS / 'subscription.json'
        nodes = CONFIGS / 'nodes.json'
        import_legacy = not library_exists() and subscription.exists()
        originals = [path for path, _raw in updates]
        if import_legacy:
            originals.append(subscription)
            if nodes.exists():
                originals.append(nodes)
        if originals:
            _backup(originals)
        for path, raw in updates:
            _save(path, raw)
        if not import_legacy:
            return
        url = json.loads(subscription.read_text()).get('url', '')
        if isinstance(url, str) and url.startswith('https://'):
            subscription_id = _subscription_id(url)
            raw_nodes = []
            if nodes.exists():
                loaded = json.loads(nodes.read_text())
                if isinstance(loaded, list):
                    raw_nodes = loaded
            library = _empty_library()
            library['subscriptions'].append({
                'id': subscription_id,
                'name': '已有订阅',
                'url': url,
                'updated_at': _now() if raw_nodes else None,
                'last_error': '',
            })
            _save(LIBRARY, library)
            if raw_nodes:
                _store_nodes(subscription_id, raw_nodes)
            _attach_legacy_selection(subscription_id)


def _attach_legacy_selection(subscription_id):
    selection = selection_map()
    cached = load_cached_nodes(subscription_id)
    changed = False
    for slug, record in list(selection.items()):
        if isinstance(record, dict) and record.get('node_id'):
            continue
        label = record.get('node') if isinstance(record, dict) else record
        if not isinstance(label, str) or not label:
            continue
        matches = [item for item in cached if item['name'] == label]
        if len(matches) != 1:
            selection[slug] = {'mode': 'global', 'node': label, 'needs_reconfirm': True}
        else:
            selection[slug] = {
                'mode': 'global',
                'node': matches[0]['name'],
                'node_id': matches[0]['id'],
                'subscription_id': subscription_id,
            }
        changed = True
    if changed:
        path = CONFIGS / 'selection.json'
        if path.exists():
            _backup([path])
        _write_selection(selection)


def _subscription_id(url):
    return 'sub' + hashlib.sha256(url.encode()).hexdigest()[:12]


def validate_subscription_name(name):
    name = name.strip() if isinstance(name, str) else ''
    if not name or len(name) > 40:
        raise ValueError('订阅名称需要 1–40 个字符。')
    return name


def validate_subscription(name, url):
    name = validate_subscription_name(name)
    url = url.strip() if isinstance(url, str) else ''
    try:
        parsed = urllib.parse.urlsplit(url)
        valid = parsed.scheme == 'https' and bool(parsed.hostname)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('请填写有效的 HTTPS 订阅地址。')
    return name, url


def add_subscription(name, url, fetcher):
    name, url = validate_subscription(name, url)
    try:
        nodes = fetcher(url)
    except catalog.CatalogError as error:
        raise ValueError(str(error)) from error
    subscription_id = _subscription_id(url)
    with exclusive():
        library = load_library() if library_exists() else _empty_library()
        existing = next((item for item in library['subscriptions'] if item.get('url') == url), None)
        if existing:
            subscription_id = existing['id']
        else:
            used_ids = {item['id'] for item in library['subscriptions']}
            # 改地址会保留旧编号；重新添加旧地址不能覆盖已经迁移走的订阅。
            while subscription_id in used_ids:
                subscription_id = 'sub' + secrets.token_hex(6)
        # 节点缓存没写成功时，库里会留下一个零节点订阅。同地址再次添加应把这一次补完。
        if existing and (_subscription_in_use(subscription_id) or load_cached_nodes(subscription_id)):
            raise ValueError('这个订阅已经添加过。')
        if existing:
            existing.update(name=name, url=url, updated_at=_now(), last_error='')
        else:
            library['subscriptions'].append({
                'id': subscription_id, 'name': name, 'url': url, 'updated_at': _now(), 'last_error': '',
            })
        _save(LIBRARY, library)
        try:
            _store_nodes(subscription_id, nodes)
        except Exception:
            if not existing:
                library['subscriptions'] = [item for item in library['subscriptions'] if item['id'] != subscription_id]
            try:
                _save(LIBRARY, library)
                cache = _cache_path(subscription_id)
                if cache.exists():
                    cache.unlink()
            except Exception as rollback_error:
                raise RuntimeError('订阅没有添加完整，而且自动撤销失败。请检查订阅列表后再试。') from rollback_error
            raise
    return subscription_id


def _subscription_in_use(subscription_id):
    return any(
        isinstance(record, dict) and record.get('subscription_id') == subscription_id
        for record in selection_map().values()
    )


def replace_subscription_url(subscription_id, url, fetcher, name=None):
    """更换订阅地址但保留订阅编号。仍能对上的节点绑定不动，消失的节点标记为需要重新确认。"""
    library = load_library()
    current = next((item for item in library['subscriptions'] if item['id'] == subscription_id), None)
    if not current:
        raise ValueError('订阅不存在。')
    cleaned_name, url = validate_subscription(name if name is not None else current.get('name') or '订阅', url)
    if url == current.get('url'):
        if name is None:
            raise ValueError('订阅地址没有变化。')
        return rename_subscription(subscription_id, cleaned_name)
    if any(item['id'] != subscription_id and item.get('url') == url for item in library['subscriptions']):
        raise ValueError('这个地址已经属于另一个订阅。')
    try:
        nodes = fetcher(url)
    except catalog.CatalogError as error:
        raise ValueError(str(error)) from error
    with exclusive():
        library = load_library()
        target = next((item for item in library['subscriptions'] if item['id'] == subscription_id), None)
        if not target:
            raise ValueError('订阅不存在。')
        if target.get('url') != current.get('url') or (name is not None and target.get('name') != current.get('name')):
            raise ValueError('订阅已被其他操作更新，请刷新后重试。')
        if any(item['id'] != subscription_id and item.get('url') == url for item in library['subscriptions']):
            raise ValueError('这个地址已经属于另一个订阅。')
        previous = load_cached_nodes(subscription_id)
        paths = (CONFIGS / 'selection.json', _cache_path(subscription_id), LIBRARY)
        snapshots = {path: path.read_bytes() if path.exists() else None for path in paths}
        target['url'] = url
        if name is not None:
            target['name'] = cleaned_name
        target['updated_at'] = _now()
        target['last_error'] = ''
        try:
            _store_nodes(subscription_id, nodes)
            _mark_missing_bindings(subscription_id, previous)
            _save(LIBRARY, library)
        except Exception:
            try:
                for path, content in snapshots.items():
                    _restore_snapshot(path, content)
            except OSError as error:
                raise RuntimeError('订阅更新失败，原配置恢复未完成，请检查本机存储后重试。') from error
            raise
        return {'id': subscription_id, 'name': target.get('name', ''), 'node_count': len(load_cached_nodes(subscription_id))}


def rename_subscription(subscription_id, name):
    cleaned = validate_subscription_name(name)
    with exclusive():
        library = load_library()
        target = next((item for item in library['subscriptions'] if item['id'] == subscription_id), None)
        if not target:
            raise ValueError('订阅不存在。')
        if target.get('name') != cleaned:
            target['name'] = cleaned
            _save(LIBRARY, library)
    return {'id': subscription_id, 'name': cleaned}


def refresh_subscription(subscription_id, fetcher):
    library = load_library()
    sub = next((item for item in library['subscriptions'] if item['id'] == subscription_id), None)
    if not sub:
        raise ValueError('订阅不存在。')
    previous = load_cached_nodes(subscription_id)
    try:
        nodes = fetcher(sub['url'])
    except catalog.CatalogError as error:
        with exclusive():
            current = load_library()
            target = next((item for item in current['subscriptions'] if item['id'] == subscription_id), None)
            if target:
                target['last_error'] = '订阅刷新失败，已保留上次的节点缓存。'
                _save(LIBRARY, current)
        raise ValueError('订阅刷新失败，已保留上次的节点缓存。') from error
    with exclusive():
        current = load_library()
        target = next((item for item in current['subscriptions'] if item['id'] == subscription_id), None)
        if not target:
            raise ValueError('订阅不存在。')
        _store_nodes(subscription_id, nodes)
        target['updated_at'] = _now()
        target['last_error'] = ''
        _save(LIBRARY, current)
        _mark_missing_bindings(subscription_id, previous)
    return len(nodes)


def _mark_missing_bindings(subscription_id, previous):
    current = {item['id']: item for item in load_cached_nodes(subscription_id)}
    selection = selection_map()
    changed = False
    for _slug, record in list(selection.items()):
        if not isinstance(record, dict) or record.get('subscription_id') != subscription_id:
            continue
        node = current.get(record.get('node_id'))
        if node and record.get('node') != node['name']:
            # 相同连接配置的稳定 ID 证明是原节点，名称同步后才能继续同名确认。
            record['node'] = node['name']
            changed = True
        elif record.get('node_id') and node is None:
            record['needs_reconfirm'] = True
            changed = True
    if changed:
        _write_selection(selection)


def delete_subscription(subscription_id):
    with exclusive():
        names = {row['slug']: row['display_name'] for row in _instances_on_disk()}
        # 绑定独立于 instance.json 保存，损坏或缺少实例登记也不能绕过删除保护。
        users = [names.get(slug, slug) for slug, record in selection_map().items()
                 if isinstance(record, dict) and record.get('subscription_id') == subscription_id]
        if users:
            raise ValueError('还有实例使用这个订阅：' + '、'.join(users) + '。请先修复相关配置或彻底删除对应实例。')
        library = load_library()
        library['subscriptions'] = [item for item in library['subscriptions'] if item['id'] != subscription_id]
        _save(LIBRARY, library)
        path = _cache_path(subscription_id)
        if path.exists():
            path.unlink()


def bind_subscription(slug, subscription_id, node_id):
    with exclusive():
        if not any(item['id'] == subscription_id for item in (load_library()['subscriptions'] if library_exists() else [])):
            raise ValueError('订阅不存在，请重新选择。')
        node = materialize_node(subscription_id, node_id)
        selection = selection_map()
        selection[slug] = {'mode': 'global', 'node': node['name'], 'node_id': node_id, 'subscription_id': subscription_id}
        _write_selection(selection)
        _write_proxy(slug, _empty_proxy())
    return node['name']


def bind_socks(slug, host, port, username, password):
    port = validate_socks(host, port, username, password)
    with exclusive():
        _write_proxy(slug, {'host': host, 'port': port, 'username': username, 'password': password, 'expected_ip': ''})
        selection = selection_map()
        if slug in selection:
            selection.pop(slug)
            _write_selection(selection)


def validate_socks(host, port, username, password):
    if not all(isinstance(value, str) and value for value in (host, username, password)):
        raise ValueError('SOCKS5 的主机、用户名和密码都要填写。')
    if any(char.isspace() for char in host) or any(char in host for char in '/\\@:#'):
        raise ValueError('SOCKS5 主机只填写域名或 IPv4，不含协议和端口。')
    if type(port) not in (str, int) or (isinstance(port, str) and not port.isascii()) or not str(port).isdigit():
        raise ValueError('代理端口无效。')
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError('代理端口无效。')
    if any(len(value.encode()) > 255 for value in (username, password)):
        raise ValueError('SOCKS5 用户名、密码不得超过 255 字节。')
    return port


def load_exits():
    if not EXITS.exists():
        return {}
    try:
        EXITS.chmod(0o600)
    except OSError:
        pass
    try:
        value = json.loads(EXITS.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SharedRecordError('出口记录无法读取，原文件已保留。') from error
    if not isinstance(value, dict):
        raise SharedRecordError('出口记录格式不正确，原文件已保留。')
    for slug, record in value.items():
        if not exit_record_readable(record) or record.get('change_id') or not record.get('changed_at'):
            continue
        # 旧版只保留最近一次变化；沿用原时间和 IP，不补造更早的历史。
        latest = record.get('last_success') if isinstance(record.get('last_success'), dict) else record
        previous_ip = record.get('previous_ip')
        ip = latest.get('ip')
        changed_at = record.get('changed_at')
        if isinstance(previous_ip, str) and isinstance(ip, str) and ip and isinstance(changed_at, str):
            source = f"{slug}|{changed_at}|{previous_ip}|{ip}"
            change_id = hashlib.sha256(source.encode()).hexdigest()[:24]
            record.update(change_id=change_id, history=[{
                'id': change_id, 'previous_ip': previous_ip, 'ip': ip, 'changed_at': changed_at,
            }])
    return value


def exit_address(value):
    """出口 IP 只能是文本地址。列表、对象和数字不能作为缓存参数或分组键。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def _exit_ip_ok(value):
    return value is None or value == '' or exit_address(value) is not None


def _exit_ips_ok(record):
    if not isinstance(record, dict):
        return False
    return _exit_ip_ok(record.get('ip')) and _exit_ip_ok(record.get('previous_ip'))


def exit_record_readable(record):
    """嵌套字段或 IP 类型不对时，调用方应跳过这条记录，不能把它当成可迁移数据。"""
    if not _exit_ips_ok(record):
        return False
    last_success = record.get('last_success')
    if last_success is not None and not _exit_ips_ok(last_success):
        return False
    history = record.get('history')
    if history is None:
        return True
    return isinstance(history, list) and all(_exit_ips_ok(item) for item in history)


def _keep_damaged_copy(path, *, move=False):
    """保留损坏原件；任务登记需移走，出口记录则复制后继续正常写入。"""
    if not path.exists():
        return
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    damaged = path.with_name(f'{path.name}.{stamp}.damaged')
    if damaged.exists():
        damaged = path.with_name(f'{path.name}.{stamp}.{secrets.token_hex(2)}.damaged')
    if move:
        path.replace(damaged)
    else:
        shutil.copy2(path, damaged)
    try:
        damaged.chmod(0o600)
    except OSError:
        if not move:
            raise


def save_exit(slug, status, ip, detail, country_code=None):
    if not _exit_ip_ok(ip):
        raise ValueError('出口 IP 无效。')
    with exclusive():
        exits = load_exits()
        previous = exits.get(slug)
        if not isinstance(previous, dict) or not exit_record_readable(previous):
            # 坏掉的 IP、last_success 或 history 不能参与这次写入，否则检测和监测都会中断。
            if previous is not None:
                _keep_damaged_copy(EXITS)
            previous = {}
        now = _now()
        last_success = previous.get('last_success')
        if not isinstance(last_success, dict):
            last_success = None
        if not last_success and previous.get('status') == 'ok' and previous.get('ip'):
            last_success = {key: previous.get(key) for key in ('ip', 'country_code', 'checked_at')}
        record = {'status': status, 'ip': ip, 'country_code': country_code if ip else None, 'detail': detail, 'checked_at': now}
        for key in ('previous_ip', 'changed_at', 'change_id', 'dismissed_change_id', 'dismissed_at'):
            if key in previous:
                record[key] = previous[key]
        history_value = previous.get('history')
        history = list(history_value) if isinstance(history_value, list) else []
        if status == 'ok' and ip:
            if isinstance(last_success, dict) and last_success.get('ip') and last_success['ip'] != ip:
                change_id = secrets.token_hex(12)
                record.update(previous_ip=last_success['ip'], changed_at=now, change_id=change_id)
                history.append({'id': change_id, 'previous_ip': last_success['ip'], 'ip': ip, 'changed_at': now})
            last_success = {'ip': ip, 'country_code': country_code, 'checked_at': now}
        record['history'] = history
        if last_success:
            record['last_success'] = last_success
        exits[slug] = record
        _save(EXITS, exits)
        return exits[slug]


def dismiss_exit_change(slug, change_id):
    with exclusive():
        exits = load_exits()
        record = exits.get(slug) or {}
        if not isinstance(change_id, str) or not change_id or record.get('change_id') != change_id:
            raise ValueError('出口 IP 记录已更新，请查看最新提醒后再关闭。')
        record.update(dismissed_change_id=change_id, dismissed_at=_now())
        exits[slug] = record
        _save(EXITS, exits)
        return record


def set_run_intent(slug, running):
    """手动操作产生新代次，使已经排队的旧恢复任务失效。"""
    generation = secrets.token_hex(8)
    def change(raw):
        raw.update(desired_running=running, run_generation=generation, recovery={})
    update_instance(slug, change)
    return generation


def mark_running(slug, generation):
    """启动成功后才记为应当运行；启动期间若有新的手动操作（代次已变），以新操作为准。"""
    def mark(raw):
        if raw.get('run_generation') == generation:
            raw['desired_running'] = True

    update_instance(slug, mark)


def reconfirm_same_name(slug):
    """锁定后只允许在原订阅里重新确认同名节点，例如服务商更换了节点密码。"""
    with exclusive():
        record = selection_map().get(slug)
        if not isinstance(record, dict) or not record.get('subscription_id') or not record.get('node'):
            raise ValueError('这个实例没有绑定订阅节点，不能重新确认。')
        subscription_id = record['subscription_id']
        cached = load_cached_nodes(subscription_id)
        if record.get('node_id') and not record.get('needs_reconfirm'):
            if any(item.get('id') == record['node_id'] for item in cached):
                raise ValueError('节点没有变化，不需要重新确认。')
        matches = [item for item in cached if item.get('name') == record['node']]
        if not matches:
            raise ValueError('原订阅里已经没有同名节点。代理已锁定，只能新建实例。')
        if len(matches) > 1:
            raise ValueError('原订阅里有多个同名节点，无法确定是哪一个。')
        return bind_subscription(slug, subscription_id, matches[0]['id'])


def forget_instance(slug):
    """彻底删除时清理共享文件里的绑定和出口记录。"""
    with exclusive():
        selection = selection_map()
        if slug in selection:
            selection.pop(slug)
            _write_selection(selection)
        try:
            exits = load_exits()
        except SharedRecordError:
            return
        if slug in exits:
            exits.pop(slug)
            _save(EXITS, exits)


def validate_display_name(name):
    if not isinstance(name, str):
        raise ValueError('请填写实例名称。')
    cleaned = name.strip()
    if not cleaned or len(cleaned) > 40 or any(char in cleaned for char in '/\\\n\r\x00'):
        raise ValueError('实例名称需要 1–40 个字符，且不能包含斜杠。')
    return cleaned
