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
  const [claimingSize, setClaimingSize] = useState(false)
  const lastSizeClaim = useRef(0)
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
  // 同一实例可能同时在多个窗口打开（另一个标签、“在新窗口打开画面”）。远程桌面只有一个分辨率，
  // 最后连上的窗口会把它改成自己的尺寸，其他窗口就会两边留黑。回到本窗口时把分辨率要回来：
  // 临时让画面窄 1px 触发 noVNC 的尺寸检查，再恢复原宽度，noVNC 会按当前可视区重新申请分辨率。
  useEffect(() => {
    if (!showFrame) return
    const claim = () => {
      const now = Date.now()
      if (now - lastSizeClaim.current < 3000) return
      lastSizeClaim.current = now
      setClaimingSize(true)
    }
    const visible = () => {
      if (document.visibilityState === 'visible') claim()
    }
    const stageElement = stage.current
    window.addEventListener('focus', claim)
    document.addEventListener('visibilitychange', visible)
    stageElement?.addEventListener('pointerenter', claim)
    return () => {
      window.removeEventListener('focus', claim)
      document.removeEventListener('visibilitychange', visible)
      stageElement?.removeEventListener('pointerenter', claim)
    }
  }, [showFrame, id])
  useEffect(() => {
    if (!claimingSize) return
    // 留出时间让服务端回应第一次请求；恢复原宽度后 noVNC 会再核对一次并补发。
    const timer = window.setTimeout(() => setClaimingSize(false), 1200)
    return () => window.clearTimeout(timer)
  }, [claimingSize])
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

  // 停止后仍显示最近一次检测到的出口，并标出国家／地区。
  const exitIp = item.exit?.ip || item.exit?.last_success?.ip || ''
  const exitCountry = item.exit?.ip ? formatCountry(item.exit.country_code) : ''
  const exitLabel = exitIp ? `${exitIp}${exitCountry ? ` · ${exitCountry}` : ''}` : '—'

  // 未运行卡片的说明随状态变化：启动中、上次启动失败、普通停止。
  const runtime = runState(item, tasks, connected)
  const idleMessage = task
    ? '正在启动，完成后会自动连接浏览器画面。可以离开工作区，任务会在后台继续。'
    : runtime.tone === 'danger'
      ? '上次启动没有成功，原因见页面顶部的提示。浏览器数据和登录状态仍然保留。'
      : '启动后即可连接浏览器，原有数据和登录状态会保留。'

  function reloadPage() {
    api.reloadPage(id).catch((reason: Error) => notify(reason.message, 'danger'))
  }

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
              {/* 出口 IP 放在前面；节点名常常很长，只放在悬停提示里，避免把 IP 挤掉。 */}
              <div className="cm-kv" title={`${proxy.source} · ${proxy.node}`}>
                <span>出口 IP</span>
                <span className="cm-mono">{exitLabel}</span>
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
              {/* 画面相关操作只在有画面时出现，停止时不再显示一排禁用图标。 */}
              {showFrame && (
                <>
                  <IconButton
                    icon="plug"
                    label="重连画面（不重启浏览器）"
                    onClick={() => setReconnect((value) => value + 1)}
                  />
                  <IconButton
                    icon="refresh"
                    label="刷新网页（容器内）"
                    disabled={busy}
                    onClick={reloadPage}
                  />
                  <IconButton
                    icon={maximized ? 'min' : 'max'}
                    label={maximized ? '退出全屏' : '全屏'}
                    onClick={() => void fullscreen()}
                  />
                  <span className="cm-toolbar__sep" />
                </>
              )}
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
        {showFrame && (
          <>
            <IconButton
              icon="plug"
              label="重连画面"
              onClick={() => setReconnect((value) => value + 1)}
            />
            <IconButton
              icon="refresh"
              label="刷新网页（容器内）"
              disabled={busy}
              onClick={reloadPage}
            />
            <IconButton icon="max" label="全屏" onClick={() => void fullscreen()} />
          </>
        )}
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
              {/* 出口检测只对运行中的实例有效；停止后给出能真正解决问题的入口。 */}
              {showFrame ? (
                <Button small busy={checking} disabled={busy} onClick={() => void checkProxy()}>
                  重新检测
                </Button>
              ) : (
                item.proxy_locked && (
                  <ButtonLink small icon="plus" to="/new">
                    新建实例
                  </ButtonLink>
                )
              )}
            </>
          }
        >
          {item.exit?.detail}
          {showFrame
            ? ' 网页暂时无法联网，浏览器数据和登录状态保留。'
            : ' 实例已停止，浏览器数据和登录状态保留。'}
          {item.proxy_locked && ' 代理已锁定，不能更换节点；需要别的出口时请新建实例。'}
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
                style={claimingSize && !frameStyle ? { width: 'calc(100% - 1px)' } : frameStyle}
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
                {connected ? (
                  <section className="ws-idle" aria-label={`${item.display_name} 未运行`}>
                    <div className="ws-idle__head">
                      <span className="ws-idle__icon">
                        <Icon name="inst" />
                      </span>
                      <div className="ws-idle__title">
                        <h2>{item.display_name}</h2>
                        <Badge state={runState(item, tasks, connected)} />
                      </div>
                    </div>
                    <p className="cm-muted">{idleMessage}</p>
                    <dl className="ws-idle__facts">
                      <div>
                        <dt>{item.exit?.ip ? '出口 IP' : '上次出口 IP'}</dt>
                        <dd className="cm-mono">{exitLabel}</dd>
                      </div>
                      <div>
                        <dt>内存上限</dt>
                        <dd className="cm-num">{formatMemory(item.usage.memory_limit_mib)}</dd>
                      </div>
                      <div className="ws-idle__wide">
                        <dt>节点</dt>
                        <dd title={proxy.node}>{proxy.node}</dd>
                      </div>
                      <div className="ws-idle__wide">
                        <dt>来源</dt>
                        <dd>{proxy.source}</dd>
                      </div>
                    </dl>
                    <div className="ws-idle__actions">
                      <LifecycleButton item={item} fullLabel />
                      {!task && (
                        <ButtonLink small icon="edit" to={`/instance/${id}/edit`}>
                          编辑实例
                        </ButtonLink>
                      )}
                    </div>
                  </section>
                ) : (
                  <div className="overlay-card">
                    <Icon name="plug" className="big" />
                    <h2 className="cm-empty__title">画面暂时无法连接</h2>
                    <p className="cm-muted">
                      请确认 Docker 已启动。实例信息和浏览器数据仍保存在本机。
                    </p>
                  </div>
                )}
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
              {item.created_at && (
                <>
                  <dt>创建时间</dt>
                  <dd>{formatDate(item.created_at)}</dd>
                </>
              )}
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
