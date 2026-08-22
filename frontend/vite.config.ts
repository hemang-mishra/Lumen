import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath } from 'node:url';

/**
 * How the app is built and how it is served while being worked on.
 *
 * The dev server runs on its own port and talks to the Python service on
 * another one, which is the arrangement production uses too: static files in
 * one place, the API in another, nothing in between. Proxying the API through
 * here would hide the cross-origin setup until deployment day.
 */
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: { port: 5173, strictPort: true },
  build: { outDir: 'dist', sourcemap: true },
});
