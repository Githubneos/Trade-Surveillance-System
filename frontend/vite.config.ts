import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  // The FastAPI explorer owns /api; in dev Vite proxies to it so the frontend runs on
  // one origin with no CORS handling. In production FastAPI serves the built bundle.
  server: {
    port: 5173,
    proxy: {
      // The dataset explorer runs as a separate app on 8011 because it reads ground-truth
      // labels; the serving API on 8000 must not. Distinct path prefixes keep that split
      // unambiguous -- /api/stats meaning two different things depending on which port
      // answered is precisely how the Dataset view silently broke.
      "/explorer": { target: "http://127.0.0.1:8011", changeOrigin: true },
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
})
