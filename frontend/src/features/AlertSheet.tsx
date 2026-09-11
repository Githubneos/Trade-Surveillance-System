import { motion } from "motion/react"
import { CheckCircle2, ShieldAlert, Undo2 } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import {
  SEVERITY_TONE,
  alertTypeLabel,
  methodLabel,
  type AlertDetail,
  type AlertStatus,
} from "@/lib/alerts"
import { fmtInt, fmtMoney } from "@/lib/api"
import { cn } from "@/lib/utils"

/** Keys rendered as prose rather than raw JSON, in the order an analyst reads them. */
const EVIDENCE_LABELS: Record<string, string> = {
  median_reciprocity: "Median reciprocity",
  median_cotrade_lift: "Co-trading vs chance",
  median_member_net_ratio: "Median member net position",
  flat_member_share: "Members ending flat",
  security_concentration: "Concentration in this name",
  market_directional_unanimity: "Market directional unanimity",
  span_minutes: "Window span (minutes)",
  liquidity_tier: "Liquidity tier",
  prior_history_ratio: "Prior history in this name",
  n_accounts: "Accounts involved",
  n_trades: "Trades involved",
  feature: "Feature breached",
  value: "Value",
  within_rule_percentile: "Percentile within rule",
  corroborated_by_network: "Corroborated by network layer",
  rules: "Rules breached",
}

/** Fields that are ratios in [0,1] and read as nonsense unless shown as percentages.
 *  "Members ending flat: 1" looks like a count of one member; it means all of them. */
const AS_PERCENT = new Set([
  "flat_member_share",
  "security_concentration",
  "market_directional_unanimity",
  "within_rule_percentile",
])

/** Fields better read as a multiple than a bare number. */
const AS_MULTIPLE = new Set(["median_cotrade_lift"])

function formatValue(key: string, v: unknown): string {
  if (typeof v === "boolean") return v ? "yes" : "no"
  if (Array.isArray(v)) return v.join(", ")
  if (typeof v === "number") {
    if (AS_PERCENT.has(key)) return `${(v * 100).toFixed(0)}%`
    if (AS_MULTIPLE.has(key)) return `${v >= 100 ? Math.round(v) : v.toFixed(1)}x`
    return Number.isInteger(v) ? String(v) : v.toFixed(3)
  }
  return String(v)
}

