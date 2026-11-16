import type { ExitInfo, Instance, Overview, Task } from '../api/client'

export type Tone = 'ok' | 'warn' | 'danger' | 'info' | 'neutral'

export type BadgeState = {
  tone: Tone
  label: string
  dot?: 'hollow' | 'square' | 'tri'
  spin?: boolean
}

const ACTIVE_STATES = ['queued', 'running']
const BOOT_KINDS = ['create', 'start', 'restart']
const LIFECYCLE_KINDS = ['create', 'start', 'restart', 'stop', 'trash', 'restore']

export const TASK_LABELS: Record<string, string> = {
  create: '创建中',
  start: '启动中',
  stop: '停止中',
  restart: '重启中',
  trash: '移入回收站中',
  restore: '恢复中',
  purge: '彻底删除中',
  'apply-proxy': '保存代理中',
  'restore-proxy': '恢复代理中',
  refresh: '刷新订阅中',
  'add-subscription': '添加订阅中',
  'update-subscription': '更新订阅中',
  'test-nodes': '节点测速中',
  'check-node-quality': 'IP 质量检测中',
  recover: '恢复原节点中',
}

export function isActive(task: Task) {
  return ACTIVE_STATES.includes(task.state)
}

export function isFinished(task: Task) {
  return ['succeeded', 'failed', 'interrupted'].includes(task.state)
}

export function hasPendingExitChange(exit: ExitInfo | null | undefined) {
  return !!exit?.change_id && exit.change_id !== exit.dismissed_change_id
}

/** 任务列表按时间倒序，取这个实例正在进行的任务。 */
export function activeTaskFor(tasks: Task[], instanceId: string) {
  return tasks.find((task) => task.instance_id === instanceId && isActive(task)) || null
}

function latestTask(tasks: Task[], instanceId: string, kinds: string[]) {
  return tasks.find((task) => task.instance_id === instanceId && kinds.includes(task.kind)) || null
}

/** 最近一次启动类任务失败，且实例没有在运行：视为“启动失败”。 */
export function startFailureFor(item: Instance, tasks: Task[]) {
  if (item.runtime === 'running') return null
  const task = latestTask(tasks, item.id, LIFECYCLE_KINDS)
  if (task && BOOT_KINDS.includes(task.kind) && task.state === 'failed') return task
  return null
}

export function stepText(task: Task) {
  const index = task.phases.findIndex((phase) => phase.state === 'current')
  const position = index >= 0 ? index + 1 : 1
  const prefix = task.state === 'queued' ? '排队中 · ' : ''
  return `${prefix}第 ${position}/${Math.max(task.phases.length, 1)} 步 · ${task.phase}`
}

export function progressText(task: Task) {
  const progress = task.progress
  if (!progress?.total) return stepText(task)
  const current = progress.label ? `，当前：${progress.label}` : ''
  return `已完成 ${progress.completed}/${progress.total}${current}`
}

/** 运行状态：与代理检测分开表达。 */
export function runState(item: Instance, tasks: Task[], dockerOk: boolean): BadgeState {
  if (!dockerOk || item.runtime === 'unknown')
    return { tone: 'neutral', dot: 'hollow', label: '状态未知' }
  const task = activeTaskFor(tasks, item.id)
  if (task) return { tone: 'info', spin: true, label: TASK_LABELS[task.kind] || '处理中' }
  if (item.runtime === 'running') return { tone: 'ok', label: '运行中' }
  if (item.runtime === 'degraded') return { tone: 'warn', dot: 'tri', label: '部分运行' }
  if (startFailureFor(item, tasks)) return { tone: 'danger', dot: 'square', label: '启动失败' }
  return { tone: 'neutral', dot: 'hollow', label: '已停止' }
}

