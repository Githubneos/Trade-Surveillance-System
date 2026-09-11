import { useEffect, useRef, useState } from "react"

export interface LiveState {
  connected: boolean
  lastEvent: Record<string, unknown> | null
  events: number
}

/**
 * WebSocket subscription to the live alert feed.
 *
 * Reconnects with capped exponential backoff. A dashboard whose feed dies silently is
 * worse than one that visibly reconnects, so connection state is surfaced rather than
 * hidden: an analyst needs to know whether "no new alerts" means calm or broken.
 */
export function useLiveAlerts(onEvent?: (payload: Record<string, unknown>) => void): LiveState {
  const [state, setState] = useState<LiveState>({
    connected: false,
    lastEvent: null,
    events: 0,
  })
  const handler = useRef(onEvent)
  useEffect(() => {
    handler.current = onEvent
  }, [onEvent])

  useEffect(() => {
    let socket: WebSocket | null = null
    let timer: ReturnType<typeof setTimeout> | undefined
    let attempt = 0
    let closed = false

    const connect = () => {
      if (closed) return
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:"
      socket = new WebSocket(`${proto}//${window.location.host}/ws/alerts`)

      socket.onopen = () => {
        attempt = 0
        setState((s) => ({ ...s, connected: true }))
      }
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as Record<string, unknown>
          if (payload.event === "ping" || payload.event === "ready") return
          setState((s) => ({ ...s, lastEvent: payload, events: s.events + 1 }))
          handler.current?.(payload)
        } catch {
          /* a malformed frame should not take the socket down */
        }
      }
      socket.onclose = () => {
        setState((s) => ({ ...s, connected: false }))
        if (closed) return
        attempt += 1
        timer = setTimeout(connect, Math.min(1000 * 2 ** attempt, 15000))
      }
      socket.onerror = () => socket?.close()
    }

    connect()
    return () => {
      closed = true
      if (timer) clearTimeout(timer)
      socket?.close()
    }
  }, [])

  return state
}
