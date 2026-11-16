import { Fragment, useState, type ReactNode } from 'react'
import { useManager } from '../../../app/ManagerProvider'
import { type Instance } from '../../../shared/api/client'
import {
  formatAgo,
  formatCountry,
  formatCpu,
  formatMemory,
  joinNames,
} from '../../../shared/lib/format'
import {
  activeTaskFor,
  hasPendingExitChange,
  instanceState,
  isFinished,
  proxySummary,
  startFailureFor,
} from '../../../shared/model/status'
import { Badge, Chip } from '../../../shared/ui/Badge'
import {
  Button,
  ButtonLink,
  Empty,
  IconButton,
  Notice,
  PageHead,
  Select,
} from '../../../shared/ui/Controls'
import { Icon } from '../../../shared/ui/Icon'
import {
  DockerNotice,
  HealthNotice,
  InstanceMenu,
  LifecycleButton,
  Loading,
} from '../components/InstanceControls'
import { InstanceDetails, type InstanceDetailTab } from '../components/InstanceDetails'

export function ListPage() {
  const { data, tasks, error, draft, resetDraft } = useManager()
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [detailTabs, setDetailTabs] = useState<Record<string, InstanceDetailTab>>({})
  if (!data) return <Loading />
  const connected = data.docker.ok && !error
  const createTask = tasks.find((task) => task.id === draft.taskId)
  const beginCreate = () => {
    if (createTask && isFinished(createTask)) resetDraft()
  }
  const rows = data.instances.filter((item) => {
    if (!item.display_name.toLowerCase().includes(query.trim().toLowerCase())) return false
    const busy = !!activeTaskFor(tasks, item.id)
    if (filter === 'running') return connected && item.runtime === 'running' && !busy
    if (filter === 'stopped')
      return connected && item.runtime === 'stopped' && !busy && !startFailureFor(item, tasks)
    if (filter === 'busy') return busy
    if (filter === 'problem')
      return (
        !!startFailureFor(item, tasks) ||
        item.exit?.status === 'failed' ||
        item.proxy.stale ||
        ['warning', 'failed'].includes(item.monitor?.state || '') ||
        hasPendingExitChange(item.exit) ||
        item.runtime === 'degraded'
      )
    return true
  })
  const stale = data.instances.filter((item) => item.proxy.stale)
  return (
    <div className="pg">
      <PageHead title="实例">查看实例状态、绑定节点和资源使用。</PageHead>
      <DockerNotice />
      <HealthNotice />
      {stale.length > 0 && (
        <Notice
          tone="warn"
          title="有实例需要重新确认节点"
          actions={stale.map((item) => (
            <ButtonLink key={item.id} small to={`/instance/${item.id}/edit`}>
              处理「{item.display_name}」
            </ButtonLink>
          ))}
        >
          {joinNames(stale.map((item) => item.display_name))}{' '}
          使用的订阅节点已变化或消失。可以在编辑页重新确认原订阅里的同名节点，系统不会换成别的节点。
        </Notice>
      )}
      <section className="metrics" aria-label="实例摘要">
        <Metric
          label="实例总数"
          value={data.summary.total}
          hint={`不含回收站中的 ${data.trash.length} 个`}
        />
        <Metric
          label="运行中"
          value={connected ? (data.summary.running ?? '未知') : '未知'}
          hint={
            connected && data.summary.running !== null
              ? `其余 ${data.summary.total - data.summary.running} 个未运行`
              : error
                ? '管理台连接异常，无法读取'
                : 'Docker 未连接，无法读取'
          }
        />
        <Metric
          label="容器内存合计"
          value={connected ? formatMemory(data.summary.memory_total_mib) : '—'}
          hint="只含容器实际占用"
        />
        <Metric
          label="Docker 状态"
          value={
            <Badge
              state={{
                tone: error ? 'neutral' : connected ? 'ok' : 'danger',
                label: error ? '状态未知' : connected ? '已连接' : '未连接',
                dot: error ? 'hollow' : connected ? undefined : 'square',
              }}
            />
          }
          hint={connected ? '共用同一台 Docker Desktop 虚拟机' : '运行操作已禁用'}
          small
        />
      </section>
      <div className="tools">
        <div className="search">
          <Icon name="search" />
          <input
            className="cm-input"
            type="search"
            value={query}
            placeholder="搜索实例名称"
            aria-label="搜索实例名称"
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <Select
          label="按状态筛选"
          value={filter}
          onChange={setFilter}
          options={[
            { value: 'all', label: '全部状态' },
            { value: 'running', label: '运行中' },
            { value: 'stopped', label: '已停止' },
            { value: 'problem', label: '异常' },
            { value: 'busy', label: '处理中' },
          ]}
        />
        <span className="spacer" />
        <ButtonLink to="/subscriptions" icon="proxy">
          代理库
        </ButtonLink>
        {connected ? (
          <ButtonLink to="/new" icon="plus" variant="primary" onClick={beginCreate}>
            创建实例
          </ButtonLink>
        ) : (
          <Button disabled icon="plus" variant="primary" title="Docker 未连接，暂时无法创建">
            创建实例
          </Button>
        )}
      </div>
      {rows.length ? (
        <div>
          <div className="cm-table-wrap">
            <table className="cm-table instances-table">
              <thead>
                <tr>
                  <th className="col-exp">
                    <span className="cm-sr">实例详情</span>
                  </th>
                  <th>实例名称</th>
                  <th>状态</th>
                  <th>代理来源与节点</th>
                  <th>最近出口 IP</th>
                  <th>出口检测时间</th>
                  <th>内存 / 上限 · CPU</th>
                  <th className="align-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <Fragment key={item.id}>
                    <InstanceRow
                      item={item}
                      expanded={!!expanded[item.id]}
                      onExpand={() => setExpanded({ ...expanded, [item.id]: !expanded[item.id] })}
                      onShowDetails={(tab) => {
                        setExpanded((current) => ({ ...current, [item.id]: true }))
                        setDetailTabs((current) => ({ ...current, [item.id]: tab }))
                      }}
                    />
                    {expanded[item.id] && (
                      <tr className="diag-row">
                        <td colSpan={8}>
                          <div id={`diagnostics-${item.id}`}>
                            <InstanceDetails
                              item={item}
                              tab={detailTabs[item.id] || 'diagnostics'}
                              onTabChange={(tab) => setDetailTabs((current) => ({ ...current, [item.id]: tab }))}
                            />
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
          <p className="cm-help table-help">
            展开实例可查看运行诊断和 IP 变化记录。内存上限不等于实际占用。
          </p>
        </div>
      ) : data.instances.length ? (
        <Empty
          title="没有符合条件的实例"
          actions={
            <Button
              onClick={() => {
                setQuery('')
                setFilter('all')
              }}
            >
              清除筛选
            </Button>
          }
        >
          换个关键词，或清除筛选。
        </Empty>
      ) : (
        <Empty
          title="还没有实例"
          actions={
            connected && (
              <ButtonLink to="/new" icon="plus" variant="primary" onClick={beginCreate}>
                创建实例
              </ButtonLink>
            )
          }
        >
          每个实例是一套独立的浏览器环境，有自己的
          Cookie、登录状态和代理。创建时可以选择订阅节点，也可以填写 SOCKS5。
        </Empty>
      )}
    </div>
  )
}

function Metric({
  label,
  value,
  hint,
  small,
}: {
  label: string
  value: ReactNode
  hint: string
  small?: boolean
}) {
  return (
    <div className="cm-metric">
      <span className="cm-metric__label">{label}</span>
      <span className={`cm-metric__value${small ? ' cm-metric__value--sm' : ''}`}>{value}</span>
      <span className="cm-metric__hint">{hint}</span>
    </div>
  )
}

function InstanceRow({
  item,
  expanded,
  onExpand,
  onShowDetails,
}: {
  item: Instance
  expanded: boolean
  onExpand: () => void
  onShowDetails: (tab: InstanceDetailTab) => void
}) {
  const { data, tasks, error } = useManager()
  const connected = !!data?.docker.ok && !error
  const proxy = proxySummary(item)
  const task = activeTaskFor(tasks, item.id)
  const peers =
    data?.exit_warnings
      .find((warning) => warning.ip === item.exit?.ip)
      ?.instances.filter((peer) => peer.id !== item.id) || []
  const users =
    item.proxy.mode === 'subscription'
      ? data?.instances.filter(
          (other) =>
            other.id !== item.id &&
            other.proxy.subscription_id === item.proxy.subscription_id &&
            other.proxy.node_id === item.proxy.node_id,
        ) || []
      : []
  return (
    <tr className={`cm-tr${task ? ' is-busy' : ''}`}>
      <td className="col-exp">
        <IconButton
          icon={expanded ? 'chevD' : 'chevR'}
          label={`实例详情：${item.display_name}`}
          aria-expanded={expanded}
          aria-controls={`diagnostics-${item.id}`}
          onClick={onExpand}
        />
      </td>
      <td>
        <div className="cm-cell-name">
          <strong>{item.display_name}</strong>
        </div>
      </td>
      <td>
        <Badge state={instanceState(item, tasks, connected, data?.monitor)} />
      </td>
      <td>
        <div className="cm-cell-name">
          <span>{proxy.node}</span>
          {item.proxy.subscription_name !== '已有订阅' && (
            <span className="cm-cell-sub">{proxy.source}</span>
          )}
          {users.length > 0 && (
            <Chip icon="link">与 {joinNames(users.map((user) => user.display_name))} 共用节点</Chip>
          )}
          {item.proxy.stale && <Chip>节点需要重新确认</Chip>}
        </div>
      </td>
      <td>
        <span className={`cm-mono${item.exit?.status !== 'ok' ? ' cm-muted' : ''}`}>
          {item.exit?.ip || '—'}
        </span>
        {item.exit?.ip && (
          <div className="cm-cell-sub">{formatCountry(item.exit.country_code)}</div>
        )}
        {!item.exit && <div className="cm-cell-sub">出口未检测</div>}
        {!item.exit?.ip && item.exit?.last_success && (
          <div className="cm-cell-sub">上次成功：{item.exit.last_success.ip}</div>
        )}
        {peers.length > 0 && (
          <div>
            <Chip>与 {joinNames(peers.map((peer) => peer.name))} 出口相同</Chip>
          </div>
        )}
      </td>
      <td>
        {formatAgo(item.exit?.checked_at)}
        {item.exit?.status === 'failed' && <div className="cm-cell-sub tone-danger">检测失败</div>}
        {item.exit?.status === 'unknown' && <div className="cm-cell-sub tone-warn">出口未测得</div>}
      </td>
      <td>
        <span className="cm-num">
          {connected ? formatMemory(item.usage.memory_mib) : '未知'}{' '}
          <span className="cm-muted">/ {formatMemory(item.usage.memory_limit_mib)}</span>
        </span>
        <div className="cm-cell-sub cm-num">
          CPU {connected ? formatCpu(item.usage.cpu_percent) : '未知'}
        </div>
      </td>
      <td>
        <div className="cm-actions">
          <ButtonLink small variant="tint-primary" icon="open" to={`/instance/${item.id}`}>
            打开
          </ButtonLink>
          <LifecycleButton item={item} />
          <InstanceMenu
            item={item}
            onDiagnostics={() => onShowDetails('diagnostics')}
            onIpHistory={() => onShowDetails('ip')}
          />
        </div>
      </td>
    </tr>
  )
}
