import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import {
  applyTheme,
  DEFAULT_CHOICE,
  isThemeChoice,
  readStoredChoice,
  resolveTheme,
  storeChoice,
  THEME_STORAGE_KEY,
} from './theme';

/**
 * Tests for how the theme is decided, remembered and applied.
 *
 * The last one in this file is the one that matters most and is the easiest
 * to break: the page settles the theme itself, before any of this code
 * exists, and the two have to agree about where the choice is kept.
 */

describe('resolving a choice into a theme', () => {
  it('follows the device when nobody has chosen', () => {
    expect(resolveTheme('system', true)).toBe('dark');
    expect(resolveTheme('system', false)).toBe('light');
  });

  it('ignores the device when somebody has chosen', () => {
    expect(resolveTheme('light', true)).toBe('light');
    expect(resolveTheme('dark', false)).toBe('dark');
  });

  it('starts by following the device rather than forcing one theme', () => {
    expect(DEFAULT_CHOICE).toBe('system');
  });
});

describe('what counts as a choice', () => {
  it('accepts the three real ones', () => {
    expect(isThemeChoice('system')).toBe(true);
    expect(isThemeChoice('light')).toBe(true);
    expect(isThemeChoice('dark')).toBe(true);
  });

  it('rejects anything else', () => {
    expect(isThemeChoice('sepia')).toBe(false);
    expect(isThemeChoice(null)).toBe(false);
    expect(isThemeChoice(3)).toBe(false);
  });
});

describe('remembering a choice', () => {
  it('reads back what was stored', () => {
    const storage = { getItem: () => 'dark' };

    expect(readStoredChoice(storage)).toBe('dark');
  });

  it('treats a value it does not recognise as never having chosen', () => {
    // A choice from an older version of the app should stop applying
    // quietly, not leave the app in a state it has no styles for.
    const storage = { getItem: () => 'midnight' };

    expect(readStoredChoice(storage)).toBe('system');
  });

  it('follows the device when storage refuses to answer', () => {
    const storage = {
      getItem: () => {
        throw new Error('private browsing');
      },
    };

    expect(readStoredChoice(storage)).toBe('system');
  });

  it('switches anyway when storage refuses to remember', () => {
    // Not being able to remember is a smaller problem than refusing to
    // switch, so this must not throw.
    const storage = {
      setItem: () => {
        throw new Error('storage is full');
      },
    };

    expect(() => storeChoice(storage, 'dark')).not.toThrow();
  });

  it('stores under the key the page itself uses', () => {
    const setItem = vi.fn();

    storeChoice({ setItem }, 'light');

    expect(setItem).toHaveBeenCalledWith(THEME_STORAGE_KEY, 'light');
  });
});

describe('applying a theme', () => {
  it('marks the document and tells the browser too', () => {
    const root = document.createElement('html');

    applyTheme(root, 'dark');

    // The second half matters for everything the app does not draw:
    // scrollbars, form controls, the flash between pages.
    expect(root.getAttribute('data-theme')).toBe('dark');
    expect(root.style.colorScheme).toBe('dark');
  });
});

describe('the script that settles the theme before anything is drawn', () => {
  const page = readFileSync(join(process.cwd(), 'index.html'), 'utf8');

  it('uses the same key this code does', () => {
    // The script runs before the bundle and cannot import anything, so the
    // key is written out twice. This is what stops the two drifting apart
    // and leaving a choice that is remembered but never read.
    expect(page).toContain(`'${THEME_STORAGE_KEY}'`);
  });

  it('runs before the app, not after it', () => {
    const scriptAt = page.indexOf('localStorage.getItem');
    const appAt = page.indexOf('src/main.tsx');

    expect(scriptAt).toBeGreaterThan(-1);
    expect(scriptAt).toBeLessThan(appAt);
  });

  it('falls back to a real theme if anything in it fails', () => {
    expect(page).toMatch(/catch[\s\S]*data-theme/);
  });
});
