import type { Instance, NodeRow, Overview, Subscription, Task } from '../shared/api/client'

export function fixtureNode(id: string, changes: Partial<NodeRow> = {}): NodeRow {
  return { id, name: `节点-${id}`, type: 'ss', port: 443, supported: true, reason: '',
    used_by: [], test: null, quality: null, ...changes }
}

export function fixtureSubscription(id: string, count = 1): Subscription {
  return { id, name: `来源-${id}`, node_count: count, updated_at: '2026-11-17T00:00:00Z',
    nodes_tested_at: null, nodes_quality_checked_at: null, last_error: '' }
}

export function fixtureInstance(id: string, nodeId: string, changes: Partial<Instance> = {}): Instance {
  return { id, display_name: `实例-${id}`, created_at: null, historical: false, trashed_at: null,
    proxy_locked: true, port: 5898, url: 'http://127.0.0.1:5898/', runtime: 'running',
    proxy: { mode: 'subscription', label: 'fixture', subscription_id: 'a', node_id: nodeId },
    exit: null, usage: { memory_mib: null, cpu_percent: null, memory_limit_mib: null },
    diagnostics: [], ...changes }
}

export function fixtureOverview(instances: Instance[] = [], trash: Instance[] = []): Overview {
  return { docker: { ok: true, detail: '' }, summary: { total: instances.length, running: instances.length,
    memory_total_mib: null, docker_vm_rss_mib: null, docker_helper_rss_mib: null, manager_rss_mib: null },
    instances, trash, exit_warnings: [] }
}

export function fixtureTask(kind: string, source: string, nodes: string[] = []): Task {
  return { id: `${kind}-${source}`, kind, subscription_id: source, node_ids: nodes,
    instance_id: '', state: 'queued', phase: 'fixture', phases: [], error: '', result: {} }
}

export function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Deferred promise not initialized') }
  const promise = new Promise<T>((accept) => { resolve = accept })
  return { promise, resolve: (value: T) => resolve(value) }
}
