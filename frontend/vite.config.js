import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { parse } from 'smol-toml'

// Ports come from ../config.toml (single source of truth). Proxy targets 127.0.0.1, never localhost.
const cfg = parse(readFileSync(fileURLToPath(new URL('../config.toml', import.meta.url)), 'utf8'))
const { backend_host, frontend_port } = cfg.server
// GC_BACKEND_PORT lets eval/ui_runner.py point a preview at its own backend on a free port.
const backend_port = Number(process.env.GC_BACKEND_PORT) || cfg.server.backend_port

const proxy = { '/api': { target: `http://${backend_host}:${backend_port}`, changeOrigin: true } }

export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: frontend_port, strictPort: true, proxy },
  // `vite preview` serves the production build (P6 timing checks) with the same /api proxy.
  preview: { host: '127.0.0.1', proxy },
})
