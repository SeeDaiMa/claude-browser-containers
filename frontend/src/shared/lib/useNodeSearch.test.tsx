import { renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { fixtureNode } from '../../test/fixtures'
import { useNodeSearch } from './useNodeSearch'

describe('共用节点搜索', () => {
  it('按名称与协议忽略大小写和首尾空白，保持原顺序和节点对象', () => {
    const nodes = [
      fixtureNode('n1', { name: 'Tokyo', type: 'SS' }),
      fixtureNode('n2', { name: '日本节点', type: 'vmess' }),
    ]
    const view = renderHook(({ query }) => useNodeSearch(nodes, query), {
      initialProps: { query: ' ss ' },
    })
    expect(view.result.current).toEqual(nodes)
    expect(view.result.current[0]).toBe(nodes[0])
    view.rerender({ query: 'TOKYO' })
    expect(view.result.current).toEqual([nodes[0]])
    view.rerender({ query: '日本' })
    expect(view.result.current).toEqual([nodes[1]])
    view.rerender({ query: 'n1' })
    expect(view.result.current).toEqual([])
  })

  it('不改变支持状态、指标、来源字段或应用额外的可用性规则', () => {
    const nodes = [{ ...fixtureNode('n1', { supported: false }), subscriptionId: 'a' }]
    const view = renderHook(() => useNodeSearch(nodes, ''))
    expect(view.result.current[0]).toBe(nodes[0])
    expect(view.result.current[0].subscriptionId).toBe('a')
  })
})
