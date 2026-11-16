import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { useManager } from '../../../app/ManagerProvider'
import { api } from '../../../shared/api/client'
import {
  formatAgo,
  formatCountry,
  formatCpu,
  formatDate,
  formatMemory,
} from '../../../shared/lib/format'
import {
  activeTaskFor,
  proxyState,
  proxySummary,
  runState,
  stepText,
} from '../../../shared/model/status'
import { Badge } from '../../../shared/ui/Badge'
import { Button, ButtonLink, Empty, IconButton, Notice, Select } from '../../../shared/ui/Controls'
import { Icon } from '../../../shared/ui/Icon'
import {
  Diagnostics,
  DockerNotice,
  HealthNotice,
  InstanceMenu,
  LifecycleButton,
  Loading,
} from '../components/InstanceControls'
import { ExitHistory } from '../components/ExitHistory'

// 固定版本 Chromium 的最小窗口宽度为 500px；高度需容纳工具栏及提示条。
const MIN_BROWSER_VIEWPORT = { width: 500, height: 144 }

export function WorkspacePage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const { data, error, tasks, isBusy, refresh, notify } = useManager()
  const item = data?.instances.find((row) => row.id === id)
  const [toolbar, setToolbar] = useState<HTMLElement | null>(null)
  const [reconnect, setReconnect] = useState(0)
  const [info, setInfo] = useState(false)
  const [checking, setChecking] = useState(false)
  const [loading, setLoading] = useState(true)
  const [slowConnection, setSlowConnection] = useState(false)
  const [maximized, setMaximized] = useState(false)
  const [diagnostics, setDiagnostics] = useState(false)
  const [frameError, setFrameError] = useState(false)
  const [frameSize, setFrameSize] = useState({ width: 0, height: 0 })
  const stage = useRef<HTMLDivElement>(null)
  const frameViewport = useRef<HTMLDivElement>(null)
  const infoPanel = useRef<HTMLDivElement>(null)
  const infoButton = useRef<HTMLButtonElement>(null)
  const ipHistory = useRef<HTMLElement>(null)
  const connected = !!data?.docker.ok && !error
  const showFrame = !!item && ['running', 'degraded'].includes(item.runtime) && !!data?.docker.ok

  useEffect(() => {
    setToolbar(document.getElementById('workspace-toolbar'))
  }, [])
  useEffect(() => {
    if (new URLSearchParams(location.search).get('info') === 'ip') setInfo(true)
  }, [location])
  useEffect(() => {
    if (info && new URLSearchParams(location.search).get('info') === 'ip')
      ipHistory.current?.scrollIntoView({ block: 'nearest' })
  }, [info, location, !!item])
  useLayoutEffect(() => {
    const viewport = frameViewport.current
    if (!viewport) return
    const measure = () => {
      const { width, height } = viewport.getBoundingClientRect()
      setFrameSize((current) =>
        current.width === width && current.height === height ? current : { width, height },
      )
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(viewport)
    return () => observer.disconnect()
  }, [id, showFrame])
  useEffect(() => {
    setLoading(true)
    setSlowConnection(false)
    setFrameError(false)
    const timer = window.setTimeout(() => setSlowConnection(true), 15000)
    return () => window.clearTimeout(timer)
  }, [id, reconnect, showFrame])
  useEffect(() => {
    const update = () => setMaximized(!!document.fullscreenElement)
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !document.fullscreenElement) setMaximized(false)
    }
    document.addEventListener('fullscreenchange', update)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('fullscreenchange', update)
      document.removeEventListener('keydown', escape)
      if (document.fullscreenElement) void document.exitFullscreen()
    }
  }, [])
  useEffect(() => {
    if (!info) return
    const close = (event: PointerEvent) => {
      if (
        !infoPanel.current?.contains(event.target as Node) &&
        !infoButton.current?.contains(event.target as Node)
      )
        setInfo(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setInfo(false)
        infoButton.current?.focus()
      }
    }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', close)
      document.removeEventListener('keydown', escape)
    }
  }, [info])

  if (!data) return <Loading />
  if (!item)
    return (
      <Empty title="这个实例不在列表中" actions={<ButtonLink to="/">返回实例列表</ButtonLink>}>
        如果已移入回收站，可以先恢复再打开。
      </Empty>
    )
  const busy = isBusy(id)
  const task = activeTaskFor(tasks, id)
  const proxy = proxySummary(item)
  const frameScale =
    frameSize.width > 0 && frameSize.height > 0
      ? Math.min(
          1,
          frameSize.width / MIN_BROWSER_VIEWPORT.width,
          frameSize.height / MIN_BROWSER_VIEWPORT.height,
        )
      : 1
  // 只在小于浏览器最小尺寸时缩放 iframe，桌面尺寸与显示区域仍保持同一比例。
  const frameStyle =
    frameScale < 1
      ? {
          width: `${Math.max(MIN_BROWSER_VIEWPORT.width, Math.round(frameSize.width / frameScale))}px`,
          height: `${Math.max(MIN_BROWSER_VIEWPORT.height, Math.round(frameSize.height / frameScale))}px`,
          transform: `scale(${frameScale})`,
          transformOrigin: 'top left',
        }
      : undefined
  // 让远程桌面随工作区调整分辨率，完整铺满可用区域，避免等比缩放留下黑边。
  const frameUrl = new URL(item.url)
  frameUrl.searchParams.set('resize', 'remote')
  frameUrl.searchParams.set('quality', '9')
  frameUrl.searchParams.set('view_clip', 'false')
  frameUrl.searchParams.set('host_clipboard_sync', 'true')
  const lastClose = tasks.find(
    (row) =>
      row.instance_id === id && ['stop', 'restart', 'trash', 'start', 'create'].includes(row.kind),
  )

  async function checkProxy() {
    setChecking(true)
    try {
      const result = await api.exit(id)
      notify(
        result.status === 'ok'
          ? '代理检测正常'
          : result.status === 'failed'
            ? '代理检测异常'
            : '出口未测得',
        result.status === 'ok' ? 'ok' : 'warn',
        result.detail,
      )
      await refresh()
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '检测失败', 'danger')
    } finally {
      setChecking(false)
    }
  }

  async function fullscreen() {
    if (document.fullscreenElement) {
      await document.exitFullscreen()
      return
    }
    if (maximized) {
      setMaximized(false)
      return
    }
    try {
      await stage.current?.requestFullscreen()
    } catch {
      setMaximized(true)
    }
  }

  return (
    <div className="ws">
      {toolbar &&
        createPortal(
          <>
            <IconButton
              icon="back"
              label="返回实例列表（实例继续运行）"
              onClick={() => navigate('/')}
            />
            <Select
              label="切换实例"
              value={id}
              onChange={(next) => {
                setInfo(false)
                navigate(`/instance/${next}`)
              }}
              options={data.instances.map((row) => ({
                value: row.id,
                label: row.display_name,
                status: runState(row, tasks, connected),
              }))}
            />
            <div className="cm-toolbar__group ws-status">
              <Badge state={proxyState(item, tasks, connected, checking)} />
            </div>
            <span className="cm-toolbar__sep" />
            <div className="cm-toolbar__group ws-kv">
              <div className="cm-kv">
                <span>代理 · 出口</span>
                <span title={`${proxy.source} · ${proxy.node}`}>
                  {proxy.node} · <span className="cm-mono">{item.exit?.ip || '—'}</span>
                </span>
              </div>
              <div className="cm-kv kv-mem">
                <span>内存</span>
                <span className="cm-num">
                  {connected ? formatMemory(item.usage.memory_mib) : '未知'}
                </span>
              </div>
            </div>
            <span className="spacer" />
            <div className="cm-toolbar__group ws-actions">
              <IconButton
                icon="plug"
                label="重连画面（不重启浏览器）"
                disabled={!showFrame}
                onClick={() => setReconnect((value) => value + 1)}
              />
              <IconButton
                icon="refresh"
                label="刷新网页（容器内）"
                disabled={!showFrame || busy}
                onClick={() =>
                  api.reloadPage(id).catch((reason: Error) => notify(reason.message, 'danger'))
                }
              />
              <IconButton
                icon={maximized ? 'min' : 'max'}
                label={maximized ? '退出全屏' : '全屏'}
                onClick={() => void fullscreen()}
              />
              <IconButton
                icon="edit"
                label="编辑实例"
                disabled={busy}
                onClick={() => navigate(`/instance/${id}/edit`)}
              />
              <button
                ref={infoButton}
                className="cm-icon-btn"
                type="button"
                aria-label="实例详细信息"
                title="实例详细信息"
                aria-expanded={info}
                onClick={() => setInfo(!info)}
              >
                <Icon name="info" />
              </button>
              <LifecycleButton item={item} fullLabel />
              <InstanceMenu
                item={item}
                onDiagnostics={() => {
                  setInfo(true)
                  setDiagnostics(!diagnostics)
                }}
              />
            </div>
          </>,
          toolbar,
        )}
      <div className="workspace-mobile-tools">
        <div className="cm-status">
          <Badge state={runState(item, tasks, connected)} />
          <Badge state={proxyState(item, tasks, connected, checking)} />
        </div>
        <span className="spacer" />
        <LifecycleButton item={item} />
        <IconButton
          icon="plug"
          label="重连画面"
          disabled={!showFrame}
          onClick={() => setReconnect((value) => value + 1)}
        />
        <IconButton
          icon="refresh"
          label="刷新网页（容器内）"
          disabled={!showFrame || busy}
          onClick={() =>
            api.reloadPage(id).catch((reason: Error) => notify(reason.message, 'danger'))
          }
        />
        <IconButton icon="max" label="全屏" onClick={() => void fullscreen()} />
        <InstanceMenu
          item={item}
          onDiagnostics={() => {
            setInfo(true)
            setDiagnostics(true)
          }}
        />
      </div>
      <DockerNotice />
      <HealthNotice item={item} />
      {task && (
        <Notice title={runState(item, tasks, connected).label}>
          {stepText(task)}。可以离开工作区，任务会在后台继续。
        </Notice>
      )}
      {!task &&
        showFrame &&
        lastClose?.state === 'failed' &&
        ['stop', 'restart', 'trash'].includes(lastClose.kind) && (
          <Notice tone="warn" title="上次操作未完成">
            {lastClose.error} 如果网页弹出关闭确认，请先在画面中处理，再重试操作。
          </Notice>
        )}
      {!task && connected && item.exit?.status === 'failed' && (
        <Notice
          tone="danger"
          title="代理异常"
          actions={
            <>
              {!item.proxy_locked && (
                <ButtonLink small to={`/instance/${id}/edit`}>
                  编辑代理
                </ButtonLink>
              )}
              <Button small busy={checking} disabled={busy} onClick={() => void checkProxy()}>
                重新检测
              </Button>
            </>
          }
        >
          {item.exit?.detail} 网页暂时无法联网，浏览器数据和登录状态保留。
          {item.proxy_locked && ' 代理已锁定，不能更换节点。'}
        </Notice>
      )}
      {!task && item.proxy.stale && (
        <Notice
          tone="warn"
          title="节点需要重新确认"
          actions={
            <ButtonLink small to={`/instance/${id}/edit`}>
              重新确认节点
            </ButtonLink>
          }
        >
          原订阅节点已变化或消失，系统不会自动换节点。
        </Notice>
      )}
      <div
        ref={stage}
        className={`ws-stage${maximized && !document.fullscreenElement ? ' is-max' : ''}`}
      >
        {maximized && (
          <IconButton
            className="workspace-fullscreen-exit"
            icon="min"
            label="退出全屏"
            onClick={() => void fullscreen()}
          />
        )}
        <div
          className={`cm-browser${item.exit?.status === 'failed' ? ' is-alert' : ''}`}
          aria-label={`浏览器画面：${item.display_name}`}
        >
          <div ref={frameViewport} className="cm-browser__view real-browser-view">
            {showFrame && (
              <iframe
                key={`${id}-${reconnect}`}
                title={`${item.display_name}的浏览器画面`}
                src={frameUrl.toString()}
                style={frameStyle}
                allow="clipboard-read; clipboard-write; fullscreen"
                onLoad={() => {
                  setLoading(false)
                  setSlowConnection(false)
                }}
                onError={() => {
                  setLoading(false)
                  setFrameError(true)
                }}
              />
            )}
            {!showFrame ? (
              <div className="cm-browser__overlay">
                <div className="overlay-card">
                  <Icon name={connected ? 'inst' : 'plug'} className="big" />
                  <h2 className="cm-empty__title">
                    {connected ? '实例已停止' : '画面暂时无法连接'}
                  </h2>
                  <p className="cm-muted">
                    {connected
                      ? '启动后即可连接浏览器，原有数据和登录状态会保留。'
                      : '请确认 Docker 已启动。实例信息和浏览器数据仍保存在本机。'}
                  </p>
                  {connected && <LifecycleButton item={item} fullLabel />}
                </div>
              </div>
            ) : (
              (loading || frameError) && (
                <div className="cm-browser__overlay">
                  <div className="overlay-card">
                    {frameError || slowConnection ? (
                      <Icon name="plug" className="big" />
                    ) : (
                      <span className="cm-spin" />
                    )}
                    <h2 className="cm-empty__title">
                      {frameError || slowConnection ? '浏览器画面尚未连接' : '正在连接浏览器画面…'}
                    </h2>
                    <p className="cm-help">
                      {slowConnection || frameError
                        ? '实例可能仍在启动，可以重连画面。重连不会重启浏览器。'
                        : '只连接当前实例的画面。离开工作区不会停止实例。'}
                    </p>
                    {(slowConnection || frameError) && (
                      <Button onClick={() => setReconnect((value) => value + 1)}>重连画面</Button>
                    )}
                  </div>
                </div>
              )
            )}
          </div>
        </div>
      </div>
      {info && (
        <div ref={infoPanel} className="cm-card ws-info" role="dialog" aria-label="实例详细信息">
          <section className="ws-info__sec">
            <div className="diagnostic-heading">
              <h2 className="cm-card__title">实例信息</h2>
              <IconButton icon="x" label="关闭详细信息" onClick={() => setInfo(false)} />
            </div>
            <dl>
              <dt>名称</dt>
              <dd>{item.display_name}</dd>
              <dt>内部 ID</dt>
              <dd className="cm-mono">{item.id}</dd>
              <dt>创建时间</dt>
              <dd>{formatDate(item.created_at)}</dd>
              <dt>画面连接</dt>
              <dd>仅当前实例</dd>
            </dl>
            {showFrame && (
              <a
                className="cm-btn align-start"
                href={frameUrl.toString()}
                target="_blank"
                rel="noreferrer"
              >
                <Icon name="open" />
                在新窗口打开画面
              </a>
            )}
          </section>
          <section className="ws-info__sec">
            <h2 className="cm-card__title">代理</h2>
            <dl>
              <dt>来源</dt>
              <dd>{proxy.source}</dd>
              <dt>节点</dt>
              <dd>{proxy.node}</dd>
              <dt>出口 IP</dt>
              <dd>
                <span className="cm-mono">{item.exit?.ip || '—'}</span>
                {item.exit?.ip && (
                  <div className="cm-help">{formatCountry(item.exit.country_code)}</div>
                )}
              </dd>
              <dt>检测</dt>
              <dd>
                <Badge state={proxyState(item, tasks, connected, checking)} />
                <div className="cm-help">{formatAgo(item.exit?.checked_at)}</div>
              </dd>
              {!item.exit?.ip && item.exit?.last_success && (
                <>
                  <dt>上次成功出口</dt>
                  <dd className="cm-mono">{item.exit.last_success.ip}</dd>
                  <dt>成功时间</dt>
                  <dd>{formatDate(item.exit.last_success.checked_at)}</dd>
                </>
              )}
            </dl>
            <Button
              small
              className="align-start"
              icon="pulse"
              busy={checking}
              disabled={!showFrame || busy}
              onClick={() => void checkProxy()}
            >
              检测代理
            </Button>
          </section>
          <section ref={ipHistory} className="ws-info__sec" aria-label="出口 IP 变化记录">
            <h2 className="cm-card__title">出口 IP 变化记录</h2>
            <ExitHistory exit={item.exit} compact />
          </section>
          <section className="ws-info__sec">
            <h2 className="cm-card__title">资源</h2>
            <dl>
              <dt>内存</dt>
              <dd className="cm-num">
                {connected ? formatMemory(item.usage.memory_mib) : '未知'} /{' '}
                {formatMemory(item.usage.memory_limit_mib)}
              </dd>
              <dt>CPU</dt>
              <dd className="cm-num">{connected ? formatCpu(item.usage.cpu_percent) : '未知'}</dd>
            </dl>
            <span className="cm-help">内存上限不等于实际占用。</span>
          </section>
          <section className="ws-info__sec">
            <details
              open={diagnostics}
              onToggle={(event) => setDiagnostics(event.currentTarget.open)}
            >
              <summary>健康状态与组件诊断</summary>
              <Diagnostics item={item} />
            </details>
          </section>
        </div>
      )}
    </div>
  )
}
