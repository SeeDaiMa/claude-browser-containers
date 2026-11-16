import type { Instance } from '../api/client'

export type ProxyDraft = {
  mode: 'subscription' | 'socks'
  subscriptionId: string
  nodeId: string
  host: string
  port: string
  username: string
}
export const emptyProxy: ProxyDraft = {
  mode: 'subscription',
  subscriptionId: '',
  nodeId: '',
  host: '',
  port: '1080',
  username: '',
}

export function proxyDraft(item: Instance): ProxyDraft {
  return {
    mode: item.proxy.mode === 'socks' ? 'socks' : 'subscription',
    subscriptionId: item.proxy.subscription_id || '',
    nodeId: item.proxy.node_id || '',
    host: item.proxy.host || '',
    port: String(item.proxy.port || 1080),
    username: item.proxy.username || '',
  }
}

export function proxyErrors(value: ProxyDraft, password: string, keepPassword = false) {
  const errors: Record<string, string> = {}
  if (value.mode === 'subscription') {
    if (!value.subscriptionId || !value.nodeId) errors.node = '请选择一个支持的订阅节点。'
  } else {
    if (!value.host.trim()) errors.host = '请填写主机地址。'
    if (!/^\d+$/.test(value.port) || Number(value.port) < 1 || Number(value.port) > 65535)
      errors.port = '端口需为 1–65535 的整数。'
    if (!value.username.trim()) errors.username = '请填写用户名。'
    if (!password && !keepPassword) errors.password = '请填写密码。'
  }
  return errors
}

export function proxyPayload(value: ProxyDraft, password: string): Record<string, unknown> {
  return value.mode === 'subscription'
    ? { mode: value.mode, subscription_id: value.subscriptionId, node_id: value.nodeId }
    : {
        mode: value.mode,
        host: value.host.trim(),
        port: Number(value.port),
        username: value.username.trim(),
        password,
      }
}
