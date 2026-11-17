import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { api, type Overview, type Subscription, type Task } from '../shared/api/client'
import { emptyProxy, type ProxyDraft } from '../shared/model/proxy'
import { hasPendingExitChange, isActive, isFinished, type Tone } from '../shared/model/status'

export type Theme = 'light' | 'dark' | 'auto'
export type Toast = {
  id: string
  title: string
  detail: string
  tone: Tone
}
export type CreateDraft = {
  name: string
  proxy: ProxyDraft
  requestId: string
  taskId: string
  step: number
}
const newDraft = (): CreateDraft => ({
  name: '',
  proxy: { ...emptyProxy },
  requestId: crypto.randomUUID(),
  taskId: '',
  step: 0,
})
const ACTION_NAMES: Record<string, string> = {
  create: '创建并启动',
  start: '启动',
  stop: '停止',
  restart: '重启',
  trash: '移入回收站',
  restore: '恢复',
  purge: '彻底删除',
  'apply-proxy': '保存代理',
  'restore-proxy': '恢复代理',
  refresh: '刷新订阅',
  'add-subscription': '添加订阅',
  'update-subscription': '更新订阅',
  'test-nodes': '节点测速',
  'check-node-quality': 'IP 质量检测',
  recover: '恢复原节点',
}

/** 轮询结果与当前内容相同就沿用旧对象，React 不会因此重渲染整个管理台。 */
function keepIfSame<T>(previous: T, next: T): T {
  return JSON.stringify(previous) === JSON.stringify(next) ? previous : next
}

function savedTheme(): Theme {
  try {
    const value = localStorage.getItem('cbc-theme')
    if (value === 'light' || value === 'dark') return value
  } catch {
    /* 存储不可用时仍可使用。 */
  }
  return 'auto'
}

type TaskOptions = { allowRecovery?: boolean }
type OverviewListener = (snapshot: Overview | null) => void
type ManagerState = {
  data: Overview | null
  tasks: Task[]
  subscriptions: Subscription[]
  error: string
  refreshing: boolean
  refresh: () => Promise<void>
  subscribeOverview: (listener: OverviewListener) => () => void
  dismissExitChange: (id: string, changeId: string) => Promise<void>
  submitTask: (key: string, request: () => Promise<Task>, options?: TaskOptions) => Promise<Task>
  isBusy: (id: string, options?: TaskOptions) => boolean
  theme: Theme
  resolvedTheme: 'light' | 'dark'
  setTheme: (theme: Theme) => void
  toasts: Toast[]
  notify: (title: string, tone?: Tone, detail?: string) => void
  dismiss: (id: string) => void
  draft: CreateDraft
  setDraft: React.Dispatch<React.SetStateAction<CreateDraft>>
  resetDraft: () => void
}
const ManagerContext = createContext<ManagerState | null>(null)

