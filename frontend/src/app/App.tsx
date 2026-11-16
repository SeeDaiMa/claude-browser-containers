import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { CreatePage } from '../features/instances/pages/CreatePage'
import { EditPage } from '../features/instances/pages/EditPage'
import { ListPage } from '../features/instances/pages/ListPage'
import { TrashPage } from '../features/instances/pages/TrashPage'
import { WorkspacePage } from '../features/instances/pages/WorkspacePage'
import { SubscriptionsPage } from '../features/subscriptions/pages/SubscriptionsPage'
import { SettingsPage } from '../features/settings/pages/SettingsPage'
import { Button, ButtonLink, Empty, IconButton, Notice } from '../shared/ui/Controls'
import { Icon, Logo, TONE_ICON, type IconName } from '../shared/ui/Icon'
import { formatMemory } from '../shared/lib/format'
import { isActive, isFinished, runState } from '../shared/model/status'
import { ManagerProvider, useManager } from './ManagerProvider'

export function App() {
  return (
    <ManagerProvider>
      <Shell />
    </ManagerProvider>
  )
}

function Shell() {
  const {
    data,
    tasks,
    subscriptions,
    error,
    refresh,
    refreshing,
    resolvedTheme,
    setTheme,
    toasts,
    dismiss,
    draft,
    resetDraft,
  } = useManager()
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(() => window.innerWidth <= 1024)
  const content = useRef<HTMLElement>(null)
  const workspace = /^\/instance\/[^/]+$/.test(location.pathname)
  const section =
    location.pathname === '/subscriptions'
      ? '代理库'
      : location.pathname === '/trash'
        ? '回收站'
        : location.pathname === '/settings'
          ? '设置'
          : '实例'
  const crumb =
    location.pathname === '/new'
      ? '创建实例'
      : location.pathname.endsWith('/edit')
        ? '编辑实例'
        : ''
  const connected = !!data?.docker.ok && !error
  const shortcutInstances = data?.instances.slice(0, 6) || []
  const activeTasks = tasks.filter(isActive).length
  const createTask = tasks.find((task) => task.id === draft.taskId)
  const beginCreate = () => {
    if (createTask && isFinished(createTask)) resetDraft()
  }
  const links: { to: string; label: string; icon: IconName; count?: number }[] = [
    { to: '/', label: '实例', icon: 'inst', count: data?.instances.length },
    { to: '/subscriptions', label: '代理库', icon: 'proxy', count: subscriptions.length },
    { to: '/trash', label: '回收站', icon: 'trash', count: data?.trash.length },
    { to: '/settings', label: '设置', icon: 'settings' },
  ]

  useEffect(() => {
    content.current?.scrollTo(0, 0)
  }, [location.pathname])
  useEffect(() => {
    const media = matchMedia('(max-width: 1024px)')
    const resize = () => setCollapsed(media.matches)
    media.addEventListener('change', resize)
    return () => media.removeEventListener('change', resize)
  }, [])

  return (
    <>
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <div className={`app${collapsed ? ' is-collapsed' : ''}`}>
        <aside className="cm-side side" aria-label="Claude 容器侧栏">
          <div className="brand" title="Claude 容器">
            <Logo />
            <div className="brand-copy">
              <span className="brand-name">Claude 容器</span>
              <span className="brand-subtitle">独立浏览器工作空间</span>
            </div>
          </div>
          {connected ? (
            <ButtonLink
              className="side-create"
              to="/new"
              icon="plus"
              variant="tint-primary"
              aria-label="新建实例"
              onClick={beginCreate}
            >
              <span className="nav-label">新建实例</span>
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
              <span className="nav-label">新建实例</span>
            </Button>
          )}
          <div className="side-section-title">工作空间</div>
          <nav id="nav" aria-label="主导航">
            {links.map((link) => (
              <Link
                key={link.to}
                to={link.to}
                title={link.label}
                aria-label={link.label}
                className="cm-nav-item"
                aria-current={
                  (link.to === '/' ? section === '实例' : location.pathname === link.to)
                    ? 'page'
                    : undefined
                }
              >
                <Icon name={link.icon} />
                <span className="nav-label">{link.label}</span>
                {link.count !== undefined && (
                  <span className="cm-nav-item__count">{link.count}</span>
                )}
              </Link>
            ))}
          </nav>
          <section className="side-shortcuts" aria-label="实例快捷入口">
            <div className="side-section-title">
              <span>实例快捷入口</span>
              <span>{data?.instances.length ?? '—'}</span>
            </div>
            <div className="side-instance-list">
              {shortcutInstances.map((item) => {
                const state = runState(item, tasks, connected)
                const selected =
                  location.pathname === `/instance/${item.id}` ||
                  location.pathname === `/instance/${item.id}/edit`
                return (
                  <Link
                    key={item.id}
                    to={`/instance/${item.id}`}
                    className="side-instance"
                    aria-label={`打开实例：${item.display_name}`}
                    aria-current={selected ? 'page' : undefined}
                    title={`${item.display_name} · ${state.label}`}
                  >
                    <span className={`side-instance-icon tone-${state.tone}`}>
                      <Icon name="inst" />
                    </span>
                    <span className="side-instance-copy">
                      <strong>{item.display_name}</strong>
                      <span>{state.label}</span>
                    </span>
                    <span className={`side-instance-state tone-${state.tone}`}>
                      {state.spin ? (
                        <span className="cm-spin" aria-hidden="true" />
                      ) : (
                        <span
                          className={`cm-dot${state.dot ? ` cm-dot--${state.dot}` : ''}`}
                          aria-hidden="true"
                        />
                      )}
                    </span>
                  </Link>
                )
              })}
              {!data && <p className="side-empty">正在读取实例…</p>}
              {data?.instances.length === 0 && (
                <p className="side-empty">创建第一个实例，开始独立工作。</p>
              )}
            </div>
            {data && data.instances.length > shortcutInstances.length && (
              <Link className="side-all" to="/">
                查看全部 {data.instances.length} 个实例
                <Icon name="chevR" />
              </Link>
            )}
          </section>
          <section className="side-overview" aria-label="运行概况">
            <div className="side-overview-heading">
              <span>运行概况</span>
              <Link to="/settings" title="查看运行环境" aria-label="查看运行环境">
                <span className={`cm-dot ${connected ? 'tone-ok' : ''}`} />
                {error || !data ? '状态未知' : data.docker.ok ? '已连接' : '未连接'}
              </Link>
            </div>
            <dl>
              <div>
                <dt>运行中</dt>
                <dd>
                  {connected ? (data?.summary.running ?? '—') : '未知'}
                  <span> / {data?.summary.total ?? '—'}</span>
                </dd>
              </div>
              <div>
                <dt>容器内存</dt>
                <dd>{connected ? formatMemory(data?.summary.memory_total_mib) : '—'}</dd>
              </div>
              <div>
                <dt>后台任务</dt>
                <dd>{error ? '—' : activeTasks ? `${activeTasks} 个进行中` : '暂无任务'}</dd>
              </div>
            </dl>
          </section>
          <div className="side-appearance">
            <span className="side-appearance-label">外观</span>
            <div className="cm-seg side-theme-options" role="group" aria-label="侧栏主题">
              <button
                type="button"
                aria-label="切换为浅色主题"
                aria-pressed={resolvedTheme === 'light'}
                onClick={() => setTheme('light')}
              >
                <Icon name="sun" />
                日间
              </button>
              <button
                type="button"
                aria-label="切换为深色主题"
                aria-pressed={resolvedTheme === 'dark'}
                onClick={() => setTheme('dark')}
              >
                <Icon name="moon" />
                夜间
              </button>
            </div>
            <IconButton
              className="side-theme-toggle"
              icon={resolvedTheme === 'dark' ? 'sun' : 'moon'}
              label={`切换为${resolvedTheme === 'dark' ? '浅色' : '深色'}主题`}
              onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
            />
          </div>
          <div className="side-foot">
            <span>
              <Icon name="shield" />
              本机运行 · 数据独立保存
            </span>
            <span>
              Claude 容器 <span className="cm-num">1.0.0</span>
            </span>
          </div>
        </aside>
        <div className="main">
          <header className={`top${workspace ? ' is-ws' : ''}`}>
            <div className="top-left">
              <IconButton
                icon="panel"
                label={collapsed ? '展开侧栏' : '收起侧栏'}
                onClick={() => setCollapsed(!collapsed)}
              />
              {!workspace && (
                <>
                  <strong className="top-title">{section}</strong>
                  {crumb && <span className="crumb">{crumb}</span>}
                </>
              )}
            </div>
            <div className="top-ws" id="workspace-toolbar" />
            <span className="top-spacer" />
          </header>
          <main
            ref={content}
            id="main-content"
            tabIndex={-1}
            className={`content${workspace ? ' is-ws' : ''}`}
          >
            {error && (
              <div className="connection-error">
                <Notice
                  tone="danger"
                  title="无法更新管理台数据"
                  actions={
                    <Button small busy={refreshing} onClick={() => void refresh()}>
                      重新连接
                    </Button>
                  }
                >
                  {error} 已读取的信息仍保留，运行操作暂时禁用。
                </Notice>
              </div>
            )}
            <Routes>
              <Route path="/" element={<ListPage />} />
              <Route path="/new" element={<CreatePage />} />
              <Route path="/subscriptions" element={<SubscriptionsPage />} />
              <Route path="/trash" element={<TrashPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/instance/:id" element={<WorkspacePage />} />
              <Route path="/instance/:id/edit" element={<EditPage />} />
              <Route
                path="*"
                element={
                  <Empty
                    title="没有这个页面"
                    actions={
                      <NavLink to="/" className="cm-btn">
                        返回实例列表
                      </NavLink>
                    }
                  />
                }
              />
            </Routes>
          </main>
        </div>
      </div>
      <div className="cm-toasts" aria-live="polite">
        {toasts.map((item) => (
          <div key={item.id} className={`cm-toast cm-toast--${item.tone}`}>
            <Icon name={TONE_ICON[item.tone] || 'info'} />
            <div className="toast-copy">
              <strong>{item.title}</strong>
              {item.detail && <div className="cm-help">{item.detail}</div>}
            </div>
            <IconButton icon="x" label="关闭通知" onClick={() => dismiss(item.id)} />
          </div>
        ))}
      </div>
    </>
  )
}
