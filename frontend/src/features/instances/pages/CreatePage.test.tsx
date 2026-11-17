import { act, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import type { CreateDraft } from '../../../app/ManagerProvider'
import type { Task } from '../../../shared/api/client'
import { emptyProxy } from '../../../shared/model/proxy'
import { fixtureOverview, fixtureTask } from '../../../test/fixtures'
import { CreatePage } from './CreatePage'

const state = vi.hoisted(() => ({
  draft: {} as CreateDraft,
  tasks: [] as Task[],
  resetDraft: vi.fn(),
}))
vi.mock('../../../app/ManagerProvider', () => ({
  useManager: () => ({
    data: fixtureOverview(),
    error: '',
    subscriptions: [],
    draft: state.draft,
    tasks: state.tasks,
    resetDraft: state.resetDraft,
    setDraft: vi.fn(),
    submitTask: vi.fn(),
  }),
}))
vi.mock('../components/ProxyEditor', () => ({ ProxyEditor: () => null, ProxyMode: () => null }))

beforeEach(() => {
  state.draft = {
    name: '测试创建',
    proxy: { ...emptyProxy },
    taskId: 'create-source',
    requestId: 'fixture',
    step: 2,
  }
  state.tasks = [{ ...fixtureTask('create', 'source'), state: 'succeeded' }]
})

function page() {
  return (
    <MemoryRouter
      initialEntries={['/new']}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/new" element={<CreatePage />} />
        <Route path="/instance/:id" element={<div>创建结果已打开</div>} />
      </Routes>
    </MemoryRouter>
  )
}

it('成功任务随后补齐实例 ID 时仍跳转并清空草稿', async () => {
  const view = render(page())
  expect(screen.queryByText('创建结果已打开')).toBeNull()
  state.tasks = [{ ...state.tasks[0], result: { instance_id: 'fixture-new' } }]
  await act(async () => {
    view.rerender(page())
  })
  expect(screen.getByText('创建结果已打开')).toBeTruthy()
  expect(state.resetDraft).toHaveBeenCalledTimes(1)
})
