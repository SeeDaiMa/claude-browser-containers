import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useManager } from '../../../app/ManagerProvider'
import { api } from '../../../shared/api/client'
import { emptyProxy, proxyDraft, proxyErrors, proxyPayload } from '../../../shared/model/proxy'
import {
  activeTaskFor,
  isActive,
  proxyState,
  proxySummary,
  runState,
  startFailureFor,
} from '../../../shared/model/status'
import { Badge } from '../../../shared/ui/Badge'
import {
  Button,
  ButtonLink,
  Card,
  Empty,
  Field,
  Notice,
  ProgressStages,
} from '../../../shared/ui/Controls'
import { Icon } from '../../../shared/ui/Icon'
import { DockerNotice, Loading } from '../components/InstanceControls'
import { ProxyEditor, ProxyMode } from '../components/ProxyEditor'

export function EditPage() {
  const { id = '' } = useParams()
  const { data, error: connectionError, tasks, refresh, submitTask, isBusy, notify } = useManager()
  const item = data?.instances.find((row) => row.id === id)
  const initialized = useRef('')
  const [name, setName] = useState('')
  const [proxy, setProxy] = useState({ ...emptyProxy })
  const [password, setPassword] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [pending, setPending] = useState(false)
  const [savingName, setSavingName] = useState(false)
  const [reconfirming, setReconfirming] = useState(false)
  const [error, setError] = useState('')
  const [taskId, setTaskId] = useState('')

  useEffect(() => {
    if (!item || initialized.current === id) return
    setName(item.display_name)
    setProxy(proxyDraft(item))
    setPassword('')
    setErrors({})
    setError('')
    setTaskId('')
    initialized.current = id
  }, [item, id])

  if (!data) return <Loading />
  if (!item)
    return (
      <Empty title="找不到这个实例" actions={<ButtonLink to="/">返回实例列表</ButtonLink>}>
        实例可能已移入回收站。
      </Empty>
    )
  const connected = data.docker.ok && !connectionError
  const running = item.runtime === 'running' || item.runtime === 'degraded'
  const busy = isBusy(id) || pending
  const task = tasks.find((row) => row.id === taskId) || activeTaskFor(tasks, id)
  const startFailure = startFailureFor(item, tasks)
  const locked = item.proxy_locked
  const canReconfirm = locked && item.proxy.mode === 'subscription' && !!item.proxy.stale

  async function saveProxy() {
    if (!item || locked) return
    const validation = proxyErrors(
      proxy,
      password,
      !!item.proxy.password_set && item.proxy.mode === 'socks',
    )
    setErrors(validation)
    if (Object.keys(validation).length || busy) return
    const payload = proxyPayload(proxy, password)
    setPassword('')
    setPending(true)
    setError('')
    try {
      const next = await submitTask(id, () => api.proxy(id, payload))
      setTaskId(next.id)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '保存失败')
    } finally {
      setPending(false)
    }
  }

  async function reconfirmNode() {
    if (busy || reconfirming) return
    setReconfirming(true)
    setError('')
    try {
      const result = await api.reconfirmProxy(id)
      notify('节点已重新确认', 'ok', result.detail)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '重新确认失败')
    } finally {
      setReconfirming(false)
    }
  }

  return (
    <div className="wz">
      <div className="wz-head">
        <div className="wz-title">
          <Link className="back-link" to={`/instance/${id}`}>
            <Icon name="back" />
            返回工作区
          </Link>
          <h1 className="form-title">编辑实例</h1>
        </div>
        <div className="edit-status">
          <strong>{item.display_name}</strong>
          <Badge state={runState(item, tasks, connected)} />
          <Badge state={proxyState(item, tasks, connected)} />
        </div>
      </div>
      <DockerNotice />
      {error && (
        <Notice tone="danger" title="操作未完成">
          {error}
        </Notice>
      )}
      <Card title="基础信息">
        <div className="name-editor">
          <Field label="显示名称" htmlFor="edit-name" required error={errors.name}>
            <input
              id="edit-name"
              className="cm-input"
              value={name}
              maxLength={32}
              disabled={busy || savingName}
              onChange={(event) => {
                setName(event.target.value)
                setErrors({ ...errors, name: '' })
              }}
              aria-invalid={!!errors.name || undefined}
            />
          </Field>
          <Button
            busy={savingName}
            disabled={busy || !!connectionError}
            onClick={async () => {
              if (!name.trim() || name.trim().length > 32) {
                setErrors({ ...errors, name: '请填写 1–32 个字符的实例名称。' })
                return
              }
              setSavingName(true)
              try {
                await api.rename(id, name.trim())
                notify('名称已保存', 'ok', '改名已生效，浏览器没有重启。')
                await refresh()
              } catch (reason) {
                setError(reason instanceof Error ? reason.message : '保存失败')
              } finally {
                setSavingName(false)
              }
            }}
          >
            保存名称
          </Button>
        </div>
        <p className="cm-help">
          改名后立即生效，不会移动数据目录，也不会重启浏览器。内部 ID{' '}
          <span className="cm-mono">{item.id}</span> 保持不变。
        </p>
      </Card>
      {locked ? (
        <Card title="代理配置">
          <div>
            当前配置：{proxySummary(item).node}
            <span className="cm-help"> · {proxySummary(item).source}</span>
          </div>
          <p className="cm-help">
            这个实例已经启动过，代理已锁定，不能更换节点或 SOCKS5，避免同一个浏览器 Profile
            换出口。需要不同出口时，请新建实例。
          </p>
          {canReconfirm && (
            <Notice
              tone="warn"
              title="节点需要重新确认"
              actions={
                <Button
                  small
                  busy={reconfirming}
                  disabled={!connected || busy}
                  onClick={() => void reconfirmNode()}
                >
                  重新确认同名节点
                </Button>
              }
            >
              原订阅节点已变化或消失（例如服务商更换了节点密码）。只能在原订阅里重新确认同名节点，
              下次启动生效；找不到同名节点时只能新建实例。
            </Notice>
          )}
          {item.proxy.stale && !canReconfirm && (
            <Notice tone="warn" title="节点需要重新确认">
              这个实例没有记录原订阅，无法自动找到同名节点。代理已锁定，只能新建实例。
            </Notice>
          )}
        </Card>
      ) : (
        <Card
          title="代理配置"
          action={
            <ProxyMode
              value={proxy.mode}
              disabled={busy}
              onChange={(mode) => {
                setProxy({ ...proxy, mode })
                setPassword('')
                setErrors({})
              }}
            />
          }
          footer={
            <>
              <span className="note">
                {running
                  ? '实例正在运行，请先停止再修改代理。'
                  : '保存后在下次启动时生效。第一次启动成功后代理会锁定。'}
              </span>
              <ButtonLink to={`/instance/${id}`}>取消</ButtonLink>
              <Button
                variant="primary"
                busy={busy}
                disabled={!connected || savingName || running}
                onClick={() => void saveProxy()}
              >
                {busy ? '正在处理…' : '保存配置'}
              </Button>
            </>
          }
        >
          <div>
            当前配置：{proxySummary(item).node}
            <span className="cm-help"> · {proxySummary(item).source}</span>
          </div>
          <Notice tone="info" title="还可以修改代理">
            这个实例还没有启动成功过，可以修改代理后重新启动。启动成功后代理会锁定，不能再更换。
          </Notice>
          {startFailure && (
            <Notice tone="danger" title="上次启动失败">
              {startFailure.error} 保存新配置后，可以在列表或工作区重新启动。
            </Notice>
          )}
          {item.proxy.stale && (
            <Notice tone="warn" title="节点需要重新确认">
              原订阅节点已变化或消失，请在下方重新选择。
            </Notice>
          )}
          <ProxyEditor
            value={proxy}
            onChange={(value) => {
              setProxy(value)
              setErrors({})
            }}
            password={password}
            setPassword={setPassword}
            hasPassword={!!item.proxy.password_set && item.proxy.mode === 'socks'}
            errors={errors}
            excludeId={id}
            returnTo={`/instance/${id}/edit`}
            disabled={busy || savingName}
          />
          {task && (
            <>
              <ProgressStages task={task} />
              {task.state === 'succeeded' && (
                <Notice tone="ok" title="配置已保存">
                  {task.result.detail || '下次启动时生效。'}
                </Notice>
              )}
              {!isActive(task) && task.state !== 'succeeded' && (
                <Notice tone="danger" title="代理操作未完成">
                  {task.error || '任务已中断。'}
                </Notice>
              )}
            </>
          )}
        </Card>
      )}
    </div>
  )
}
