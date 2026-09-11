import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const button = cva(
  "inline-flex items-center justify-center gap-2 rounded-lg font-medium whitespace-nowrap " +
    "transition-all duration-150 outline-none focus-visible:ring-2 " +
    "focus-visible:ring-[var(--color-brand)]/40 focus-visible:ring-offset-2 " +
    "focus-visible:ring-offset-[var(--color-canvas)] disabled:pointer-events-none " +
    "disabled:opacity-50 active:scale-[0.98] [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-[var(--color-brand)] text-white shadow-[var(--shadow-card)] hover:brightness-110",
        outline:
          "border border-[var(--color-line)] bg-[var(--color-surface)] text-[var(--color-ink)] " +
          "hover:bg-[var(--color-surface-muted)] hover:border-[var(--color-line-strong)]",
        ghost: "text-[var(--color-ink-soft)] hover:bg-[var(--color-surface-muted)] hover:text-[var(--color-ink)]",
      },
      size: {
        sm: "h-8 px-3 text-[13px] [&_svg]:size-3.5",
        md: "h-9 px-4 text-sm [&_svg]:size-4",
        icon: "size-9 [&_svg]:size-4",
      },
    },
    defaultVariants: { variant: "outline", size: "md" },
  },
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof button> {
  asChild?: boolean
}

export function Button({ className, variant, size, asChild, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : "button"
  return <Comp className={cn(button({ variant, size }), className)} {...props} />
}
