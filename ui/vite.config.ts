import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  // 打包后由 FastAPI 同源托管，用相对路径省掉"部署在子路径"时的麻烦。
  base: './',
  server: {
    port: 5173,
    proxy: {
      // 开发时把 /api 转给后端，前端代码里的路径与生产完全一致。
      '/api': { target: 'http://127.0.0.1:8848', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
