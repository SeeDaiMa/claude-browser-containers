import { useNodeOperation } from './useNodeOperation'

export function useNodeQuality() {
  const { activeFor, isNodeActive, busy, run } = useNodeOperation('check-node-quality')
  return {
    activeQuality: activeFor,
    isNodeChecking: isNodeActive,
    qualityBusy: busy,
    checkQuality: run,
  }
}
