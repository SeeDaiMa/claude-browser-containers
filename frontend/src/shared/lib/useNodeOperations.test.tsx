import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Task } from '../api/client'
import { deferred, fixtureTask } from '../../test/fixtures'
import { useNodeTesting } from './useNodeTesting'
import { useNodeQuality } from './useNodeQuality'

type Submit = (key: string, request: () => Promise<Task>) => Promise<Task>
const state = vi.hoisted(() => ({
  tasks: [] as Task[],
  submit: vi.fn<Submit>(),
  notify: vi.fn(),
  latency: vi.fn<(source: string, nodes: string[]) => Promise<Task>>(),
  quality: vi.fn<(source: string, nodes: string[]) => Promise<Task>>(),
}))

vi.mock('../../app/ManagerProvider', () => ({
  useManager: () => ({ tasks: state.tasks, submitTask: state.submit, notify: state.notify }),
}))
vi.mock('../api/client', async (original) => {
  const actual = await original<typeof import('../api/client')>()
  return {
    ...actual,
    api: { ...actual.api, testNodes: state.latency, checkNodeQuality: state.quality },
  }
})

function useBoth() {
  return { latency: useNodeTesting(), quality: useNodeQuality() }
}

beforeEach(() => {
  state.tasks = []
  state.submit = vi.fn<Submit>(async (_key, request) => request())
  state.latency.mockImplementation(async (source, nodes) =>
    fixtureTask('test-nodes', source, nodes),
  )
  state.quality.mockImplementation(async (source, nodes) =>
    fixtureTask('check-node-quality', source, nodes),
  )
})

describe('节点检测操作共用逻辑', () => {
  it.each(['latency', 'quality'] as const)(
    '%s 操作引用稳定，并使用最新 Manager 回调',
    async (kind) => {
      const view = renderHook(useBoth)
      const before =
        kind === 'latency'
          ? view.result.current.latency.testNodes
          : view.result.current.quality.checkQuality
      const previousSubmit = state.submit
      state.submit = vi.fn<Submit>(async (_key, request) => request())
      await act(async () => {
        view.rerender()
      })
      const after =
        kind === 'latency'
          ? view.result.current.latency.testNodes
          : view.result.current.quality.checkQuality
      expect(after).toBe(before)
      await act(async () => {
        await before('a', ['n1'])
      })
      expect(state.submit).toHaveBeenCalledOnce()
      expect(previousSubmit).not.toHaveBeenCalled()
      expect(state[kind]).toHaveBeenCalledWith('a', ['n1'])
    },
  )

  it('分别匹配两种任务和来源，结束后移除忙碌状态', async () => {
    const latency = fixtureTask('test-nodes', 'a', ['n1'])
    const quality = fixtureTask('check-node-quality', 'b', ['n2'])
    state.tasks = [latency, quality]
    const view = renderHook(useBoth)
    expect(view.result.current.latency.activeTest('a')).toBe(latency)
    expect(view.result.current.latency.activeTest('b')).toBeUndefined()
    expect(view.result.current.latency.isNodeTesting('a', 'n1')).toBe(true)
    expect(view.result.current.latency.isNodeTesting('b', 'n1')).toBe(false)
    expect(view.result.current.latency.isNodeTesting('a', 'n2')).toBe(false)
    expect(view.result.current.quality.activeQuality('b')).toBe(quality)
    expect(view.result.current.quality.qualityBusy('b')).toBe(true)
    expect(view.result.current.quality.isNodeChecking('b', 'n2')).toBe(true)
    expect(view.result.current.quality.isNodeChecking('a', 'n2')).toBe(false)
    state.tasks = [{ ...quality, state: 'interrupted' }]
    await act(async () => {
      view.rerender()
    })
    expect(view.result.current.quality.activeQuality('b')).toBeUndefined()
    expect(view.result.current.quality.qualityBusy('b')).toBe(false)
    expect(view.result.current.quality.isNodeChecking('b', 'n2')).toBe(false)
  })

  it('质量请求未返回时按来源显示忙碌，完成后恢复', async () => {
    const request = deferred<Task>()
    state.quality.mockReturnValueOnce(request.promise)
    const view = renderHook(useBoth)
    let pending: Promise<void> | undefined
    act(() => {
      pending = view.result.current.quality.checkQuality('a', ['n1'])
    })
    expect(view.result.current.quality.qualityBusy('a')).toBe(true)
    expect(view.result.current.quality.qualityBusy('b')).toBe(false)
    await act(async () => {
      request.resolve(fixtureTask('check-node-quality', 'a'))
      await pending
    })
    expect(view.result.current.quality.qualityBusy('a')).toBe(false)
  })

  it('保留旧入口在重复活动快照中的选择顺序', () => {
    const latency = fixtureTask('test-nodes', 'a', ['n1'])
    const laterLatency = { ...latency, id: 'later-latency' }
    const quality = fixtureTask('check-node-quality', 'a', ['n2'])
    const laterQuality = { ...quality, id: 'later-quality' }
    state.tasks = [latency, laterLatency, quality, laterQuality]
    const view = renderHook(useBoth)
    expect(view.result.current.latency.activeTest('a')).toBe(latency)
    expect(view.result.current.quality.activeQuality('a')).toBe(laterQuality)
  })

  it.each(['latency', 'quality'] as const)('%s 保留原有错误提示和参数', async (kind) => {
    state[kind].mockRejectedValueOnce(new Error('fixture failure'))
    const view = renderHook(useBoth)
    await act(async () => {
      if (kind === 'latency') await view.result.current.latency.testNodes('a', ['n1', 'n2'])
      else await view.result.current.quality.checkQuality('a', ['n1', 'n2'])
    })
    expect(state.notify).toHaveBeenCalledWith(
      kind === 'latency' ? '无法开始测速' : '无法开始 IP 质量检测',
      'danger',
      'fixture failure',
    )
    expect(state[kind]).toHaveBeenCalledWith('a', ['n1', 'n2'])
  })
})
