import type { Overview } from '../api/client'

export type NodeOccupant = { id: string; name: string; trashed: boolean }
export type NodeOccupancyIndex = Map<string, NodeOccupant[]>
const EMPTY: NodeOccupant[] = []

export function nodeOccupancyIndex(
  data: Overview | null | undefined,
  previous?: NodeOccupancyIndex,
): NodeOccupancyIndex | undefined {
  if (!data) return undefined
  const index: NodeOccupancyIndex = new Map()
  for (const item of [...data.instances, ...data.trash]) {
    const id = item.proxy.node_id
    if (item.proxy.mode !== 'subscription' || !id) continue
    const occupants = index.get(id) || []
    occupants.push({ id: item.id, name: item.display_name, trashed: Boolean(item.trashed_at) })
    index.set(id, occupants)
  }
  for (const [id, occupants] of index) {
    const old = previous?.get(id)
    if (
      old &&
      old.length === occupants.length &&
      occupants.every(
        (user, offset) =>
          user.id === old[offset].id &&
          user.name === old[offset].name &&
          user.trashed === old[offset].trashed,
      )
    ) {
      index.set(id, old)
    }
  }
  return index
}

/** 总览到达后按当前实例和回收站计算；总览尚未返回时才沿用这次节点响应里的占用。 */
export function nodeOccupants(
  data: Overview | NodeOccupancyIndex | null | undefined,
  nodeId: string,
  snapshot: NodeOccupant[] = EMPTY,
  excludeId?: string,
): NodeOccupant[] {
  if (!nodeId) return EMPTY
  const index = data instanceof Map ? data : nodeOccupancyIndex(data)
  const occupants = index ? index.get(nodeId) || EMPTY : snapshot
  if (!occupants.length) return EMPTY
  return excludeId ? occupants.filter((user) => user.id !== excludeId) : occupants
}
