import { useManager } from '../../app/ManagerProvider'
import { api } from '../api/client'
import { isActive } from '../model/status'

export function useNodeTesting() {
  const { tasks, submitTask, notify } = useManager()
  return {
    activeTest: (subscriptionId: string) =>
      tasks.find((task) => task.kind === 'test-nodes' && task.subscription_id === subscriptionId && isActive(task)),
    testNodes: async (subscriptionId: string, nodeIds: string[]) => {
      try {
        await submitTask(subscriptionId, () => api.testNodes(subscriptionId, nodeIds))
      } catch (reason) {
        notify('无法开始测速', 'danger', reason instanceof Error ? reason.message : '请稍后重试。')
      }
    },
  }
}
