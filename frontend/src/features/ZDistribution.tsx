import { motion } from "motion/react"
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from "recharts"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import type { ZHistogram } from "@/lib/api"

/**
 * The chart that carries the project's central claim.
 *
 * Palette: categorical slots 1-3 from the validated reference palette (blue / orange /
 * aqua), verified with the six-check validator in both modes on the all-pairs list. Light
 * mode raises a contrast WARN on aqua (2.74:1), so every series carries a visible direct
 * label -- that is the documented relief, not an optional nicety.
 *
 * Ordinary activity is drawn in de-emphasis gray rather than a fourth hue: it is context,
 * not a peer series, and spending a categorical slot on it would weaken the three that
 * matter.
 */
interface SeriesSpec {
  key: string
  light: string
  dark: string
  label: string
  /** Drawn as recessive context rather than a peer series. */
  context?: boolean
}

const SERIES: SeriesSpec[] = [
  { key: "background", light: "#9aa0aa", dark: "#6f7681", label: "Ordinary activity", context: true },
  { key: "wash_ring", light: "#2a78d6", dark: "#3987e5", label: "Wash rings" },
  { key: "coordinated_cluster", light: "#1baf7a", dark: "#199e70", label: "Coordinated clusters" },
  { key: "size_spike", light: "#eb6834", dark: "#d95926", label: "Size spikes" },
]

type Row = { z: number } & Record<string, number>

export function ZDistribution({
  data,
  loading,
  theme,
}: {
  data: ZHistogram | null
  loading: boolean
  theme: "light" | "dark"
}) {
  const colour = (s: SeriesSpec) => (theme === "dark" ? s.dark : s.light)

  const rows: Row[] =
    data?.bins.map((z, i) => {
      const row = { z } as Row
      for (const s of data.series) row[s.key] = s.counts[i]
      return row
    }) ?? []

  const totals = Object.fromEntries((data?.series ?? []).map((s) => [s.key, s.total]))

  return (
    <Card className="overflow-hidden">
      <CardHeader>
        <CardTitle>Is the anomaly actually hidden?</CardTitle>
        <CardDescription>
          Distribution of every trade's notional measured in standard deviations from{" "}
          <strong className="font-medium text-[var(--color-ink)]">
            the placing account's own
          </strong>{" "}
          trading history. Wash rings sit directly on top of ordinary activity — no per-trade
          size rule can separate them, which is precisely why the graph layer exists. Size
          spikes sit far to the right, where a per-trade rule finds them easily.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-[320px] w-full" />
        ) : (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-2">
              {SERIES.map((s) => (
                <span key={s.key} className="inline-flex items-center gap-2 text-[12.5px]">
                  <span
                    aria-hidden
                    className="inline-block h-[3px] w-5 rounded-full"
                    style={{ background: colour(s) }}
                  />
                  <span className={s.context ? "text-[var(--color-ink-faint)]" : "text-[var(--color-ink-soft)]"}>
                    {s.label}
                  </span>
                  <span className="tnum text-[var(--color-ink-faint)]">
                    n={totals[s.key]?.toLocaleString() ?? "—"}
                  </span>
                </span>
              ))}
            </div>

            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
              style={{ height: 320 }}
            >
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={rows} margin={{ top: 22, right: 16, bottom: 24, left: 4 }}>
                  {/* The region a common |z|>3 rule would flag. Shading it makes the
                      "which series lives here" question answerable at a glance. */}
                  <ReferenceArea
                    x1={3}
                    x2={8}
                    fill="var(--color-warn)"
                    fillOpacity={theme === "dark" ? 0.09 : 0.07}
                  />
                  <CartesianGrid
                    stroke="var(--color-line)"
                    strokeDasharray="2 4"
                    vertical={false}
                  />
                  <XAxis
                    dataKey="z"
                    type="number"
                    domain={[-4, 8]}
                    ticks={[-4, -2, 0, 2, 3, 4, 6, 8]}
                    tickFormatter={(v: number) => (v > 0 ? `+${v}` : `${v}`)}
                    stroke="var(--color-line-strong)"
                    tick={{ fill: "var(--color-ink-faint)", fontSize: 11 }}
                    tickLine={false}
                    label={{
                      value: "z-score vs the account's own notional distribution",
                      position: "insideBottom",
                      offset: -14,
                      style: { fill: "var(--color-ink-faint)", fontSize: 11.5 },
                    }}
                  />
                  <YAxis
                    tickFormatter={(v: number) => `${Math.round(v * 100)}%`}
                    stroke="var(--color-line-strong)"
                    tick={{ fill: "var(--color-ink-faint)", fontSize: 11 }}
                    tickLine={false}
                    axisLine={false}
                    width={44}
                  />
                  <ReferenceLine
                    x={3}
                    stroke="var(--color-warn)"
                    strokeDasharray="4 3"
                    strokeWidth={1.5}
                    label={{
                      value: "|z| = 3",
                      position: "top",
                      style: { fill: "var(--color-warn)", fontSize: 11, fontWeight: 600 },
                    }}
                  />
                  <RTooltip
                    cursor={{ stroke: "var(--color-line-strong)", strokeWidth: 1 }}
                    content={({ active, payload, label }) => {
                      if (!active || !payload?.length) return null
                      return (
                        <div className="rounded-lg border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-2 shadow-[var(--shadow-raised)]">
                          <div className="tnum mb-1.5 text-[11px] font-medium text-[var(--color-ink-faint)]">
                            z = {Number(label) > 0 ? "+" : ""}
                            {Number(label).toFixed(2)}
                          </div>
                          {payload
                            .filter((p) => (p.value as number) > 0)
                            .map((p) => (
                              <div
                                key={p.dataKey as string}
                                className="flex items-center gap-2 text-[12.5px]"
                              >
                                <span
                                  aria-hidden
                                  className="inline-block size-2 rounded-full"
                                  style={{ background: p.color }}
                                />
                                <span className="text-[var(--color-ink-soft)]">
                                  {SERIES.find((s) => s.key === p.dataKey)?.label}
                                </span>
                                <span className="tnum ml-auto font-medium text-[var(--color-ink)]">
                                  {((p.value as number) * 100).toFixed(1)}%
                                </span>
                              </div>
                            ))}
                        </div>
                      )
                    }}
                  />
                  {SERIES.map((s) => (
                    <Line
                      key={s.key}
                      type="monotone"
                      dataKey={s.key}
                      stroke={colour(s)}
                      strokeWidth={s.context ? 1.75 : 2}
                      strokeOpacity={s.context ? 0.55 : 1}
                      dot={false}
                      activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--color-surface)" }}
                      isAnimationActive
                      animationDuration={700}
                      animationEasing="ease-out"
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </motion.div>

            <p className="mt-3 text-[12.5px] leading-relaxed text-[var(--color-ink-soft)]">
              Read the shaded band: it is where a conventional{" "}
              <span className="tnum">|z| &gt; 3</span> rule fires. Size spikes live there.
              Wash rings never enter it.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  )
}
