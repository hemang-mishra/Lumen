import { defineConfig, devices } from '@playwright/test';

/**
 * The journeys, and the two shapes of device they all run on.
 *
 * Every journey runs twice: once on a desktop with a mouse, and once at
 * 375px on a phone. A screen that was only ever looked at in one of those has
 * not been reviewed, and half of Lumen's use is one-handed.
 *
 * Themes are not a third dimension here — a journey switches theme itself,
 * because that is also how it proves the switch works.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    {
      // A phone as the requirements describe one: 375px wide, touch, no
      // mouse. Chromium rather than a named iPhone so the whole suite runs
      // on one engine and one download.
      name: 'phone',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 375, height: 812 },
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
