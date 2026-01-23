import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => ({
  plugins: [react({
    jsxRuntime: 'automatic',
  })],
  server: {
    port: 5173,
    // Proxy API requests to Arena Server in development
    proxy: {
      '/api': {
        target: process.env.VITE_API_URL || 'http://localhost:3001',
        changeOrigin: true,
      },
      '/ws': {
        target: process.env.VITE_API_URL || 'http://localhost:3001',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  define: {
    // Make env vars available at build time
    __USE_MOCK_DATA__: JSON.stringify(process.env.VITE_USE_MOCK_DATA !== 'false'),
  },
}))
