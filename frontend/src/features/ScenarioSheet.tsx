import { motion } from "motion/react"
import { Badge } from "@/components/ui/badge"
import { Sheet, SheetContent } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { fmtInt, fmtMoney, fmtZ, layerLabel, typeLabel, verdict, type ScenarioDetail } from "@/lib/api"
import { cn } from "@/lib/utils"

const TONE = { good: "success", bad: "danger", warn: "warn" } as const

export function ScenarioSheet({
  detail,
  loading,
  open,
  onOpenChange,
}: {
  detail: ScenarioDetail | null
  loading: boolean
  open: boolean
  onOpenChange: (v: boolean) => void
}) {
  const v = detail
    ? verdict({ ...detail, n_accounts: 0, n_trades: 0 } as never)
    : null

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      {open && (
        <SheetContent
          title={
            <div className="min-w-0">
              <div className="font-mono text-[11.5px] tracking-wide text-[var(--color-ink-faint)]">
                {detail?.case_ref ?? ""}
              </div>
              <div className="mt-0.5 text-[16px] leading-snug font-semibold tracking-tight">
                {detail?.title ?? "Loading…"}
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-2">
                {detail && (
                  <>
                    <Badge tone={detail.label === "positive" ? "danger" : "brand"}>
                      {detail.label === "positive" ? "Confirmed abuse" : "Benign look-alike"}
                    </Badge>
                    <Badge>{typeLabel(detail.subtype)}</Badge>
                    <Badge>{layerLabel(detail.expected_layer)}</Badge>
                    <Badge>{detail.difficulty}</Badge>
                    {v && <Badge tone={TONE[v.tone]}>{v.text}</Badge>}
                  </>
                )}
              </div>
            </div>
          }
        >
          {loading || !detail ? (
            <div className="space-y-3">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-64 w-full" />
            </div>
          ) : (
            <div className="space-y-6">
              <div className="rounded-lg border-l-2 border-[var(--color-brand)] bg-[var(--color-brand-soft)]/50 px-4 py-3 text-[13px] leading-relaxed text-[var(--color-ink)]">
                {detail.notes}
              </div>

              {v && (
                <p className="text-[13px] leading-relaxed text-[var(--color-ink-soft)]">
                  {v.detail}
                </p>
              )}

              <dl className="grid gap-x-8 gap-y-2 text-[13px] sm:grid-cols-[7rem_1fr]">
                <dt className="text-[var(--color-ink-faint)]">Window</dt>
                <dd className="tnum font-mono text-[12px]">
                  {detail.window_start.slice(0, 19).replace("T", " ")} →{" "}
                  {detail.window_end.slice(0, 19).replace("T", " ")}
                </dd>
                <dt className="text-[var(--color-ink-faint)]">Accounts</dt>
                <dd className="flex flex-wrap gap-1.5">
                  {detail.accounts.map((a) => (
                    <Badge key={a.id}>
                      <span>{a.name ?? a.external_ref ?? a.id}</span>
                      <span className="text-[var(--color-ink-faint)]">
                        {a.account_type?.replace(/_/g, " ")}
                      </span>
                    </Badge>
                  ))}
                </dd>
                <dt className="text-[var(--color-ink-faint)]">Securities</dt>
                <dd className="flex flex-wrap gap-1.5">
                  {detail.securities.map((s) => (
                    <Badge key={s.id} tone="brand">
                      <span className="font-mono font-semibold">{s.ticker ?? s.id}</span>
                      <span>{s.name}</span>
                      <span className="opacity-70">{s.liquidity_tier}</span>
                    </Badge>
                  ))}
                </dd>
              </dl>

              <div>
                <div className="mb-2 flex items-baseline justify-between gap-4">
                  <h4 className="text-[13px] font-semibold">
                    Trades{" "}
                    <span className="font-normal text-[var(--color-ink-faint)]">
                      ({fmtInt(detail.trades.length)} shown)
                    </span>
                  </h4>
                  <p className="text-[12px] text-[var(--color-ink-faint)]">
                    z near zero = indistinguishable from that account's ordinary activity
                  </p>
                </div>
                <div className="scrollbar-slim overflow-x-auto rounded-lg border border-[var(--color-line)]">
                  <table className="w-full min-w-[880px] border-collapse text-[12.5px]">
                    <thead>
                      <tr className="bg-[var(--color-surface-muted)] text-[10.5px] tracking-wide text-[var(--color-ink-faint)] uppercase">
                        <th className="px-3 py-2 text-left font-semibold">Trade</th>
                        <th className="px-3 py-2 text-left font-semibold">Account</th>
                        <th className="px-3 py-2 text-left font-semibold">Ticker</th>
                        <th className="px-3 py-2 text-left font-semibold">Side</th>
                        <th className="px-3 py-2 text-right font-semibold">Qty</th>
                        <th className="px-3 py-2 text-right font-semibold">Price</th>
                        <th className="px-3 py-2 text-right font-semibold">Notional</th>
                        <th className="px-3 py-2 text-right font-semibold">z</th>
                        <th className="px-3 py-2 text-left font-semibold">Time</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.trades.map((t, i) => (
                        <motion.tr
                          key={t.external_id}
                          initial={{ opacity: 0 }}
                          animate={{ opacity: 1 }}
                          transition={{ duration: 0.2, delay: Math.min(i * 0.006, 0.35) }}
                          className="border-t border-[var(--color-line)] transition-colors hover:bg-[var(--color-surface-muted)]"
                        >
                          <td className="px-3 py-2 font-mono text-[11.5px] text-[var(--color-ink-faint)]">
                            {t.external_id}
                          </td>
                          <td className="px-3 py-2 whitespace-nowrap">{t.account_name}</td>
                          <td className="px-3 py-2">
                            <span className="font-medium">{t.ticker}</span>{" "}
                            <span className="text-[var(--color-ink-faint)]">
                              {t.liquidity_tier?.[0]}
                            </span>
                          </td>
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
                          <td className="tnum px-3 py-2 text-right">{t.price.toFixed(2)}</td>
                          <td className="tnum px-3 py-2 text-right">{fmtMoney(t.notional)}</td>
                          <td
                            className={cn(
                              "tnum px-3 py-2 text-right font-medium",
                              Math.abs(t.z ?? 0) > 3
                                ? "text-[var(--color-warn)]"
                                : "text-[var(--color-ink-faint)]",
                            )}
                          >
                            {fmtZ(t.z)}
                          </td>
                          <td className="tnum px-3 py-2 font-mono text-[11.5px] text-[var(--color-ink-faint)]">
                            {t.executed_at.slice(0, 19).replace("T", " ")}
                          </td>
                        </motion.tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </SheetContent>
      )}
    </Sheet>
  )
}
