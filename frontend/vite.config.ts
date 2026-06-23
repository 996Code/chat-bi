import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/chat-bi': {
        target: 'http://localhost:8999',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8999',
        changeOrigin: true,
      },
    },
  },
})
