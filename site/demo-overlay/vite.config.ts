import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  plugins: [react()],
  base: './',
  publicDir: 'public',
  esbuild: { charset: 'ascii' },
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false },
});
