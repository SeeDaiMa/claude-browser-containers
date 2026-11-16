import { useEffect, useRef, useState } from 'react'
import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { CreatePage } from '../features/instances/pages/CreatePage'
import { EditPage } from '../features/instances/pages/EditPage'
import { ListPage } from '../features/instances/pages/ListPage'
import { TrashPage } from '../features/instances/pages/TrashPage'
import { WorkspacePage } from '../features/instances/pages/WorkspacePage'
import { SubscriptionsPage } from '../features/subscriptions/pages/SubscriptionsPage'
import { SettingsPage } from '../features/settings/pages/SettingsPage'
import { Button, Empty, IconButton, Notice } from '../shared/ui/Controls'
import { Icon, TONE_ICON } from '../shared/ui/Icon'
import { ManagerProvider, useManager } from './ManagerProvider'
import { Sidebar } from './Sidebar'

export function App() {
  return (
    <ManagerProvider>
      <Shell />
    </ManagerProvider>
  )
}

function Shell() {
  const { error, refresh, refreshing, toasts, dismiss } = useManager()
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
        <Sidebar collapsed={collapsed} />
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
