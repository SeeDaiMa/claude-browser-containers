import { useEffect, useState } from 'react'
import { useManager, type Theme } from '../../../app/ManagerProvider'
import { api, type Settings } from '../../../shared/api/client'
import { formatMemory } from '../../../shared/lib/format'
import { Badge } from '../../../shared/ui/Badge'
import { Button, Card, Field, Notice, PageHead } from '../../../shared/ui/Controls'

export function SettingsPage() {
  const { data, error: connectionError, refresh, refreshing, theme, setTheme } = useManager()
  const [settings, setSettings] = useState<Settings | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    api
      .settings()
      .then(setSettings)
      .catch((reason: Error) => setError(reason.message))
  }, [])
  return (
    <div className="pg">
      <PageHead title="设置">运行环境、资源口径与外观。</PageHead>
      {error && (
        <Notice tone="danger" title="无法读取运行配置">
          {error}
        </Notice>
      )}
      <div className="settings">
        <Card
          title="运行环境"
          action={
            <Button small icon="refresh" busy={refreshing} onClick={() => void refresh()}>
              重新检测
            </Button>
          }
        >
          <dl className="kv-list">
            <dt>Docker</dt>
            <dd>
              <Badge
                state={
                  connectionError
                    ? { tone: 'neutral', label: '状态未知', dot: 'hollow' }
                    : !data
                      ? { tone: 'neutral', label: '正在读取', dot: 'hollow' }
                      : {
                          tone: data.docker.ok ? 'ok' : 'danger',
                          label: data.docker.ok ? '已连接' : '未连接',
                          dot: data.docker.ok ? undefined : 'square',
                        }
                }
              />
              <div className="cm-help">
                {connectionError
                  ? '暂时无法读取运行状态，下面保留最近一次数据。'
                  : data?.docker.ok
                    ? 'Docker Desktop · 本机运行'
                    : data?.docker.detail || '正在读取状态…'}
              </div>
            </dd>
            <dt>管理台版本</dt>
            <dd>{settings?.version || '—'}</dd>
            <dt>共用镜像</dt>
            <dd className="cm-mono">
              {settings?.images.length ? settings.images.join('、') : '—'}
            </dd>
            <dt>Docker VM RSS</dt>
            <dd className="cm-num">{formatMemory(data?.summary.docker_vm_rss_mib)}</dd>
            <dt>Docker 辅助进程</dt>
            <dd className="cm-num">{formatMemory(data?.summary.docker_helper_rss_mib)}</dd>
            <dt>管理台进程</dt>
            <dd className="cm-num">{formatMemory(data?.summary.manager_rss_mib)}</dd>
          </dl>
        </Card>
        <Card title="默认资源配置">
          <div className="cm-grid-2">
            <Field label="浏览器内存上限">
              <div className="cm-input readonly-control">
                {formatMemory(settings?.resources.browser_memory_mib)}
              </div>
            </Field>
            <Field label="浏览器 CPU 上限">
              <div className="cm-input readonly-control">
                {settings ? `${settings.resources.browser_cpus} 核` : '—'}
              </div>
            </Field>
            <Field label="代理内存上限">
              <div className="cm-input readonly-control">
                {formatMemory(settings?.resources.proxy_memory_mib)}
              </div>
            </Field>
            <Field label="网络守卫内存上限">
              <div className="cm-input readonly-control">
                {formatMemory(settings?.resources.guard_memory_mib)}
              </div>
            </Field>
          </div>
          <span className="cm-help">
            上限是限制，不是预留，也不等于实际占用。当前资源配置为只读；修改本机资源配置后，需要重启对应实例才能生效。
          </span>
        </Card>
        <Card title="资源口径说明">
          <ul className="rules">
            <li>CPU 用百分比显示，100% 相当于一个核心满载。</li>
            <li>内存用 MiB / GiB 显示，内存上限不代表实际占用。</li>
            <li>列表里的“容器内存合计”只累加各实例的实际占用。</li>
            <li>Docker VM RSS 包含虚拟机自身和缓存，与容器内存存在重叠，不能直接相加。</li>
            <li>Docker 辅助进程不含虚拟机；管理台进程单独统计。</li>
            <li>读不到的数据显示“—”或“未知”，不会用 0 冒充测量结果。</li>
          </ul>
        </Card>
        <Card title="数据保存位置">
          <p className="page-note">每个实例的数据都保存在本机。</p>
          <dl className="kv-list">
            <dt>浏览器数据</dt>
            <dd className="cm-mono break-word">
              {settings?.data_directory || '—'}/&lt;内部 ID&gt;/
            </dd>
            <dt>实例配置</dt>
            <dd className="cm-mono break-word">
              {settings?.config_directory || '—'}/&lt;内部 ID&gt;/
            </dd>
          </dl>
          <ul className="rules">
            <li>改名不会移动目录。</li>
            <li>停止实例和移入回收站都不会删除浏览器数据。</li>
            <li>只有在回收站“彻底删除”才会清除。</li>
          </ul>
        </Card>
        <Card title="外观">
          <Field label="主题">
            <div className="cm-seg align-start" role="group" aria-label="主题">
              {(
                [
                  { value: 'light', label: '浅色' },
                  { value: 'dark', label: '深色' },
                  { value: 'auto', label: '跟随系统' },
                ] as { value: Theme; label: string }[]
              ).map((option) => (
                <button
                  key={option.value}
                  type="button"
                  aria-pressed={theme === option.value}
                  onClick={() => setTheme(option.value)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </Field>
          <span className="cm-help">主题选择会保存在当前浏览器中。</span>
        </Card>
      </div>
    </div>
  )
}
