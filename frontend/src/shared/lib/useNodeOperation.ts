import { useCallback, useMemo, useState } from 'react'
import { useManager } from '../../app/ManagerProvider'
import { api, type Task } from '../api/client'
import { isActive } from '../model/status'
import { useEventCallback } from './useEventCallback'

type NodeOperation = 'test-nodes' | 'check-node-quality'

export function useNodeOperation(kind: NodeOperation) {
  const { tasks, submitTask, notify } = useManager()
  const [pending, setPending] = useState<string[]>([])
  const active = useMemo(() => {
    const result = new Map<string, Task>()
    for (const task of tasks) {
      if (task.kind === kind && task.subscription_id && isActive(task)) {
        if (kind === 'test-nodes' && result.has(task.subscription_id)) continue
        result.set(task.subscription_id, task)
      }
    }
    return result
  }, [tasks, kind])
  const nodeIds = useMemo(
    () => new Map([...active].map(([id, task]) => [id, new Set(task.node_ids || [])])),
    [active],
  )
  const activeFor = useCallback((id: string) => active.get(id), [active])
  const isNodeActive = useCallback(
    (id: string, node: string) => nodeIds.get(id)?.has(node) || false,
    [nodeIds],
  )
  const busy = useCallback(
    (id: string) => pending.includes(id) || active.has(id),
    [pending, active],
  )
  const run = useEventCallback(async (id: string, nodes: string[]) => {
    setPending((items) => [...items, id])
    try {
      await submitTask(id, () =>
        kind === 'test-nodes' ? api.testNodes(id, nodes) : api.checkNodeQuality(id, nodes),
      )
    } catch (reason) {
      notify(
        kind === 'test-nodes' ? '无法开始测速' : '无法开始 IP 质量检测',
        'danger',
        reason instanceof Error ? reason.message : '请稍后重试。',
      )
    } finally {
      setPending((items) => items.filter((item) => item !== id))
    }
  })
  return { activeFor, isNodeActive, busy, run }
}
