import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { useManager } from '../../../app/ManagerProvider'
import { api, type Instance } from '../../../shared/api/client'
import { formatAgo, formatDate } from '../../../shared/lib/format'
import {
  activeTaskFor,
  diagnosticLabel,
  diagnosticTone,
  hasPendingExitChange,
  healthState,
  runState,
  stepText,
} from '../../../shared/model/status'
import { Badge } from '../../../shared/ui/Badge'
import { Button, ButtonLink, Dialog, Notice } from '../../../shared/ui/Controls'
import { Icon } from '../../../shared/ui/Icon'

export function DockerNotice() {
  const { data, refresh, refreshing } = useManager()
  if (!data || data.docker.ok) return null
  return (
    <Notice
      tone="danger"
      title="Docker 未连接"
      actions={
        <>
          <Button small busy={refreshing} onClick={() => void refresh()}>
            重新检测
          </Button>
          <ButtonLink variant="ghost" small to="/settings">
            查看运行环境
          </ButtonLink>
        </>
      }
    >
      已有实例信息仍可查看，但运行状态暂时未知。运行操作已禁用，这不代表实例已停止。
    </Notice>
  )
}

export function Diagnostics({ item }: { item: Instance }) {
  const { data, error } = useManager()
  const connected = !!data?.docker.ok && !error && item.runtime !== 'unknown'
  const service = data?.monitor
  let recoveryLabel = item.monitor?.enabled ? '已启用' : '未启用'
  if (!connected) recoveryLabel = '状态未知'
  else if (service && !service.running) recoveryLabel = '已暂停'
  else if (service?.error) recoveryLabel = '暂时异常'
  else if (item.monitor?.state === 'failed') recoveryLabel = '已停止'
  else if (item.monitor?.state === 'warning' && !item.monitor.enabled) recoveryLabel = '已暂停'
  const detail = !connected
    ? '暂时无法读取最新实例状态，请检查管理台和 Docker 连接。'
    : service?.error || (service && !service.running
      ? '健康监测服务未运行，暂时无法确认实例健康状态。'
      : item.monitor?.detail || '尚未获得健康监测结果。')
  const descriptions: Record<string, string> = {
    browser: '浏览器、网页画面和独立数据',
    proxy: '连接所选上游代理',
    guard: '网络隔离和防火墙保护',
  }
  return (
    <div className="cm-diag">
      <section className="cm-diag__health" aria-label="健康状态">
        <div className="cm-diag__health-heading">
          <div className="cm-diag__health-title">
            <strong>健康状态</strong>
            <Badge state={healthState(item, connected, service)} />
          </div>
          <span className="cm-help">最近检查：{formatAgo(item.monitor?.checked_at)}</span>
        </div>
        <p className="cm-help">{detail}</p>
        {item.monitor && (
          <div className="cm-diag__meta">
            <span>自动恢复：{recoveryLabel}</span>
            <span>已尝试恢复：{item.monitor.recovery_attempts} / 3 次</span>
            {item.monitor.fault_since && <span>异常开始：{formatDate(item.monitor.fault_since)}</span>}
            {item.monitor.next_retry_at && (
              <span>下次重试：{formatDate(new Date(item.monitor.next_retry_at * 1000).toISOString())}</span>
            )}
          </div>
        )}
      </section>
      {item.diagnostics.map((component) => (
        <dl key={component.role} className="cm-diag__item">
          <div className="diagnostic-heading">
            <strong className="cm-mono">{component.role}</strong>
            <Badge
              state={{
                tone: diagnosticTone(component.state),
                label: diagnosticLabel(component.state),
                dot: component.state === 'running' ? undefined : 'hollow',
              }}
            />
          </div>
          <span className="cm-help">{descriptions[component.role]}</span>
          <dt>容器</dt>
          <dd>
            cbc-{item.id}-{component.role}
          </dd>
          {component.role === 'browser' && (
            <>
              <dt>本机画面</dt>
              <dd>{item.url}</dd>
            </>
          )}
        </dl>
      ))}
    </div>
  )
}