export function proxyState(
  item: Instance,
  tasks: Task[],
  dockerOk: boolean,
  checking = false,
): BadgeState {
  if (!dockerOk) return { tone: 'neutral', dot: 'hollow', label: 'Docker 未连接' }
  if (checking) return { tone: 'info', spin: true, label: '检测中' }
  if (item.proxy.mode === 'none') return { tone: 'warn', dot: 'tri', label: '未配置代理' }
  const exit = item.exit
  if (['running', 'degraded'].includes(item.runtime)) {
    if (!exit) return { tone: 'neutral', dot: 'hollow', label: '出口未检测' }
    if (exit.status === 'failed') return { tone: 'danger', dot: 'square', label: '代理异常' }
    if (exit.status === 'unknown') return { tone: 'warn', dot: 'tri', label: '出口未测得' }
    return { tone: 'ok', label: '代理正常' }
  }
  if (startFailureFor(item, tasks) && exit?.status === 'failed')
    return { tone: 'danger', dot: 'square', label: '代理连接失败' }
  if (!exit) return { tone: 'neutral', dot: 'hollow', label: '出口未检测' }
  if (exit.status === 'ok') return { tone: 'neutral', dot: 'hollow', label: '最近一次检测正常' }
  if (exit.status === 'failed') return { tone: 'warn', dot: 'tri', label: '最近一次检测异常' }
  return { tone: 'warn', dot: 'tri', label: '出口未测得' }
}

/** 使用后端监测结果；管理台或监测服务不可用时，不沿用旧的正常状态。 */
export function healthState(
  item: Instance,
  connected: boolean,
  service?: Overview['monitor'],
): BadgeState {
  if (!connected || item.runtime === 'unknown')
    return { tone: 'neutral', dot: 'hollow', label: '状态未知' }
  if (service && !service.running)
    return { tone: 'warn', dot: 'tri', label: '监测已中断' }
  if (service?.error) return { tone: 'warn', dot: 'tri', label: '监测异常' }
  if (!item.monitor) return { tone: 'neutral', dot: 'hollow', label: '尚未监测' }
  switch (item.monitor.state) {
    case 'healthy':
      return { tone: 'ok', label: '监测正常' }
    case 'warning':
      return { tone: 'warn', dot: 'tri', label: '发现异常' }
    case 'failed':
      return { tone: 'danger', dot: 'square', label: '恢复已停止' }
    case 'idle':
      return { tone: 'neutral', dot: 'hollow', label: '监测已暂停' }
    default:
      return { tone: 'neutral', dot: 'hollow', label: '状态未知' }
  }
}

/** 列表只显示一个状态，运行或代理有问题时优先呈现已有异常结果。 */
export function instanceState(
  item: Instance,
  tasks: Task[],
  connected: boolean,
  service?: Overview['monitor'],
): BadgeState {
  const runtime = runState(item, tasks, connected)
  if (runtime.tone !== 'ok') return runtime
  const health = healthState(item, connected, service)
  if (health.tone === 'danger') return health
  const proxy = proxyState(item, tasks, connected)
  if (proxy.tone === 'danger') return proxy
  return health.tone === 'ok' && proxy.tone !== 'ok' ? proxy : health
}

export function proxySummary(item: Instance) {
  const proxy = item.proxy
  if (proxy.mode === 'socks') return { node: `${proxy.host}:${proxy.port}`, source: '手动 SOCKS5' }
  if (proxy.mode === 'subscription') {
    return {
      node: proxy.label || '—',
      source: `订阅 · ${proxy.subscription_name || '已删除的订阅'}`,
    }
  }
  return { node: '未配置', source: '没有代理，不能访问公网' }
}

const DIAG_LABELS: Record<string, string> = {
  running: '运行中',
  exited: '已停止',
  created: '已创建',
  paused: '已暂停',
  restarting: '重启中',
  dead: '异常退出',
  absent: '未创建',
  unknown: '未知',
  unhealthy: '健康检查失败',
  starting: '健康检查中',
}

export function diagnosticLabel(state: string) {
  return DIAG_LABELS[state] || state
}

export function diagnosticTone(state: string): Tone {
  if (state === 'running') return 'ok'
  if (state === 'dead' || state === 'restarting' || state === 'unhealthy') return 'danger'
  if (state === 'starting') return 'warn'
  return 'neutral'
}
