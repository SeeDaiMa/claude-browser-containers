import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { Profiler } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { NodeRow, Overview, Subscription, Task } from '../../../shared/api/client'
import { fixtureInstance, fixtureNode, fixtureOverview, fixtureSubscription, fixtureTask } from '../../../test/fixtures'
import { SubscriptionsPage } from './SubscriptionsPage'

type Submit = (key: string, request: () => Promise<Task>) => Promise<Task>
const state = vi.hoisted(() => ({
  subscriptions: [] as Subscription[],
  data: null as Overview | null,
  nodes: [] as NodeRow[],
  tasks: [] as Task[],
  renders: new Map<string, number>(),
  load: vi.fn<(id: string) => Promise<{ nodes: NodeRow[] }>>(),
  measure: vi.fn<(id: string, nodes: string[]) => Promise<Task>>(),
  submit: vi.fn<Submit>(),
  notify: vi.fn(),
  refresh: vi.fn(),
}))

vi.mock('../../../app/ManagerProvider', () => ({
  useManager: () => ({ data: state.data, subscriptions: state.subscriptions, tasks: state.tasks,
    isBusy: () => false, submitTask: state.submit, notify: state.notify, refresh: state.refresh, draft: { name: '' } }),
}))
vi.mock('../../../shared/api/client', async (original) => {
  const actual = await original<typeof import('../../../shared/api/client')>()
  return { ...actual, api: { ...actual.api, nodes: state.load, testNodes: state.measure } }
})
vi.mock('../../../shared/ui/NodeLatency', () => ({
  NodeLatency: ({ node }: { node: NodeRow }) => {
    state.renders.set(node.id, (state.renders.get(node.id) || 0) + 1)
    return <span>{node.test?.delay_ms ?? 'fixture latency'}</span>
  },
}))

function nodeId(index: number) {
  return 'n' + index.toString(16).padStart(20, '0')
}

function setCount(count: number) {
  state.subscriptions = [fixtureSubscription('a', count)]
  state.nodes = Array.from({ length: count }, (_, index) => fixtureNode(nodeId(index), {
    name: `节点-${String(index).padStart(4, '0')}`,
  }))
}

function page(onRender = (_id: string, _phase: string, _duration: number) => {}) {
  return <MemoryRouter initialEntries={['/subscriptions?view=nodes']}
    future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
    <Profiler id="nodes" onRender={onRender}><SubscriptionsPage /></Profiler>
  </MemoryRouter>
}

beforeEach(() => {
  localStorage.clear()
  state.data = fixtureOverview()
  state.tasks = []
  state.renders.clear()
  setCount(200)
  state.load.mockImplementation(async () => ({ nodes: state.nodes }))
  state.measure.mockImplementation(async (id, nodes) => fixtureTask('test-nodes', id, nodes))
  state.submit = vi.fn<Submit>(async (_key, request) => request())
})

describe('节点表格有界渲染和跨页选择', () => {
  it('5000 节点只渲染一页，翻页保留选择且全选覆盖全部筛选结果', async () => {
    setCount(5000)
    state.nodes[4999] = { ...state.nodes[4999], supported: false, reason: 'fixture unsupported' }
    const durations: number[] = []
    const started = performance.now()
    const view = render(page((_id, _phase, duration) => durations.push(duration)))
    await screen.findByText('节点-0000', {}, { timeout: 30000 })
    const rows = view.container.querySelectorAll('.subscription-node-table tbody tr').length
    console.info('B4_PROFILE', JSON.stringify({ total: 5000, rows, initialMs: performance.now() - started,
      maximumRenderMs: Math.max(...durations) }))
    expect(rows).toBe(50)
    await act(async () => { fireEvent.click(screen.getByRole('checkbox', { name: /^全选/ })) })
    expect(screen.getByText(/已选 4999 个/)).toBeTruthy()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '下一页节点' })) })
    expect((screen.getByRole('checkbox', { name: '选择节点 节点-0050' }) as HTMLInputElement).checked).toBe(true)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '测速所选' })) })
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/4999 个节点/)).toBeTruthy()
    await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: '开始测速' })) })
    expect(state.measure).toHaveBeenCalledWith('a', state.nodes.filter((node) => node.supported).map((node) => node.id))
    await act(async () => {
      fireEvent.change(screen.getByRole('searchbox', { name: '搜索节点名称' }), { target: { value: '节点-4998' } })
    })
    expect(view.container.querySelectorAll('.subscription-node-table tbody tr')).toHaveLength(1)
    expect(screen.getByText(/已选 0 个/)).toBeTruthy()
    expect(screen.getByText(/第 1 \/ 1 页/)).toBeTruthy()
    expect((screen.getByRole('checkbox', { name: '选择节点 节点-4998' }) as HTMLInputElement).checked).toBe(false)
  }, 60000)

  it('勾选一行只重新渲染该行', async () => {
    render(page())
    await screen.findByText('节点-0000')
    state.renders.clear()
    await act(async () => { fireEvent.click(screen.getByRole('checkbox', { name: '选择节点 节点-0000' })) })
    expect([...state.renders.keys()]).toEqual([nodeId(0)])
  })

  it('占用索引只扫描一次实例，不按每行遍历', async () => {
    let reads = 0
    const instances = Array.from({ length: 20 }, (_, index) => {
      const item = fixtureInstance(String(index), nodeId(index))
      Object.defineProperty(item.proxy, 'mode', { enumerable: true, get: () => { reads += 1; return 'subscription' } })
      return item
    })
    state.data = fixtureOverview(instances)
    render(page())
    await screen.findByText('节点-0000')
    expect(reads).toBeLessThanOrEqual(40)
  })

  it('只变概览资源指标不重新渲染行，操作仍使用最新回调', async () => {
    state.data = fixtureOverview([fixtureInstance('owner', nodeId(0))])
    const view = render(page())
    await screen.findByText('节点-0000')
    state.renders.clear()
    const previousSubmit = state.submit
    state.submit = vi.fn<Submit>(async (_key, request) => request())
    state.data = { ...state.data, instances: [{ ...state.data.instances[0],
      usage: { ...state.data.instances[0].usage, memory_mib: 200 } }] }
    await act(async () => { view.rerender(page()) })
    expect(state.renders.size).toBe(0)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '测速节点 节点-0000' })) })
    expect(state.submit).toHaveBeenCalledOnce()
    expect(previousSubmit).not.toHaveBeenCalled()
    expect(state.measure).toHaveBeenCalledWith('a', [nodeId(0)])
  })

  it('数据缩减时页码收敛，保留仍存在的勾选', async () => {
    const view = render(page())
    await screen.findByText('节点-0000')
    await act(async () => { fireEvent.click(screen.getByRole('checkbox', { name: '选择节点 节点-0000' })) })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '下一页节点' })) })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '下一页节点' })) })
    state.nodes = state.nodes.slice(0, 10)
    state.subscriptions = [{ ...state.subscriptions[0], node_count: 10, updated_at: '2026-11-17T00:01:00Z' }]
    await act(async () => { view.rerender(page()) })
    expect(screen.getByText(/第 1 \/ 1 页/)).toBeTruthy()
    expect(view.container.querySelectorAll('.subscription-node-table tbody tr')).toHaveLength(10)
    expect((screen.getByRole('checkbox', { name: '选择节点 节点-0000' }) as HTMLInputElement).checked).toBe(true)
  })
})
