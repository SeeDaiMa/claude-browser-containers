import type { NodeRow } from '../api/client'
import { formatDate } from '../lib/format'
import { Badge } from './Badge'

export function NodeQualityBadge({
  node,
  checking = false,
}: {
  node: NodeRow
  checking?: boolean
}) {
  const quality = node.quality
  const title = quality
    ? `${quality.detail} 检测时间：${formatDate(quality.checked_at)}。IPPure 系数 0–100%，越低风险越低。`
    : node.supported
      ? '点击「检测 IP」查询 IPPure 系数，0–100%，越低风险越低。'
      : node.reason
  const label = !node.supported
    ? 'IPPure —'
    : quality?.fraud_score != null
      ? `IPPure ${quality.fraud_score}%`
      : checking
        ? 'IPPure 检测中…'
        : quality?.status === 'failed'
          ? 'IPPure 检测失败'
          : quality
            ? 'IPPure 未提供'
            : 'IPPure 未检测'

  return (
    <span title={title} aria-busy={checking}>
      <Badge
        state={{
          tone: checking ? 'info' : quality?.status === 'failed' ? 'danger' : 'neutral',
          label,
          spin: checking,
          dot: 'hollow',
        }}
      />
    </span>
  )
}

export function NodeQualityCells({ node, checking }: { node: NodeRow; checking: boolean }) {
  const quality = node.quality
  const unavailable = !node.supported
    ? '—'
    : checking
      ? '检测中…'
      : !quality
        ? '未检测'
        : quality.status === 'failed'
          ? '检测失败'
          : '未提供'
  const detail = quality
    ? `${quality.detail} 检测时间：${formatDate(quality.checked_at)}`
    : '点击「检测 IP」查询此节点的出口 IP 质量。'
  const residential = quality?.is_residential
  const broadcast = quality?.is_broadcast
  return (
    <>
      <td title={detail} aria-busy={checking}>
        <div className="node-quality-cell">
          <span>
            {typeof residential === 'boolean' ? (residential ? '住宅 IP' : '机房 IP') : unavailable}
            {checking && quality && <span className="cm-cell-sub">更新中…</span>}
          </span>
          {typeof broadcast === 'boolean' && (
            <span className="cm-cell-sub">{broadcast ? '广播 IP' : '原生 IP'}</span>
          )}
          {quality?.ip && <span className="cm-cell-sub cm-mono">{quality.ip}</span>}
        </div>
      </td>
      <td title={detail} aria-busy={checking}>
        <div className="node-quality-cell">
          <span className={quality?.fraud_score == null ? 'cm-muted' : 'cm-num'}>
            {quality?.fraud_score == null ? unavailable : `${quality.fraud_score}%`}
          </span>
          {quality && <span className="cm-cell-sub">{formatDate(quality.checked_at)}</span>}
        </div>
      </td>
    </>
  )
}
