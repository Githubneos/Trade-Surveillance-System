import { animate, useMotionValue, useTransform, motion } from "motion/react"
import { useEffect } from "react"
import { Card } from "@/components/ui/card"
import { Tooltip } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

/**
 * A KPI tile. The number counts up on mount because a dashboard that resolves into place
 * reads as live; the motion is decorative only and is disabled under prefers-reduced-motion
 * via the global CSS rule.
 */
export function StatCard({
  label,
  value,
  format,
  caption,
  hint,
  tone = "neutral",
  delay = 0,
}: {
  label: string
  value: number
  format: (n: number) => string
  caption?: string
  hint?: string
  tone?: "neutral" | "good" | "bad"
  delay?: number
}) {
  const mv = useMotionValue(0)
  const text = useTransform(mv, (v) => format(v))

  useEffect(() => {
    const controls = animate(mv, value, {
      duration: 0.9,
      delay,
      ease: [0.22, 1, 0.36, 1],
    })
    return () => controls.stop()
  }, [mv, value, delay])

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay, ease: [0.22, 1, 0.36, 1] }}
    >
      <Card className="group h-full p-4 transition-shadow duration-200 hover:shadow-[var(--shadow-raised)]">
        <Tooltip content={hint}>
          <div className="cursor-default">
            <div className="text-[11px] font-medium tracking-wide text-[var(--color-ink-faint)] uppercase">
              {label}
            </div>
            <motion.div
              className={cn(
                "tnum mt-1.5 text-2xl font-semibold tracking-tight",
                tone === "good" && "text-[var(--color-success)]",
                tone === "bad" && "text-[var(--color-danger)]",
              )}
            >
              {text}
            </motion.div>
            {caption && (
              <div className="mt-1 text-[12px] leading-snug text-[var(--color-ink-soft)]">
                {caption}
              </div>
            )}
          </div>
        </Tooltip>
      </Card>
    </motion.div>
  )
}
