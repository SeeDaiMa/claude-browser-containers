import type { ReactNode } from 'react'
import type { BadgeState } from '../model/status'
import { Icon, type IconName } from './Icon'

/** 状态徽标：文字 + 形状，不只靠颜色区分。 */
export function Badge({ state }: { state: BadgeState }) {
  const tone = state.tone === 'neutral' ? '' : ` cm-badge--${state.tone}`
  return (
    <span className={`cm-badge${tone}`}>
      {state.spin ? (
        <span className="cm-spin" aria-hidden="true" />
      ) : (
        <span className={`cm-dot${state.dot ? ` cm-dot--${state.dot}` : ''}`} aria-hidden="true" />
      )}
      {state.label}
    </span>
  )
}

/** 适度提示：同节点复用、相同出口、需要重新确认等，不阻止操作。 */
export function Chip({
  icon = 'alert',
  tone,
  title,
  children,
}: {
  icon?: IconName
  tone?: 'danger' | 'info'
  title?: string
  children: ReactNode
}) {
  return (
    <span className={`cm-chip${tone ? ` cm-chip--${tone}` : ''}`} title={title}>
      <Icon name={icon} />
      {children}
    </span>
  )
}
