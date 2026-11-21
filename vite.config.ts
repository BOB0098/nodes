import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// base './' 使产物可用相对路径引用，兼容 GitHub Pages 子路径部署。
export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
})
