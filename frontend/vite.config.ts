import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
  plugins: [react(), tailwindcss()],

  // 生产构建的产物挂在 FastAPI 的 /static 下，因此需要 base='/static/'；
  // 但开发服务器必须用 base='/'，否则应用会被挂到 http://localhost:5173/static/ 下，
  // 前端路由（/task/:id 等）在开发模式下无法直接访问。
  base: command === 'build' ? '/static/' : '/',

  server: {
    port: 5173,
    // 允许从局域网访问（手机实机联调时需要）
    host: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/solutions': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/uploads': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },

  build: {
    outDir: '../webapp/static',
    emptyOutDir: true,
  },
}))
