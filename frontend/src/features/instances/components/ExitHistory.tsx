import type { ExitInfo } from '../../../shared/api/client'
import { formatDate } from '../../../shared/lib/format'
import { Icon } from '../../../shared/ui/Icon'

export function ExitHistory({ exit, compact = false }: { exit: ExitInfo | null; compact?: boolean }) {
  const changes = [...(exit?.history || [])].reverse()
  return (
    <div className={`exit-history${compact ? ' exit-history--compact' : ''}`}>
      {!compact && (
        <div className="exit-history__summary">
          <div>
            <span className="cm-help">当前出口</span>
            <strong className="cm-mono">{exit?.ip || '暂未测得'}</strong>
          </div>
          <span className="cm-help">节点保持绑定 · 按时间倒序</span>
        </div>
      )}
      {changes.length ? (
        <ol className="exit-timeline" aria-label="出口 IP 变化记录">
          {changes.map((change) => (
            <li key={change.id}>
              <time dateTime={change.changed_at}>{formatDate(change.changed_at)}</time>
              <span className="exit-timeline__marker" aria-hidden="true" />
              <div className="exit-timeline__change cm-mono">
                <span>{change.previous_ip}</span>
                <Icon name="fwd" />
                <span className="cm-sr">变为</span>
                <strong>{change.ip}</strong>
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <div className="exit-history__empty">
          <Icon name="clock" />
          <div>
            <strong>尚无 IP 变化</strong>
            <p className="cm-help">检测到的出口变化会自动保存在这里。</p>
          </div>
        </div>
      )}
    </div>
  )
}
