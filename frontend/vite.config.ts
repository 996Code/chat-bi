import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  base: '/chat-bi/',
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
      '/chat-bi/api': {
        target: 'http://127.0.0.1:8999',
        changeOrigin: true,
      },
    },
  },
})
