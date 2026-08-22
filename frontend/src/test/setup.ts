import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

/**
 * What every test run starts from.
 *
 * The browser stand-in has no matchMedia, and the app asks it two questions —
 * whether the device prefers dark, and whether it has a mouse. Left undefined
 * those questions throw; answered "no" by default, every test starts in the
 * light theme on a touch device and says so explicitly when it wants
 * something else.
 */

/** Answer the device questions with whatever a test wants. */
export function setMediaQueries(answers: Record<string, boolean>) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: answers[query] ?? false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

beforeEach(() => {
  setMediaQueries({});
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
