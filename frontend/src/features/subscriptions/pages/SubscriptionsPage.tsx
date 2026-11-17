import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useManager } from '../../../app/ManagerProvider'
import { api, type NodeRow, type Subscription, type Task } from '../../../shared/api/client'
import { formatAgo, formatDate, joinNames } from '../../../shared/lib/format'
import { countryFromNodeName } from '../../../shared/lib/nodeCountry'
import { nodeOccupants } from '../../../shared/model/nodeOccupants'
import { useNodeTesting } from '../../../shared/lib/useNodeTesting'
import { useNodeQuality } from '../../../shared/lib/useNodeQuality'
import { isActive, isFinished, TASK_LABELS } from '../../../shared/model/status'
import { NodeTaskNotice } from '../../../shared/ui/NodeTaskNotice'
import { Badge } from '../../../shared/ui/Badge'
import {
  Button,
  ButtonLink,
  Card,
  Dialog,
  Empty,
  Field,
  Notice,
  PageHead,
  ProgressStages,
  Select,
} from '../../../shared/ui/Controls'
import { Icon } from '../../../shared/ui/Icon'
import { NodeLatency } from '../../../shared/ui/NodeLatency'
import { NodeQualityCells } from '../../../shared/ui/NodeQuality'

type LibraryPreferences = {
  tab: 'subscriptions' | 'nodes'
  source: string
  country: string
  query: string
}
const LIBRARY_PREFERENCES_KEY = 'cbc-library-preferences'
type LibraryNode = NodeRow & { subscriptionId: string }
const nodeKey = (node: LibraryNode) => `${node.subscriptionId}:${node.id}`

function savedLibraryPreferences(): LibraryPreferences {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(LIBRARY_PREFERENCES_KEY) || 'null')
    if (saved && typeof saved === 'object') {
      const values = saved as Partial<LibraryPreferences>
      return {
        tab: values.tab === 'nodes' ? 'nodes' : 'subscriptions',
        source: typeof values.source === 'string' && values.source ? values.source : 'all',
        country: typeof values.country === 'string' && /^(?:[A-Z]{2}|unknown)$/.test(values.country)
          ? values.country
          : 'all',
        query: typeof values.query === 'string' ? values.query : '',
      }
    }
  } catch {
    /* 存储不可用或内容损坏时仍可使用。 */
  }
  return { tab: 'subscriptions', source: 'all', country: 'all', query: '' }
}

