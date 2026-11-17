import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, type Overview } from '../shared/api/client'
import { deferred, fixtureOverview, fixtureSubscription, fixtureTask } from '../test/fixtures'
import { ManagerProvider, useManager } from './ManagerProvider'

let manager: ReturnType<typeof useManager>
function Observe() { manager = useManager(); return null }
async function mount() {
  let view: ReturnType<typeof render>
  await act(async () => { view = render(<ManagerProvider><Observe /></ManagerProvider>) })
  return view!
}
async function advance(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
  vi.spyOn(api, 'overview').mockResolvedValue(fixtureOverview())
  vi.spyOn(api, 'tasks').mockResolvedValue({ tasks: [fixtureTask('start', 'source')] })
  vi.spyOn(api, 'subscriptions').mockResolvedValue({ subscriptions: [fixtureSubscription('source')] })
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('概览和任务轮询', () => {
  it('相同 JSON 沿用三个集合引用，字段变化才替换对应集合', async () => {
    await mount()
    const before = { data: manager.data, tasks: manager.tasks, subscriptions: manager.subscriptions }
    vi.mocked(api.overview).mockResolvedValue(structuredClone(manager.data!))
    vi.mocked(api.tasks).mockResolvedValue({ tasks: structuredClone(manager.tasks) })
    vi.mocked(api.subscriptions).mockResolvedValue({ subscriptions: structuredClone(manager.subscriptions) })
    await act(async () => { await manager.refresh() })
    expect(manager.data).toBe(before.data)
    expect(manager.tasks).toBe(before.tasks)
    expect(manager.subscriptions).toBe(before.subscriptions)
    vi.mocked(api.overview).mockResolvedValue({ ...fixtureOverview(), summary: { ...fixtureOverview().summary, manager_rss_mib: 42 } })
    await act(async () => { await manager.refresh() })
    expect(manager.data).not.toBe(before.data)
    expect(manager.data?.summary.manager_rss_mib).toBe(42)
    expect(manager.tasks).toBe(before.tasks)
  })

  it.each([
    { hidden: false, active: false, interval: 5000 },
    { hidden: false, active: true, interval: 2000 },
    { hidden: true, active: true, interval: 30000 },
  ])('隐藏=$hidden 活动任务=$active 时每 $interval ms 轮询', async ({ hidden, active, interval }) => {
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(hidden)
    vi.mocked(api.tasks).mockResolvedValue({ tasks: active ? [fixtureTask('start', 'source')] : [] })
    await mount()
    expect(api.overview).toHaveBeenCalledTimes(1)
    await advance(interval - 1)
    expect(api.overview).toHaveBeenCalledTimes(1)
    await advance(1)
    expect(api.overview).toHaveBeenCalledTimes(2)
  })

  it('请求尚未完成时手动刷新和恢复可见不重复发请求，卸载取消后续轮询', async () => {
    const pending = deferred<Overview>()
    vi.mocked(api.overview).mockReturnValue(pending.promise)
    const view = await mount()
    await act(async () => {
      await manager.refresh()
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await advance(30000)
    expect(api.overview).toHaveBeenCalledTimes(1)
    await act(async () => { pending.resolve(fixtureOverview()) })
    view.unmount()
    await advance(30000)
    document.dispatchEvent(new Event('visibilitychange'))
    expect(api.overview).toHaveBeenCalledTimes(1)
  })

  it('从隐藏恢复可见立即刷新，任务结束后改为普通间隔', async () => {
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    await mount()
    hidden.mockReturnValue(false)
    vi.mocked(api.tasks).mockResolvedValue({ tasks: [] })
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
    expect(api.overview).toHaveBeenCalledTimes(2)
    await advance(4999)
    expect(api.overview).toHaveBeenCalledTimes(2)
    await advance(1)
    expect(api.overview).toHaveBeenCalledTimes(3)
  })

  it('一个接口失败仍更新其他集合、保留旧概览，并在恢复后清除错误', async () => {
    await mount()
    const before = manager.data
    const observed = vi.fn()
    const unsubscribe = manager.subscribeOverview(observed)
    vi.mocked(api.overview).mockRejectedValueOnce(new Error('fixture offline'))
    vi.mocked(api.subscriptions).mockResolvedValue({ subscriptions: [fixtureSubscription('new')] })
    await act(async () => { await manager.refresh() })
    expect(manager.data).toBe(before)
    expect(manager.subscriptions[0].id).toBe('new')
    expect(manager.error).toBe('fixture offline')
    expect(observed).toHaveBeenLastCalledWith(null)
    await act(async () => { await manager.refresh() })
    expect(manager.error).toBe('')
    expect(observed).toHaveBeenLastCalledWith(fixtureOverview())
    unsubscribe()
  })

  it('提交的任务在旧轮询快照中缺失时仍保留，服务端确认后不重复', async () => {
    vi.mocked(api.tasks).mockResolvedValue({ tasks: [] })
    await mount()
    const pending = deferred<Overview>()
    vi.mocked(api.overview).mockReturnValueOnce(pending.promise)
    let refreshing: Promise<void>
    await act(async () => { refreshing = manager.refresh() })
    const submitted = fixtureTask('start', 'source')
    await act(async () => { await manager.submitTask('source', async () => submitted) })
    await act(async () => { pending.resolve(fixtureOverview()); await refreshing! })
    expect(manager.tasks).toEqual([submitted])
    expect(manager.isBusy('source')).toBe(true)
    vi.mocked(api.tasks).mockResolvedValue({ tasks: [submitted] })
    await act(async () => { await manager.refresh() })
    expect(manager.tasks).toEqual([submitted])
  })
})
