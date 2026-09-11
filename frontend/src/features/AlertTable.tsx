import { motion } from "motion/react"
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react"
import { useMemo, useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Tooltip } from "@/components/ui/tooltip"
import {
  SEVERITY_ORDER,
  SEVERITY_TONE,
  alertTypeLabel,
  methodLabel,
  type Alert,
  type Severity,
} from "@/lib/alerts"
import { fmtInt } from "@/lib/api"
import { cn } from "@/lib/utils"

type SortKey = "severity" | "score" | "alert_type" | "n_accounts" | "n_trades" | "created_at"

const FILTERS = [
  { id: "all", label: "All" },
  { id: "critical", label: "Critical" },
  { id: "high", label: "High" },
  { id: "graph", label: "Network" },
  { id: "statistical", label: "Per-trade" },
  { id: "open", label: "Open" },
] as const

export function AlertTable({
  alerts,
  onSelect,
  selectedId,
}: {
  alerts: Alert[]
  onSelect: (id: number) => void
  selectedId?: number | null
}) {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["id"]>("all")
  const [sort, setSort] = useState<{ key: SortKey; asc: boolean }>({
    key: "score",
    asc: false,
  })

  const view = useMemo(() => {
    const filtered = alerts.filter((a) => {
      switch (filter) {
        case "all":
          return true
        case "critical":
        case "high":
          return a.severity === filter
        case "graph":
        case "statistical":
          return a.detection_method.includes(filter)
        case "open":
          return a.status === "open"
      }
    })
    return [...filtered].sort((a, b) => {
      const pick = (x: Alert) =>
        sort.key === "severity" ? SEVERITY_ORDER[x.severity] : (x[sort.key] as number | string)
      const l = pick(a)
      const r = pick(b)
      const cmp = l > r ? 1 : l < r ? -1 : 0
      return sort.asc ? cmp : -cmp
    })
  }, [alerts, filter, sort])

  const toggle = (key: SortKey) =>
    setSort((s) => ({ key, asc: s.key === key ? !s.asc : false }))

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--color-line)] px-4 py-3">
        {FILTERS.map((f) => (
          <Button
            key={f.id}
            size="sm"
            variant={filter === f.id ? "primary" : "ghost"}
            onClick={() => setFilter(f.id)}
          >
            {f.label}
          </Button>
        ))}
        <span className="tnum ml-auto text-[12.5px] text-[var(--color-ink-faint)]">
          {view.length} of {alerts.length}
        </span>
      </div>

      <div className="scrollbar-slim max-h-[62vh] overflow-auto">
        <table className="w-full min-w-[1080px] border-collapse text-[13px]">
          <thead className="sticky top-0 z-10">
            <tr className="bg-[var(--color-surface-muted)]">
              <Th k="severity" sort={sort} onClick={toggle}>Severity</Th>
              <Th k="alert_type" sort={sort} onClick={toggle}>Type</Th>
              <th className="px-4 py-2.5 text-left text-[11px] font-semibold tracking-wide text-[var(--color-ink-faint)] uppercase">
                Subject
              </th>
              <th className="px-4 py-2.5 text-left text-[11px] font-semibold tracking-wide text-[var(--color-ink-faint)] uppercase">
                Found by
              </th>
              <Th k="n_accounts" sort={sort} onClick={toggle} num>Accts</Th>
              <Th k="n_trades" sort={sort} onClick={toggle} num>Trades</Th>
              <Th k="score" sort={sort} onClick={toggle} num>Score</Th>
              <th className="px-4 py-2.5 text-left text-[11px] font-semibold tracking-wide text-[var(--color-ink-faint)] uppercase">
                Status
              </th>
            </tr>
          </thead>
          <motion.tbody
            key={`${filter}-${sort.key}-${sort.asc}`}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
          >
            {view.map((a) => (
              <tr
                key={a.id}
                onClick={() => onSelect(a.id)}
                tabIndex={0}
                onKeyDown={(e) => e.key === "Enter" && onSelect(a.id)}
                className={cn(
                  "cursor-pointer border-t border-[var(--color-line)] transition-colors",
                  "hover:bg-[var(--color-surface-muted)] focus-visible:outline-none",
                  selectedId === a.id && "bg-[var(--color-brand-soft)]/60",
                )}
              >
                <td className="px-4 py-2.5">
                  <Badge tone={SEVERITY_TONE[a.severity as Severity]}>{a.severity}</Badge>
                </td>
                <td className="px-4 py-2.5 whitespace-nowrap">{alertTypeLabel(a.alert_type)}</td>
                <td className="max-w-[22rem] truncate px-4 py-2.5">
                  {a.tickers.length > 0 && (
                    <span className="font-mono font-medium">{a.tickers.slice(0, 2).join(", ")}</span>
                  )}
                  <span className="text-[var(--color-ink-soft)]">
                    {a.accounts.length === 1
                      ? ` · ${a.accounts[0]}`
                      : a.accounts.length > 1
                        ? ` · ${a.accounts.length} accounts`
                        : ""}
                  </span>
                </td>
                <td className="px-4 py-2.5 whitespace-nowrap">
                  <span className="flex gap-1">
                    {a.detection_method.map((m) => (
                      <Badge key={m} tone={m === "graph" ? "brand" : "neutral"}>
                        {methodLabel(m)}
                      </Badge>
                    ))}
                  </span>
                </td>
                <td className="tnum px-4 py-2.5 text-right">{a.n_accounts}</td>
                <td className="tnum px-4 py-2.5 text-right">{fmtInt(a.n_trades)}</td>
                <td className="tnum px-4 py-2.5 text-right font-medium">{a.score.toFixed(2)}</td>
                <td className="px-4 py-2.5">
                  <Tooltip content={a.summary}>
                    <span>
                      <Badge
                        tone={
                          a.status === "escalated"
                            ? "danger"
                            : a.status === "cleared"
                              ? "success"
                              : "neutral"
                        }
                      >
                        {a.status}
                      </Badge>
                    </span>
                  </Tooltip>
                </td>
              </tr>
            ))}
          </motion.tbody>
        </table>
        {view.length === 0 && (
          <p className="px-4 py-10 text-center text-[13px] text-[var(--color-ink-faint)]">
            No alerts match this filter.
          </p>
        )}
      </div>
    </Card>
  )
}

function Th({
  children,
  k,
  sort,
  onClick,
  num,
}: {
  children: React.ReactNode
  k: SortKey
  sort: { key: SortKey; asc: boolean }
  onClick: (k: SortKey) => void
  num?: boolean
}) {
  const active = sort.key === k
  const Icon = !active ? ChevronsUpDown : sort.asc ? ArrowUp : ArrowDown
  return (
    <th
      onClick={() => onClick(k)}
      className={cn(
        "cursor-pointer px-4 py-2.5 text-[11px] font-semibold tracking-wide uppercase select-none",
        "bg-[var(--color-surface-muted)] transition-colors hover:text-[var(--color-ink)]",
        active ? "text-[var(--color-ink)]" : "text-[var(--color-ink-faint)]",
        num ? "text-right" : "text-left",
      )}
    >
      <span className={cn("inline-flex items-center gap-1", num && "flex-row-reverse")}>
        {children}
        <Icon className={cn("size-3", !active && "opacity-40")} />
      </span>
    </th>
  )
}
