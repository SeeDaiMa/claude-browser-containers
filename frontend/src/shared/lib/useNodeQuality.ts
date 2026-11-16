import { useState } from 'react'
import { useManager } from '../../app/ManagerProvider'
import { api } from '../api/client'
import { isActive } from '../model/status'

export function useNodeQuality() {
  const { tasks, submitTask, notify } = useManager()
  const [pending, setPending] = useState<string[]>([])
  const activeTasks = new Map(
    tasks
      .filter((task) => task.kind === 'check-node-quality' && isActive(task))
      .map((task) => [task.subscription_id, task]),
  )

  return {
    activeQuality: (subscriptionId: string) => activeTasks.get(subscriptionId),
    qualityBusy: (subscriptionId: string) => pending.includes(subscriptionId) || activeTasks.has(subscriptionId),
    checkQuality: async (subscriptionId: string, nodeIds: string[]) => {
      setPending((items) => [...items, subscriptionId])
      try {
        await submitTask(subscriptionId, () => api.checkNodeQuality(subscriptionId, nodeIds))
      } catch (reason) {
        notify('无法开始 IP 质量检测', 'danger', reason instanceof Error ? reason.message : '请稍后重试。')
      } finally {
        setPending((items) => items.filter((id) => id !== subscriptionId))
      }
    },
  }
}
