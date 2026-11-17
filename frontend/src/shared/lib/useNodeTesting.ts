import { useNodeOperation } from './useNodeOperation'

export function useNodeTesting() {
  const { activeFor, isNodeActive, run } = useNodeOperation('test-nodes')
  return {
    activeTest: activeFor,
    isNodeTesting: isNodeActive,
    testNodes: run,
  }
}
