import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The browser calls /api; Vite forwards it to the local Python backend.
// No API key is sent to or stored in the browser.
const proxy = {
  '/api': {
    target: process.env.COMPANYMIND_API_TARGET || 'http://127.0.0.1:8000',
    changeOrigin: true,
    rewrite: path => path.replace(/^\/api/, ''),
  },
}

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, proxy },
  preview: { port: 4173, strictPort: true, proxy },
})

