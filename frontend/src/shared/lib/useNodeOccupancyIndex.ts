import { useLayoutEffect, useMemo, useRef } from 'react'
import type { Overview } from '../api/client'
import { nodeOccupancyIndex, type NodeOccupancyIndex } from '../model/nodeOccupants'

export function useNodeOccupancyIndex(data: Overview | null | undefined) {
  const previous = useRef<NodeOccupancyIndex>()
  const index = useMemo(() => nodeOccupancyIndex(data, previous.current), [data])
  useLayoutEffect(() => { previous.current = index }, [index])
  return index
}
