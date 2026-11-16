const MINUTE = 60 * 1000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR
const countryNames = new Intl.DisplayNames(['zh-CN'], { type: 'region', fallback: 'none' })

/** 国家与出口 IP 属于同一次检测；未知时不根据节点名称推断。 */
export function formatCountry(code: string | null | undefined) {
  const country = code?.trim().toUpperCase()
  if (!country || !/^[A-Z]{2}$/.test(country) || ['XX', 'ZZ'].includes(country))
    return '国家／地区未知'
  return countryNames.of(country) || '国家／地区未知'
}

/** 内存：读不到时显示“—”，不以 0 代替。 */
export function formatMemory(mib: number | null | undefined) {
  if (mib === null || mib === undefined) return '—'
  if (mib >= 1024) return `${(mib / 1024).toFixed(2)} GiB`
  return `${Math.round(mib)} MiB`
}

export function formatCpu(percent: number | null | undefined) {
  if (percent === null || percent === undefined) return '—'
  return `${percent.toFixed(1)}%`
}

/** 相对时间，例如“3 分钟前”。 */
export function formatAgo(value: string | null | undefined) {
  if (!value) return '—'
  const time = new Date(value).getTime()
  if (Number.isNaN(time)) return '—'
  const diff = Date.now() - time
  if (diff < MINUTE) return '刚刚'
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)} 分钟前`
  if (diff < DAY) return `${Math.floor(diff / HOUR)} 小时前`
  return `${Math.floor(diff / DAY)} 天前`
}

export function formatDate(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getMonth() + 1}月${date.getDate()}日 ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function joinNames(names: string[]) {
  return names.map((name) => `「${name}」`).join('、')
}
