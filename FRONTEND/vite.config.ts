import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: '127.0.0.1',
    proxy: {
      // 开发期把 /api 代理到 Java 网关（8080），避免 CORS 与跨域 cookie 问题。
      // 生产部署时由 Nginx 承担同样的转发职责。
      '/api': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks: {
          // ECharts 体积较大，单独分包，避免首屏加载被拖慢
          echarts: ['echarts'],
          react: ['react', 'react-dom', 'react-router-dom'],
        },
      },
    },
  },
})
