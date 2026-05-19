import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 4388,
    proxy: {
      '/api': {
        target: 'http://localhost:8000', // Engine
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/identity': {
        target: 'http://localhost:8004', // Identity
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/identity/, ''),
      }
    }
  }
})