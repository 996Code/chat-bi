import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig(({ mode }) => {
  const rootDir = resolve(__dirname, '..')
  const env = loadEnv(mode, rootDir, '')
  const basePath = env.VITE_BASE_PATH || '/chat-bi/'
  const apiPrefix = env.VITE_API_PREFIX || '/chat-bi/api/v1'
  const apiBase = apiPrefix.replace(/\/api\/v1$/, '')

  return {
    envDir: rootDir,
    base: basePath,
    plugins: [vue()],
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src'),
      },
    },
    optimizeDeps: {
      include: ['exceljs'],
    },
    server: {
      port: 5173,
      proxy: {
        [apiBase]: {
          target: 'http://127.0.0.1:8999',
          changeOrigin: true,
        },
      },
    },
  }
})