export function ManagerProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<Overview | null>(null)
  const [tasks, setTasks] = useState<Task[]>([])
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([])
  const [error, setError] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [pending, setPending] = useState<string[]>([])
  const locks = useRef(new Set<string>())
  const polling = useRef(false)
  const latestOverview = useRef<Overview | null>(null)
  const overviewListeners = useRef(new Set<OverviewListener>())
  const hasActiveTasks = useRef(false)
  const seen = useRef(new Map<string, string>())
  const healthSeen = useRef(new Map<string, { state: string; changeId?: string }>())
  const dismissedExitChanges = useRef(new Map<string, string>())
  const unconfirmedTasks = useRef(new Map<string, Task>())
  const [theme, setThemeValue] = useState<Theme>(savedTheme)
  const [systemDark, setSystemDark] = useState(
    () => matchMedia('(prefers-color-scheme: dark)').matches,
  )
  const [toasts, setToasts] = useState<Toast[]>([])
  const [draft, setDraft] = useState<CreateDraft>(newDraft)
  const resolvedTheme = theme === 'auto' ? (systemDark ? 'dark' : 'light') : theme

  const subscribeOverview = useCallback((listener: OverviewListener) => {
    overviewListeners.current.add(listener)
    listener(latestOverview.current)
    return () => { overviewListeners.current.delete(listener) }
  }, [])

  const notify = useCallback((title: string, tone: Tone = 'info', detail = '') => {
    const notice = {
      id: crypto.randomUUID(),
      title,
      detail,
      tone,
    }
    setToasts((items) => [...items.slice(-2), notice])
  }, [])

  // 后台轮询不切换 refreshing，刷新按钮只在用户手动刷新时显示忙碌。
  const load = useCallback(async (showBusy: boolean) => {
    if (polling.current) return
    polling.current = true
    if (showBusy) setRefreshing(true)
    try {
      const results = await Promise.allSettled([api.overview(), api.tasks(), api.subscriptions()])
      const [overview, taskList, subscriptionList] = results
      if (overview.status === 'fulfilled') {
        // 已在途的旧快照不能重新显示刚刚关闭的提醒；新变化仍按独立 ID 提醒。
        const latest = {
          ...overview.value,
          instances: overview.value.instances.map((item) =>
            item.exit?.change_id && dismissedExitChanges.current.get(item.id) === item.exit.change_id
              ? { ...item, exit: { ...item.exit, dismissed_change_id: item.exit.change_id } }
              : item,
          ),
        }
        setData((current) => keepIfSame(current, latest))
        // 每次实际观测都通知工作区；内容相同时仍沿用 data，避免全局重渲染。
        latestOverview.current = latest
        overviewListeners.current.forEach((listener) => listener(latest))
        for (const item of latest.instances) {
          const state = item.monitor?.state || ''
          const previous = healthSeen.current.get(item.id)
          if (previous) {
            if (previous.state !== state && item.monitor?.state === 'failed')
              notify(`${item.display_name}：自动恢复已停止`, 'danger', item.monitor.detail)
            else if (previous.state !== state && item.monitor?.state === 'warning')
              notify(`${item.display_name}：运行异常`, 'warn', item.monitor.detail)
            if (hasPendingExitChange(item.exit) && previous.changeId !== item.exit?.change_id)
              notify(
                `${item.display_name}：Claude 出口 IP 已变化`,
                'warn',
                `${item.exit?.previous_ip} → ${item.exit?.last_success?.ip || item.exit?.ip}`,
              )
          }
          healthSeen.current.set(item.id, { state, changeId: item.exit?.change_id })
        }
      } else {
        latestOverview.current = null
        overviewListeners.current.forEach((listener) => listener(null))
      }
      if (subscriptionList.status === 'fulfilled') {
        const incoming = subscriptionList.value.subscriptions
        setSubscriptions((current) => keepIfSame(current, incoming))
      }
      if (taskList.status === 'fulfilled') {
        for (const task of taskList.value.tasks) {
          const previous = seen.current.get(task.id)
          if (previous && previous !== task.state && isFinished(task)) {
            const name = ACTION_NAMES[task.kind] || '后台任务'
            notify(
              `${name}${task.cancel_requested && task.state === 'interrupted' ? '已取消' : task.state === 'succeeded' ? '已完成' : task.state === 'interrupted' ? '已中断' : '失败'}`,
              task.state === 'succeeded' ? 'ok' : task.cancel_requested ? 'info' : 'danger',
              task.error || task.result.detail || '',
            )
          }
          seen.current.set(task.id, task.state)
        }
        const incoming = taskList.value.tasks
        incoming.forEach((task) => unconfirmedTasks.current.delete(task.id))
        const merged = [...incoming, ...unconfirmedTasks.current.values()]
        hasActiveTasks.current = merged.some(isActive)
        setTasks((current) => keepIfSame(current, merged))
      }
      const failed = results.find((result) => result.status === 'rejected')
      setError(
        failed?.status === 'rejected'
          ? failed.reason instanceof Error
            ? failed.reason.message
            : '管理台连接失败，请稍后重试。'
          : '',
      )
    } finally {
      polling.current = false
      if (showBusy) setRefreshing(false)
    }
  }, [notify])
  const refresh = useCallback(() => load(true), [load])
  const resetDraft = useCallback(() => setDraft(newDraft()), [])

  async function dismissExitChange(id: string, changeId: string) {
    const exit = await api.dismissExitChange(id, changeId)
    dismissedExitChanges.current.set(id, changeId)
    setData((current) => current && {
      ...current,
      instances: current.instances.map((item) =>
        item.id === id && item.exit?.change_id === exit.change_id ? { ...item, exit } : item,
      ),
    })
  }

  useEffect(() => {
    let cancelled = false
    let running = false
    let timer: number
    const poll = async () => {
      if (running || cancelled) return
      running = true
      await load(false)
      running = false
      if (cancelled) return
      timer = window.setTimeout(
        () => void poll(),
        document.hidden ? 30000 : hasActiveTasks.current ? 2000 : 5000,
      )
    }
    const visible = () => {
      if (!document.hidden) {
        window.clearTimeout(timer)
        void poll()
      }
    }
    void poll()
    document.addEventListener('visibilitychange', visible)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', visible)
    }
  }, [load])

  useEffect(() => {
    document.documentElement.dataset.theme = resolvedTheme
  }, [resolvedTheme])
  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)')
    const update = () => setSystemDark(media.matches)
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])

  useEffect(() => {
    if (!toasts.length) return
    const timer = window.setTimeout(() => setToasts((items) => items.slice(1)), 6000)
    return () => window.clearTimeout(timer)
  }, [toasts])

  async function submitTask(key: string, request: () => Promise<Task>, options: TaskOptions = {}) {
    if (
      locks.current.has(key) ||
      tasks.some(
        (task) =>
          (task.instance_id === key || task.subscription_id === key) &&
          isActive(task) &&
          (!options.allowRecovery || task.kind !== 'recover'),
      )
    )
      throw new Error('已有任务正在处理，请等待完成。')
    locks.current.add(key)
    setPending([...locks.current])
    try {
      const task = await request()
      seen.current.set(task.id, task.state)
      unconfirmedTasks.current.set(task.id, task)
      setTasks((items) => [task, ...items.filter((item) => item.id !== task.id)])
      void load(false)
      return task
    } finally {
      locks.current.delete(key)
      setPending([...locks.current])
    }
  }

  function setTheme(value: Theme) {
    setThemeValue(value)
    try {
      localStorage.setItem('cbc-theme', value)
    } catch {
      /* 主题仍在当前页面生效。 */
    }
  }

  return (
    <ManagerContext.Provider
      value={{
        data,
        tasks,
        subscriptions,
        error,
        refreshing,
        refresh,
        subscribeOverview,
        dismissExitChange,
        submitTask,
        isBusy: (id, options = {}) =>
          pending.includes(id) ||
          tasks.some(
            (task) =>
              (task.instance_id === id || task.subscription_id === id) &&
              isActive(task) &&
              (!options.allowRecovery || task.kind !== 'recover'),
          ),
        theme,
        resolvedTheme,
        setTheme,
        toasts,
        notify,
        dismiss: (id) => setToasts((items) => items.filter((item) => item.id !== id)),
        draft,
        setDraft,
        resetDraft,
      }}
    >
      {children}
    </ManagerContext.Provider>
  )
}

export function useManager() {
  const value = useContext(ManagerContext)
  if (!value) throw new Error('管理台上下文未初始化。')
  return value
}
