import { useEffect, useState } from 'react'
import { useManager, type Theme } from '../../../app/ManagerProvider'
import { api, type Settings } from '../../../shared/api/client'
import { formatMemory } from '../../../shared/lib/format'
import { Badge } from '../../../shared/ui/Badge'
import { Button, Card, Notice, PageHead } from '../../../shared/ui/Controls'
import { Icon, type IconName } from '../../../shared/ui/Icon'

const themeOptions: { value: Theme; label: string; icon: IconName }[] = [
  { value: 'light', label: '浅色', icon: 'sun' },
  { value: 'dark', label: '深色', icon: 'moon' },
  { value: 'auto', label: '跟随系统', icon: 'panel' },
]

export function SettingsPage() {
  const { data, error: connectionError, refresh, theme, setTheme, notify } = useManager()
  const [settings, setSettings] = useState<Settings | null>(null)
  const [error, setError] = useState('')
  // 只在用户手动点击时显示忙碌，不跟随后台轮询闪烁。
  const [checking, setChecking] = useState(false)

  useEffect(() => {
    api
      .settings()
      .then(setSettings)
      .catch((reason: Error) => setError(reason.message))
  }, [])

  async function recheck() {
    setChecking(true)
    try {
      await refresh()
    } finally {
      setChecking(false)
    }
  }

  async function copyPath(path: string) {
    try {
      await navigator.clipboard.writeText(path)
      notify('路径已复制', 'ok')
    } catch {
      notify('无法复制，请手动选择路径', 'warn')
    }
  }

  const dockerState = connectionError
    ? { tone: 'neutral' as const, label: '状态未知', dot: 'hollow' as const }
    : !data
      ? { tone: 'neutral' as const, label: '正在读取', dot: 'hollow' as const }
      : {
          tone: data.docker.ok ? ('ok' as const) : ('danger' as const),
          label: data.docker.ok ? '已连接' : '未连接',
          dot: data.docker.ok ? undefined : ('square' as const),
        }
  const dockerDetail = connectionError
    ? '暂时无法读取运行状态，下面保留最近一次数据。'
    : data?.docker.ok
      ? 'Docker Desktop · 本机运行'
      : data?.docker.detail || '正在读取状态…'

  const limits = [
    { label: '浏览器内存', value: formatMemory(settings?.resources.browser_memory_mib) },
    { label: '浏览器 CPU', value: settings ? `${settings.resources.browser_cpus} 核` : '—' },
    { label: '代理内存', value: formatMemory(settings?.resources.proxy_memory_mib) },
    { label: '网络守卫内存', value: formatMemory(settings?.resources.guard_memory_mib) },
  ]
  const usage = [
    { label: 'Docker VM RSS', value: formatMemory(data?.summary.docker_vm_rss_mib) },
    { label: 'Docker 辅助进程', value: formatMemory(data?.summary.docker_helper_rss_mib) },
    { label: '管理台进程', value: formatMemory(data?.summary.manager_rss_mib) },
  ]
  const paths = [
    { label: '浏览器数据', base: settings?.data_directory },
    { label: '实例配置', base: settings?.config_directory },
  ]

  return (
    <div className="pg settings-page">
      <PageHead title="设置">运行环境、资源上限、数据位置与外观。</PageHead>
      {error && (
        <Notice tone="danger" title="无法读取运行配置">
          {error}
        </Notice>
      )}

      <div className="settings">
        <Card
          title="运行环境"
          action={
            <Button small icon="refresh" busy={checking} onClick={() => void recheck()}>
              重新检测
            </Button>
          }
        >
          <div className="settings-docker">
            <Badge state={dockerState} />
            <span className="cm-help">{dockerDetail}</span>
          </div>
          <dl className="kv-list">
            <dt>管理台版本</dt>
            <dd className="cm-num">{settings?.version || '—'}</dd>
            <dt>共用镜像</dt>
            <dd className="cm-mono">
              {settings?.images.length ? settings.images.join('、') : '—'}
            </dd>
          </dl>
          <div className="settings-section-label">本机占用</div>
          <dl className="settings-stats">
            {usage.map((item) => (
              <div key={item.label}>
                <dt>{item.label}</dt>
                <dd className="cm-num">{item.value}</dd>
              </div>
            ))}
          </dl>
        </Card>

        <Card title="每个实例的资源上限">
          <dl className="settings-stats settings-stats--2">
            {limits.map((item) => (
              <div key={item.label}>
                <dt>{item.label}</dt>
                <dd className="cm-num">{item.value}</dd>
              </div>
            ))}
          </dl>
          <span className="cm-help">
            上限是限制，不是预留，也不等于实际占用。这里只读显示；修改本机资源配置后，需要重启对应实例才能生效。
          </span>
        </Card>

        <Card title="数据保存位置">
          <ul className="settings-paths">
            {paths.map((item) => {
              const path = item.base ? `${item.base}/<内部 ID>/` : ''
              return (
                <li key={item.label}>
                  <span className="settings-paths__label">{item.label}</span>
                  <code className="settings-paths__value" title={path || undefined}>
                    {path || '—'}
                  </code>
                  <Button
                    small
                    variant="ghost"
                    disabled={!item.base}
                    aria-label={`复制${item.label}目录`}
                    onClick={() => void copyPath(item.base || '')}
                  >
                    复制
                  </Button>
                </li>
              )
            })}
          </ul>
          <ul className="rules">
            <li>改名不会移动目录；停止实例和移入回收站都不会删除浏览器数据。</li>
            <li>只有在回收站“彻底删除”才会清除。</li>
          </ul>
        </Card>

        <Card title="外观">
          <div className="settings-themes" role="radiogroup" aria-label="主题">
            {themeOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={theme === option.value}
                className="settings-theme"
                onClick={() => setTheme(option.value)}
              >
                <Icon name={option.icon} />
                {option.label}
              </button>
            ))}
          </div>
          <span className="cm-help">
            主题保存在当前浏览器中；侧栏底部也可以快速切换日间 / 夜间。
          </span>
        </Card>
      </div>

      <details className="library-help settings-glossary">
        <summary>资源数字怎么看</summary>
        <ul className="rules">
          <li>CPU 用百分比显示，100% 相当于一个核心满载。</li>
          <li>内存用 MiB / GiB 显示，内存上限不代表实际占用。</li>
          <li>列表里的“容器内存合计”只累加各实例的实际占用。</li>
          <li>Docker VM RSS 包含虚拟机自身和缓存，与容器内存存在重叠，不能直接相加。</li>
          <li>Docker 辅助进程不含虚拟机；管理台进程单独统计。</li>
          <li>读不到的数据显示“—”或“未知”，不会用 0 冒充测量结果。</li>
        </ul>
      </details>
    </div>
  )
}
