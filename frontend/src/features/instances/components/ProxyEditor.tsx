import { useEffect, useState } from 'react'
import { useManager } from '../../../app/ManagerProvider'
import { api, type NodeRow } from '../../../shared/api/client'
import { formatAgo, joinNames } from '../../../shared/lib/format'
import { nodeOccupants } from '../../../shared/model/nodeOccupants'
import { NodeTaskNotice } from '../../../shared/ui/NodeTaskNotice'
import { useNodeTesting } from '../../../shared/lib/useNodeTesting'
import { useNodeQuality } from '../../../shared/lib/useNodeQuality'
import type { ProxyDraft } from '../../../shared/model/proxy'
import { Chip } from '../../../shared/ui/Badge'
import { Button, ButtonLink, Field, Notice, Select } from '../../../shared/ui/Controls'
import { NodeLatency } from '../../../shared/ui/NodeLatency'
import { NodeQualityBadge } from '../../../shared/ui/NodeQuality'

const proxyModes = [
  {
    value: 'subscription' as const,
    label: '订阅节点',
    description: '从已添加的 Clash / Mihomo 订阅里选一个节点',
  },
  {
    value: 'socks' as const,
    label: '手动 SOCKS5',
    description: '自己填写主机、端口、用户名和密码',
  },
]

