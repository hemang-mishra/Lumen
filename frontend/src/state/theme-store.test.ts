import { describe, expect, it, vi } from 'vitest';
import { initialChoice, useThemeStore } from './theme-store';
import { THEME_STORAGE_KEY } from '@/theme/theme';

/** Tests for where the theme choice is kept between visits. */

describe('the theme store', () => {
  it('starts from what was remembered', () => {
    expect(initialChoice({ getItem: () => 'dark' })).toBe('dark');
  });

  it('starts by following the device when nothing was remembered', () => {
    expect(initialChoice({ getItem: () => null })).toBe('system');
  });

  it('remembers a new choice for next time', () => {
    useThemeStore.getState().setChoice('dark');

    expect(useThemeStore.getState().choice).toBe('dark');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
  });

  it('still follows the device when the browser hands over no storage at all', () => {
    // Some browsers throw on the very act of reaching for storage rather
    // than on using it.
    const original = Object.getOwnPropertyDescriptor(window, 'localStorage');
    Object.defineProperty(window, 'localStorage', {
      get() {
        throw new Error('storage is blocked');
      },
      configurable: true,
    });

    expect(initialChoice()).toBe('system');

    if (original) Object.defineProperty(window, 'localStorage', original);
  });

  it('still switches when the browser will not remember it', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage is full');
    });

    useThemeStore.getState().setChoice('light');

    expect(useThemeStore.getState().choice).toBe('light');
  });
});
