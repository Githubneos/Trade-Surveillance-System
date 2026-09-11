import { useEffect, useState } from "react"

export type Theme = "light" | "dark"
const KEY = "surv-theme"

function initial(): Theme {
  try {
    const saved = localStorage.getItem(KEY)
    if (saved === "light" || saved === "dark") return saved
  } catch {
    /* private browsing / blocked storage — fall through to the OS preference */
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
}

/** Read-only view of the active theme, for components that only need to pick colours. */
export function useThemeMode(): Theme {
  const [theme, setTheme] = useState<Theme>(() =>
    document.documentElement.classList.contains("dark") ? "dark" : "light",
  )
  useEffect(() => {
    const observer = new MutationObserver(() =>
      setTheme(document.documentElement.classList.contains("dark") ? "dark" : "light"),
    )
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] })
    return () => observer.disconnect()
  }, [])
  return theme
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(initial)

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark")
    try {
      localStorage.setItem(KEY, theme)
    } catch {
      /* non-fatal: the theme still applies for this session */
    }
  }, [theme])

  return { theme, toggle: () => setTheme((t) => (t === "light" ? "dark" : "light")) }
}
