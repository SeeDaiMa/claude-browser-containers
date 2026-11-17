"""用独立的临时 Mihomo 容器测试订阅节点，不接触实例和浏览器数据。"""
import json
import math
from pathlib import Path
import secrets
import subprocess
import time

import cbc
import node_probe_worker
import store

PROBE_BATCH_SIZE = 8

_QUALITY_FAILURE_DETAILS = {
    '节点配置无法通过 Mihomo 校验。', '节点配置校验超时。',
    '检测服务未能启动，请重试。', 'IPPure 请求受限，请稍后重试。',
    '经节点查询 IPPure 失败或超时，请稍后重试。',
    *(f'IPPure 暂不可用（HTTP {status}）。' for status in range(400, 600)),
}


def select_nodes(subscription_id, node_ids=None):
    if not any(sub['id'] == subscription_id for sub in store.load_library()['subscriptions']):
        raise ValueError('订阅不存在。')
    nodes = store.load_cached_nodes(subscription_id)
    if node_ids is not None:
        if not isinstance(node_ids, list) or not node_ids or any(not isinstance(identity, str) for identity in node_ids):
            raise ValueError('请选择需要测速的节点。')
        requested = set(node_ids)
        nodes = [node for node in nodes if node['id'] in requested]
        if {node['id'] for node in nodes} != requested:
            raise ValueError('所选节点已变化，请刷新列表后重试。')
        if any(not node.get('supported', True) for node in nodes):
            raise ValueError('所选节点包含不支持的协议，无法测速。')
    else:
        nodes = [node for node in nodes if node.get('supported', True)]
    if not nodes:
        raise ValueError('没有支持测速的节点。')
    return nodes


class ProbeCancelled(Exception):
    """调用方要求停在当前批之后，已完成的节点不会被记成失败。"""

    def __init__(self, results):
        super().__init__('节点检测已取消。')
        self.results = results
        self.result = {}


def _measure_each(nodes, quality=False, on_progress=None, on_result=None):
    if on_progress is None:
        return _measure(nodes, quality=True) if quality else _measure(nodes)
    results = {}
    total = len(nodes)
    for index in range(0, total, PROBE_BATCH_SIZE):
        batch = nodes[index:index + PROBE_BATCH_SIZE]
        if quality and index:
            time.sleep(2)
        if on_progress(index, total, batch[0]) is False:
            raise ProbeCancelled(results)
        measured = _measure(batch, quality=quality)
        results.update(measured)
        if on_result:
            on_result(measured)
        if on_progress(index + len(batch), total, None) is False:
            raise ProbeCancelled(results)
    return results


def test_subscription(subscription_id, node_ids, on_progress=None):
    return _run_subscription(subscription_id, node_ids, False, on_progress)


def check_quality(subscription_id, node_ids, on_progress=None):
    return _run_subscription(subscription_id, node_ids, True, on_progress)


def _run_subscription(subscription_id, node_ids, quality, on_progress):
    nodes = select_nodes(subscription_id, node_ids)
    save = store.save_node_quality if quality else store.save_node_tests
    saved = 0

    def save_results(results):
        nonlocal saved
        saved += save(subscription_id, results)

    cancelled = False
    cancellation = None
    try:
        results = _measure_each(nodes, quality=quality, on_progress=on_progress,
                                on_result=save_results if on_progress else None)
    except ProbeCancelled as error:
        cancelled, cancellation, results = True, error, error.results
    if on_progress is None:
        save_results(results)
    completed = len(results)
    succeeded = sum(result['status'] == 'ok' for result in results.values())
    partial = sum(result['status'] == 'partial' for result in results.values())
    failed = completed - succeeded - partial
    detail = (f'已检测 {completed} 个节点：{succeeded} 个完整结果，{partial} 个部分结果，{failed} 个失败。'
              if quality else f'已测 {completed} 个节点：{succeeded} 个成功，{failed} 个失败。')
    if cancelled:
        detail += f' 已取消，已完成 {completed}/{len(nodes)}，其余节点未检测。'
    if saved < completed:
        detail += ' 部分节点已变化或订阅已删除，过期结果未保存。'
    result = {
        'total': len(nodes), 'completed': completed, 'succeeded': succeeded, 'failed': failed,
        'detail': detail,
    }
    if quality:
        result['partial'] = partial
    if cancellation:
        cancellation.result = result
        raise cancellation
    return result


def _measure(nodes, quality=False):
    name = 'cbc-node-test-' + secrets.token_hex(6)
    worker = Path(__file__).with_name('node_probe_worker.py').read_text()
    image = cbc.read_json(cbc.ROOT / 'images.lock.json')['network']['reference']
    node_configs = [{'id': node['id'], 'config': node['config']} for node in nodes]
    payload = {'mode': 'quality', 'nodes': node_configs} if quality else node_configs
    timeout = 20 + len(nodes) * 3 + (len(nodes) * 14 if quality else math.ceil(len(nodes) / 4) * 10)
    action = 'IP 质量检测' if quality else '测速'
    try:
        result = cbc.docker(
            'run', '--rm', '--name', name, '--pull', 'never', '--platform', 'linux/arm64',
            '--init', '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--memory', '256m', '--cpus', '0.5', '--pids-limit', '64',
            '--network', 'bridge', '--label', cbc.LABEL, '--label', 'io.cbc.role=node-test',
            '-i', '--entrypoint', 'python3', image, '-c', worker,
            input=json.dumps(payload), check=False, timeout=timeout,
        )
        if result.returncode:
            raise ValueError(f'{action}环境无法启动，请确认 Docker 正常且项目网络镜像已安装。')
        results = json.loads(result.stdout)
        if not isinstance(results, dict) or set(results) != {node['id'] for node in nodes}:
            raise ValueError(f'{action}服务返回了无效结果，请重试。')
        for value in results.values():
            if quality:
                if not isinstance(value, dict) or value.get('status') not in ('ok', 'partial', 'failed'):
                    raise ValueError('IP 质量服务返回了无效结果，请重试。')
                if value['status'] == 'failed':
                    detail = value.get('detail')
                    value.clear()
                    value.update(node_probe_worker.quality_failed(
                        detail if detail in _QUALITY_FAILURE_DETAILS else '经节点查询 IPPure 失败，请稍后重试。'))
                else:
                    public = node_probe_worker.normalize_quality({
                        'ip': value.get('ip'), 'asn': value.get('asn'), 'fraudScore': value.get('fraud_score'),
                        'isResidential': value.get('is_residential'), 'isBroadcast': value.get('is_broadcast'),
                    })
                    value.clear()
                    value.update(public)
                continue
            if (not isinstance(value, dict) or value.get('status') not in ('ok', 'failed')
                    or (value['status'] == 'ok' and (type(value.get('delay_ms')) is not int or not 0 <= value['delay_ms'] <= 65535))):
                raise ValueError('测速服务返回了无效结果，请重试。')
        return results
    except subprocess.TimeoutExpired:
        raise ValueError(f'节点{action}超时，请减少节点数量后重试。') from None
    except (OSError, json.JSONDecodeError):
        raise ValueError(f'无法读取{action}结果，请确认 Docker 正常后重试。') from None
    finally:
        # 超时或异常时仍只清理本次临时容器，不按标签批量删除。
        try:
            cbc.docker('rm', '-f', name, check=False, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