export function AlertSheet({
  detail,
  loading,
  open,
  onOpenChange,
  onStatus,
}: {
  detail: AlertDetail | null
  loading: boolean
  open: boolean
  onOpenChange: (v: boolean) => void
  onStatus: (id: number, status: AlertStatus) => void
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      {open && (
        <SheetContent
          title={
            <div className="min-w-0">
              <div className="font-mono text-[11.5px] tracking-wide text-[var(--color-ink-faint)]">
                ALERT-{String(detail?.id ?? "").padStart(5, "0")}
              </div>
              <div className="mt-0.5 text-[16px] leading-snug font-semibold tracking-tight">
                {detail ? alertTypeLabel(detail.alert_type) : "Loading…"}
                {detail?.tickers?.length ? (
                  <span className="font-mono"> · {detail.tickers.join(", ")}</span>
                ) : null}
              </div>
              {detail && (
                <div className="mt-1.5 flex flex-wrap items-center gap-2">
                  <Badge tone={SEVERITY_TONE[detail.severity]}>{detail.severity}</Badge>
                  {detail.detection_method.map((m) => (
                    <Badge key={m} tone={m === "graph" ? "brand" : "neutral"}>
                      {methodLabel(m)}
                    </Badge>
                  ))}
                  <Badge>score {detail.score.toFixed(2)}</Badge>
                  <Badge
                    tone={
                      detail.status === "escalated"
                        ? "danger"
                        : detail.status === "cleared"
                          ? "success"
                          : "neutral"
                    }
                  >
                    {detail.status}
                  </Badge>
                </div>
              )}
            </div>
          }
        >
          {loading || !detail ? (
            <div className="space-y-3">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-32 w-full" />
              <Skeleton className="h-64 w-full" />
            </div>
          ) : (
            <div className="space-y-6">
              <div className="rounded-lg border-l-2 border-[var(--color-brand)] bg-[var(--color-brand-soft)]/50 px-4 py-3 text-[13px] leading-relaxed">
                {detail.summary || "No rule description recorded."}
              </div>

              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant={detail.status === "escalated" ? "primary" : "outline"}
                  onClick={() => onStatus(detail.id, "escalated")}
                >
                  <ShieldAlert /> Escalate
                </Button>
                <Button
                  size="sm"
                  variant={detail.status === "cleared" ? "primary" : "outline"}
                  onClick={() => onStatus(detail.id, "cleared")}
                >
                  <CheckCircle2 /> Clear
                </Button>
                {detail.status !== "open" && (
                  <Button size="sm" variant="ghost" onClick={() => onStatus(detail.id, "open")}>
                    <Undo2 /> Reopen
                  </Button>
                )}
              </div>

              <section>
                <h4 className="mb-2 text-[13px] font-semibold">Evidence</h4>
                <dl className="grid gap-x-8 gap-y-1.5 text-[13px] sm:grid-cols-[16rem_1fr]">
                  {Object.entries(detail.details)
                    .filter(([k]) => k !== "rule" && k !== "members")
                    .map(([k, v]) => (
                      <div key={k} className="contents">
                        <dt className="text-[var(--color-ink-faint)]">
                          {EVIDENCE_LABELS[k] ?? k.replace(/_/g, " ")}
                        </dt>
                        <dd className="tnum font-medium">{formatValue(k, v)}</dd>
                      </div>
                    ))}
                </dl>
              </section>

              {detail.neighbourhood.length > 0 && (
                <section>
                  <h4 className="mb-1 text-[13px] font-semibold">Network neighbourhood</h4>
                  <p className="mb-2 text-[12px] text-[var(--color-ink-faint)]">
                    Accounts implicated in this alert and what they did inside its window.
                    Net side near zero is the wash-trade signature: position recycled, nobody
                    ends up owning anything.
                  </p>
                  <div className="scrollbar-slim overflow-x-auto rounded-lg border border-[var(--color-line)]">
                    <table className="w-full min-w-[560px] border-collapse text-[12.5px]">
                      <thead>
                        <tr className="bg-[var(--color-surface-muted)] text-[10.5px] tracking-wide text-[var(--color-ink-faint)] uppercase">
                          <th className="px-3 py-2 text-left font-semibold">Account</th>
                          <th className="px-3 py-2 text-left font-semibold">Type</th>
                          <th className="px-3 py-2 text-right font-semibold">Trades</th>
                          <th className="px-3 py-2 text-right font-semibold">Notional</th>
                          <th className="px-3 py-2 text-right font-semibold">Net side</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detail.neighbourhood.map((n, i) => (
                          <motion.tr
                            key={n.name}
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            transition={{ duration: 0.2, delay: Math.min(i * 0.02, 0.3) }}
                            className="border-t border-[var(--color-line)]"
                          >
                            <td className="px-3 py-2">{n.name}</td>
                            <td className="px-3 py-2 text-[var(--color-ink-faint)]">
                              {n.account_type?.replace(/_/g, " ")}
                            </td>
                            <td className="tnum px-3 py-2 text-right">{fmtInt(n.trades)}</td>
                            <td className="tnum px-3 py-2 text-right">
                              {fmtMoney(n.notional, true)}
                            </td>
                            <td
                              className={cn(
                                "tnum px-3 py-2 text-right font-medium",
                                Math.abs(n.net_side) <= 1
                                  ? "text-[var(--color-success)]"
                                  : "text-[var(--color-ink-soft)]",
                              )}
                            >
                              {n.net_side > 0 ? "+" : ""}
                              {n.net_side}
                            </td>
                          </motion.tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}

              <section>
                <h4 className="mb-2 text-[13px] font-semibold">
                  Trades{" "}
                  <span className="font-normal text-[var(--color-ink-faint)]">
                    ({fmtInt(detail.trades.length)} shown)
                  </span>
                </h4>
                <div className="scrollbar-slim max-h-[40vh] overflow-auto rounded-lg border border-[var(--color-line)]">
                  <table className="w-full min-w-[720px] border-collapse text-[12.5px]">
                    <thead className="sticky top-0">
                      <tr className="bg-[var(--color-surface-muted)] text-[10.5px] tracking-wide text-[var(--color-ink-faint)] uppercase">
                        <th className="px-3 py-2 text-left font-semibold">Trade</th>
                        <th className="px-3 py-2 text-left font-semibold">Account</th>
                        <th className="px-3 py-2 text-left font-semibold">Ticker</th>
                        <th className="px-3 py-2 text-left font-semibold">Side</th>
                        <th className="px-3 py-2 text-right font-semibold">Qty</th>
                        <th className="px-3 py-2 text-right font-semibold">Notional</th>
                        <th className="px-3 py-2 text-left font-semibold">Time</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.trades.map((t) => (
                        <tr key={t.external_id} className="border-t border-[var(--color-line)]">
                          <td className="px-3 py-2 font-mono text-[11.5px] text-[var(--color-ink-faint)]">
                            {t.external_id}
                          </td>
                          <td className="px-3 py-2 whitespace-nowrap">{t.account_name}</td>
                          <td className="px-3 py-2 font-mono">{t.ticker}</td>
                          <td
                            className={cn(
                              "px-3 py-2 font-medium",
                              t.side === "buy"
                                ? "text-[var(--color-success)]"
                                : "text-[var(--color-danger)]",
                            )}
                          >
                            {t.side}
                          </td>
                          <td className="tnum px-3 py-2 text-right">{fmtInt(t.quantity)}</td>
                          <td className="tnum px-3 py-2 text-right">{fmtMoney(t.notional)}</td>
                          <td className="tnum px-3 py-2 font-mono text-[11.5px] text-[var(--color-ink-faint)]">
                            {t.executed_at.slice(0, 19).replace("T", " ")}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </div>
          )}
        </SheetContent>
      )}
    </Sheet>
  )
}
