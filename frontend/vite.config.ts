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
      // The dataset explorer runs as a separate app on 8001 because it reads ground-truth
      // labels; the serving API on 8000 must not. Two proxy targets keeps that split
      // visible in development instead of blurring it behind one origin.
      "/api/scenarios": { target: "http://127.0.0.1:8001", changeOrigin: true },
      "/api/z-histogram": { target: "http://127.0.0.1:8001", changeOrigin: true },
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
})
