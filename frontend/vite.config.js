import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * The dev server proxies `/api` to the FastAPI backend so the browser talks to
 * a single origin and CORS never becomes a development problem. The backend
 * also allows http://localhost:5173 explicitly, for the case where the UI is
 * served without this proxy.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: true },
})
