import { create } from 'zustand';
import {
  DEFAULT_CHOICE,
  readStoredChoice,
  storeChoice,
  type ThemeChoice,
} from '@/theme/theme';

/**
 * What the person has chosen about the theme, and nothing else.
 *
 * The choice is here rather than in a component because the theme switcher
 * and the thing that paints the document are in different places, and passing
 * it down through everything in between would tie every layout to it.
 *
 * Resolving the choice into a theme that can be drawn is deliberately not
 * here — that depends on the device, which is a subscription rather than a
 * stored value.
 */
interface ThemeState {
  /** system, light or dark. */
  choice: ThemeChoice;
  /** Choose, and remember it for next time. */
  setChoice: (choice: ThemeChoice) => void;
}

/**
 * The store's starting value, read from storage once.
 *
 * Taken as an argument so a test can hand in its own storage instead of
 * reaching for the real one.
 */
export function initialChoice(storage?: Pick<Storage, 'getItem'>): ThemeChoice {
  const source = storage ?? safeStorage();
  return source ? readStoredChoice(source) : DEFAULT_CHOICE;
}

export const useThemeStore = create<ThemeState>((set) => ({
  choice: initialChoice(),
  setChoice: (choice) => {
    const storage = safeStorage();
    if (storage) storeChoice(storage, choice);
    set({ choice });
  },
}));

/** Local storage, or nothing at all if this browser will not hand it over. */
function safeStorage(): Storage | null {
  try {
    // Some browsers throw on the act of reaching for it, not on using it.
    return window.localStorage;
  } catch {
    return null;
  }
}
