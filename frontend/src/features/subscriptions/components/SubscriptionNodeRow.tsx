import { memo } from 'react'
import type { NodeRow } from '../../../shared/api/client'
import { joinNames } from '../../../shared/lib/format'
import type { NodeOccupant } from '../../../shared/model/nodeOccupants'
import { Button } from '../../../shared/ui/Controls'
import { NodeLatency } from '../../../shared/ui/NodeLatency'
import { NodeQualityCells } from '../../../shared/ui/NodeQuality'

type LibraryNode = NodeRow & { subscriptionId: string }
type Props = {
  node: LibraryNode
  sourceName: string
  sourceFailed: boolean
  selected: boolean
  selectionDisabled: boolean
  actionDisabled: boolean
  testing: boolean
  checking: boolean
  occupants: NodeOccupant[]
  onToggle: (node: LibraryNode, checked: boolean) => void
  onTest: (source: string, nodes: string[]) => Promise<void>
  onQuality: (source: string, nodes: string[]) => Promise<void>
}

export const SubscriptionNodeRow = memo(function SubscriptionNodeRow({ node, sourceName, sourceFailed,
  selected, selectionDisabled, actionDisabled, testing, checking, occupants, onToggle, onTest, onQuality }: Props) {
  return (
    <tr className={`cm-tr${selected ? ' is-selected' : ''}`}>
      <td className="node-select-cell">
        <input type="checkbox" className="node-checkbox" aria-label={`选择节点 ${node.name}`}
          title={node.supported ? `选择节点 ${node.name}` : '此节点协议暂不支持，无法选择'}
          checked={selected} disabled={!node.supported || selectionDisabled}
          onChange={(event) => onToggle(node, event.target.checked)} />
      </td>
      <td className="node-source-cell">
        {sourceName}
        {sourceFailed && <div className="cm-cell-sub tone-warn">缓存数据</div>}
      </td>
      <td><div className="cm-cell-name"><strong>{node.name}</strong>
        <span className="cm-cell-sub cm-mono">{node.id}</span></div></td>
      <td><div className="cm-status"><span className="cm-badge">{node.type}</span>
        {!node.supported && <span className="cm-cell-sub">不支持 · {node.reason}</span>}</div></td>
      <td><NodeLatency node={node} testing={testing} /></td>
      <NodeQualityCells node={node} checking={checking} />
      <td>{occupants.length
        ? joinNames(occupants.map((user) => user.name + (user.trashed ? '（回收站）' : '')))
        : <span className="cm-muted">—</span>}</td>
      <td><div className="node-row-actions">
        <Button small variant="tint-primary" aria-label={`测速节点 ${node.name}`}
          disabled={!node.supported || actionDisabled} onClick={() => void onTest(node.subscriptionId, [node.id])}>
          测速
        </Button>
        <Button small variant="tint-primary" aria-label={`检测节点 IP ${node.name}`}
          disabled={!node.supported || actionDisabled} busy={checking}
          onClick={() => void onQuality(node.subscriptionId, [node.id])}>
          {checking ? '检测中…' : '检测 IP'}
        </Button>
      </div></td>
    </tr>
  )
})
