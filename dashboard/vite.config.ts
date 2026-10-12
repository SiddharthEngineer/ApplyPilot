/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Built into the Python package, so `strelitzia serve` (and pip installs) need no Node.
export default defineConfig({
  base: '/app/',
  plugins: [react()],
  build: {
    outDir: '../src/strelitzia/web/static',
    emptyOutDir: true,
  },
  server: {
    proxy: { '/app/api': 'http://127.0.0.1:8765' },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
  },
});
