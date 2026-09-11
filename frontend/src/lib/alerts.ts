/**
 * Client for the surveillance API.
 *
 * Separate module from `api.ts` (the evaluation explorer) because they are separate
 * applications: this one has no access to ground-truth labels, and keeping the clients
 * apart makes that boundary visible in the frontend too.
 */

export type Severity = "low" | "medium" | "high" | "critical"
export type AlertStatus = "open" | "cleared" | "escalated"

export interface Alert {
  id: number
  alert_type: string
  severity: Severity
  score: number
  detection_method: string[]
  status: AlertStatus
  window_start: string | null
  window_end: string | null
  created_at: string
  resolved_at: string | null
  n_trades: number
  n_accounts: number
  accounts: string[]
  tickers: string[]
  summary: string
}

export interface AlertTrade {
  external_id: string
  account_id: number
  account_name: string | null
  security_id: number
  ticker: string | null
  side: string
  quantity: number
  price: number
  notional: number
  executed_at: string
  venue: string
}

export interface Neighbour {
  name: string
  account_type: string
  trades: number
  notional: number
  net_side: number
}

export interface AlertDetail extends Alert {
  details: Record<string, unknown>
  trades: AlertTrade[]
  neighbourhood: Neighbour[]
}

export interface ApiStats {
  trades: number
  accounts: number
  securities: number
  alerts: number
  open_alerts: number
  by_severity: Record<string, number>
  by_type: Record<string, number>
  by_method: Record<string, number>
  window: [string, string]
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for ${path}`)
  return (await res.json()) as T
}

export const alertsApi = {
  stats: () => get<ApiStats>("/api/stats"),
  list: () => get<Alert[]>("/api/alerts?limit=1000"),
  detail: (id: number) => get<AlertDetail>(`/api/alerts/${id}`),
  setStatus: async (id: number, status: AlertStatus) => {
    const res = await fetch(`/api/alerts/${id}/status?status=${status}`, { method: "POST" })
    if (!res.ok) throw new Error(`failed to set status: ${res.status}`)
    return (await res.json()) as { id: number; status: AlertStatus }
  },
}

export const SEVERITY_ORDER: Record<Severity, number> = {
  critical: 3,
  high: 2,
  medium: 1,
  low: 0,
}

export const SEVERITY_TONE: Record<Severity, "danger" | "warn" | "brand" | "neutral"> = {
  critical: "danger",
  high: "warn",
  medium: "brand",
  low: "neutral",
}

const TYPE_LABELS: Record<string, string> = {
  wash_trade_ring: "Wash trade ring",
  coordinated_cluster: "Coordinated cluster",
  statistical_outlier: "Per-trade anomaly",
}
export const alertTypeLabel = (k: string) =>
  TYPE_LABELS[k] ?? k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())

const METHOD_LABELS: Record<string, string> = {
  statistical: "Per-trade",
  graph: "Network",
}
export const methodLabel = (k: string) => METHOD_LABELS[k] ?? k
