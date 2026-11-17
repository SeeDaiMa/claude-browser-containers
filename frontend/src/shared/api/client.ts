export type ExitChange = {
  id: string
  previous_ip: string
  ip: string
  changed_at: string
}
export type ExitInfo = {
  status: 'ok' | 'failed' | 'unknown'
  ip: string | null
  country_code?: string | null
  detail: string
  checked_at: string
  last_success?: { ip: string; country_code: string | null; checked_at: string }
  previous_ip?: string
  changed_at?: string
  change_id?: string
  dismissed_change_id?: string
  dismissed_at?: string
  history?: ExitChange[]
}
export type Instance = {
  id: string
  display_name: string
  created_at: string | null
  historical: boolean
  trashed_at: string | null
  deleting?: boolean
  /** 第一次启动成功后为 true，之后不能更换节点或 SOCKS5。 */
  proxy_locked: boolean
  port: number
  url: string
  runtime: 'running' | 'stopped' | 'degraded' | 'unknown'
  proxy: {
    mode: string
    label: string
    stale?: boolean
    subscription_name?: string
    host?: string
    port?: number
    username?: string
    password_set?: boolean
    node_id?: string
    subscription_id?: string
  }
  exit: ExitInfo | null
  usage: { memory_mib: number | null; cpu_percent: number | null; memory_limit_mib: number | null }
  diagnostics: { role: string; state: string }[]
  monitor?: {
    enabled: boolean
    state: 'idle' | 'healthy' | 'warning' | 'failed' | 'unknown'
    detail: string
    fault_since: string | null
    recovery_attempts: number
    next_retry_at: number | null
    checked_at: string | null
  }
}
export type Overview = {
  docker: { ok: boolean; detail: string }
  summary: {
    total: number
    running: number | null
    memory_total_mib: number | null
    docker_vm_rss_mib: number | null
    docker_helper_rss_mib: number | null
    manager_rss_mib: number | null
  }
  instances: Instance[]
  trash: Instance[]
  exit_warnings: { ip: string; instances: { id: string; name: string; checked_at: string }[] }[]
  config_errors?: { id: string; file: string; detail: string }[]
  monitor?: { running: boolean; checked_at: string | null; error: string }
}
export type Task = {
  id: string
  kind: string
  instance_id: string
  subscription_id?: string
  node_ids?: string[]
  state: string
  phase: string
  phases: { name: string; state: string }[]
  error: string
  result: { instance_id?: string; id?: string; detail?: string; node_count?: number }
  progress?: { completed: number; total: number; label: string }
  cancel_requested?: boolean
}
export type Subscription = {
  id: string
  name: string
  node_count: number
  updated_at: string | null
  nodes_tested_at?: string | null
  nodes_quality_checked_at?: string | null
  last_error: string
}
export type NodeRow = {
  id: string
  name: string
  type: string
  port: number
  supported: boolean
  reason: string
  used_by: { id: string; name: string; trashed: boolean }[]
  quality?: {
    status: 'ok' | 'partial' | 'failed'
    ip: string | null
    asn: number | null
    is_residential: boolean | null
    is_broadcast: boolean | null
    fraud_score: number | null
    detail: string
    checked_at: string
  } | null
  test?: {
    status: 'ok' | 'failed'
    delay_ms: number | null
    detail: string
    checked_at: string
  } | null
}
export type Settings = {
  version: string
  resources: {
    browser_memory_mib: number
    browser_cpus: number
    proxy_memory_mib: number
    guard_memory_mib: number
  }
  images: string[]
  data_directory: string
  config_directory: string
}

const headers = { 'Content-Type': 'application/json', 'X-CBC-Request': '1' }

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options)
  let payload
  try {
    payload = await response.json()
  } catch {
    throw new Error('管理台连接异常，请确认本机管理服务已启动。')
  }
  if (!response.ok) throw new Error(payload.error || '操作失败')
  return payload as T
}

export const api = {
  settings: () => request<Settings>('/api/settings'),
  overview: () => request<Overview>('/api/instances'),
  tasks: () => request<{ tasks: Task[] }>('/api/tasks'),
  subscriptions: () => request<{ subscriptions: Subscription[] }>('/api/subscriptions'),
  nodes: (id: string) => request<{ nodes: NodeRow[] }>(`/api/subscriptions/${id}/nodes`),
  testNodes: (id: string, node_ids: string[]) =>
    request<Task>(`/api/subscriptions/${id}/nodes/test`, {
      method: 'POST', headers, body: JSON.stringify({ node_ids }),
    }),
  checkNodeQuality: (id: string, node_ids: string[]) =>
    request<Task>(`/api/subscriptions/${id}/nodes/quality`, {
      method: 'POST', headers, body: JSON.stringify({ node_ids }),
    }),
  addSubscription: (name: string, url: string) =>
    request<Task>('/api/subscriptions', {
      method: 'POST',
      headers,
      body: JSON.stringify({ name, url }),
    }),
  refreshSubscription: (id: string) =>
    request<Task>(`/api/subscriptions/${id}/refresh`, { method: 'POST', headers, body: '{}' }),
  renameSubscription: (id: string, name: string) =>
    request<Pick<Subscription, 'id' | 'name'>>(`/api/subscriptions/${id}`, {
      method: 'PATCH',
      headers,
      body: JSON.stringify({ name }),
    }),
  updateSubscription: (id: string, name: string, url: string) =>
    request<Task>(`/api/subscriptions/${id}`, {
      method: 'PATCH', headers, body: JSON.stringify({ name, url }),
    }),
  cancelTask: (id: string) =>
    request<Task>(`/api/tasks/${id}/cancel`, { method: 'POST', headers, body: '{}' }),
  deleteSubscription: (id: string) =>
    request<{ ok: boolean }>(`/api/subscriptions/${id}`, { method: 'DELETE', headers }),
  createInstance: (body: Record<string, unknown>) =>
    request<Task>('/api/instances', { method: 'POST', headers, body: JSON.stringify(body) }),
  action: (id: string, action: string) =>
    request<Task>(`/api/instances/${id}/${action}`, { method: 'POST', headers, body: '{}' }),
  rename: (id: string, display_name: string) =>
    request<{ id: string }>(`/api/instances/${id}`, {
      method: 'PATCH',
      headers,
      body: JSON.stringify({ display_name }),
    }),
  proxy: (id: string, body: Record<string, unknown>) =>
    request<Task>(`/api/instances/${id}/proxy`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    }),
  reconfirmProxy: (id: string) =>
    request<{ ok: boolean; node: string; detail: string }>(`/api/instances/${id}/proxy/reconfirm`, {
      method: 'POST',
      headers,
      body: '{}',
    }),
  purge: (id: string, confirm_name: string) =>
    request<Task>(`/api/instances/${id}/purge`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ confirm_name }),
    }),
  exit: (id: string) =>
    request<ExitInfo>(`/api/instances/${id}/exit`, { method: 'POST', headers, body: '{}' }),
  dismissExitChange: (id: string, change_id: string) =>
    request<ExitInfo>(`/api/instances/${id}/exit/dismiss`, {
      method: 'POST', headers, body: JSON.stringify({ change_id }),
    }),
  reloadPage: (id: string) =>
    request<{ ok: boolean }>(`/api/instances/${id}/reload-page`, {
      method: 'POST',
      headers,
      body: '{}',
    }),
}