export function HealthNotice({ item }: { item?: Instance }) {
  const { data, dismissExitChange, notify } = useManager()
  const [dismissing, setDismissing] = useState<string[]>([])
  if (!data) return null
  const rows = item ? [item] : data.instances
  const faults = rows.filter((row) => ['warning', 'failed'].includes(row.monitor?.state || ''))
  const changed = rows.filter((row) => hasPendingExitChange(row.exit))

  async function closeChange(row: Instance) {
    const changeId = row.exit?.change_id
    if (!changeId) return
    setDismissing((current) => [...current, row.id])
    try {
      await dismissExitChange(row.id, changeId)
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : '关闭提醒失败，请重试。', 'danger')
    } finally {
      setDismissing((current) => current.filter((id) => id !== row.id))
    }
  }

  return (
    <>
      {!item && !!data.config_errors?.length && (
        <Notice tone="danger" title="有实例配置需要修复">
          {data.config_errors.map((issue) => (
            <p key={`${issue.id}-${issue.file}`}>
              {issue.id}：{issue.detail} <span className="cm-mono">{issue.file}</span>
            </p>
          ))}
          原文件和浏览器数据已保留，其他正常实例可以继续使用。新建时会检查端口和网络是否能安全分配。
        </Notice>
      )}
      {data.docker.ok && data.monitor?.error && (
        <Notice tone="warn" title="健康监测暂时异常">{data.monitor.error}</Notice>
      )}
      {faults.length > 0 && (
        <Notice tone={faults.some((row) => row.monitor?.state === 'failed') ? 'danger' : 'warn'} title="实例运行提醒">
          {faults.map((row) => (
            <p key={row.id}>{row.display_name}：{row.monitor?.detail}</p>
          ))}
        </Notice>
      )}
      {changed.map((row) => (
        <Notice
          key={row.id}
          tone="warn"
          title="检测到 Claude 出口 IP 变化"
          actions={
            <>
              <Button small busy={dismissing.includes(row.id)} onClick={() => void closeChange(row)}>
                关闭本次提醒
              </Button>
              <ButtonLink variant="ghost" small to={`/instance/${row.id}?info=ip`}>
                查看 IP 记录
              </ButtonLink>
            </>
          }
        >
          <p>
            {row.display_name}：<span className="cm-mono">{row.exit?.previous_ip}</span> →{' '}
            <span className="cm-mono">{row.exit?.last_success?.ip || row.exit?.ip}</span>，
            {formatAgo(row.exit?.changed_at)}。允许同一节点的出口 IP 变化，实例继续使用原绑定节点。
          </p>
          <p className="cm-help">关闭后保留变化记录，下次 IP 变化时会再次提醒。</p>
        </Notice>
      ))}
    </>
  )
}

export function LifecycleButton({
  item,
  fullLabel = false,
}: {
  item: Instance
  fullLabel?: boolean
}) {
  const { data, error, tasks, isBusy, submitTask, notify } = useManager()
  const task = activeTaskFor(tasks, item.id)
  const running = item.runtime === 'running' || item.runtime === 'degraded'
  const recovering = task?.kind === 'recover'
  const busy = isBusy(item.id, { allowRecovery: recovering })
  const stop = running || recovering
  return (
    <Button
      small
      variant={stop ? 'tint-danger' : 'tint-success'}
      icon={stop ? 'stop' : 'play'}
      busy={busy}
      disabled={!data?.docker.ok || !!error || busy || !!item.deleting}
      title={
        item.deleting
          ? '已开始彻底删除，只能继续清理'
          : task
            ? stepText(task)
            : !data?.docker.ok
              ? 'Docker 未连接'
              : undefined
      }
      onClick={() => {
        submitTask(item.id, () => api.action(item.id, stop ? 'stop' : 'start'), { allowRecovery: recovering }).catch(
          (reason: Error) => notify(reason.message, 'danger'),
        )
      }}
    >
      {busy
        ? runState(item, tasks, !!data?.docker.ok).label
        : recovering
          ? '停止恢复'
          : `${running ? '停止' : '启动'}${fullLabel ? '实例' : ''}`}
    </Button>
  )
}

