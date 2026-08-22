import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/**
 * The design system, walked through the way it is meant to be reviewed.
 *
 * Everything here is a rule from the review checklist turned into something
 * that fails on its own rather than something somebody has to remember to
 * look at.
 */

const THEMES = ['light', 'dark'] as const;

/** Put the app in a theme before it loads, the way a returning person would. */
async function openInTheme(page: Page, theme: 'light' | 'dark') {
  await page.addInitScript((chosen) => {
    window.localStorage.setItem('lumen.theme', chosen);
  }, theme);
  await page.goto('/kitchen-sink');
  await expect(page.getByTestId('kitchen-sink')).toBeVisible();
}

test.describe('the kitchen sink', () => {
  for (const theme of THEMES) {
    test(`renders every part in ${theme}`, async ({ page }) => {
      await openInTheme(page, theme);

      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(page.getByRole('heading', { name: 'Kitchen sink' })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Primary' })).toBeVisible();
      await expect(page.getByText('needs you')).toBeVisible();
    });

    test(`has no accessibility failures in ${theme}`, async ({ page }) => {
      await openInTheme(page, theme);

      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .analyze();

      expect(results.violations).toEqual([]);
    });
  }

  test('never scrolls the page sideways', async ({ page }) => {
    await openInTheme(page, 'dark');

    const overflowing = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );

    expect(overflowing).toBe(false);
  });

  test('keeps every column when the table becomes cards', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'the card form only exists below the tablet breakpoint');
    await openInTheme(page, 'light');

    const cards = page.getByRole('list', { name: 'Recent runs' });
    // The heading column is the card's title; the other four are labelled
    // values inside it. None of them may be dropped to make a phone fit.
    for (const label of ['Triggered by', 'Records written', 'Took', 'Started']) {
      await expect(cards.getByText(label).first()).toBeVisible();
    }
    await expect(cards.getByText('Tuesday evening, after the review')).toBeVisible();
  });

  test('says something different for each of the four empty states', async ({ page }) => {
    await openInTheme(page, 'light');

    await expect(page.getByText('Looking for runs.')).toBeVisible();
    await expect(page.getByText('Nothing has been processed yet.')).toBeVisible();
    await expect(page.getByText('No run matches these filters.')).toBeVisible();
    await expect(page.getByText(/The runs could not be loaded\./)).toBeVisible();
  });

  test('is reachable by keyboard, with focus always visible', async ({ page, isMobile }) => {
    test.skip(isMobile, 'there is no keyboard traversal on a touch device');
    await openInTheme(page, 'dark');

    // The first stop is the skip link, which is what makes a keyboard user's
    // second stop the page rather than the whole navigation.
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();

    const outline = await page.evaluate(() => {
      const focused = document.activeElement as HTMLElement;
      return window.getComputedStyle(focused).outlineWidth;
    });
    expect(outline).not.toBe('0px');
  });

  test('opens and closes the overlay from the keyboard', async ({ page, isMobile }) => {
    test.skip(isMobile, 'driven from the keyboard');
    await openInTheme(page, 'light');

    await page.getByRole('button', { name: 'Open it' }).click();
    await expect(page.getByRole('dialog')).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toBeHidden();
  });

  test.describe('with motion turned down', () => {
    test.use({ contextOptions: { reducedMotion: 'reduce' } });

    test('nothing animates', async ({ page }) => {
      await openInTheme(page, 'dark');

      const durations = await page.evaluate(() =>
        [...document.querySelectorAll('*')]
          .map((element) => window.getComputedStyle(element).transitionDuration)
          .filter((duration) => duration !== '0s'),
      );

      expect(durations).toEqual([]);
    });
  });
});
