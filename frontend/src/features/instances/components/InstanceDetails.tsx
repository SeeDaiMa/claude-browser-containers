import type { Instance } from '../../../shared/api/client'
import { Diagnostics } from './InstanceControls'
import { ExitHistory } from './ExitHistory'

export type InstanceDetailTab = 'diagnostics' | 'ip'
const TABS: { id: InstanceDetailTab; label: string }[] = [
  { id: 'diagnostics', label: '运行诊断' },
  { id: 'ip', label: 'IP 变化' },
]

export function InstanceDetails({
  item,
  tab,
  onTabChange,
}: {
  item: Instance
  tab: InstanceDetailTab
  onTabChange: (tab: InstanceDetailTab) => void
}) {
  const count = item.exit?.history?.length || 0
  return (
    <section className="instance-detail" aria-label={`${item.display_name}的实例详情`}>
      <div className="instance-detail__header">
        <div className="instance-detail__title">
          <strong>实例详情</strong>
          <span>{item.display_name}</span>
        </div>
        <div
          className="cm-seg instance-detail__tabs"
          role="tablist"
          aria-label={`${item.display_name}的详情视图`}
          onKeyDown={(event) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
            event.preventDefault()
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? 1 : tab === 'diagnostics' ? 1 : 0
            onTabChange(TABS[next].id)
            event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus()
          }}
        >
          {TABS.map((option) => (
            <button
              key={option.id}
              type="button"
              role="tab"
              id={`detail-tab-${item.id}-${option.id}`}
              aria-controls={`detail-panel-${item.id}`}
              aria-selected={tab === option.id}
              aria-pressed={tab === option.id}
              tabIndex={tab === option.id ? 0 : -1}
              onClick={() => onTabChange(option.id)}
            >
              {option.label}
              {option.id === 'ip' && count > 0 && <span className="instance-detail__count" aria-hidden="true">{count}</span>}
            </button>
          ))}
        </div>
      </div>
      <div
        className="instance-detail__panel"
        role="tabpanel"
        tabIndex={0}
        id={`detail-panel-${item.id}`}
        aria-labelledby={`detail-tab-${item.id}-${tab}`}
      >
        {tab === 'diagnostics' ? <Diagnostics item={item} /> : <ExitHistory exit={item.exit} />}
      </div>
    </section>
  )
}
