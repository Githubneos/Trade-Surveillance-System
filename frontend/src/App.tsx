import { AnimatePresence, motion } from "motion/react"
import { Activity, Database, Moon, ShieldAlert, Sun } from "lucide-react"
import { useCallback, useState } from "react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { TooltipProvider } from "@/components/ui/tooltip"
import { ScenarioSheet } from "@/features/ScenarioSheet"
import { ScenarioTable } from "@/features/ScenarioTable"
import { StatCard } from "@/features/StatCard"
import { ZDistribution } from "@/features/ZDistribution"
import { api, fmtInt, fmtMoney, fmtPct, type ScenarioDetail } from "@/lib/api"
import { useTheme } from "@/lib/theme"
import { useAsync } from "@/lib/useAsync"

export default function App() {
  const { theme, toggle } = useTheme()
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
    <TooltipProvider>
      <div className="min-h-dvh bg-[var(--color-canvas)]">
        <header className="sticky top-0 z-30 border-b border-[var(--color-line)] bg-[var(--color-surface)]/85 backdrop-blur-xl">
          <div className="mx-auto flex max-w-[1400px] items-center gap-4 px-6 py-3.5">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-[var(--color-brand)] text-white shadow-[var(--shadow-card)]">
              <ShieldAlert className="size-[18px]" />
            </div>
            <div className="min-w-0">
              <h1 className="text-[15px] leading-tight font-semibold tracking-tight">
                Trade Surveillance
              </h1>
              <p className="truncate text-[12.5px] text-[var(--color-ink-soft)]">
                Dataset explorer · Phase 1 output
              </p>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <span className="hidden items-center gap-1.5 rounded-full border border-[var(--color-line)] px-3 py-1.5 text-[12px] text-[var(--color-ink-soft)] sm:inline-flex">
                <Database className="size-3.5" />
                <span className="tnum">{fmtInt(s?.db_trades ?? null)}</span> in Postgres
              </span>
              <Button size="icon" variant="ghost" onClick={toggle} aria-label="Toggle theme">
                <AnimatePresence mode="wait" initial={false}>
                  <motion.span
                    key={theme}
                    initial={{ opacity: 0, rotate: -60, scale: 0.7 }}
                    animate={{ opacity: 1, rotate: 0, scale: 1 }}
                    exit={{ opacity: 0, rotate: 60, scale: 0.7 }}
                    transition={{ duration: 0.18 }}
                    className="flex"
                  >
                    {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
                  </motion.span>
                </AnimatePresence>
              </Button>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-[1400px] px-6 pt-6 pb-20">
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35 }}
            className="mb-6 flex items-start gap-3 rounded-xl border border-[var(--color-success)]/25 bg-[var(--color-success-soft)]/60 px-4 py-3"
          >
            <Activity className="mt-0.5 size-4 shrink-0 text-[var(--color-success)]" />
            <p className="text-[12.5px] leading-relaxed text-[var(--color-ink-soft)]">
              <strong className="font-semibold text-[var(--color-ink)]">
                Evaluation-side tool.
              </strong>{" "}
              This reads ground-truth labels so you can inspect what was planted. The Phase 6
              serving API is a separate application with no label access — a boundary enforced
              by <code className="font-mono text-[11.5px]">tests/test_detection_isolation.py</code>,
              which parses the AST of every detection module rather than trusting a grep.
            </p>
          </motion.div>

          {error && (
            <Card className="mb-6 border-[var(--color-danger)]/30 bg-[var(--color-danger-soft)]/50 p-4 text-[13px]">
              <strong className="text-[var(--color-danger)]">Cannot reach the API.</strong>{" "}
              <span className="text-[var(--color-ink-soft)]">
                Start it with{" "}
                <code className="font-mono text-[12px]">python -m surveillance.cli serve</code>.{" "}
                ({error.message})
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
              <h2 className="text-[15px] font-semibold tracking-tight">Planted scenarios</h2>
              <p className="mt-1 max-w-3xl text-[12.5px] leading-relaxed text-[var(--color-ink-soft)]">
                <strong className="font-medium text-[var(--color-ink)]">Size sep.</strong> is the
                share of a scenario's trades beyond |z| = 3 of that account's own background
                distribution. It measures one feature, so it says nothing about timing or price.
                The rows that matter are the positives expecting the{" "}
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
      </div>
    </TooltipProvider>
  )
}
