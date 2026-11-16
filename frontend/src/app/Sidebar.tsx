import { Link, useLocation } from 'react-router-dom'
import { Button, ButtonLink, IconButton } from '../shared/ui/Controls'
import { Icon, Logo, type IconName } from '../shared/ui/Icon'
import { formatMemory } from '../shared/lib/format'
import { isActive, isFinished, runState } from '../shared/model/status'
import { useManager } from './ManagerProvider'

const APP_VERSION = '1.0.0'

type NavLinkItem = { to: string; label: string; icon: IconName; count?: number }

/**
 * 管理台侧栏：品牌、新建入口、主导航、实例列表和底部运行状态。
 * 收起状态只保留图标，文字信息通过 title 提示。
 */
export function Sidebar({ collapsed }: { collapsed: boolean }) {
  const { data, tasks, subscriptions, error, draft, resetDraft } = useManager()
  const location = useLocation()
  const connected = !!data?.docker.ok && !error
  const createTask = tasks.find((task) => task.id === draft.taskId)

  // 上一次创建已结束时，从侧栏重新进入要清空草稿。
  const beginCreate = () => {
    if (createTask && isFinished(createTask)) {
      resetDraft()
    }
  }

  const links: NavLinkItem[] = [
    { to: '/', label: '实例', icon: 'inst', count: data?.instances.length },
    { to: '/subscriptions', label: '代理库', icon: 'proxy', count: subscriptions.length },
    { to: '/trash', label: '回收站', icon: 'trash', count: data?.trash.length },
    { to: '/settings', label: '设置', icon: 'settings' },
  ]

  // 实例列表页、创建页和编辑页都归属“实例”导航项。
  const isLinkCurrent = (to: string) =>
    to === '/'
      ? !['/subscriptions', '/trash', '/settings'].includes(location.pathname)
      : location.pathname === to

  return (
    <aside className="cm-side side" aria-label="Claude 容器侧栏">
      <div className="side-brand" title="Claude 容器">
        <Logo />
        <div className="side-brand__copy">
          <span className="side-brand__name">Claude 容器</span>
          <span className="side-brand__subtitle">独立浏览器工作空间</span>
        </div>
      </div>

      {connected ? (
        <ButtonLink
          className="side-create"
          to="/new"
          icon="plus"
          variant="tint-primary"
          aria-label="新建实例"
          title={collapsed ? '新建实例' : undefined}
          onClick={beginCreate}
        >
          <span className="side-label">新建实例</span>
        </ButtonLink>
      ) : (
        <Button
          className="side-create"
          icon="plus"
          variant="tint-primary"
          aria-label="新建实例"
          title="连接 Docker 后即可创建实例"
          disabled
        >
          <span className="side-label">新建实例</span>
        </Button>
      )}

      <nav className="side-nav" aria-label="主导航">
        {links.map((link) => (
          <Link
            key={link.to}
            to={link.to}
            title={link.label}
            aria-label={link.label}
            className="cm-nav-item"
            aria-current={isLinkCurrent(link.to) ? 'page' : undefined}
          >
            <Icon name={link.icon} />
            <span className="side-label">{link.label}</span>
            {link.count !== undefined && (
              <span className="cm-nav-item__count">{link.count}</span>
            )}
          </Link>
        ))}
      </nav>

      <SidebarInstances connected={connected} />

      <SidebarFooter connected={connected} activeTasks={tasks.filter(isActive).length} />
    </aside>
  )
}

