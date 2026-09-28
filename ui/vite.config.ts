import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 开发期：vite dev 5173 → 后端 8792（SSE 不缓冲）
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8792',
        changeOrigin: true,
        // SSE 支持：禁用代理缓冲
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
              proxyRes.headers['cache-control'] = 'no-cache'
              proxyRes.headers['x-accel-buffering'] = 'no'
            }
          })
        },
      },
    },
  },
})
