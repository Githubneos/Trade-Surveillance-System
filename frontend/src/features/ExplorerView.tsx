import { useCallback, useState } from "react"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { ScenarioSheet } from "@/features/ScenarioSheet"
import { ScenarioTable } from "@/features/ScenarioTable"
import { StatCard } from "@/features/StatCard"
import { ZDistribution } from "@/features/ZDistribution"
import { api, fmtInt, fmtMoney, fmtPct, type ScenarioDetail } from "@/lib/api"
import { useThemeMode } from "@/lib/theme"
import { useAsync } from "@/lib/useAsync"

export function ExplorerView() {
  const theme = useThemeMode()
  const stats = useAsync(api.stats)
  const scenarios = useAsync(api.scenarios)
  const hist = useAsync(api.zHistogram)

  const [selected, setSelected] = useState<ScenarioDetail | null>(null)
  const [sheetOpen, setSheetOpen] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)

  const select = useCallback((id: string) => {
    setSheetOpen(true)
    setDetailLoading(true)
    setSelected(null)
    api
      .scenario(id)
      .then(setSelected)
      .catch(() => setSelected(null))
      .finally(() => setDetailLoading(false))
  }, [])

  const s = stats.data
  const error = stats.error ?? scenarios.error ?? hist.error

  return (
    <>
      <main className="space-y-6">
          {error && (
            <Card className="mb-6 border-[var(--color-danger)]/30 bg-[var(--color-danger-soft)]/50 p-4 text-[13px]">
              <strong className="text-[var(--color-danger)]">
                Dataset explorer not reachable.
              </strong>{" "}
              <span className="text-[var(--color-ink-soft)]">
                This view is served by a <em>separate</em> application, because it reads
                ground-truth labels and the surveillance API deliberately cannot. Start it with{" "}
                <code className="font-mono text-[12px]">
                  python -m surveillance.cli explorer
                </code>
                . ({error.message})
              </span>
            </Card>
          )}

          <section className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            {stats.loading || !s
              ? Array.from({ length: 6 }).map((_, i) => (
                  <Skeleton key={i} className="h-[104px] rounded-[var(--radius-card)]" />
                ))
              : [
                  {
                    label: "Trades",
                    value: s.trades,
                    format: fmtInt,
                    caption: `${fmtInt(s.background)} background`,
                    hint: "Total executions generated across the simulated window.",
                  },
                  {
                    label: "Planted",
                    value: s.planted,
                    format: fmtInt,
                    caption: `${fmtPct(s.planted_pct, 2)} of all trades`,
                    hint: "Real market abuse is under 0.001% of order flow. This dataset is ~1000x richer, so any precision figure measured here is optimistic by roughly three orders of magnitude.",
                  },
                  {
                    label: "Scenarios",
                    value: s.scenarios,
                    format: fmtInt,
                    caption: "positives + hard negatives",
                    hint: "Each labelled scenario states up front which detection layer should catch it.",
                  },
                  {
                    label: "Accounts",
                    value: s.accounts,
                    format: fmtInt,
                    caption: `${s.securities} securities · ${s.days} days`,
                    hint: `${s.window[0]} to ${s.window[1]}`,
                  },
                  {
                    label: "Total notional",
                    value: s.total_notional,
                    format: (n: number) => fmtMoney(n, true),
                    caption: `median ${fmtMoney(s.median_notional)}`,
                    hint: "Gross traded value across the dataset.",
                  },
                  {
                    label: "Background tail",
                    value: s.background_tail_rate,
                    format: (n: number) => fmtPct(n, 2),
                    caption: "baseline |z| > 3 rate",
                    hint: "The share of ordinary trades that land beyond 3 standard deviations by chance. Every difficulty claim in the table is measured against this number, not against zero.",
                  },
                ].map((c, i) => (
                  <StatCard key={c.label} {...c} delay={i * 0.05} />
                ))}
          </section>

          <section className="mb-6">
            <ZDistribution data={hist.data} loading={hist.loading} theme={theme} />
          </section>

          <section>
            <div className="mb-3">
              <h2 className="text-[15px] font-semibold tracking-tight">Case log</h2>
              <p className="mt-1 max-w-3xl text-[12.5px] leading-relaxed text-[var(--color-ink-soft)]">
                <strong className="font-medium text-[var(--color-ink)]">Size sep.</strong> is the
                share of a case's trades beyond |z| = 3 of that account's own trading history. It
                measures one feature, so it says nothing about timing or price. The rows that
                matter are the confirmed cases expecting the{" "}
                <strong className="font-medium text-[var(--color-ink)]">graph</strong> layer: a low
                value there confirms no per-trade size rule can find them. Select a row for detail.
              </p>
            </div>
            {scenarios.loading ? (
              <Skeleton className="h-[420px] rounded-[var(--radius-card)]" />
            ) : (
              <ScenarioTable rows={scenarios.data ?? []} onSelect={select} />
            )}
          </section>
      </main>
      <ScenarioSheet
        detail={selected}
        loading={detailLoading}
        open={sheetOpen}
        onOpenChange={setSheetOpen}
      />
    </>
  )
}
