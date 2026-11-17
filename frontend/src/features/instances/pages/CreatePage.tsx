import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useManager } from '../../../app/ManagerProvider'
import { api, type NodeRow, type Task } from '../../../shared/api/client'
import { isFinished } from '../../../shared/model/status'
import { proxyErrors, proxyPayload } from '../../../shared/model/proxy'
import { Button, ButtonLink, Card, Notice, ProgressStages } from '../../../shared/ui/Controls'
import { Icon } from '../../../shared/ui/Icon'
import { DockerNotice } from '../components/InstanceControls'
import { ProxyEditor, ProxyMode } from '../components/ProxyEditor'

type CreateCheck = {
  key: string
  ok: boolean
  text: string
  target?: string
  bad?: boolean
}

export function CreatePage() {
  const {
    data,
    error: connectionError,
    subscriptions,
    draft,
    setDraft,
    resetDraft,
    tasks,
    submitTask,
  } = useManager()
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [selectedNode, setSelectedNode] = useState<NodeRow>()
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const [submitted, setSubmitted] = useState<Task | null>(null)
  const task = tasks.find((item) => item.id === draft.taskId) || submitted
  const createdId = task?.result.instance_id || task?.instance_id
  const createdInstance = data?.instances.find((item) => item.id === createdId)
  const connected = !!data?.docker.ok && !connectionError
  const showingProgress = draft.step === 2 && !!task
  const name = draft.name.trim()
  const nameError = !name || name.length > 32 ? '请填写 1–32 个字符的实例名称。' : ''
  const proxyValidation = proxyErrors(draft.proxy, password, pending)
  const subscription = subscriptions.find((item) => item.id === draft.proxy.subscriptionId)
  const node = selectedNode?.id === draft.proxy.nodeId ? selectedNode : undefined
  const checks: CreateCheck[] = [
    {
      key: 'docker',
      ok: connected,
      text: connected
        ? 'Docker 已连接'
        : connectionError
          ? '管理台连接异常，暂时无法创建'
          : data
            ? 'Docker 未连接，暂时无法创建'
            : '正在检查 Docker 连接…',
      bad: !!connectionError || !!data,
    },
    {
      key: 'name',
      ok: !nameError,
      text: nameError ? '填写实例名称（1–32 个字符）' : '名称已填写',
      target: 'instance-name',
    },
  ]

  if (draft.proxy.mode === 'subscription') {
    checks.push({
      key: 'node',
      ok: !proxyValidation.node && !!node?.supported,
      text:
        !proxyValidation.node && node?.supported
          ? '网络出口已选择'
          : !subscriptions.length
            ? '添加订阅，或改用手动 SOCKS5'
            : '选择一个支持的订阅节点',
      target: !subscriptions.length ? 'proxy-modes' : 'proxy-node-list',
    })
  } else {
    const missing = Object.entries(proxyValidation)
    if (missing.length) {
      checks.push(
        ...missing.map(([field, text]) => ({
          key: field,
          ok: false,
          text,
          target: `proxy-${field}`,
          bad: field === 'port' && !!draft.proxy.port.trim(),
        })),
      )
    } else {
      checks.push({ key: 'proxy', ok: true, text: '网络出口已填写' })
    }
  }

  const missingCount = checks.filter((check) => !check.ok).length
  const exitText =
    draft.proxy.mode === 'subscription'
      ? node
        ? `${subscription?.name || '订阅节点'} · ${node.name}（${node.type}）`
        : showingProgress && createdInstance
          ? createdInstance.proxy.label
          : '还没有选择节点'
      : draft.proxy.host.trim()
        ? `SOCKS5 · ${draft.proxy.host.trim()}${draft.proxy.port ? `:${draft.proxy.port}` : ''}`
        : 'SOCKS5 · 待填写'

  useEffect(() => {
    if (draft.taskId && task?.state === 'succeeded' && task.result.instance_id) {
      navigate(`/instance/${task.result.instance_id}`, { replace: true })
      resetDraft()
    }
  }, [draft.taskId, task?.id, task?.state, task?.result.instance_id, navigate, resetDraft])

  function focusField(target: string) {
    const element = document.getElementById(target)
    if (!element) return
    const control = element.querySelector<HTMLInputElement>('input:not(:disabled)') || element
    control.focus({ preventScroll: true })
    control.scrollIntoView({ block: 'center' })
  }

  async function submit() {
    const validation = { ...proxyValidation }
    if (nameError) validation.name = nameError
    setErrors(validation)
    if (missingCount || pending || draft.taskId) return
    setPending(true)
    setError('')
    const body = {
      ...proxyPayload(draft.proxy, password),
      display_name: name,
      request_id: draft.requestId,
    }
    setPassword('')
    try {
      const created = await submitTask(`create-${draft.requestId}`, () => api.createInstance(body))
      setSubmitted(created)
      setDraft((value) => ({ ...value, taskId: created.id, step: 2 }))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '创建失败')
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="cx">
      <header className="cx-head">
        <Link className="back-link" to="/">
          <Icon name="back" />
          返回实例列表
        </Link>
        <h1>
          {showingProgress
            ? task.state === 'succeeded'
              ? `「${name}」已创建并启动`
              : isFinished(task)
                ? `「${name}」没能启动`
                : `正在创建「${name}」`
            : '创建实例'}
        </h1>
        <p>
          {showingProgress
            ? '可以离开此页面，创建会在后台继续；完成后会通知你。'
            : '每个实例是一套独立的浏览器环境：Cookie、登录状态、缓存和下载互不影响。端口和网络由系统自动分配。'}
        </p>
      </header>
      <DockerNotice />
      {error && (
        <Notice tone="danger" title="无法创建实例">
          {error}
        </Notice>
      )}
      <div className="cx-cols">
        {showingProgress ? (
          <div className="cx-main">
            <Card
              title="创建进度"
              footer={
                <>
                  <ButtonLink
                    to="/"
                    onClick={() => {
                      if (isFinished(task)) resetDraft()
                    }}
                  >
                    返回实例列表
                  </ButtonLink>
                  {isFinished(task) && task.state !== 'succeeded' && createdId && (
                    <ButtonLink
                      variant="primary"
                      to={`/instance/${createdId}/edit`}
                      onClick={resetDraft}
                    >
                      编辑并重试
                    </ButtonLink>
                  )}
                </>
              }
            >
              {task.state === 'succeeded' && (
                <Notice tone="ok" title="创建完成，浏览器已启动">
                  正在进入工作区…
                </Notice>
              )}
              {isFinished(task) && task.state !== 'succeeded' && (
                <Notice
                  tone="danger"
                  title={task.state === 'interrupted' ? '创建已中断' : '创建失败'}
                >
                  {task.error || '任务未完成。'}
                  {createdId
                    ? ' 实例和配置已保留，请编辑这个实例后重新启动。'
                    : ' 尚未建立实例，可以返回配置后重试。'}
                </Notice>
              )}
              <ProgressStages task={task} />
              {isFinished(task) && task.state !== 'succeeded' && !createdId && (
                <Button
                  onClick={() => {
                    setDraft({
                      ...draft,
                      taskId: '',
                      step: 0,
                      requestId: crypto.randomUUID(),
                    })
                    setSubmitted(null)
                  }}
                >
                  返回配置
                </Button>
              )}
            </Card>
          </div>
        ) : (
          <form
            className="cx-main"
            id="create-instance-form"
            onSubmit={(event) => {
              event.preventDefault()
              void submit()
            }}
          >
            <section className="cm-card" aria-labelledby="create-name-title">
              <div className="cm-card__head cx-sec-head">
                <span className="cx-num" aria-hidden="true">
                  1
                </span>
                <h2 className="cm-card__title" id="create-name-title">
                  名称
                </h2>
              </div>
              <div className="cm-card__body">
                <div className="cm-field">
                  <div className="cx-label-row">
                    <label className="cm-label" htmlFor="instance-name">
                      实例名称
                      <span className="cm-req" aria-hidden="true">
                        *
                      </span>
                    </label>
                    <span className="cx-count">{draft.name.length} / 32</span>
                  </div>
                  <input
                    className="cm-input"
                    id="instance-name"
                    maxLength={32}
                    value={draft.name}
                    disabled={pending}
                    onChange={(event) => {
                      setDraft({ ...draft, name: event.target.value })
                      setErrors((value) => ({ ...value, name: '' }))
                    }}
                    placeholder="例如：资料整理"
                    autoComplete="off"
                    aria-required="true"
                    aria-invalid={!!errors.name || undefined}
                    aria-describedby={`instance-name-help${errors.name ? ' instance-name-error' : ''}`}
                  />
                  {errors.name && (
                    <span className="cm-error" id="instance-name-error">
                      {errors.name}
                    </span>
                  )}
                  <span className="cm-help" id="instance-name-help">
                    支持中文。之后可以改名，内部 ID 和数据目录不会变。
                  </span>
                </div>
              </div>
            </section>
            <section className="cm-card" aria-labelledby="create-network-title">
              <div className="cm-card__head cx-sec-head">
                <span className="cx-num" aria-hidden="true">
                  2
                </span>
                <h2 className="cm-card__title" id="create-network-title">
                  网络出口
                </h2>
                <span className="cx-hint">浏览器只能通过这里选的代理上网</span>
              </div>
              <div className="cm-card__body form">
                <ProxyMode
                  variant="cards"
                  value={draft.proxy.mode}
                  disabled={pending}
                  onChange={(mode) => {
                    setDraft({ ...draft, proxy: { ...draft.proxy, mode } })
                    setPassword('')
                    setErrors({})
                  }}
                />
                <ProxyEditor
                  value={draft.proxy}
                  onChange={(proxy) => {
                    setDraft((value) => ({ ...value, proxy }))
                    setErrors({})
                  }}
                  password={password}
                  setPassword={setPassword}
                  errors={errors}
                  returnTo="/new"
                  disabled={pending}
                  onSelectedNodeChange={setSelectedNode}
                />
              </div>
            </section>
          </form>
        )}
        <aside className="cm-card cx-side" aria-labelledby="create-summary-title">
          <div className="cm-card__head">
            <h2 className="cm-card__title" id="create-summary-title">
              {showingProgress ? '已提交的配置' : '即将创建'}
            </h2>
            <span className={`cm-badge${showingProgress ? '' : ' cm-badge--info'}`}>
              {showingProgress && <Icon name="lock" />}
              {showingProgress ? '已提交' : '创建后自动启动'}
            </span>
          </div>
          <div className="cm-card__body form">
            <div className="cx-preview">
              <span className="cx-preview__icon">
                <Icon name="inst" />
              </span>
              <span className="cx-preview__text">
                <span className={`cx-preview__name${name ? '' : ' is-empty'}`}>
                  {name || '未命名实例'}
                </span>
                <span className="cx-preview__sub">
                  {showingProgress && createdId ? (
                    <>
                      内部 ID <span className="cm-mono">{createdId}</span>
                    </>
                  ) : (
                    '独立 Profile · 独立下载目录'
                  )}
                </span>
              </span>
            </div>
            <dl className="cx-kv">
              <dt>网络出口</dt>
              <dd>{exitText}</dd>
              <dt>端口网络</dt>
              <dd>系统自动分配</dd>
              <dt>浏览器数据</dt>
              <dd>单独保存，删除前可进回收站</dd>
            </dl>
            {showingProgress ? (
              <p className="cx-note">配置已提交。创建完成后，可以在实例的“编辑”里改名或换代理。</p>
            ) : (
              <>
                <div className="cx-divider" />
                <div className="cm-field">
                  <span className="cm-label">创建前检查</span>
                  <ul className="cx-checks" aria-live="polite">
                    {checks.map((check) => (
                      <li
                        key={check.key}
                        className={`cx-check is-${check.ok ? 'ok' : check.bad ? 'bad' : 'todo'}`}
                      >
                        <Icon name={check.ok ? 'okc' : check.bad ? 'alert' : 'clock'} />
                        {!check.ok && check.target ? (
                          <button type="button" onClick={() => focusField(check.target!)}>
                            {check.text}
                          </button>
                        ) : (
                          <span>{check.text}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
                <div className="cx-actions">
                  <Button
                    type="submit"
                    form="create-instance-form"
                    variant="primary"
                    icon="play"
                    busy={pending}
                    disabled={missingCount > 0}
                  >
                    {pending
                      ? '正在提交…'
                      : missingCount
                        ? `创建并启动 · 还差 ${missingCount} 项`
                        : '创建并启动'}
                  </Button>
                  <ButtonLink to="/" variant="ghost">
                    取消
                  </ButtonLink>
                  <p className="cx-note">
                    点击后按钮立即锁定，不会重复创建。完成后自动进入工作区；也可以离开此页，创建会在后台继续。
                  </p>
                </div>
              </>
            )}
          </div>
        </aside>
      </div>
    </div>
  )
}
