import { motion } from "motion/react"
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react"
import { useMemo, useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Tooltip } from "@/components/ui/tooltip"
import { fmtInt, fmtPct, fmtZ, layerLabel, typeLabel, verdict, type ScenarioRow } from "@/lib/api"
import { cn } from "@/lib/utils"

type SortKey = keyof Pick<
  ScenarioRow,
  "case_ref" | "title" | "subtype" | "label" | "expected_layer" | "n_accounts" | "n_trades" | "median_z" | "size_sep" | "difficulty"
>

const FILTERS = [
  { id: "all", label: "All" },
  { id: "positive", label: "Confirmed abuse" },
  { id: "hard_negative", label: "Benign look-alikes" },
  { id: "graph", label: "Network detection" },
  { id: "statistical", label: "Per-trade detection" },
] as const

const TONE = { good: "success", bad: "danger", warn: "warn" } as const

export function ScenarioTable({
  rows,
  onSelect,
}: {
  rows: ScenarioRow[]
  onSelect: (id: string) => void
}) {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["id"]>("all")
  const [sort, setSort] = useState<{ key: SortKey; asc: boolean }>({
    key: "label",
    asc: true,
  })

  const view = useMemo(() => {
    const filtered = rows.filter((r) => {
      if (filter === "all") return true
      if (filter === "positive" || filter === "hard_negative") return r.label === filter
      return r.expected_layer === filter
    })
    return [...filtered].sort((a, b) => {
      const x = a[sort.key] ?? -Infinity
      const y = b[sort.key] ?? -Infinity
      const cmp = x > y ? 1 : x < y ? -1 : 0
      return sort.asc ? cmp : -cmp
    })
  }, [rows, filter, sort])

  const toggle = (key: SortKey) =>
    setSort((s) => ({ key, asc: s.key === key ? !s.asc : true }))

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
          {view.length} of {rows.length}
        </span>
      </div>

      <div className="scrollbar-slim overflow-x-auto">
        <table className="w-full min-w-[1180px] border-collapse text-[13px]">
          <thead>
            <tr className="bg-[var(--color-surface-muted)]">
              <Th onClick={() => toggle("label")} sort={sort} k="label">Label</Th>
              <Th onClick={() => toggle("case_ref")} sort={sort} k="case_ref">Case</Th>
              <Th onClick={() => toggle("title")} sort={sort} k="title">Summary</Th>
              <Th onClick={() => toggle("subtype")} sort={sort} k="subtype">Type</Th>
              <Th onClick={() => toggle("expected_layer")} sort={sort} k="expected_layer">
                Caught by
              </Th>
              <Th onClick={() => toggle("n_accounts")} sort={sort} k="n_accounts" num>Accts</Th>
              <Th onClick={() => toggle("n_trades")} sort={sort} k="n_trades" num>Trades</Th>
              <Th onClick={() => toggle("median_z")} sort={sort} k="median_z" num>Median z</Th>
              <Th onClick={() => toggle("size_sep")} sort={sort} k="size_sep" num>Size sep.</Th>
              <th className="px-4 py-2.5 text-left text-[11px] font-semibold tracking-wide text-[var(--color-ink-faint)] uppercase">
                Verdict
              </th>
            </tr>
          </thead>
          {/*
            Animated as a single body keyed on the active filter+sort, NOT per row.
            Framer Motion's `layout` (FLIP) prop transforms elements, and transforms on
            <tr> fight the browser's table layout algorithm -- rows end up stranded at
            opacity 0. Fading the whole body sidesteps that entirely and reads better
            anyway: the list is replaced wholesale, so it should move as one object.
          */}
          <motion.tbody
            key={`${filter}-${sort.key}-${sort.asc}`}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
          >
            {view.map((r) => {
              const v = verdict(r)
              return (
                <tr
                  key={r.scenario_id}
                  onClick={() => onSelect(r.scenario_id)}
                  tabIndex={0}
                  onKeyDown={(e) => e.key === "Enter" && onSelect(r.scenario_id)}
                  className={cn(
                    "cursor-pointer border-t border-[var(--color-line)] transition-colors",
                    "hover:bg-[var(--color-surface-muted)]",
                    "focus-visible:bg-[var(--color-brand-soft)] focus-visible:outline-none",
                  )}
                >
                  <td className="px-4 py-2.5">
                    <Badge tone={r.label === "positive" ? "danger" : "brand"}>
                      {r.label === "positive" ? "Confirmed" : "Benign"}
                    </Badge>
                  </td>
                  <td className="px-4 py-2.5 font-mono text-[12px] whitespace-nowrap text-[var(--color-ink-faint)]">
                    {r.case_ref}
                  </td>
                  <td className="max-w-[24rem] px-4 py-2.5 font-medium">{r.title}</td>
                  <td className="px-4 py-2.5 whitespace-nowrap text-[var(--color-ink-soft)]">
                    {typeLabel(r.subtype)}
                  </td>
                  <td className="px-4 py-2.5 whitespace-nowrap">
                    <span className="text-[var(--color-ink-faint)]">
                      {layerLabel(r.expected_layer)}
                    </span>
                  </td>
                  <td className="tnum px-4 py-2.5 text-right">{r.n_accounts}</td>
                  <td className="tnum px-4 py-2.5 text-right">{fmtInt(r.n_trades)}</td>
                  <td className="tnum px-4 py-2.5 text-right">{fmtZ(r.median_z)}</td>
                  <td
                    className={cn(
                      "tnum px-4 py-2.5 text-right",
                      (r.size_sep ?? 0) > 0.5 && "font-semibold text-[var(--color-warn)]",
                    )}
                  >
                    {fmtPct(r.size_sep)}
                  </td>
                  <td className="px-4 py-2.5">
                    <Tooltip content={v.detail}>
                      <span>
                        <Badge tone={TONE[v.tone]}>{v.text}</Badge>
                      </span>
                    </Tooltip>
                  </td>
                </tr>
              )
            })}
          </motion.tbody>
        </table>
      </div>
    </Card>
  )
}

function Th({
  children,
  onClick,
  sort,
  k,
  num,
}: {
  children: React.ReactNode
  onClick: () => void
  sort: { key: SortKey; asc: boolean }
  k: SortKey
  num?: boolean
}) {
  const active = sort.key === k
  const Icon = !active ? ChevronsUpDown : sort.asc ? ArrowUp : ArrowDown
  return (
    <th
      onClick={onClick}
      className={cn(
        "cursor-pointer px-4 py-2.5 text-[11px] font-semibold tracking-wide uppercase select-none",
        "transition-colors hover:text-[var(--color-ink)]",
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
