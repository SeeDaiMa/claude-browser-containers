import { afterEach, describe, expect, it, vi } from 'vitest'
import { formatAgo, formatCountry, formatCpu, formatDate, formatMemory, joinNames } from './format'

afterEach(() => {
  vi.useRealTimers()
})

describe('数值和地区显示', () => {
  it.each([null, undefined])('未知资源 %s 不显示为零', (value) => {
    expect(formatMemory(value)).toBe('—')
    expect(formatCpu(value)).toBe('—')
  })
  it('保留真实零值、单位边界和小数精度', () => {
    expect(formatMemory(0)).toBe('0 MiB')
    expect(formatMemory(1023.4)).toBe('1023 MiB')
    expect(formatMemory(1024)).toBe('1.00 GiB')
    expect(formatMemory(2560)).toBe('2.50 GiB')
    expect(formatCpu(0)).toBe('0.0%')
    expect(formatCpu(12.34)).toBe('12.3%')
  })
  it('仅使用检测返回的有效国家代码', () => {
    expect(formatCountry(' jp ')).toBe('日本')
    for (const value of [null, undefined, '', 'XX', 'ZZ', 'JP node', '1A', 'AA'])
      expect(formatCountry(value)).toBe('国家／地区未知')
  })
  it('按原顺序列出名称', () => {
    expect(joinNames([])).toBe('')
    expect(joinNames(['甲', '乙'])).toBe('「甲」、「乙」')
  })
})

describe('时间显示', () => {
  it('空值或无效日期显示占位符', () => {
    for (const value of [null, undefined, '', 'invalid']) {
      expect(formatAgo(value)).toBe('—')
      expect(formatDate(value)).toBe('—')
    }
  })
  it('相对时间在分钟、小时和天的边界切换', () => {
    vi.useFakeTimers()
    const now = new Date('2026-11-17T12:00:00Z').getTime()
    vi.setSystemTime(now)
    const ago = (seconds: number) => formatAgo(new Date(now - seconds * 1000).toISOString())
    expect(ago(-10)).toBe('刚刚')
    expect(ago(59)).toBe('刚刚')
    expect(ago(60)).toBe('1 分钟前')
    expect(ago(3599)).toBe('59 分钟前')
    expect(ago(3600)).toBe('1 小时前')
    expect(ago(86400)).toBe('1 天前')
  })
  it('绝对时间使用本地年月日并补齐小时分钟', () => {
    expect(formatDate(new Date(2026, 0, 2, 3, 4).toISOString())).toBe('1月2日 03:04')
  })
})