export function ProxyMode({
  value,
  onChange,
  disabled,
  variant,
}: {
  value: ProxyDraft['mode']
  onChange: (value: ProxyDraft['mode']) => void
  disabled?: boolean
  variant?: 'cards'
}) {
  if (variant === 'cards')
    return (
      <div className="cx-modes" role="radiogroup" aria-label="代理方式" id="proxy-modes">
        {proxyModes.map((option) => (
          <label key={option.value} className={`cx-mode${value === option.value ? ' is-on' : ''}`}>
            <input
              type="radio"
              name="create-proxy-mode"
              value={option.value}
              checked={value === option.value}
              disabled={disabled}
              onChange={() => onChange(option.value)}
            />
            <span className="cx-mode__text">
              <span className="cx-mode__title">{option.label}</span>
              <span className="cx-mode__desc">{option.description}</span>
            </span>
          </label>
        ))}
      </div>
    )

  return (
    <div className="cm-seg" role="group" aria-label="代理方式">
      {proxyModes.map((option) => (
        <button
          type="button"
          key={option.value}
          aria-pressed={value === option.value}
          disabled={disabled}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

export function ProxyEditor({
  value,
  onChange,
  password,
  setPassword,
  hasPassword = false,
  errors = {},
  excludeId,
  returnTo,
  disabled,
  onSelectedNodeChange,
}: {
  value: ProxyDraft
  onChange: (value: ProxyDraft) => void
  password: string
  setPassword: (value: string) => void
  hasPassword?: boolean
  errors?: Record<string, string>
  excludeId?: string
  returnTo: string
  disabled?: boolean
  onSelectedNodeChange?: (node: NodeRow | undefined) => void
}) {
  const { data, subscriptions, isBusy } = useManager()
  const { activeTest, testNodes } = useNodeTesting()
  const { activeQuality, qualityBusy, checkQuality } = useNodeQuality()
  const [nodes, setNodes] = useState<NodeRow[]>([])
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const subscription = subscriptions.find((item) => item.id === value.subscriptionId)
  const updated = subscription?.updated_at
  const tested = subscription?.nodes_tested_at
  const qualityChecked = subscription?.nodes_quality_checked_at
  const testing = activeTest(value.subscriptionId)
  const checking = activeQuality(value.subscriptionId)
  const checkingQuality = qualityBusy(value.subscriptionId)

  useEffect(() => {
    if (!value.subscriptionId && subscriptions[0])
      onChange({ ...value, subscriptionId: subscriptions[0].id })
  }, [subscriptions.length, value.subscriptionId])

  useEffect(() => {
    let cancelled = false
    setNodes([])
    setError('')
    if (!value.subscriptionId) return
    setLoading(true)
    api
      .nodes(value.subscriptionId)
      .then((payload) => {
        if (!cancelled) setNodes(payload.nodes)
      })
      .catch((reason: Error) => {
        if (!cancelled) setError(reason.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [value.subscriptionId, updated, tested, qualityChecked])

  const visible = nodes.filter((node) =>
    `${node.name} ${node.type}`.toLowerCase().includes(query.trim().toLowerCase()),
  )
  const selected = nodes.find((node) => node.id === value.nodeId)
  const testable = visible.filter((node) => node.supported)
  useEffect(() => {
    onSelectedNodeChange?.(selected)
  }, [selected, onSelectedNodeChange])

  const users = nodeOccupants(data, selected?.id || '', selected?.used_by || []).filter(
    (user) => user.id !== excludeId,
  )
  const addLink = `/subscriptions?return=${encodeURIComponent(returnTo)}&add=1`
  const inputProps = (name: string) => ({
    'aria-invalid': errors[name] ? (true as const) : undefined,
    'aria-describedby': errors[name] ? `proxy-${name}-error` : undefined,
  })

  if (value.mode === 'socks')
    return (
      <fieldset className="proxy-fields proxy-editor" disabled={disabled}>
        <div className="cm-grid-2">
          <Field label="Host · 主机" htmlFor="proxy-host" required error={errors.host}>
            <input
              id="proxy-host"
              className="cm-input"
              value={value.host}
              onChange={(event) => onChange({ ...value, host: event.target.value })}
              placeholder="例如 203.0.113.20"
              autoComplete="off"
              spellCheck={false}
              {...inputProps('host')}
            />
          </Field>
          <Field label="Port · 端口" htmlFor="proxy-port" required error={errors.port}>
            <input
              id="proxy-port"
              className="cm-input"
              inputMode="numeric"
              value={value.port}
              onChange={(event) => onChange({ ...value, port: event.target.value })}
              placeholder="1080"
              {...inputProps('port')}
            />
          </Field>
          <Field
            label="Username · 用户名"
            htmlFor="proxy-username"
            required
            error={errors.username}
          >
            <input
              id="proxy-username"
              className="cm-input"
              value={value.username}
              onChange={(event) => onChange({ ...value, username: event.target.value })}
              autoComplete="off"
              spellCheck={false}
              {...inputProps('username')}
            />
          </Field>
          <Field
            label="Password · 密码"
            htmlFor="proxy-password"
            required={!hasPassword}
            error={errors.password}
            help={
              hasPassword
                ? '已保存的密码不会显示。输入新值才会替换。'
                : '密码只用于本次提交，提交后立即清空，不会出现在任务详情里。'
            }
          >
            <input
              id="proxy-password"
              className="cm-input"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={hasPassword ? '已设置 · 留空保留原密码' : ''}
              {...inputProps('password')}
            />
          </Field>
        </div>
      </fieldset>
    )

  if (!subscriptions.length)
    return (
      <div className="form proxy-editor">
        <div className="cm-card inset-empty">
          <div className="cm-empty">
            <h3 className="cm-empty__title">还没有订阅</h3>
            <p className="cm-muted empty-copy">
              添加 Clash / Mihomo 兼容订阅后，就能从节点中选择。已填写的实例名称会保留。
            </p>
            <div className="cm-row">
              <ButtonLink to={addLink} icon="plus" variant="primary">
                添加订阅
              </ButtonLink>
              <Button disabled={disabled} onClick={() => onChange({ ...value, mode: 'socks' })}>
                改用手动 SOCKS5
              </Button>
            </div>
          </div>
        </div>
        {errors.node && <span className="cm-error">{errors.node}</span>}
      </div>
    )

  return (
    <div className="form proxy-editor">
      <div className="cm-grid-2">
        <Field
          label="订阅来源"
          htmlFor="proxy-subscription"
          help="更换订阅来源会清除已选节点，避免选错。"
        >
          <Select
            id="proxy-subscription"
            label="订阅来源"
            value={value.subscriptionId}
            options={subscriptions.map((item) => ({
              value: item.id,
              label: item.name + (item.last_error ? '（刷新失败，显示缓存）' : ''),
            }))}
            disabled={disabled}
            onChange={(subscriptionId) => {
              onChange({ ...value, subscriptionId, nodeId: '' })
              setQuery('')
            }}
          />
        </Field>
        <Field label="搜索节点" htmlFor="proxy-search">
          <input
            id="proxy-search"
            type="search"
            className="cm-input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="输入节点名称或协议"
            disabled={disabled}
          />
        </Field>
      </div>
      <div className="cm-row node-test-tools">
        <span className="cm-help">延迟为经节点访问测试网址的耗时；未测速的节点也可选择。IPPure 系数 0–100%，越低风险越低。</span>
        <div className="cm-actions">
          <Button
            small
            busy={!!testing}
            disabled={disabled || loading || !testable.length || isBusy(value.subscriptionId)}
            onClick={() => void testNodes(value.subscriptionId, testable.map((node) => node.id))}
          >
            {testing ? '正在测速…' : '测速当前列表'}
          </Button>
          <Button
            small
            busy={checkingQuality}
            disabled={disabled || loading || !testable.length || isBusy(value.subscriptionId)}
            onClick={() => void checkQuality(value.subscriptionId, testable.map((node) => node.id))}
          >
            {checkingQuality ? '正在检测 IP…' : '检测当前列表 IP'}
          </Button>
        </div>
      </div>
      {testing && <NodeTaskNotice task={testing} />}
      {checking && <NodeTaskNotice task={checking} />}
      {subscription?.last_error && (
        <Notice tone="warn" title="此订阅最近一次刷新失败">
          下面是 {formatAgo(subscription.updated_at)}
          成功获取的缓存节点，仍可选择。
          {subscription.last_error}
        </Notice>
      )}
      {error && (
        <Notice tone="danger" title="无法读取节点">
          {error}
        </Notice>
      )}
      {loading ? (
        <div className="loading-state">
          <span className="cm-spin" />
          正在读取节点…
        </div>
      ) : visible.length ? (
        <div
          className="cm-nodes"
          role="radiogroup"
          aria-label="选择节点"
          id="proxy-node-list"
          tabIndex={-1}
        >
          {visible.map((node) => {
            const nodeUsers = nodeOccupants(data, node.id, node.used_by).filter(
              (user) => user.id !== excludeId,
            )
            const nodeChecking = !!checking?.node_ids?.includes(node.id)
            return (
              <div
                key={node.id}
                className={`cm-node${value.nodeId === node.id ? ' is-selected' : ''}${!node.supported ? ' is-disabled' : ''}`}
              >
                <input
                  id={`proxy-node-${node.id}`}
                  type="radio"
                  name="proxy-node"
                  checked={value.nodeId === node.id}
                  onChange={() => onChange({ ...value, nodeId: node.id })}
                  disabled={!node.supported || disabled}
                />
                <label className="cm-node__main" htmlFor={`proxy-node-${node.id}`}>
                  <span className="cm-node__name">
                    {node.name}
                    <span className="cm-badge">{node.type}</span>
                    {nodeUsers.length > 0 && (
                      <Chip icon="link">
                        已被 {joinNames(nodeUsers.map((user) => user.name))} 使用
                      </Chip>
                    )}
                  </span>
                  <span className="cm-node__meta">
                    {node.supported ? (
                      <>
                        标识 <span className="cm-mono">{node.id}</span> · 来自「
                        {subscription?.name}」
                      </>
                    ) : (
                      node.reason
                    )}
                  </span>
                </label>
                <div className="cm-node__actions">
                  <NodeLatency node={node} testing={!!testing?.node_ids?.includes(node.id)} />
                  <Button
                    small
                    variant="tint-primary"
                    aria-label={`测速节点 ${node.name}`}
                    disabled={!node.supported || disabled || isBusy(value.subscriptionId)}
                    onClick={() => void testNodes(value.subscriptionId, [node.id])}
                  >
                    测速
                  </Button>
                  <NodeQualityBadge node={node} checking={nodeChecking} />
                  <Button
                    small
                    variant="tint-primary"
                    aria-label={`检测节点 IP ${node.name}`}
                    busy={nodeChecking}
                    disabled={!node.supported || disabled || isBusy(value.subscriptionId) || checkingQuality}
                    onClick={() => void checkQuality(value.subscriptionId, [node.id])}
                  >
                    {nodeChecking ? '检测中…' : '检测 IP'}
                  </Button>
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <p className="cm-help">没有匹配的节点。</p>
      )}
      {errors.node && <span className="cm-error">{errors.node}</span>}
      {value.nodeId && !loading && !selected && (
        <Notice tone="warn" title="原节点已不在当前订阅中">
          请重新选择一个节点，系统不会自动替换。
        </Notice>
      )}
      {users.length > 0 && (
        <Notice
          tone="warn"
          title={`「${selected?.name}」已被 ${joinNames(users.map((user) => user.name))} 使用`}
        >
          可以继续复用。多个实例共用一个节点时，通常会共享同一个出口 IP。
        </Notice>
      )}
      <ButtonLink to={addLink} icon="plus" variant="ghost" small className="align-start">
        添加订阅
      </ButtonLink>
    </div>
  )
}
