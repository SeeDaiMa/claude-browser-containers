import { useState } from 'react'
import { useManager } from '../../app/ManagerProvider'
import { api, type Task } from '../api/client'
import { progressText } from '../model/status'
import { Button, Notice } from './Controls'

export function NodeTaskNotice({ task, subscriptionName }: { task: Task; subscriptionName?: string }) {
  const { refresh, notify } = useManager()
  const [pending, setPending] = useState(false)
  const cancelling = pending || task.cancel_requested
  const title = cancelling ? '正在取消节点检测' : task.state === 'queued' ? '节点检测在排队' : '正在检测节点'
  return (
    <Notice
      title={subscriptionName ? `${subscriptionName} · ${title}` : title}
      actions={
        <Button
          small
          busy={pending}
          disabled={!!cancelling}
          onClick={async () => {
            setPending(true)
            try {
              await api.cancelTask(task.id)
              await refresh()
            } catch (reason) {
              notify(reason instanceof Error ? reason.message : '取消失败，请重试。', 'danger')
            } finally {
              setPending(false)
            }
          }}
        >
          {cancelling ? '正在取消…' : '取消检测'}
        </Button>
      }
    >
      {progressText(task)}。取消后，当前批次结束就停止后续检测，已完成结果会保留。
    </Notice>
  )
}
