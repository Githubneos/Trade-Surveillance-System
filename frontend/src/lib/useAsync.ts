import { useEffect, useState } from "react"

export interface AsyncState<T> {
  data: T | null
  error: Error | null
  loading: boolean
}

/**
 * Minimal fetch-on-mount hook. No cache layer: this app makes three calls in total, and a
 * query library would be more machinery than the problem has.
 *
 * `fn` must be referentially stable -- pass a module-level function (as `api.stats` is),
 * not an inline arrow, or the effect reruns every render. It is the sole dependency, which
 * keeps the dependency list an array literal the linter can actually verify.
 */
export function useAsync<T>(fn: () => Promise<T>): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({
    data: null,
    error: null,
    loading: true,
  })

  useEffect(() => {
    let alive = true
    fn()
      .then((data) => {
        if (alive) setState({ data, error: null, loading: false })
      })
      .catch((error: unknown) => {
        if (alive) {
          setState({
            data: null,
            error: error instanceof Error ? error : new Error(String(error)),
            loading: false,
          })
        }
      })
    return () => {
      alive = false
    }
  }, [fn])

  return state
}
