/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

const api = process.env.NETWATCH_API ?? 'http://127.0.0.1:8085';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': api,
      '/health': api,
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
