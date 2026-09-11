import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const badge = cva(
  "inline-flex items-center gap-1.5 rounded-full border font-medium whitespace-nowrap transition-colors",
  {
    variants: {
      tone: {
        neutral: "border-[var(--color-line)] bg-[var(--color-surface-muted)] text-[var(--color-ink-soft)]",
        brand: "border-transparent bg-[var(--color-brand-soft)] text-[var(--color-brand-ink)]",
        danger: "border-transparent bg-[var(--color-danger-soft)] text-[var(--color-danger)]",
        success: "border-transparent bg-[var(--color-success-soft)] text-[var(--color-success)]",
        warn: "border-transparent bg-[var(--color-warn-soft)] text-[var(--color-warn)]",
      },
      size: {
        sm: "px-2 py-0.5 text-[11px]",
        md: "px-2.5 py-1 text-xs",
      },
    },
    defaultVariants: { tone: "neutral", size: "sm" },
  },
)

export type BadgeProps = React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badge>

export function Badge({ className, tone, size, ...props }: BadgeProps) {
  return <span className={cn(badge({ tone, size }), className)} {...props} />
}
