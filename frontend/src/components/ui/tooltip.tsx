import * as T from "@radix-ui/react-tooltip"
import { cn } from "@/lib/utils"

export const TooltipProvider = T.Provider

export function Tooltip({
  content,
  children,
  side = "top",
  className,
}: {
  content: React.ReactNode
  children: React.ReactNode
  side?: "top" | "right" | "bottom" | "left"
  className?: string
}) {
  if (!content) return <>{children}</>
  return (
    <T.Root delayDuration={150}>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content
          side={side}
          sideOffset={6}
          className={cn(
            "z-50 max-w-xs rounded-lg border border-[var(--color-line)] px-3 py-2",
            "bg-[var(--color-surface)] text-[12.5px] leading-relaxed text-[var(--color-ink-soft)]",
            "shadow-[var(--shadow-raised)] anim-pop",
            className,
          )}
        >
          {content}
        </T.Content>
      </T.Portal>
    </T.Root>
  )
}
