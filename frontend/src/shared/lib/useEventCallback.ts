import { useCallback, useLayoutEffect, useRef } from 'react'

/** 事件引用保持稳定，执行时使用最近一次已提交渲染的回调。 */
export function useEventCallback<Args extends unknown[], Result>(callback: (...args: Args) => Result) {
  const latest = useRef(callback)
  useLayoutEffect(() => { latest.current = callback }, [callback])
  return useCallback((...args: Args) => latest.current(...args), [])
}
