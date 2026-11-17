"""本机实例管理台。只监听 127.0.0.1。"""

import json
import sys
from pathlib import Path, PurePosixPath

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import manager_core
import store
import cbc
import monitor
import node_probe

HOSTS = {'127.0.0.1:5790', 'localhost:5790'}
ORIGINS = {'http://127.0.0.1:5790', 'http://localhost:5790'}
DIST = ROOT / 'frontend' / 'dist'
NODE_PROBE_BODY_BYTES = 256 * 1024


@asynccontextmanager
async def lifespan(_app):
    store.migrate()
    manager_core.ensure_worker()
    monitor.start()
    try:
        yield
    finally:
        monitor.stop()


app = FastAPI(title='实例管理台', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


class RequestBodyError(Exception):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


@app.exception_handler(RequestValidationError)
async def hide_validation_error(_request, _error):
    return JSONResponse({'error': '请求参数不正确。'}, status_code=422)


@app.exception_handler(RequestBodyError)
async def hide_body_error(_request, error):
    return JSONResponse({'error': str(error)}, status_code=error.status)


@app.exception_handler(ValueError)
async def hide_value_error(_request, error):
    message = await run_in_threadpool(manager_core.safe_error, error)
    return JSONResponse({'error': message}, status_code=400)


@app.middleware('http')
async def local_only(request: Request, call_next):
    host = request.headers.get('host', '')
    if host not in HOSTS:
        return JSONResponse({'error': '只接受本机访问。'}, status_code=403)
    if request.method != 'GET':
        if request.headers.get('x-cbc-request') != '1':
            return JSONResponse({'error': '拒绝跨站请求。'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin not in ORIGINS:
            return JSONResponse({'error': '拒绝跨站请求。'}, status_code=403)
    return await call_next(request)


@app.get('/api/instances')
def instances():
    return manager_core.list_instances()


@app.get('/api/settings')
def settings():
    """仅公开界面所需的资源配置，不读取代理或订阅凭据。"""
    resources = cbc.resource_env()
    lock_path = ROOT / 'images.lock.json'
    images = cbc.read_json(lock_path) if lock_path.exists() else {}
    return {
        'version': '1.0.0',
        'resources': {
            'browser_memory_mib': float(resources['BROWSER_MEMORY_MIB']),
            'browser_cpus': float(resources['BROWSER_CPUS']),
            'proxy_memory_mib': float(resources['PROXY_MEMORY_MIB']),
            'guard_memory_mib': float(resources['GUARD_MEMORY_MIB']),
        },
        'images': [images[key]['tag'] for key in ('browser', 'network') if images.get(key, {}).get('tag')],
        'data_directory': str(ROOT / 'data'),
        'config_directory': str(ROOT / 'configs'),
    }


@app.get('/api/tasks')
def tasks():
    return {'tasks': manager_core.list_tasks()}


@app.get('/api/tasks/{task_id}')
def task(task_id: str):
    try:
        return manager_core.get_task(task_id)
    except ValueError as error:
        return JSONResponse({'error': manager_core.safe_error(error)}, status_code=404)


@app.get('/api/subscriptions')
def subscriptions():
    return {'subscriptions': store.public_subscriptions()}


@app.get('/api/subscriptions/{subscription_id}/nodes')
def nodes(subscription_id: str):
    return {'nodes': store.public_nodes(subscription_id)}


@app.post('/api/subscriptions/{subscription_id}/nodes/test')
async def test_nodes(subscription_id: str, request: Request):
    body = await _body(request, max_bytes=NODE_PROBE_BODY_BYTES)

    def operation():
        selected = node_probe.select_nodes(subscription_id, body.get('node_ids'))
        return manager_core.enqueue(
            'test-nodes',
            payload={
                'subscription_id': subscription_id,
                'node_ids': [node['id'] for node in selected],
            },
        )

    return await run_in_threadpool(operation)


@app.post('/api/subscriptions/{subscription_id}/nodes/quality')
async def check_node_quality(subscription_id: str, request: Request):
    body = await _body(request, max_bytes=NODE_PROBE_BODY_BYTES)

    def operation():
        selected = node_probe.select_nodes(subscription_id, body.get('node_ids'))
        return manager_core.enqueue(
            'check-node-quality',
            payload={
                'subscription_id': subscription_id,
                'node_ids': [node['id'] for node in selected],
            },
        )

    return await run_in_threadpool(operation)


@app.post('/api/subscriptions')
async def add_subscription(request: Request):
    body = await _body(request)

    def operation():
        name, url = store.validate_subscription(body.get('name'), body.get('url'))
        return manager_core.enqueue(
            'add-subscription', request_id=str(body.get('request_id', '')), payload={'name': name, 'url': url}
        )

    return await run_in_threadpool(operation)


@app.post('/api/subscriptions/{subscription_id}/refresh')
def refresh_subscription(subscription_id: str):
    task = manager_core.enqueue('refresh', request_id='', payload={'subscription_id': subscription_id})
    return task


@app.patch('/api/subscriptions/{subscription_id}')
async def rename_subscription(subscription_id: str, request: Request):
    body = await _body(request)

    def operation():
        updated = None
        if 'name' in body:
            store.validate_subscription_name(body.get('name'))
        if 'url' in body:
            current = next((sub for sub in store.load_library()['subscriptions'] if sub['id'] == subscription_id), None)
            if not current:
                raise ValueError('订阅不存在。')
            _name, url = store.validate_subscription(body.get('name', current.get('name')), body.get('url'))
            return manager_core.enqueue(
                'update-subscription',
                payload={
                    'subscription_id': subscription_id,
                    'url': url,
                    **({'name': body['name']} if 'name' in body else {}),
                },
            )
        if 'name' in body:
            updated = store.rename_subscription(subscription_id, body.get('name'))
        if updated is None:
            raise ValueError('请填写新的名称或订阅地址。')
        return updated

    return await run_in_threadpool(operation)


@app.post('/api/tasks/{task_id}/cancel')
def cancel_task(task_id: str):
    return manager_core.cancel_task(task_id)


@app.delete('/api/subscriptions/{subscription_id}')
def delete_subscription(subscription_id: str):
    store.delete_subscription(subscription_id)
    return {'ok': True}


@app.post('/api/instances')
async def create_instance(request: Request):
    body = await _body(request)

    def operation():
        return manager_core.enqueue(
            'create',
            request_id=str(body.get('request_id', '')),
            payload=_proxy_payload(body) | {'display_name': body.get('display_name', '')},
        )

    return await run_in_threadpool(operation)


@app.post('/api/instances/{instance_id}/start')
def start_instance(instance_id: str):
    return _simple('start', instance_id)


@app.post('/api/instances/{instance_id}/stop')
def stop_instance(instance_id: str):
    return _simple('stop', instance_id)


@app.post('/api/instances/{instance_id}/restart')
def restart_instance(instance_id: str):
    return _simple('restart', instance_id)


@app.post('/api/instances/{instance_id}/trash')
def trash_instance(instance_id: str):
    return _simple('trash', instance_id)


@app.post('/api/instances/{instance_id}/restore')
def restore_instance(instance_id: str):
    return _simple('restore', instance_id)


@app.post('/api/instances/{instance_id}/purge')
async def purge_instance(instance_id: str, request: Request):
    body = await _body(request)

    def operation():
        return manager_core.enqueue('purge', instance_id, payload={'confirm_name': body.get('confirm_name', '')})

    return await run_in_threadpool(operation)


@app.post('/api/instances/{instance_id}/proxy/reconfirm')
def reconfirm_proxy(instance_id: str):
    return manager_core.reconfirm_node(instance_id)


@app.post('/api/instances/{instance_id}/proxy')
async def apply_proxy(instance_id: str, request: Request):
    body = await _body(request)

    def operation():
        row = store.instance_by_id(instance_id)
        manager_core.ensure_proxy_editable(row)
        payload = _proxy_payload(body)
        if payload['mode'] == 'socks' and not payload['password']:
            current = store.read_proxy(row['slug'])
            if not current.get('password'):
                raise ValueError('请填写 SOCKS5 密码。')
            payload['password'] = current['password']
        return manager_core.enqueue('apply-proxy', instance_id, payload=payload)

    return await run_in_threadpool(operation)


@app.patch('/api/instances/{instance_id}')
async def rename_instance(instance_id: str, request: Request):
    body = await _body(request)

    def operation():
        return manager_core.rename_instance(instance_id, body.get('display_name', ''))

    return await run_in_threadpool(operation)


@app.post('/api/instances/{instance_id}/exit')
def exit_ip(instance_id: str):
    return manager_core.check_exit(instance_id)


@app.post('/api/instances/{instance_id}/reload-page')
def reload_page(instance_id: str):
    try:
        return manager_core.reload_browser_page(instance_id)
    except RuntimeError as error:
        return JSONResponse({'error': manager_core.safe_error(error)}, status_code=400)


@app.post('/api/instances/{instance_id}/exit/dismiss')
async def dismiss_exit_change(instance_id: str, request: Request):
    body = await _body(request)

    def operation():
        return manager_core.dismiss_exit_change(instance_id, body.get('change_id'))

    return await run_in_threadpool(operation)


def _simple(kind, instance_id):
    store.instance_by_id(instance_id)
    return manager_core.enqueue(kind, instance_id)


def _proxy_payload(body):
    mode = body.get('mode')
    if mode == 'subscription':
        if not body.get('subscription_id') or not body.get('node_id'):
            raise ValueError('请选择订阅和节点。')
        return {'mode': 'subscription', 'subscription_id': body['subscription_id'], 'node_id': body['node_id']}
    if mode == 'socks':
        return {
            'mode': 'socks',
            'host': str(body.get('host', '')),
            'port': body.get('port', 1080),
            'username': str(body.get('username', '')),
            'password': str(body.get('password', '')),
        }
    raise ValueError('请选择一种代理方式。')


async def _body(request: Request, max_bytes: int = 16384):
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise RequestBodyError('请求过大。', 413)
        chunks.append(chunk)
    raw = b''.join(chunks)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RequestBodyError('请求格式不正确。', 400) from error
    if not isinstance(value, dict):
        raise RequestBodyError('请求格式不正确。', 400)
    return value


def _static_target(path: str):
    """返回 dist 内的文件、前端路由回退，或拒绝越界路径。"""
    if path.startswith(('/', '\\')) or '\x00' in path:
        return 'reject'
    parts = PurePosixPath(path).parts if path else ()
    if any(part in ('', '.', '..') for part in parts):
        return 'reject'
    if not path:
        return 'index'
    root = DIST.resolve()
    candidate = root.joinpath(*parts)
    if candidate.is_symlink():
        return 'reject'
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return 'index'
    if not resolved.is_relative_to(root):
        return 'reject'
    if resolved.is_file():
        return resolved
    return 'index'


if DIST.exists():
    app.mount('/assets', StaticFiles(directory=DIST / 'assets', follow_symlink=False), name='assets')


@app.get('/{path:path}')
def frontend(path: str):
    if path.startswith('api/'):
        return JSONResponse({'error': '没有这个接口。'}, status_code=404)
    target = _static_target(path)
    if target == 'reject':
        return JSONResponse({'error': '没有这个页面。'}, status_code=404)
    if isinstance(target, Path):
        return FileResponse(target)
    index = DIST / 'index.html'
    try:
        index_resolved = index.resolve(strict=True)
    except OSError:
        index_resolved = None
    if index_resolved and index_resolved.is_file() and index_resolved.is_relative_to(DIST.resolve()):
        return FileResponse(index_resolved)
    return JSONResponse({'error': '管理台页面还没有构建。请运行 ./scripts/setup.sh。'}, status_code=503)
