import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { readFileSync } from 'node:fs'
const version = readFileSync(new URL('../core/src/tobili/__init__.py', import.meta.url), 'utf8').match(/__version__ = "([^"]+)"/)![1]

export default defineConfig({
  plugins: [vue(), {name:'release-version', generateBundle(){this.emitFile({type:'asset',fileName:'version.json',source:JSON.stringify({version})})}}],
  define: { __APP_VERSION__: JSON.stringify(version) },
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
