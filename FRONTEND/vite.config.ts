import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/** 开发/预览期统一把 /api 转发到 Java 网关（8080），避免 CORS 与跨域 cookie 问题。
 *  生产部署时由 Nginx 承担同样的转发职责。 */
const apiProxy = {
  '/api': {
    target: 'http://127.0.0.1:8080',
    changeOrigin: true,
  },
}

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: '127.0.0.1',
    proxy: apiProxy,
  },
  // `vite preview`（伺服 dist/ 生产构建）**不会**继承 server.proxy，必须单独配置。
  // 否则预览模式下所有 /api 请求都 404，页面提示「无法连接后端服务」——
  // 这与「后端真的没启动」的表现完全一样，极易误判为后端故障。
  preview: {
    port: 5173,
    host: '127.0.0.1',
    proxy: apiProxy,
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