/** 实例快捷入口：占满侧栏剩余高度，超出时在列表内部滚动。 */
function SidebarInstances({ connected }: { connected: boolean }) {
  const { data, tasks } = useManager()
  const location = useLocation()
  const instances = data?.instances || []

  return (
    <section className="side-instances" aria-label="实例快捷入口">
      <div className="side-section-title">
        <span>快速打开</span>
        <span className="cm-num">{data ? instances.length : '—'}</span>
      </div>
      <div className="side-instances__list">
        {instances.map((item) => {
          const state = runState(item, tasks, connected)
          const selected = location.pathname.startsWith(`/instance/${item.id}`)

          return (
            <Link
              key={item.id}
              to={`/instance/${item.id}`}
              className="side-instance"
              aria-label={`打开实例：${item.display_name}，${state.label}`}
              aria-current={selected ? 'page' : undefined}
              title={`${item.display_name} · ${state.label}`}
            >
              <span className="side-instance__icon">
                <Icon name="inst" />
                <span className={`side-instance__badge tone-${state.tone}`} aria-hidden="true">
                  {state.spin ? (
                    <span className="cm-spin" />
                  ) : (
                    <span className={`cm-dot${state.dot ? ` cm-dot--${state.dot}` : ''}`} />
                  )}
                </span>
              </span>
              <span className="side-instance__name">{item.display_name}</span>
              <span className={`side-instance__state tone-${state.tone}`}>{state.label}</span>
            </Link>
          )
        })}
        {!data && <p className="side-empty">正在读取实例…</p>}
        {data?.instances.length === 0 && (
          <p className="side-empty">创建第一个实例，开始独立工作。</p>
        )}
      </div>
    </section>
  )
}

/** 底部状态区：运行概况、外观切换和版本信息合并为一个紧凑面板。 */
function SidebarFooter({ connected, activeTasks }: { connected: boolean; activeTasks: number }) {
  const { data, error, resolvedTheme, setTheme } = useManager()
  const dockerLabel = error || !data ? '状态未知' : data.docker.ok ? 'Docker 已连接' : 'Docker 未连接'
  const nextTheme = resolvedTheme === 'dark' ? 'light' : 'dark'
  const nextThemeLabel = `切换为${nextTheme === 'dark' ? '深色' : '浅色'}主题`

  return (
    <footer className="side-footer">
      <section className="side-status" aria-label="运行概况">
        <Link
          className="side-status__head"
          to="/settings"
          title={`${dockerLabel} · 查看运行环境`}
          aria-label={`${dockerLabel}，查看运行环境`}
        >
          <span className={`cm-dot${connected ? ' tone-ok' : ' cm-dot--hollow'}`} aria-hidden="true" />
          <span className="side-label">{dockerLabel}</span>
          <Icon name="chevR" />
        </Link>
        <dl className="side-status__metrics">
          <div>
            <dt>运行中</dt>
            <dd className="cm-num">
              {connected ? (data?.summary.running ?? '—') : '—'}
              <span>/{data?.summary.total ?? '—'}</span>
            </dd>
          </div>
          <div>
            <dt>内存</dt>
            <dd className="cm-num">
              {connected ? formatMemory(data?.summary.memory_total_mib) : '—'}
            </dd>
          </div>
          <div>
            <dt>任务</dt>
            <dd className="cm-num">{error ? '—' : activeTasks}</dd>
          </div>
        </dl>
      </section>

      <div className="side-meta">
        <span className="side-meta__info" title="本机运行 · 数据独立保存">
          <Icon name="shield" />
          本机运行 · v{APP_VERSION}
        </span>
        <div className="cm-seg side-theme" role="group" aria-label="外观">
          <button
            type="button"
            title="日间"
            aria-label="切换为浅色主题"
            aria-pressed={resolvedTheme === 'light'}
            onClick={() => setTheme('light')}
          >
            <Icon name="sun" />
          </button>
          <button
            type="button"
            title="夜间"
            aria-label="切换为深色主题"
            aria-pressed={resolvedTheme === 'dark'}
            onClick={() => setTheme('dark')}
          >
            <Icon name="moon" />
          </button>
        </div>
        <IconButton
          className="side-theme-toggle"
          icon={nextTheme === 'dark' ? 'moon' : 'sun'}
          label={nextThemeLabel}
          onClick={() => setTheme(nextTheme)}
        />
      </div>
    </footer>
  )
}
