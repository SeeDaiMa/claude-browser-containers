import { describe, expect, it } from 'vitest'
import { fixtureInstance, fixtureOverview } from '../../test/fixtures'
import { nodeOccupancyIndex, nodeOccupants } from './nodeOccupants'

describe('节点使用者索引及兼容入口', () => {
  it('未知总览使用节点快照，已知空总览覆盖旧快照', () => {
    const snapshot = [{ id: 'old', name: '旧占用', trashed: false }]
    expect(nodeOccupants(undefined, 'n1', snapshot)).toBe(snapshot)
    expect(nodeOccupants(fixtureOverview(), 'n1', snapshot)).toEqual([])
    expect(nodeOccupants(undefined, '', snapshot)).toEqual([])
  })

  it('按全局节点 ID 汇总活动与回收站实例，排除手动代理并保留顺序', () => {
    const active = fixtureInstance('a', 'n1')
    const manual = fixtureInstance('manual', 'n1', { proxy: { mode: 'socks', label: 'fixture', node_id: 'n1' } })
    const other = fixtureInstance('other', 'n2')
    const trash = fixtureInstance('b', 'n1', { trashed_at: '2026-11-17T00:00:00Z',
      proxy: { mode: 'subscription', label: 'fixture', subscription_id: 'different', node_id: 'n1' } })
    const data = fixtureOverview([active, manual, other], [trash])
    const index = nodeOccupancyIndex(data)
    const expected = [{ id: 'a', name: '实例-a', trashed: false }, { id: 'b', name: '实例-b', trashed: true }]
    expect(nodeOccupants(index, 'n1')).toEqual(expected)
    expect(nodeOccupants(data, 'n1')).toEqual(expected)
    expect(nodeOccupants(index, 'n1', [], 'a')).toEqual(expected.slice(1))
  })

  it('仅变资源指标时复用同一占用数组，名称或绑定变化会更新', () => {
    const original = fixtureInstance('a', 'n1')
    const before = nodeOccupancyIndex(fixtureOverview([original]))!
    const changedUsage = { ...original, usage: { ...original.usage, memory_mib: 200 } }
    const after = nodeOccupancyIndex(fixtureOverview([changedUsage]), before)!
    expect(after.get('n1')).toBe(before.get('n1'))
    const renamed = nodeOccupancyIndex(fixtureOverview([{ ...original, display_name: '新名字' }]), after)!
    expect(renamed.get('n1')).not.toBe(after.get('n1'))
    expect(renamed.get('n1')?.[0].name).toBe('新名字')
    expect(before.get('n1')?.[0].name).toBe('实例-a')
    const moved = nodeOccupancyIndex(fixtureOverview([fixtureInstance('a', 'n2')]), renamed)!
    expect(moved.has('n1')).toBe(false)
    expect(moved.get('n2')?.[0].id).toBe('a')
  })

  it('空结果复用常量，不把旧快照带回已知索引', () => {
    const index = nodeOccupancyIndex(fixtureOverview())
    expect(nodeOccupants(index, 'n1')).toBe(nodeOccupants(index, 'n2'))
    expect(nodeOccupants(index, 'n1', [{ id: 'old', name: '旧占用', trashed: false }])).toEqual([])
  })
})
