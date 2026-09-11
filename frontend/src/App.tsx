import { AnimatePresence, motion } from "motion/react"
import { Activity, Database, Moon, Radio, ShieldAlert, Sun } from "lucide-react"
import { useCallback, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Tooltip, TooltipProvider } from "@/components/ui/tooltip"
import { AlertSheet } from "@/features/AlertSheet"
import { AlertTable } from "@/features/AlertTable"
import { ExplorerView } from "@/features/ExplorerView"
import { StatCard } from "@/features/StatCard"
import { alertsApi, type Alert, type AlertDetail, type AlertStatus } from "@/lib/alerts"
import { fmtInt, fmtPct } from "@/lib/api"
import { useTheme } from "@/lib/theme"
import { useLiveAlerts } from "@/lib/useLiveAlerts"
import { cn } from "@/lib/utils"

type View = "alerts" | "dataset"

export default function App() {
  const { theme, toggle } = useTheme()
  const [view, setView] = useState<View>("alerts")

  const [alerts, setAlerts] = useState<Alert[] | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [stats, setStats] = useState<Awaited<ReturnType<typeof alertsApi.stats>> | null>(null)

  const [detail, setDetail] = useState<AlertDetail | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [sheetOpen, setSheetOpen] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)

  const refresh = useCallback(() => {
    Promise.all([alertsApi.list(), alertsApi.stats()])
      .then(([a, s]) => {
        setAlerts(a)
        setStats(s)
        setError(null)
      })
      .catch(setError)
  }, [])

  useEffect(refresh, [refresh])

  // A live event means the server's view has moved on; refetch rather than trying to
  // reconcile a partial payload into local state. At this volume the refetch is cheap and
  // the alternative is two sources of truth that drift.
  const live = useLiveAlerts(refresh)

  const select = useCallback((id: number) => {
    setSelectedId(id)
    setSheetOpen(true)
    setDetailLoading(true)
    setDetail(null)
    alertsApi
      .detail(id)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setDetailLoading(false))
  }, [])

  const setStatus = useCallback(
    (id: number, status: AlertStatus) => {
      alertsApi
        .setStatus(id, status)
        .then(() => alertsApi.detail(id))
        .then(setDetail)
        .then(refresh)
        .catch(setError)
    },
    [refresh],
  )

  const bySeverity = stats?.by_severity ?? {}
  const graphAlerts = stats?.by_method?.graph ?? 0

  return (
    <TooltipProvider>
      <div className="min-h-dvh bg-[var(--color-canvas)]">
        <header className="sticky top-0 z-30 border-b border-[var(--color-line)] bg-[var(--color-surface)]/85 backdrop-blur-xl">
          <div className="mx-auto flex max-w-[1500px] items-center gap-4 px-6 py-3.5">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-[var(--color-brand)] text-white shadow-[var(--shadow-card)]">
              <ShieldAlert className="size-[18px]" />
            </div>
            <div className="min-w-0">
              <h1 className="text-[15px] leading-tight font-semibold tracking-tight">
                Trade Surveillance
              </h1>
              <p className="truncate text-[12.5px] text-[var(--color-ink-soft)]">
                Market abuse detection
              </p>
            </div>

            <nav className="ml-6 flex items-center gap-1 rounded-lg bg-[var(--color-surface-muted)] p-1">
              {(
                [
                  ["alerts", "Alerts"],
                  ["dataset", "Dataset"],
                ] as const
              ).map(([id, label]) => (
                <button
                  key={id}
                  onClick={() => setView(id)}
                  className={cn(
                    "relative rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors",
                    view === id
                      ? "text-[var(--color-ink)]"
                      : "text-[var(--color-ink-faint)] hover:text-[var(--color-ink-soft)]",
                  )}
                >
                  {view === id && (
                    <motion.span
                      layoutId="nav-pill"
                      className="absolute inset-0 rounded-md bg-[var(--color-surface)] shadow-[var(--shadow-card)]"
                      transition={{ type: "spring", stiffness: 400, damping: 32 }}
                    />
                  )}
                  <span className="relative">{label}</span>
                </button>
              ))}
            </nav>

            <div className="ml-auto flex items-center gap-2">
              <Tooltip
                content={
                  live.connected
                    ? `Live feed connected. ${live.events} update${live.events === 1 ? "" : "s"} received.`
                    : "Live feed disconnected — reconnecting. The table may be stale."
                }
              >
                <span
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12px]",
                    live.connected
                      ? "border-[var(--color-success)]/30 text-[var(--color-success)]"
                      : "border-[var(--color-warn)]/30 text-[var(--color-warn)]",
                  )}
                >
                  <Radio className={cn("size-3.5", live.connected && "animate-pulse")} />
                  {live.connected ? "Live" : "Reconnecting"}
                </span>
              </Tooltip>
              <span className="hidden items-center gap-1.5 rounded-full border border-[var(--color-line)] px-3 py-1.5 text-[12px] text-[var(--color-ink-soft)] lg:inline-flex">
                <Database className="size-3.5" />
                <span className="tnum">{fmtInt(stats?.trades ?? null)}</span> trades
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

        <main className="mx-auto max-w-[1500px] px-6 pt-7 pb-20">
          {error && (
            <Card className="mb-6 border-[var(--color-danger)]/30 bg-[var(--color-danger-soft)]/50 p-4 text-[13px]">
              <strong className="text-[var(--color-danger)]">Cannot reach the API.</strong>{" "}
              <span className="text-[var(--color-ink-soft)]">
                Start it with{" "}
                <code className="font-mono text-[12px]">python -m surveillance.cli serve</code>. (
                {error.message})
              </span>
            </Card>
          )}

          {view === "alerts" ? (
            <>
              <section className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
                {[
                  {
                    label: "Open alerts",
                    value: stats?.open_alerts ?? 0,
                    format: fmtInt,
                    caption: `${fmtInt(stats?.alerts ?? 0)} total`,
                    hint: "Alerts awaiting analyst review.",
                  },
                  {
                    label: "Critical",
                    value: bySeverity.critical ?? 0,
                    format: fmtInt,
                    caption: `${bySeverity.high ?? 0} high`,
                    tone: "bad" as const,
                    hint: "Severity is ranked within each rule, so the bands spread rather than collapsing onto one value.",
                  },
                  {
                    label: "Network alerts",
                    value: graphAlerts,
                    format: fmtInt,
                    caption: "found by the graph layer",
                    hint: "Wash rings and coordinated clusters. The per-trade layer catches none of these — every trade in them is ordinary for the account that placed it.",
                  },
                  {
                    label: "Alert rate",
                    value: stats ? stats.alerts / Math.max(stats.trades, 1) : 0,
                    format: (n: number) => fmtPct(n, 3),
                    caption: "of all trades",
                    hint: "A compliance team's real constraint is total alert volume, not precision.",
                  },
                  {
                    label: "Accounts",
                    value: stats?.accounts ?? 0,
                    format: fmtInt,
                    caption: `${stats?.securities ?? 0} securities`,
                  },
                  {
                    label: "Trades monitored",
                    value: stats?.trades ?? 0,
                    format: fmtInt,
                    caption: stats?.window ? `${stats.window[0]} → ${stats.window[1]}` : "",
                  },
                ].map((c, i) => (
                  <StatCard key={c.label} {...c} delay={i * 0.05} />
                ))}
              </section>

              <div className="mb-3 flex items-baseline gap-3">
                <h2 className="text-[15px] font-semibold tracking-tight">Alert queue</h2>
                <p className="text-[12.5px] text-[var(--color-ink-soft)]">
                  Sorted by score. <strong className="font-medium">Found by</strong> shows which
                  detection layer fired — network alerts are invisible to per-trade rules.
                </p>
              </div>

              {alerts === null ? (
                <Card className="p-10 text-center text-[13px] text-[var(--color-ink-faint)]">
                  <Activity className="mx-auto mb-2 size-5 animate-pulse" />
                  Loading alerts…
                </Card>
              ) : (
                <AlertTable alerts={alerts} onSelect={select} selectedId={selectedId} />
              )}
            </>
          ) : (
            <ExplorerView />
          )}
        </main>

        <AlertSheet
          detail={detail}
          loading={detailLoading}
          open={sheetOpen}
          onOpenChange={setSheetOpen}
          onStatus={setStatus}
        />
      </div>
    </TooltipProvider>
  )
}
