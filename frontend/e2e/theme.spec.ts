import { test, expect } from '@playwright/test';

/**
 * The theme: chosen, remembered, and never wrong for a moment on load.
 *
 * The flash is the interesting one. Everything else here would be caught by
 * somebody looking at the screen; a white flash on a dark morning lasts one
 * frame, is impossible to review by hand, and is the thing people notice
 * about an app before anything else.
 */

test.describe('the theme', () => {
  test('follows the device when nobody has chosen', async ({ browser }) => {
    const context = await browser.newContext({ colorScheme: 'dark' });
    const page = await context.newPage();

    await page.goto('/kitchen-sink');

    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await context.close();
  });

  test('a choice overrides the device', async ({ browser }) => {
    const context = await browser.newContext({ colorScheme: 'dark' });
    const page = await context.newPage();
    await page.addInitScript(() => window.localStorage.setItem('lumen.theme', 'light'));

    await page.goto('/kitchen-sink');

    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await context.close();
  });

  test('the choice survives a reload', async ({ page }) => {
    await page.goto('/kitchen-sink');
    await page.getByRole('button', { name: 'Dark' }).click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');

    await page.reload();

    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  });

  test('is right on the very first paint, not corrected afterwards', async ({ browser }) => {
    const context = await browser.newContext({ colorScheme: 'light' });
    const page = await context.newPage();
    await page.addInitScript(() => window.localStorage.setItem('lumen.theme', 'dark'));

    // Read the attribute as the document starts, before the app's own code
    // has had a chance to run. If it were only set by React, this would be
    // the light theme here and the dark one a frame later — which is exactly
    // what a flash is.
    const atFirstPaint = await page.evaluate(async () => {
      const seen: string[] = [];
      const observer = new MutationObserver(() => {
        seen.push(document.documentElement.getAttribute('data-theme') ?? '');
      });
      observer.observe(document.documentElement, { attributes: true });
      return seen;
    });
    await page.goto('/kitchen-sink');

    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    expect(atFirstPaint).toEqual([]);

    const background = await page.evaluate(() =>
      window.getComputedStyle(document.body).backgroundColor,
    );
    // The dark canvas, not the light one. A body with no background of its
    // own would come back transparent here.
    expect(background).toBe('rgb(19, 20, 22)');
    await context.close();
  });
});
