import { act, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { NodeRow, Subscription } from '../../../shared/api/client'
import { emptyProxy, type ProxyDraft } from '../../../shared/model/proxy'
import { ProxyEditor } from './ProxyEditor'

const state = vi.hoisted(() => ({
  subscriptions: [] as Subscription[],
  nodes: vi.fn<(id: string) => Promise<{ nodes: NodeRow[] }>>(),
  changed: vi.fn(),
  selected: vi.fn(),
}))

vi.mock('../../../app/ManagerProvider', () => ({
  useManager: () => ({ data: null, subscriptions: state.subscriptions, tasks: [], isBusy: () => false,
    notify: vi.fn(), submitTask: vi.fn() }),
}))

vi.mock('../../../shared/api/client', async (original) => {
  const actual = await original<typeof import('../../../shared/api/client')>()
  return { ...actual, api: { ...actual.api, nodes: state.nodes } }
})

function source(id: string): Subscription {
  return { id, name: `来源-${id}`, node_count: 1, updated_at: '2026-11-17T00:00:00Z',
    nodes_tested_at: null, nodes_quality_checked_at: null, last_error: '' }
}

function node(id: string, name = `节点-${id}`): NodeRow {
  return { id: `node-${id}`, name, type: 'ss', port: 443, supported: true,
    reason: '', used_by: [], test: null, quality: null }
}

function page(value: ProxyDraft = { ...emptyProxy, subscriptionId: 'a', nodeId: 'node-a' }) {
  return (
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <ProxyEditor value={value} onChange={state.changed} password="" setPassword={vi.fn()}
        returnTo="/new" onSelectedNodeChange={state.selected} />
    </MemoryRouter>
  )
}

function deferred<T>() {
  let resolve: (value: T) => void = () => { throw new Error('Deferred promise not initialized') }
  const promise = new Promise<T>((accept) => { resolve = accept })
  return { promise, resolve: (value: T) => resolve(value) }
}

beforeEach(() => {
  state.subscriptions = [source('a'), source('b')]
  state.nodes.mockImplementation(async (id) => ({ nodes: [node(id)] }))
})

describe('代理节点列表后台刷新', () => {
  it('尚未选来源时，同数量订阅被替换仍选用当前首个来源', async () => {
    const value = { ...emptyProxy, subscriptionId: '' }
    const view = render(page(value))
    expect(state.changed).toHaveBeenLastCalledWith({ ...value, subscriptionId: 'a' })
    state.changed.mockClear()
    state.subscriptions = [source('c'), source('d')]
    await act(async () => { view.rerender(page(value)) })
    expect(state.changed).toHaveBeenLastCalledWith({ ...value, subscriptionId: 'c' })
  })

  it.each(['updated_at', 'nodes_tested_at', 'nodes_quality_checked_at'] as const)(
    '%s 改变时保留列表、选择和滚动容器', async (field) => {
      const view = render(page())
      await screen.findByText('节点-a')
      const list = screen.getByRole('radiogroup', { name: '选择节点' })
      list.scrollTop = 120
      state.selected.mockClear()
      const pending = deferred<{ nodes: NodeRow[] }>()
      state.nodes.mockReturnValueOnce(pending.promise)
      state.subscriptions = [{ ...state.subscriptions[0], [field]: '2026-11-17T00:01:00Z' }, state.subscriptions[1]]
      await act(async () => { view.rerender(page()) })
      expect(screen.getByRole('radiogroup', { name: '选择节点' })).toBe(list)
      expect(list.scrollTop).toBe(120)
      expect((screen.getByRole('radio', { name: /节点-a/ }) as HTMLInputElement).checked).toBe(true)
      expect(state.selected).not.toHaveBeenCalledWith(undefined)
      await act(async () => { pending.resolve({ nodes: [node('a')] }) })
      expect(screen.getByRole('radiogroup', { name: '选择节点' })).toBe(list)
      expect(list.scrollTop).toBe(120)
    },
  )

  it('后台刷新失败仍保留原列表和选择', async () => {
    const view = render(page())
    await screen.findByText('节点-a')
    const list = screen.getByRole('radiogroup', { name: '选择节点' })
    state.selected.mockClear()
    state.nodes.mockRejectedValueOnce(new Error('fixture refresh failed'))
    state.subscriptions = [{ ...state.subscriptions[0], nodes_tested_at: '2026-11-17T00:01:00Z' }, state.subscriptions[1]]
    await act(async () => { view.rerender(page()) })
    expect(screen.getByText('fixture refresh failed')).toBeTruthy()
    expect(screen.getByRole('radiogroup', { name: '选择节点' })).toBe(list)
    expect(state.selected).not.toHaveBeenCalledWith(undefined)
  })

  it('切换订阅先清空旧节点，迟到响应不能带回旧来源', async () => {
    const view = render(page())
    await screen.findByText('节点-a')
    const oldA = deferred<{ nodes: NodeRow[] }>()
    state.nodes.mockReturnValueOnce(oldA.promise)
    state.subscriptions = [{ ...state.subscriptions[0], nodes_tested_at: '2026-11-17T00:01:00Z' }, state.subscriptions[1]]
    await act(async () => { view.rerender(page()) })
    const nextB = deferred<{ nodes: NodeRow[] }>()
    state.nodes.mockReturnValueOnce(nextB.promise)
    await act(async () => { view.rerender(page({ ...emptyProxy, subscriptionId: 'b' })) })
    expect(screen.queryByText('节点-a')).toBeNull()
    await act(async () => { oldA.resolve({ nodes: [node('a', '迟到的旧节点')] }) })
    expect(screen.queryByText('迟到的旧节点')).toBeNull()
    await act(async () => { nextB.resolve({ nodes: [node('b')] }) })
    expect(screen.getByText('节点-b')).toBeTruthy()
  })

  it('已选来源被删除时清除旧节点', async () => {
    const view = render(page())
    await screen.findByText('节点-a')
    state.nodes.mockRejectedValueOnce(new Error('fixture source removed'))
    state.subscriptions = [state.subscriptions[1]]
    await act(async () => { view.rerender(page()) })
    expect(screen.queryByText('节点-a')).toBeNull()
  })
})