export function InstanceMenu({
  item,
  onDiagnostics,
  onIpHistory,
}: {
  item: Instance
  onDiagnostics?: () => void
  onIpHistory?: () => void
}) {
  const { data, error, tasks, isBusy, submitTask, refresh, notify } = useManager()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [confirm, setConfirm] = useState<'trash' | 'restart' | null>(null)
  const [checking, setChecking] = useState(false)
  const anchor = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const [position, setPosition] = useState({ top: 0, left: 0 })
  const busy = isBusy(item.id)
  const task = activeTaskFor(tasks, item.id)
  const recovering = task?.kind === 'recover'
  const connected = !!data?.docker.ok && !error
  const running = item.runtime === 'running' || item.runtime === 'degraded'

  useLayoutEffect(() => {
    if (!open || !anchor.current) return
    const rect = anchor.current.getBoundingClientRect()
    const height = menu.current?.offsetHeight || 308
    setPosition({
      top: Math.max(8, Math.min(rect.bottom + 5, window.innerHeight - height - 8)),
      left: Math.max(8, Math.min(rect.right - 222, window.innerWidth - 230)),
    })
    menu.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus()
  }, [open])
  useEffect(() => {
    if (!open) return
    const close = (event: Event) => {
      if (
        !anchor.current?.contains(event.target as Node) &&
        !menu.current?.contains(event.target as Node)
      )
        setOpen(false)
    }
    document.addEventListener('pointerdown', close)
    document.addEventListener('scroll', close, true)
    return () => {
      document.removeEventListener('pointerdown', close)
      document.removeEventListener('scroll', close, true)
    }
  }, [open])

  function act(action: string) {
    setOpen(false)
    return submitTask(item.id, () => api.action(item.id, action))
      .then(() => {
        setConfirm(null)
      })
      .catch((reason: Error) => notify(reason.message, 'danger'))
  }

  return (
    <>
      <button
        ref={anchor}
        type="button"
        className="cm-icon-btn"
        title={`更多操作：${item.display_name}`}
        aria-label={`更多操作：${item.display_name}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <Icon name="more" />
      </button>
      {open &&
        createPortal(
          <div
            ref={menu}
            role="menu"
            aria-label={`${item.display_name}的操作`}
            className="cm-menu instance-menu"
            style={position}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                setOpen(false)
                anchor.current?.focus()
              }
              if (event.key === 'Tab') setOpen(false)
              if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault()
                const buttons = Array.from(
                  menu.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') || [],
                )
                const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
                buttons[
                  (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
                ]?.focus()
              }
            }}
          >
            <button
              role="menuitem"
              disabled={!connected || busy || running || recovering || !!item.deleting}
              onClick={() => void act('start')}
            >
              <Icon name="play" />
              {running ? '启动（已在运行）' : '启动'}
            </button>
            <button
              role="menuitem"
              disabled={!connected || busy || !running || recovering || !!item.deleting}
              onClick={() => {
                setOpen(false)
                setConfirm('restart')
              }}
            >
              <Icon name="restart" />
              重启{!running && '（未运行）'}
            </button>
            <button
              role="menuitem"
              disabled={!connected || (busy && !recovering) || (!running && !recovering)}
              onClick={() => void act('stop')}
            >
              <Icon name="stop" />
              停止{!running && '（未运行）'}
            </button>
            <hr />
            <button
              role="menuitem"
              disabled={busy}
              onClick={() => {
                setOpen(false)
                navigate(`/instance/${item.id}/edit`)
              }}
            >
              <Icon name="edit" />
              编辑实例
            </button>
            <button
              role="menuitem"
              disabled={!connected || busy || !running || checking}
              onClick={async () => {
                setOpen(false)
                setChecking(true)
                try {
                  const result = await api.exit(item.id)
                  notify(
                    result.status === 'ok'
                      ? '代理检测正常'
                      : result.status === 'unknown'
                        ? '出口未测得'
                        : '代理检测异常',
                    result.status === 'ok' ? 'ok' : 'warn',
                    result.detail,
                  )
                  await refresh()
                } catch (reason) {
                  notify(reason instanceof Error ? reason.message : '检测失败', 'danger')
                } finally {
                  setChecking(false)
                }
              }}
            >
              <Icon name="pulse" />
              {checking ? '正在检测…' : '检测代理'}
            </button>
            {onDiagnostics && (
              <button
                role="menuitem"
                onClick={() => {
                  setOpen(false)
                  onDiagnostics()
                }}
              >
                <Icon name="box" />
                健康状态与组件诊断
              </button>
            )}
            {onIpHistory && (
              <button
                role="menuitem"
                onClick={() => {
                  setOpen(false)
                  onIpHistory()
                }}
              >
                <Icon name="clock" />
                IP 变化记录
              </button>
            )}
            <hr />
            <button
              role="menuitem"
              className="is-danger"
              disabled={!connected || busy}
              onClick={() => {
                setOpen(false)
                setConfirm('trash')
              }}
            >
              <Icon name="trash" />
              删除到回收站
            </button>
          </div>,
          document.body,
        )}
      {confirm && (
        <Dialog
          title={
            confirm === 'trash'
              ? `将「${item.display_name}」移到回收站？`
              : `重启「${item.display_name}」？`
          }
          onClose={() => setConfirm(null)}
          locked={busy}
          footer={
            <>
              <Button disabled={busy} onClick={() => setConfirm(null)}>
                取消
              </Button>
              <Button
                variant={confirm === 'trash' ? 'danger' : 'primary'}
                busy={busy}
                onClick={() => void act(confirm)}
              >
                {confirm === 'trash' ? '删除到回收站' : '重启实例'}
              </Button>
            </>
          }
        >
          <p className="dialog-copy">
            {confirm === 'trash'
              ? '实例会先停止，配置和浏览器数据会保留。之后可以在回收站恢复，只有彻底删除才会清除数据。'
              : '会暂时关闭浏览器并重新启动。浏览器数据和已保存的登录状态会保留，请先保存网页中的内容。'}
          </p>
          {activeTaskFor(tasks, item.id) && (
            <Notice title="任务正在进行">{stepText(activeTaskFor(tasks, item.id)!)}</Notice>
          )}
        </Dialog>
      )}
    </>
  )
}

export function Loading() {
  return (
    <div className="loading-state" role="status">
      <span className="cm-spin" />
      正在读取本机实例…
    </div>
  )
}
