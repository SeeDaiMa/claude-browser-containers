import type { NodeRow } from '../api/client'
import { Badge } from './Badge'

export function NodeLatency({ node, testing = false }: { node: NodeRow; testing?: boolean }) {
  const result = node.test
  const title = result
    ? `${result.detail} 最近测速：${new Date(result.checked_at).toLocaleString()}`
    : node.supported ? '尚未测速，可选择此节点或手动测速。' : node.reason
  return (
    <span title={title}>
      <Badge state={
        !node.supported ? { tone: 'neutral', label: '不支持测速', dot: 'hollow' }
          : testing ? { tone: 'info', label: '测速中…', spin: true }
            : result?.status === 'ok' ? { tone: 'ok', label: `${result.delay_ms} ms` }
              : result?.status === 'failed' ? { tone: 'danger', label: '测速失败' }
                : { tone: 'neutral', label: '未测速', dot: 'hollow' }
      } />
    </span>
  )
}
