import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ManagerProvider, useManager } from '../../../app/ManagerProvider'
import { api, type Instance, type Overview, type Task } from '../../../shared/api/client'
import { WorkspacePage } from './WorkspacePage'

let snapshot: Overview
let observedData: Overview | null = null
let tasks: Task[] = []

function instance(id = 'a', runtime: Instance['runtime'] = 'running'): Instance {
  return { id, display_name: `测试${id.toUpperCase()}`, created_at: null, historical: false,
    trashed_at: null, proxy_locked: true, port: id === 'a' ? 5898 : 5899,
    url: `http://127.0.0.1:${id === 'a' ? 5898 : 5899}/`, runtime,
    proxy: { mode: 'socks', label: 'fixture' }, exit: null,
    usage: { memory_mib: null, cpu_percent: null, memory_limit_mib: null }, diagnostics: [] }
}

function overview(runtime: Instance['runtime'] = 'running', docker = true): Overview {
  return { docker: { ok: docker, detail: '' },
    summary: { total: 1, running: runtime === 'running' ? 1 : 0, memory_total_mib: null,
      docker_vm_rss_mib: null, docker_helper_rss_mib: null, manager_rss_mib: null },
    instances: [instance('a', runtime)], trash: [], exit_warnings: [] }
}

function ObserveData() {
  observedData = useManager().data
  return null
}

function TestRoutes() {
  const navigate = useNavigate()
  return <>
    <button onClick={() => navigate('/instance/b')}>切换到 B</button>
    <Routes><Route path="/instance/:id" element={<WorkspacePage />} /></Routes>
  </>
}

