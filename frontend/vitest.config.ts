import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

// Dates are formatted in whatever zone the machine is in, which is right in
// the product and useless in a test — an assertion about "11 Jun" would pass
// in London and fail in Delhi. The test run is pinned so the only thing being
// checked is the formatting.
process.env.TZ = 'UTC';

/**
 * The unit test run, and what it insists on being covered.
 *
 * The bar is per-directory rather than one number for the whole project. A
 * single project-wide percentage lets a thoroughly tested component file hide
 * an untested API client, and the API client is the half where a mistake is
 * silent and expensive.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    // Only the unit tests. The journeys in e2e/ are driven by a real browser
    // and are run by their own command.
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./src/test/setup.ts'],
    css: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.test.{ts,tsx}',
        'src/api/schema.d.ts',
        'src/api/sockets.d.ts',
        'src/test/**',
        'src/main.tsx',
        // Barrels re-export and hold no logic of their own.
        'src/**/index.ts',
      ],
      thresholds: {
        'src/api/**': { lines: 90, branches: 90, functions: 90, statements: 90 },
        'src/lib/**': { lines: 90, branches: 90, functions: 90, statements: 90 },
        'src/theme/**': { lines: 90, branches: 90, functions: 90, statements: 90 },
        'src/state/**': { lines: 90, branches: 90, functions: 90, statements: 90 },
        'src/shell/**': { lines: 90, branches: 90, functions: 90, statements: 90 },
      },
    },
  },
});
