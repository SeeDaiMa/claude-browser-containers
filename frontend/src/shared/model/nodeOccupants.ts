import type { Overview } from '../api/client'

export type NodeOccupant = { id: string; name: string; trashed: boolean }

/** 总览到达后按当前实例和回收站计算；总览尚未返回时才沿用这次节点响应里的占用。 */
export function nodeOccupants(
  data: Overview | null | undefined,
  nodeId: string,
  snapshot: NodeOccupant[] = [],
): NodeOccupant[] {
  if (!nodeId) return []
  if (!data) return snapshot
  return [...data.instances, ...data.trash]
    .filter((item) => item.proxy.mode === 'subscription' && item.proxy.node_id === nodeId)
    .map((item) => ({
      id: item.id,
      name: item.display_name,
      trashed: Boolean(item.trashed_at),
    }))
}
