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
import { Button, ButtonLink, Field, IconButton, Notice, Select } from '../../../shared/ui/Controls'
import { Icon } from '../../../shared/ui/Icon'
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

const NODE_METRICS_HELP =
  '延迟为经节点访问测试网址的耗时；未测速的节点也可选择。IPPure 系数 0–100%，越低风险越低。'

type NodeSort = 'default' | 'latency' | 'risk'

const nodeSorts: { value: NodeSort; label: string }[] = [
  { value: 'default', label: '默认' },
  { value: 'latency', label: '延迟最低' },
  { value: 'risk', label: '风险最低' },
]

/** 可用节点：协议受支持，且最近一次测速没有失败。 */
function isUsable(node: NodeRow) {
  return node.supported && node.test?.status !== 'failed'
}

/** 排序键：没有结果的节点排在有结果的后面，测速失败或不支持的排在最后。 */
function sortKey(node: NodeRow, sort: NodeSort) {
  if (!node.supported) {
    return Number.MAX_SAFE_INTEGER
  }
  if (sort === 'latency') {
    if (node.test?.status === 'ok' && node.test.delay_ms != null) {
      return node.test.delay_ms
    }
    return node.test?.status === 'failed' ? 2_000_000 : 1_000_000
  }
  const score = node.quality?.fraud_score
  return score == null ? 1_000_000 : score
}

function sortNodes(nodes: NodeRow[], sort: NodeSort) {
  if (sort === 'default') {
    return nodes
  }
  // Array.prototype.sort 是稳定排序，同分节点保持订阅里的原顺序。
  return [...nodes].sort((left, right) => sortKey(left, sort) - sortKey(right, sort))
}

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
  const [sort, setSort] = useState<NodeSort>('default')
  const [onlyUsable, setOnlyUsable] = useState(false)
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

  const keyword = query.trim().toLowerCase()
  const visible = sortNodes(
    nodes.filter(
      (node) =>
        `${node.name} ${node.type}`.toLowerCase().includes(keyword) &&
        // 已选中的节点始终保留，避免过滤后看不到自己的选择。
        (!onlyUsable || isUsable(node) || node.id === value.nodeId),
    ),
    sort,
  )
  const testedCount = nodes.filter((node) => node.test?.status === 'ok').length
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
      {subscription?.last_error && (
        <Notice tone="warn" title="订阅最近一次刷新失败，正在显示缓存节点">
          缓存获取于 {formatAgo(subscription.updated_at)}，节点仍可选择。{subscription.last_error}
        </Notice>
      )}
      {testing && <NodeTaskNotice task={testing} />}
      {checking && <NodeTaskNotice task={checking} />}
      {error && (
        <Notice tone="danger" title="无法读取节点">
          {error}
        </Notice>
      )}

      <div className="node-picker">
        <div className="node-picker__bar">
          <label className="node-picker__search" htmlFor="proxy-search">
            <Icon name="search" />
            <input
              id="proxy-search"
              type="search"
              className="cm-input"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索节点名称或协议"
              aria-label="搜索节点"
              disabled={disabled}
            />
          </label>
          <div className="cm-seg node-picker__sort" role="group" aria-label="节点排序">
            {nodeSorts.map((option) => (
              <button
                type="button"
                key={option.value}
                aria-pressed={sort === option.value}
                disabled={disabled}
                onClick={() => setSort(option.value)}
              >
                {option.label}
              </button>
            ))}
          </div>
          <label className="node-picker__toggle">
            <input
              type="checkbox"
              checked={onlyUsable}
              disabled={disabled}
              onChange={(event) => setOnlyUsable(event.target.checked)}
            />
            只看可用
          </label>
        </div>
        <div className="node-picker__summary">
          <span className="cm-help" title={NODE_METRICS_HELP}>
            {loading
              ? '正在读取节点…'
              : `显示 ${visible.length} / ${nodes.length} 个节点 · 已测速 ${testedCount} 个`}
          </span>
          <div className="cm-actions">
            <Button
              small
              variant="ghost"
              icon="pulse"
              busy={!!testing}
              disabled={disabled || loading || !testable.length || isBusy(value.subscriptionId)}
              onClick={() => void testNodes(value.subscriptionId, testable.map((node) => node.id))}
            >
              {testing ? '正在测速…' : '全部测速'}
            </Button>
            <Button
              small
              variant="ghost"
              icon="shield"
              busy={checkingQuality}
              disabled={disabled || loading || !testable.length || isBusy(value.subscriptionId)}
              onClick={() => void checkQuality(value.subscriptionId, testable.map((node) => node.id))}
            >
              {checkingQuality ? '正在检测 IP…' : '全部检测 IP'}
            </Button>
          </div>
        </div>
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
              const nodeTesting = !!testing?.node_ids?.includes(node.id)
              const nodeChecking = !!checking?.node_ids?.includes(node.id)
              const actionDisabled = !node.supported || disabled || isBusy(value.subscriptionId)
              const rowClass = [
                'cm-node',
                value.nodeId === node.id ? 'is-selected' : '',
                node.supported ? '' : 'is-disabled',
              ]
                .filter(Boolean)
                .join(' ')

              return (
                <div key={node.id} className={rowClass}>
                  <input
                    id={`proxy-node-${node.id}`}
                    type="radio"
                    name="proxy-node"
                    checked={value.nodeId === node.id}
                    onChange={() => onChange({ ...value, nodeId: node.id })}
                    disabled={!node.supported || disabled}
                  />
                  <label
                    className="cm-node__main"
                    htmlFor={`proxy-node-${node.id}`}
                    title={`标识 ${node.id} · 来自「${subscription?.name || '订阅'}」`}
                  >
                    <span className="cm-node__name">
                      <span className="cm-node__title">{node.name}</span>
                      <span className="cm-badge">{node.type}</span>
                      {nodeUsers.length > 0 && (
                        <Chip icon="link">
                          {joinNames(nodeUsers.map((user) => user.name))} 在用
                        </Chip>
                      )}
                    </span>
                    {!node.supported && <span className="cm-node__meta">{node.reason}</span>}
                  </label>
                  <div className="cm-node__actions">
                    <NodeLatency node={node} testing={nodeTesting} />
                    <NodeQualityBadge node={node} checking={nodeChecking} />
                    <IconButton
                      icon="pulse"
                      label={`测速节点 ${node.name}`}
                      title="测速"
                      disabled={actionDisabled}
                      onClick={() => void testNodes(value.subscriptionId, [node.id])}
                    />
                    <IconButton
                      icon="shield"
                      label={`检测节点 IP ${node.name}`}
                      title="检测 IP"
                      disabled={actionDisabled || checkingQuality}
                      onClick={() => void checkQuality(value.subscriptionId, [node.id])}
                    />
                  </div>
                </div>
              )
            })}
          </div>
        ) : (
          <p className="node-picker__empty">
            {nodes.length
              ? '没有符合条件的节点，换个关键词或取消“只看可用”。'
              : '这个订阅里没有节点。'}
          </p>
        )}
      </div>
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
