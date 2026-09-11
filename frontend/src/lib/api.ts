/**
 * Client for the evaluation-side explorer API.
 *
 * Types mirror the Pydantic/FastAPI responses in surveillance/eval/explorer.py. When the
 * Phase 6 serving API lands it gets its own module here -- the two are deliberately
 * separate applications, because only this one is allowed to see ground-truth labels.
 */

export type Label = "positive" | "hard_negative"
export type ExpectedLayer = "statistical" | "graph" | "none"

export interface Stats {
  trades: number
  background: number
  planted: number
  planted_pct: number
  accounts: number
  securities: number
  days: number
  window: [string, string]
  median_notional: number
  total_notional: number
  scenarios: number
  background_tail_rate: number
  db_trades: number | null
}

export interface ScenarioRow {
  scenario_id: string
  scenario_type: string
  subtype: string
  label: Label
  expected_layer: ExpectedLayer
  difficulty: string
  n_accounts: number
  n_trades: number
  median_z: number | null
  size_sep: number | null
  background_tail_rate: number
  window_start: string
  window_end: string
  notes: string
}

export interface Trade {
  external_id: string
  account_id: number
  account_ref: string | null
  security_id: number
  ticker: string | null
  liquidity_tier: string | null
  side: "buy" | "sell"
  quantity: number
  price: number
  notional: number
  executed_at: string
  venue: string
  scenario_id: string | null
  z: number | null
}

export interface ScenarioDetail extends Omit<ScenarioRow, "n_accounts" | "n_trades"> {
  accounts: { id: number; external_ref: string | null; account_type: string | null }[]
  securities: { id: number; ticker: string | null; liquidity_tier: string | null }[]
  trades: Trade[]
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for ${path}`)
  return (await res.json()) as T
}

export const api = {
  stats: () => get<Stats>("/api/stats"),
  scenarios: () => get<ScenarioRow[]>("/api/scenarios"),
  scenario: (id: string) => get<ScenarioDetail>(`/api/scenarios/${id}`),
  zHistogram: () => get<ZHistogram>("/api/z-histogram"),
}

export interface ZHistogram {
  bins: number[]
  series: { key: string; label: string; counts: number[]; total: number }[]
}

/* ---------- formatting helpers, colocated so every view formats identically ---------- */

export const fmtInt = (n: number | null | undefined) =>
  n == null ? "—" : n.toLocaleString("en-US")

export const fmtMoney = (n: number | null | undefined, compact = false) => {
  if (n == null) return "—"
  if (compact && Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(1)}B`
  if (compact && Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(1)}M`
  if (compact && Math.abs(n) >= 1e3) return `$${(n / 1e3).toFixed(0)}K`
  return `$${Math.round(n).toLocaleString("en-US")}`
}

export const fmtPct = (n: number | null | undefined, dp = 1) =>
  n == null ? "—" : `${(n * 100).toFixed(dp)}%`

export const fmtZ = (n: number | null | undefined) =>
  n == null ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(2)}`

/**
 * The verdict shown against each scenario.
 *
 * This mirrors surveillance/eval/dataset_report.py exactly and must stay in sync: it is a
 * claim about ONE feature (notional size relative to the placing account's own history),
 * never about detectability in general. A scenario can be trivially detectable by timing
 * and still read "needs a non-size feature" here.
 */
export function verdict(r: ScenarioRow): {
  text: string
  tone: "good" | "bad" | "warn"
  detail: string
} {
  const base = r.background_tail_rate || 0.0024
  const sep = r.size_sep ?? 0
  if (r.label === "hard_negative") {
    const bad = sep > Math.max(base * 10, 0.05)
    return bad
      ? { text: "contaminated", tone: "bad", detail: "Fires the statistical layer for a reason unrelated to what it tests — any false positive here would be a generator artefact, not a finding." }
      : { text: "clean", tone: "good", detail: "Contains no more size outliers than ordinary background activity, so it tests only what it is meant to test." }
  }
  const separable = sep > Math.max(base * 10, 0.5)
  if (r.expected_layer === "graph") {
    return separable
      ? { text: "leaks to per-trade", tone: "bad", detail: "These trades are individually large enough for a per-trade rule to catch, which would make the graph layer unmotivated." }
      : { text: "hidden from size", tone: "good", detail: "No per-trade size rule can separate these from the account's ordinary activity. Recall here will measure the network layer and nothing else." }
  }
  return separable
    ? { text: "size alone suffices", tone: "good", detail: "A per-trade size feature separates this cleanly — this is the statistical layer's core case." }
    : { text: "needs non-size feature", tone: "warn", detail: "Not separable by size. The statistical layer must catch this through timing, price deviation or frequency instead." }
}
