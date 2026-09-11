import * as D from "@radix-ui/react-dialog"
import { X } from "lucide-react"
import { cn } from "@/lib/utils"

export const Sheet = D.Root
export const SheetTrigger = D.Trigger

/**
 * A right-hand detail panel. Radix handles focus trapping, escape-to-close, scroll lock
 * and the aria wiring; we only supply the surface and the motion.
 */
export function SheetContent({
  className,
  children,
  title,
  description,
}: {
  className?: string
  children: React.ReactNode
  title: React.ReactNode
  description?: React.ReactNode
}) {
  return (
    <D.Portal>
      <D.Overlay className="anim-overlay fixed inset-0 z-40 bg-black/35 backdrop-blur-[2px]" />
      <D.Content
        className={cn(
          "anim-sheet fixed top-0 right-0 z-50 flex h-dvh w-full flex-col",
          "border-l border-[var(--color-line)] bg-[var(--color-surface)]",
          "shadow-[var(--shadow-overlay)] sm:max-w-5xl",
          className,
        )}
      >
        <div className="flex shrink-0 items-start justify-between gap-6 border-b border-[var(--color-line)] px-6 py-5">
          <div className="min-w-0">
            <D.Title asChild>{title}</D.Title>
            {description ? (
              <D.Description asChild>{description}</D.Description>
            ) : (
              <D.Description className="sr-only">Scenario detail</D.Description>
            )}
          </div>
          <D.Close
            className={cn(
              "shrink-0 rounded-lg border border-[var(--color-line)] p-2 text-[var(--color-ink-faint)]",
              "transition-colors hover:bg-[var(--color-surface-muted)] hover:text-[var(--color-ink)]",
              "outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand)]/40",
            )}
            aria-label="Close"
          >
            <X className="size-4" />
          </D.Close>
        </div>
        <div className="scrollbar-slim flex-1 overflow-y-auto px-6 py-5">{children}</div>
      </D.Content>
    </D.Portal>
  )
}