async function mount() {
  await act(async () => {
    render(
      <MemoryRouter initialEntries={['/instance/a']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <ManagerProvider><ObserveData /><TestRoutes /></ManagerProvider>
      </MemoryRouter>,
    )
  })
}

async function poll(next?: Overview) {
  if (next) snapshot = next
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
}

beforeEach(() => {
  vi.useFakeTimers()
  snapshot = overview()
  observedData = null
  tasks = []
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    disconnect() {}
  })
  vi.spyOn(api, 'overview').mockImplementation(async () => snapshot)
  vi.spyOn(api, 'tasks').mockImplementation(async () => ({ tasks }))
  vi.spyOn(api, 'subscriptions').mockResolvedValue({ subscriptions: [] })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('工作区保留现有画面', () => {
  it.each(['running', 'degraded'] as const)('从 %s 遇到一次 Docker 失败不卸载 iframe', async (runtime) => {
    snapshot = overview(runtime)
    await mount()
    const frame = screen.getByTitle('测试A的浏览器画面')
    await poll(overview('unknown', false))
    expect(screen.getByTitle('测试A的浏览器画面')).toBe(frame)
    expect(within(screen.getByRole('alert')).getByText('Docker 未连接')).toBeTruthy()
    await poll(overview())
    expect(screen.getByTitle('测试A的浏览器画面')).toBe(frame)
  })

  it('Docker 正常但状态未知时保留画面并提示', async () => {
    await mount()
    const frame = screen.getByTitle('测试A的浏览器画面')
    await poll(overview('unknown'))
    expect(screen.getByTitle('测试A的浏览器画面')).toBe(frame)
    expect(screen.getByText('正在确认实例状态')).toBeTruthy()
  })

  it('初次未知不创建画面，确认运行后才连接', async () => {
    snapshot = overview('unknown', false)
    await mount()
    expect(screen.queryByTitle('测试A的浏览器画面')).toBeNull()
    await poll(overview())
    expect(screen.getByTitle('测试A的浏览器画面')).toBeTruthy()
  })

  it('三次内容相同的停止快照才卸载，保留概览去重', async () => {
    await mount()
    const frame = screen.getByTitle('测试A的浏览器画面')
    const stopped = overview('stopped')
    await poll(stopped)
    const firstStoppedData = observedData
    expect(screen.getByTitle('测试A的浏览器画面')).toBe(frame)
    await poll(stopped)
    expect(screen.getByTitle('测试A的浏览器画面')).toBe(frame)
    expect(observedData).toBe(firstStoppedData)
    await poll(stopped)
    expect(screen.queryByTitle('测试A的浏览器画面')).toBeNull()
    expect(observedData).toBe(firstStoppedData)
  })

  it.each(['unknown', 'request-failure'] as const)('%s 会打断连续停止确认', async (interruption) => {
    await mount()
    const frame = screen.getByTitle('测试A的浏览器画面')
    await poll(overview('stopped'))
    expect(screen.getByTitle('测试A的浏览器画面')).toBe(frame)
    if (interruption === 'request-failure') {
      vi.mocked(api.overview).mockRejectedValueOnce(new Error('fixture offline'))
      await poll()
    } else {
      await poll(overview('unknown', false))
    }
    const stopped = overview('stopped')
    await poll(stopped)
    expect(screen.getByTitle('测试A的浏览器画面')).toBe(frame)
    await poll(stopped)
    expect(screen.getByTitle('测试A的浏览器画面')).toBe(frame)
    await poll(stopped)
    expect(screen.queryByTitle('测试A的浏览器画面')).toBeNull()
  })

  it('普通界面重渲染不冒充新的停止快照', async () => {
    await mount()
    const frame = screen.getByTitle('测试A的浏览器画面')
    const stopped = overview('stopped')
    await poll(stopped)
    expect(screen.getByTitle('测试A的浏览器画面')).toBe(frame)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '全屏' })) })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '退出全屏' })) })
    expect(screen.getByTitle('测试A的浏览器画面')).toBe(frame)
    await poll(stopped)
    expect(screen.getByTitle('测试A的浏览器画面')).toBe(frame)
    await poll(stopped)
    expect(screen.queryByTitle('测试A的浏览器画面')).toBeNull()
  })

  it('切换实例不会把旧实例画面状态带给未知的新实例', async () => {
    snapshot.instances.push(instance('b', 'unknown'))
    await mount()
    const oldFrame = screen.getByTitle('测试A的浏览器画面')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '切换到 B' })) })
    expect(screen.queryByTitle('测试B的浏览器画面')).toBeNull()
    snapshot = { ...snapshot, instances: [instance(), instance('b')] }
    await poll()
    expect(screen.getByTitle('测试B的浏览器画面')).not.toBe(oldFrame)
  })

  it.each(['start', 'restart'])('%s 任务完成后重连一次，不保留旧连接', async (kind) => {
    await mount()
    const oldFrame = screen.getByTitle('测试A的浏览器画面')
    tasks = [{ id: 'fixture-task', kind, instance_id: 'a', state: 'running', phase: 'fixture',
      phases: [], error: '', result: {} }]
    await poll(overview('unknown'))
    expect(screen.getByTitle('测试A的浏览器画面')).toBe(oldFrame)
    tasks = [{ ...tasks[0], state: 'succeeded' }]
    await poll(overview())
    const reconnected = screen.getByTitle('测试A的浏览器画面')
    expect(reconnected).not.toBe(oldFrame)
    await poll(overview())
    expect(screen.getByTitle('测试A的浏览器画面')).toBe(reconnected)
  })

  it('画面已关闭后启动只创建一个新 iframe', async () => {
    snapshot = overview('stopped')
    await mount()
    expect(screen.queryByTitle('测试A的浏览器画面')).toBeNull()
    tasks = [{ id: 'fixture-start', kind: 'start', instance_id: 'a', state: 'running',
      phase: 'fixture', phases: [], error: '', result: {} }]
    await poll(overview('unknown'))
    const create = vi.spyOn(document, 'createElement')
    tasks = [{ ...tasks[0], state: 'succeeded' }]
    await poll(overview())
    expect(screen.getByTitle('测试A的浏览器画面')).toBeTruthy()
    expect(create.mock.calls.filter(([tag]) => tag === 'iframe')).toHaveLength(1)
  })
})
