import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { NodeRow, Subscription } from '../../../shared/api/client'
import { SubscriptionsPage } from './SubscriptionsPage'

const state = vi.hoisted(() => ({
  subscriptions: [] as Subscription[],
  nodes: vi.fn<(id: string) => Promise<{ nodes: NodeRow[] }>>(),
  refresh: vi.fn(),
  notify: vi.fn(),
  submitTask: vi.fn(),
}))

vi.mock('../../../app/ManagerProvider', () => ({
  useManager: () => ({
    data: null,
    subscriptions: state.subscriptions,
    tasks: [],
    isBusy: () => false,
    refresh: state.refresh,
    notify: state.notify,
    submitTask: state.submitTask,
    draft: { name: '' },
  }),
}))

vi.mock('../../../shared/api/client', async (original) => {
  const actual = await original<typeof import('../../../shared/api/client')>()
  return { ...actual, api: { ...actual.api, nodes: state.nodes } }
})

function source(id: string): Subscription {
  return {
    id,
    name: `来源-${id}`,
    node_count: 1,
    updated_at: '2026-11-17T00:00:00Z',
    nodes_tested_at: null,
    nodes_quality_checked_at: null,
    last_error: '',
  }
}

function node(id: string, name = `节点-${id}`): NodeRow {
  return {
    id: `node-${id}`,
    name,
    type: 'ss',
    port: 443,
    supported: true,
    reason: '',
    used_by: [],
    test: null,
    quality: null,
  }
}

function page(view = 'nodes') {
  return (
    <MemoryRouter
      initialEntries={[`/subscriptions?view=${view}`]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <SubscriptionsPage />
    </MemoryRouter>
  )
}

function deferred<T>() {
  let resolve: (value: T) => void = () => {
    throw new Error('Deferred promise not initialized')
  }
  const promise = new Promise<T>((accept) => {
    resolve = accept
  })
  return { promise, resolve: (value: T) => resolve(value) }
}

beforeEach(() => {
  localStorage.clear()
  state.subscriptions = [source('a'), source('b')]
  state.nodes.mockImplementation(async (id) => ({ nodes: [node(id)] }))
})

describe('订阅节点增量刷新', () => {
  it.each(['updated_at', 'nodes_tested_at', 'nodes_quality_checked_at'] as const)(
    '只有 %s 变化的订阅重新请求 nodes',
    async (field) => {
      const view = render(page())
      await screen.findByText('节点-a')
      await screen.findByText('节点-b')
      state.nodes.mockClear()
      state.subscriptions = [
        { ...state.subscriptions[0], [field]: '2026-11-17T00:01:00Z' },
        state.subscriptions[1],
      ]
      await act(async () => {
        view.rerender(page())
      })
      expect(state.nodes.mock.calls.map(([id]) => id)).toEqual(['a'])
      expect(screen.getByText('节点-b')).toBeTruthy()
    },
  )

  it('删除来源立即移除其节点，其他来源无需重取', async () => {
    const view = render(page())
    await screen.findByText('节点-a')
    await screen.findByText('节点-b')
    state.nodes.mockClear()
    state.subscriptions = [state.subscriptions[0]]
    await act(async () => {
      view.rerender(page())
    })
    expect(screen.queryByText('节点-b')).toBeNull()
    expect(screen.getByText('节点-a')).toBeTruthy()
    expect(state.nodes).not.toHaveBeenCalled()
  })

  it('刷新期间复用未变化来源的请求，旧响应不能覆盖新版本', async () => {
    const oldA = deferred<{ nodes: NodeRow[] }>()
    const pendingB = deferred<{ nodes: NodeRow[] }>()
    let requestsA = 0
    state.nodes.mockImplementation((id) => {
      if (id === 'b') return pendingB.promise
      requestsA += 1
      return requestsA === 1 ? oldA.promise : Promise.resolve({ nodes: [node('a', '新节点-a')] })
    })
    const view = render(page())
    await waitFor(() => expect(state.nodes).toHaveBeenCalledTimes(2))
    state.subscriptions = [
      { ...state.subscriptions[0], nodes_tested_at: '2026-11-17T00:01:00Z' },
      state.subscriptions[1],
    ]
    await act(async () => {
      view.rerender(page())
    })
    await act(async () => {
      pendingB.resolve({ nodes: [node('b')] })
    })
    expect(screen.getByText('新节点-a')).toBeTruthy()
    await act(async () => {
      oldA.resolve({ nodes: [node('a', '旧节点-a')] })
    })
    expect(screen.queryByText('旧节点-a')).toBeNull()
    expect(state.nodes.mock.calls.map(([id]) => id)).toEqual(['a', 'b', 'a'])
  })

  it('失败保留旧列表，返回节点页时只重试失败来源', async () => {
    const view = render(page())
    await screen.findByText('节点-a')
    await screen.findByText('节点-b')
    state.nodes.mockImplementation((id) =>
      id === 'a'
        ? Promise.reject(new Error('fixture refresh failed'))
        : Promise.resolve({ nodes: [node(id)] }),
    )
    state.subscriptions = [
      { ...state.subscriptions[0], updated_at: '2026-11-17T00:01:00Z' },
      state.subscriptions[1],
    ]
    await act(async () => {
      view.rerender(page())
    })
    expect(screen.getByText('fixture refresh failed')).toBeTruthy()
    expect(screen.getByText('节点-b')).toBeTruthy()
    state.nodes.mockClear()
    state.nodes.mockImplementation(async (id) => ({ nodes: [node(id)] }))
    await act(async () => {
      fireEvent.click(screen.getByRole('tab', { name: /^订阅/ }))
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('tab', { name: /^节点/ }))
    })
    expect(state.nodes.mock.calls.map(([id]) => id)).toEqual(['a'])
    expect(screen.queryByText('fixture refresh failed')).toBeNull()
  })

  it('未进入节点页时不请求节点', async () => {
    render(page('subscriptions'))
    expect(state.nodes).not.toHaveBeenCalled()
    await act(async () => {
      fireEvent.click(screen.getByRole('tab', { name: /^节点/ }))
    })
    expect(state.nodes.mock.calls.map(([id]) => id)).toEqual(['a', 'b'])
  })
})
