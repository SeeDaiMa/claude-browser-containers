import { useMemo } from 'react'
import type { NodeRow } from '../api/client'

export function useNodeSearch<Node extends NodeRow>(nodes: Node[], query: string) {
  const keyword = query.trim().toLowerCase()
  return useMemo(
    () => nodes.filter((node) => `${node.name} ${node.type}`.toLowerCase().includes(keyword)),
    [nodes, keyword],
  )
}