export function SubscriptionsPage() {
  const { data, subscriptions, tasks, submitTask, isBusy, refresh, notify, draft } = useManager()
  const { activeTest, testNodes } = useNodeTesting()
  const { activeQuality, qualityBusy, checkQuality } = useNodeQuality()
  const [params, setParams] = useSearchParams()
  const returnParam = params.get('return') || ''
  const back = /^\/(new|instance\/[^/]+\/edit)$/.test(returnParam) ? returnParam : ''
  const [preferences, setPreferences] = useState<LibraryPreferences>(() => {
    const saved = savedLibraryPreferences()
    const view = params.get('view')
    return {
      ...saved,
      tab: view === 'nodes'
        ? 'nodes'
        : view === 'subscriptions' || params.get('add') === '1'
          ? 'subscriptions'
          : saved.tab,
    }
  })
  const { tab, query } = preferences
  const [adding, setAdding] = useState(params.get('add') === '1')
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const [addTaskId, setAddTaskId] = useState('')
  const [submittedTask, setSubmittedTask] = useState<Task | null>(null)
  const [removing, setRemoving] = useState<Subscription | null>(null)
  const [renaming, setRenaming] = useState<Subscription | null>(null)
  const [renameName, setRenameName] = useState('')
  const [renameUrl, setRenameUrl] = useState('')
  const [renameError, setRenameError] = useState('')
  const [renamePending, setRenamePending] = useState(false)
  const [nodes, setNodes] = useState<LibraryNode[]>([])
  const [selectedNodeKeys, setSelectedNodeKeys] = useState<Set<string>>(() => new Set())
  const [nodeError, setNodeError] = useState('')
  const [nodeLoading, setNodeLoading] = useState(false)
  // 首次读取完成前不能显示“没有匹配的节点”；之后后台刷新时保留旧表格，避免闪烁。
  const [nodesFetched, setNodesFetched] = useState(false)
  const nodeRequests = useRef(new Map<string, { key: string; request: Promise<LibraryNode[]> }>())
  const [nodeAction, setNodeAction] = useState<'test' | 'quality' | null>(null)
  const addTask = tasks.find((task) => task.id === addTaskId) || submittedTask
  const addingBusy = pending || !!(addTask && isActive(addTask))

  useEffect(() => {
    try {
      localStorage.setItem(LIBRARY_PREFERENCES_KEY, JSON.stringify(preferences))
    } catch {
      /* 存储不可用时，筛选仍在当前页面生效。 */
    }
  }, [preferences])

  function changeTab(tab: LibraryPreferences['tab']) {
    setPreferences((previous) => ({ ...previous, tab }))
    setSelectedNodeKeys(new Set())
    if (params.has('view')) {
      const nextParams = new URLSearchParams(params)
      nextParams.set('view', tab)
      setParams(nextParams, { replace: true })
    }
  }

  function changeFilters(changes: Partial<Pick<LibraryPreferences, 'source' | 'country' | 'query'>>) {
    setPreferences((previous) => ({ ...previous, ...changes }))
    setSelectedNodeKeys(new Set())
  }

  useEffect(() => {
    if (addTask?.state === 'succeeded') {
      setAdding(false)
      setName('')
      setAddTaskId('')
      setSubmittedTask(null)
      void refresh()
    }
  }, [addTask?.id, addTask?.state])

  useEffect(() => {
    if (tab !== 'nodes') return
    let cancelled = false
    const cache = nodeRequests.current
    const currentIds = new Set(subscriptions.map((subscription) => subscription.id))
    for (const id of cache.keys()) {
      if (!currentIds.has(id)) cache.delete(id)
    }
    setNodeLoading(true)
    setNodeError('')
    Promise.all(
      subscriptions.map((subscription) => {
        const key = JSON.stringify([subscription.updated_at, subscription.nodes_tested_at, subscription.nodes_quality_checked_at])
        const cached = cache.get(subscription.id)
        if (cached?.key === key) return cached.request
        const request = api.nodes(subscription.id).then((payload) =>
          payload.nodes.map((node) => ({ ...node, subscriptionId: subscription.id })),
        )
        const entry = { key, request }
        cache.set(subscription.id, entry)
        void request.catch(() => {
          if (cache.get(subscription.id) === entry) cache.delete(subscription.id)
        })
        return request
      }),
    )
      .then((rows) => {
        if (!cancelled) {
          const nextNodes = rows.flat()
          setNodes(nextNodes)
          const available = new Set(nextNodes.filter((node) => node.supported).map(nodeKey))
          setSelectedNodeKeys((previous) => new Set([...previous].filter((key) => available.has(key))))
        }
      })
      .catch((reason: Error) => {
        if (!cancelled) setNodeError(reason.message)
      })
      .finally(() => {
        if (!cancelled) {
          setNodeLoading(false)
          setNodesFetched(true)
        }
      })
    return () => {
      cancelled = true
    }
  }, [tab, subscriptions])

  async function add() {
    const validation: Record<string, string> = {}
    if (!name.trim()) validation.name = '请填写订阅名称。'
    try {
      const parsed = new URL(url)
      if (parsed.protocol !== 'https:')
        validation.url = '请填写 HTTPS 订阅地址。'
    } catch {
      validation.url = '请填写有效的订阅地址。'
    }
    setErrors(validation)
    if (Object.keys(validation).length || addingBusy) return
    const submittedUrl = url
    setPending(true)
    setError('')
    try {
      const task = await submitTask('add-subscription', () =>
        api.addSubscription(name.trim(), submittedUrl),
      )
      setUrl('')
      setAddTaskId(task.id)
      setSubmittedTask(task)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '添加失败')
    } finally {
      setPending(false)
    }
  }

  async function saveSubscriptionName() {
    if (!renaming || renamePending) return
    const cleaned = renameName.trim()
    if (!cleaned || cleaned.length > 40) {
      setRenameError('订阅名称需要 1–40 个字符。')
      return
    }
    const nextUrl = renameUrl.trim()
    if (nextUrl) {
      try {
        const parsed = new URL(nextUrl)
        if (parsed.protocol !== 'https:') {
          setRenameError('请填写 HTTPS 订阅地址。')
          return
        }
      } catch {
        setRenameError('请填写有效的订阅地址。')
        return
      }
    }
    setRenamePending(true)
    setRenameError('')
    try {
      if (nextUrl) {
        await submitTask(renaming.id, () => api.updateSubscription(renaming.id, cleaned, nextUrl))
      } else {
        await api.renameSubscription(renaming.id, cleaned)
        await refresh()
      }
      setRenaming(null)
      setRenameUrl('')
      notify(nextUrl ? '订阅更新已提交，完成后会通知你' : '订阅名称已保存', nextUrl ? 'info' : 'ok')
    } catch (reason) {
      setRenameError(reason instanceof Error ? reason.message : '保存失败，请重试。')
    } finally {
      setRenamePending(false)
    }
  }

  const subscriptionById = new Map(subscriptions.map((item) => [item.id, item]))
  const source = subscriptionById.has(preferences.source) ? preferences.source : 'all'
  const nodeTotal = subscriptions.reduce((total, item) => total + item.node_count, 0)
  const nodesWithCountry = useMemo(
    () => nodes.map((node) => ({ ...node, country: countryFromNodeName(node.name) })),
    [nodes],
  )
  const countries = new Map(nodesWithCountry.map((node) => [
    node.country?.code || 'unknown',
    node.country?.label || '未识别国家／地区',
  ]))
  const country = countries.has(preferences.country) ? preferences.country : 'all'
  const searchQuery = query.trim().toLowerCase()
  const visible = nodesWithCountry.filter(
    (node) =>
      (source === 'all' || source === node.subscriptionId) &&
      (country === 'all' || country === (node.country?.code || 'unknown')) &&
      `${node.name} ${node.type}`.toLowerCase().includes(searchQuery),
  )
  const testable = visible.filter((node) => node.supported)
  const selectedNodes = testable.filter((node) => selectedNodeKeys.has(nodeKey(node)))
  const allVisibleSelected = testable.length > 0 && selectedNodes.length === testable.length
  const nodeActionsDisabled =
    nodeLoading || !!nodeError || !selectedNodes.length ||
    selectedNodes.some((node) => isBusy(node.subscriptionId) || qualityBusy(node.subscriptionId))
  const testingSelected = selectedNodes.some((node) => !!activeTest(node.subscriptionId))
  const checkingSelected = selectedNodes.some((node) => qualityBusy(node.subscriptionId))
  const showNodeLoading = !nodesFetched || (nodeLoading && !nodes.length)

  function toggleNode(node: LibraryNode, checked: boolean) {
    setSelectedNodeKeys((previous) => {
      const next = new Set(previous)
      if (checked) next.add(nodeKey(node))
      else next.delete(nodeKey(node))
      return next
    })
  }

  async function runSelectedNodeAction(action: 'test' | 'quality') {
    if (nodeActionsDisabled) return
    const groups = new Map<string, string[]>()
    for (const node of selectedNodes) {
      const group = groups.get(node.subscriptionId) || []
      group.push(node.id)
      groups.set(node.subscriptionId, group)
    }
    const run = action === 'test' ? testNodes : checkQuality
    await Promise.all([...groups].map(([id, nodeIds]) => run(id, nodeIds)))
  }
  return (
    <div className="pg">
      <PageHead title="代理库">订阅来源与节点。刷新失败会保留旧节点，不会自动换节点。</PageHead>
      {back && (
        <Notice
          title={
            back === '/new'
              ? `正在创建实例${draft.name ? `「${draft.name}」` : ''}`
              : '正在编辑实例'
          }
          actions={
            <ButtonLink small variant="primary" to={back}>
              返回{back === '/new' ? '创建' : '编辑'}实例
            </ButtonLink>
          }
        >
          添加订阅后可以返回继续。
        </Notice>
      )}
      <div className="tabs-row">
        <div className="cm-seg" role="tablist" aria-label="代理库视图">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'subscriptions'}
            aria-pressed={tab === 'subscriptions'}
            onClick={() => changeTab('subscriptions')}
          >
            订阅（{subscriptions.length}）
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'nodes'}
            aria-pressed={tab === 'nodes'}
            onClick={() => changeTab('nodes')}
          >
            节点（{nodeTotal}）
          </button>
        </div>
        <span className="spacer" />
        <Button
          variant="primary"
          icon="plus"
          disabled={adding}
          onClick={() => {
            setAdding(true)
            setError('')
            setErrors({})
          }}
        >
          添加订阅
        </Button>
      </div>
      {adding && (
        <Card
          title="添加订阅"
          footer={
            <>
              <Button
                disabled={addingBusy}
                onClick={() => {
                  setAdding(false)
                  setUrl('')
                  params.delete('add')
                  setParams(params, { replace: true })
                }}
              >
                取消
              </Button>
              <Button variant="primary" busy={addingBusy} onClick={() => void add()}>
                {addingBusy ? '正在添加…' : '添加订阅'}
              </Button>
            </>
          }
        >
          <fieldset disabled={addingBusy} className="proxy-fields">
            <div className="cm-grid-2">
              <Field label="订阅名称" htmlFor="subscription-name" required error={errors.name}>
                <input
                  id="subscription-name"
                  className="cm-input"
                  value={name}
                  maxLength={24}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="例如：主力订阅"
                  autoComplete="off"
                />
              </Field>
              <Field
                label="Clash / Mihomo 兼容订阅地址"
                htmlFor="subscription-url"
                required
                error={errors.url}
                help="地址只用于本次提交，提交后立即清空。已保存的地址和令牌不会回显。"
              >
                <input
                  id="subscription-url"
                  className="cm-input"
                  type="password"
                  value={url}
                  onChange={(event) => setUrl(event.target.value)}
                  placeholder="https://…"
                  autoComplete="new-password"
                />
              </Field>
            </div>
          </fieldset>
          {error && (
            <Notice tone="danger" title="添加失败">
              {error}
            </Notice>
          )}
          {addTask && (
            <>
              <ProgressStages task={addTask} />
              {isFinished(addTask) && addTask.state !== 'succeeded' && (
                <Notice tone="danger" title="订阅未添加">
                  {addTask.error || '任务已中断。'} 请重新填写订阅地址后重试。
                </Notice>
              )}
            </>
          )}
        </Card>
      )}
      {tab === 'subscriptions' &&
        (!subscriptions.length ? (
          <Empty title="还没有订阅" icon="proxy">
            添加 Clash / Mihomo 兼容订阅后，创建实例时就能从节点中选择。也可以在实例里填写 SOCKS5。
          </Empty>
        ) : (
          <>
            <div className="cm-table-wrap">
              <table className="cm-table">
                <thead>
                  <tr>
                    <th>订阅名称</th>
                    <th>节点数量</th>
                    <th>最近成功刷新</th>
                    <th>刷新状态</th>
                    <th>使用它的实例</th>
                    <th className="align-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {subscriptions.map((item) => {
                    const users = [...(data?.instances || []), ...(data?.trash || [])].filter(
                      (instance) => instance.proxy.subscription_id === item.id,
                    )
                    const busy = isBusy(item.id)
                    const active = tasks.find((task) => task.subscription_id === item.id && isActive(task))
                    return (
                      <tr className="cm-tr" key={item.id}>
                        <td>
                          <div className="cm-cell-name">
                            <strong>{item.name}</strong>
                            <span className="cm-cell-sub">订阅地址已安全保存</span>
                          </div>
                        </td>
                        <td className="cm-num">{item.node_count}</td>
                        <td>
                          {formatAgo(item.updated_at)}
                          <div className="cm-cell-sub">{formatDate(item.updated_at)}</div>
                        </td>
                        <td>
                          <div className="cm-status">
                            <Badge
                              state={
                                busy
                                  ? { tone: 'info', spin: true, label: active ? TASK_LABELS[active.kind] || '处理中' : '处理中' }
                                  : item.last_error
                                    ? { tone: 'warn', dot: 'tri', label: '刷新失败 · 显示旧缓存' }
                                    : { tone: 'ok', label: '正常' }
                              }
                            />
                            {item.last_error && (
                              <span className="cm-cell-sub long-copy">{item.last_error}</span>
                            )}
                          </div>
                        </td>
                        <td>
                          {users.length ? (
                            <>
                              {users.length} 个
                              <div className="cm-cell-sub">
                                {joinNames(users.map((user) => user.display_name))}
                              </div>
                            </>
                          ) : (
                            <span className="cm-muted">未使用</span>
                          )}
                        </td>
                        <td>
                          <div className="cm-actions">
                            <Button
                              small
                              icon="refresh"
                              busy={busy}
                              onClick={() => {
                                submitTask(item.id, () => api.refreshSubscription(item.id)).catch(
                                  (reason: Error) => notify(reason.message, 'danger'),
                                )
                              }}
                            >
                              刷新
                            </Button>
                            <Button
                              small
                              icon="edit"
                              aria-label={`编辑订阅：${item.name}`}
                              disabled={busy}
                              onClick={() => {
                                setRenaming(item)
                                setRenameName(item.name)
                                setRenameUrl('')
                                setRenameError('')
                              }}
                            >
                              编辑
                            </Button>
                            <Button
                              small
                              icon="trash"
                              variant="danger-ghost"
                              disabled={busy}
                              onClick={() => {
                                setRemoving(item)
                                setError('')
                              }}
                            >
                              删除
                            </Button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <p className="cm-help table-help">
              刷新失败时保留旧节点，使用中的实例不受影响。节点变化或消失时，相关实例会标记“需要重新确认”。
            </p>
          </>
        ))}
      {tab === 'nodes' && (
        <>
          <div className="library-toolbar">
            <div className="tools">
              <Select
                label="按订阅筛选"
                value={source}
                onChange={(source) => changeFilters({ source })}
                options={[
                  { value: 'all', label: '全部订阅' },
                  ...subscriptions.map((item) => ({ value: item.id, label: item.name })),
                ]}
              />
              <Select
                label="按国家／地区筛选"
                value={country}
                onChange={(country) => changeFilters({ country })}
                options={[
                  { value: 'all', label: '全部国家／地区' },
                  ...[...countries]
                    .filter(([code]) => code !== 'unknown')
                    .sort((a, b) => a[1].localeCompare(b[1], 'zh-CN'))
                    .map(([value, label]) => ({ value, label })),
                  ...(countries.has('unknown')
                    ? [{ value: 'unknown', label: countries.get('unknown')! }]
                    : []),
                ]}
              />
              <div className="search">
                <Icon name="search" />
                <input
                  className="cm-input"
                  type="search"
                  aria-label="搜索节点名称"
                  placeholder="搜索节点名称或协议"
                  value={query}
                  onChange={(event) => changeFilters({ query: event.target.value })}
                />
              </div>
            </div>
            <div className="library-selection">
              <span className="cm-help" role="status">
                {showNodeLoading
                  ? '正在读取节点…'
                  : `当前列表 ${visible.length} 个节点 · 已选 ${selectedNodes.length} 个`}
              </span>
              {selectedNodes.length > 0 && (
                <Button small variant="ghost" onClick={() => setSelectedNodeKeys(new Set())}>
                  清空选择
                </Button>
              )}
              <span className="spacer" />
              <Button
                small
                icon="pulse"
                busy={testingSelected}
                disabled={nodeActionsDisabled}
                onClick={() => setNodeAction('test')}
              >
                {testingSelected ? '正在测速…' : '测速所选'}
              </Button>
              <Button
                small
                icon="shield"
                busy={checkingSelected}
                disabled={nodeActionsDisabled}
                onClick={() => setNodeAction('quality')}
              >
                {checkingSelected ? '正在检测 IP…' : '检测所选 IP'}
              </Button>
            </div>
          </div>
          <details className="library-help">
            <summary>列表说明</summary>
            <ul className="rules">
              <li>单个节点可直接点击行内按钮；勾选后可批量测速或检测 IP，表头复选框全选当前列表中支持的节点。切换筛选条件会清空选择。</li>
              <li>国家／地区按节点名称中的国旗或名称识别，不代表实测出口 IP 归属。</li>
              <li>延迟为经节点访问测试网址的耗时，结果也会显示在创建实例时。</li>
              <li>IP 属性和系数由 IPPure 提供，系数范围 0–100%，数值越低风险越低。IP 检测按节点依次进行，结果标注检测时间。</li>
            </ul>
          </details>
          {tasks.filter((task) => isActive(task) && ['test-nodes', 'check-node-quality'].includes(task.kind)).map((task) => (
            <NodeTaskNotice key={task.id} task={task} subscriptionName={subscriptionById.get(task.subscription_id || '')?.name} />
          ))}
          {nodeError && (
            <Notice tone="danger" title="无法读取节点">
              {nodeError}
            </Notice>
          )}
          {showNodeLoading ? (
            <div className="loading-state">
              <span className="cm-spin" />
              正在读取节点…
            </div>
          ) : visible.length ? (
            <div className="cm-table-wrap">
              <table className="cm-table subscription-node-table">
                <thead>
                  <tr>
                    <th className="node-select-cell">
                      <input
                        type="checkbox"
                        className="node-checkbox"
                        aria-label="全选当前列表中支持的节点"
                        title="全选当前列表中支持的节点"
                        checked={allVisibleSelected}
                        ref={(input) => {
                          if (input) input.indeterminate = selectedNodes.length > 0 && !allVisibleSelected
                        }}
                        disabled={nodeLoading || !!nodeError || !testable.length}
                        onChange={(event) => setSelectedNodeKeys(new Set(event.target.checked ? testable.map(nodeKey) : []))}
                      />
                    </th>
                    <th>所属订阅</th>
                    <th>节点名称</th>
                    <th>协议</th>
                    <th>延迟</th>
                    <th>IP 属性</th>
                    <th title="IPPure 风险系数，0–100%，越低风险越低">IPPure 系数</th>
                    <th>已有使用者</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((node) => {
                    const subscription = subscriptionById.get(node.subscriptionId)
                    const checking = !!activeQuality(node.subscriptionId)?.node_ids?.includes(node.id)
                    const selected = node.supported && selectedNodeKeys.has(nodeKey(node))
                    const occupants = nodeOccupants(data, node.id, node.used_by)
                    return (
                      <tr key={nodeKey(node)} className={`cm-tr${selected ? ' is-selected' : ''}`}>
                        <td className="node-select-cell">
                          <input
                            type="checkbox"
                            className="node-checkbox"
                            aria-label={`选择节点 ${node.name}`}
                            title={node.supported ? `选择节点 ${node.name}` : '此节点协议暂不支持，无法选择'}
                            checked={selected}
                            disabled={!node.supported || !!nodeError}
                            onChange={(event) => toggleNode(node, event.target.checked)}
                          />
                        </td>
                        <td className="node-source-cell">
                          {subscription?.name || '已删除的订阅'}
                          {subscription?.last_error && (
                            <div className="cm-cell-sub tone-warn">缓存数据</div>
                          )}
                        </td>
                        <td>
                          <div className="cm-cell-name">
                            <strong>{node.name}</strong>
                            <span className="cm-cell-sub cm-mono">{node.id}</span>
                          </div>
                        </td>
                        <td>
                          <div className="cm-status">
                            <span className="cm-badge">{node.type}</span>
                            {!node.supported && (
                              <span className="cm-cell-sub">不支持 · {node.reason}</span>
                            )}
                          </div>
                        </td>
                        <td>
                          <NodeLatency
                            node={node}
                            testing={!!activeTest(node.subscriptionId)?.node_ids?.includes(node.id)}
                          />
                        </td>
                        <NodeQualityCells node={node} checking={checking} />
                        <td>
                          {occupants.length ? (
                            joinNames(
                              occupants.map(
                                (user) => user.name + (user.trashed ? '（回收站）' : ''),
                              ),
                            )
                          ) : (
                            <span className="cm-muted">—</span>
                          )}
                        </td>
                        <td>
                          <div className="node-row-actions">
                            <Button
                              small
                              variant="tint-primary"
                              aria-label={`测速节点 ${node.name}`}
                              disabled={!node.supported || !!nodeError || isBusy(node.subscriptionId) || qualityBusy(node.subscriptionId)}
                              onClick={() => void testNodes(node.subscriptionId, [node.id])}
                            >
                              测速
                            </Button>
                            <Button
                              small
                              variant="tint-primary"
                              aria-label={`检测节点 IP ${node.name}`}
                              disabled={!node.supported || !!nodeError || isBusy(node.subscriptionId) || qualityBusy(node.subscriptionId)}
                              busy={checking}
                              onClick={() => void checkQuality(node.subscriptionId, [node.id])}
                            >
                              {checking ? '检测中…' : '检测 IP'}
                            </Button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title={subscriptions.length ? '没有匹配的节点' : '还没有节点'} icon="proxy">
              {subscriptions.length ? '换个关键词，或选择其他订阅、国家／地区。' : '先添加一个订阅。'}
            </Empty>
          )}
        </>
      )}
      {nodeAction && (
        <Dialog
          title={nodeAction === 'test' ? '测速所选节点？' : '检测所选节点 IP？'}
          onClose={() => setNodeAction(null)}
          footer={
            <>
              <Button onClick={() => setNodeAction(null)}>取消</Button>
              <Button
                variant="primary"
                disabled={nodeActionsDisabled}
                onClick={() => {
                  setNodeAction(null)
                  void runSelectedNodeAction(nodeAction)
                }}
              >
                {nodeAction === 'test' ? '开始测速' : '开始检测'}
              </Button>
            </>
          }
        >
          <p className="dialog-copy">
            {nodeAction === 'test'
              ? `将只对已勾选的 ${selectedNodes.length} 个节点进行测速，并更新延迟结果。`
              : `将只对已勾选的 ${selectedNodes.length} 个节点依次检测 IP，并更新 IP 属性和风险系数等结果。`}
          </p>
        </Dialog>
      )}
      {renaming && (
        <Dialog
          title="编辑订阅"
          locked={renamePending}
          onClose={() => setRenaming(null)}
          footer={
            <>
              <Button disabled={renamePending} onClick={() => setRenaming(null)}>
                取消
              </Button>
              <Button
                variant="primary"
                busy={renamePending}
                onClick={() => void saveSubscriptionName()}
              >
                {renamePending ? '正在保存…' : renameUrl.trim() ? '更新地址' : '保存名称'}
              </Button>
            </>
          }
        >
          <Field
            label="订阅名称"
            htmlFor="rename-subscription-name"
            required
            error={renameUrl.trim() ? '' : renameError}
            help="支持中文，1–40 个字符。使用该订阅的实例会同步显示新名称。"
          >
            <input
              id="rename-subscription-name"
              className="cm-input"
              maxLength={40}
              autoComplete="off"
              value={renameName}
              disabled={renamePending}
              aria-invalid={!!renameError || undefined}
              aria-describedby={renameError ? 'rename-subscription-name-error' : undefined}
              onChange={(event) => {
                setRenameName(event.target.value)
                setRenameError('')
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
                  event.preventDefault()
                  void saveSubscriptionName()
                }
              }}
            />
          </Field>
          <Field
            label="新的订阅地址"
            htmlFor="rename-subscription-url"
            error={renameError && renameUrl.trim() ? renameError : ''}
            help="留空则只改名称。填写后保留订阅编号和仍存在的节点绑定，不重启实例。已保存的地址不会回显。"
          >
            <input
              id="rename-subscription-url"
              className="cm-input"
              type="password"
              autoComplete="new-password"
              value={renameUrl}
              disabled={renamePending}
              placeholder="https://…"
              onChange={(event) => {
                setRenameUrl(event.target.value)
                setRenameError('')
              }}
            />
          </Field>
          <p className="cm-help">
            只改名称时，地址和当前连接保持不变。更新地址后，配置已经变化的节点会标记为需要重新确认。
          </p>
        </Dialog>
      )}
      {removing && (
        <Dialog
          title={`删除订阅「${removing.name}」？`}
          locked={pending}
          onClose={() => setRemoving(null)}
          footer={
            <>
              <Button disabled={pending} onClick={() => setRemoving(null)}>
                取消
              </Button>
              <Button
                variant="danger"
                busy={pending}
                onClick={async () => {
                  setPending(true)
                  setError('')
                  try {
                    await api.deleteSubscription(removing.id)
                    setRemoving(null)
                    await refresh()
                    notify('订阅已删除', 'ok')
                  } catch (reason) {
                    setError(reason instanceof Error ? reason.message : '删除失败')
                  } finally {
                    setPending(false)
                  }
                }}
              >
                删除订阅
              </Button>
            </>
          }
        >
          <p className="dialog-copy">
            删除订阅会清除其缓存节点。有实例引用的订阅需要先解除引用，包括回收站里的实例。
          </p>
          {error && (
            <Notice tone="danger" title="无法删除订阅">
              {error}
            </Notice>
          )}
        </Dialog>
      )}
    </div>
  )
}
