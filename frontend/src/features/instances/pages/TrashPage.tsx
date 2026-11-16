import { useState } from 'react'
import { useManager } from '../../../app/ManagerProvider'
import { api, type Instance } from '../../../shared/api/client'
import { formatAgo, formatDate } from '../../../shared/lib/format'
import { activeTaskFor, proxySummary, stepText } from '../../../shared/model/status'
import { Badge } from '../../../shared/ui/Badge'
import {
  Button,
  ButtonLink,
  Dialog,
  Empty,
  Field,
  Notice,
  PageHead,
} from '../../../shared/ui/Controls'
import { DockerNotice, Loading } from '../components/InstanceControls'

export function TrashPage() {
  const { data, error: connectionError, tasks, isBusy, submitTask } = useManager()
  const [dialog, setDialog] = useState<{ item: Instance; action: 'restore' | 'purge' } | null>(null)
  const [confirmation, setConfirmation] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  if (!data) return <Loading />
  const connected = data.docker.ok && !connectionError

  async function submit() {
    if (!dialog || pending) return
    if (dialog.action === 'purge' && confirmation !== dialog.item.display_name) return
    setPending(true)
    setError('')
    try {
      await submitTask(dialog.item.id, () =>
        dialog.action === 'restore'
          ? api.action(dialog.item.id, 'restore')
          : api.purge(dialog.item.id, confirmation),
      )
      setDialog(null)
      setConfirmation('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '操作失败')
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="pg">
      <PageHead title="回收站">
        删除的实例会先停止再移到这里，配置和浏览器数据一直保留，直到彻底删除。恢复后为“已停止”，需要手动启动。
      </PageHead>
      <DockerNotice />
      {!data.trash.length ? (
        <Empty
          title="回收站是空的"
          icon="trash"
          actions={<ButtonLink to="/">返回实例列表</ButtonLink>}
        >
          从实例列表删除的实例会出现在这里，可以恢复或彻底删除。
        </Empty>
      ) : (
        <>
          <div className="trash-summary">
            <span>
              共 <strong className="cm-num">{data.trash.length}</strong> 个实例
            </span>
            <span className="cm-help">彻底删除前，它们的数据仍占用本机磁盘。</span>
          </div>
          <div className="cm-table-wrap">
            <table className="cm-table">
              <thead>
                <tr>
                  <th>实例名称</th>
                  <th>删除时间</th>
                  <th>原代理</th>
                  <th>数据保留</th>
                  <th className="align-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {data.trash.map((item) => {
                  const task = activeTaskFor(tasks, item.id)
                  const proxy = proxySummary(item)
                  return (
                    <tr key={item.id} className="cm-tr">
                      <td>
                        <div className="cm-cell-name">
                          <strong>{item.display_name}</strong>
                          <span className="cm-cell-sub cm-mono">{item.id}</span>
                        </div>
                      </td>
                      <td>
                        {formatDate(item.trashed_at)}
                        <div className="cm-cell-sub">{formatAgo(item.trashed_at)}</div>
                      </td>
                      <td>
                        <div className="cm-cell-name">
                          <span>{proxy.node}</span>
                          <span className="cm-cell-sub">{proxy.source}</span>
                        </div>
                      </td>
                      <td>
                        <div className="cm-status">
                          <Badge
                            state={
                              task
                                ? {
                                    tone: 'info',
                                    spin: true,
                                    label: task.kind === 'restore' ? '恢复中' : '彻底删除中',
                                  }
                                : item.deleting
                                  ? { tone: 'warn', label: '删除未完成，请重试清理' }
                                  : { tone: 'ok', label: '配置和浏览器数据已保留' }
                            }
                          />
                          {task && <span className="cm-cell-sub">{stepText(task)}</span>}
                        </div>
                      </td>
                      <td>
                        <div className="cm-actions">
                          <Button
                            small
                            variant="tint-primary"
                            icon="restore"
                            disabled={!connected || isBusy(item.id) || item.deleting}
                            onClick={() => {
                              setDialog({ item, action: 'restore' })
                              setError('')
                            }}
                          >
                            恢复
                          </Button>
                          <Button
                            small
                            variant="danger-ghost"
                            icon="trash"
                            disabled={!connected || isBusy(item.id)}
                            onClick={() => {
                              setDialog({ item, action: 'purge' })
                              setConfirmation('')
                              setError('')
                            }}
                          >
                            {item.deleting ? '重试清理' : '彻底删除'}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
      {dialog && (
        <Dialog
          title={
            dialog.action === 'restore'
              ? `恢复「${dialog.item.display_name}」？`
              : `彻底删除「${dialog.item.display_name}」？`
          }
          onClose={() => setDialog(null)}
          locked={pending}
          footer={
            <>
              <Button disabled={pending} onClick={() => setDialog(null)}>
                取消
              </Button>
              <Button
                variant={dialog.action === 'restore' ? 'primary' : 'danger'}
                busy={pending}
                disabled={
                  !connected ||
                  (dialog.action === 'purge' && confirmation !== dialog.item.display_name)
                }
                onClick={() => void submit()}
              >
                {pending ? '正在提交…' : dialog.action === 'restore' ? '恢复实例' : '彻底删除'}
              </Button>
            </>
          }
        >
          {dialog.action === 'restore' ? (
            <Notice title="恢复后不会自动启动">
              配置和浏览器数据会恢复，实例保持“已停止”。你可以回到列表手动启动。
            </Notice>
          ) : (
            <>
              <Notice tone="danger" title="此操作无法撤销">
                将永久删除实例配置、Cookie、登录状态、缓存和下载文件。
              </Notice>
              <dl className="kv-list">
                <dt>实例 ID</dt>
                <dd className="cm-mono">{dialog.item.id}</dd>
                <dt>本机端口</dt>
                <dd className="cm-mono">{dialog.item.port}</dd>
                <dt>原代理</dt>
                <dd>{proxySummary(dialog.item).node}</dd>
              </dl>
              <Field
                label={`输入实例名称「${dialog.item.display_name}」以继续`}
                htmlFor="purge-name"
              >
                <input
                  id="purge-name"
                  className="cm-input"
                  value={confirmation}
                  onChange={(event) => setConfirmation(event.target.value)}
                  placeholder={dialog.item.display_name}
                  autoComplete="off"
                />
              </Field>
              {confirmation && confirmation !== dialog.item.display_name && (
                <span className="cm-error">名称不一致，请完整输入实例名称。</span>
              )}
            </>
          )}
          {error && (
            <Notice tone="danger" title="操作未提交">
              {error}
            </Notice>
          )}
        </Dialog>
      )}
    </div>
  )
}
