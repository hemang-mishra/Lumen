import { useEffect, type ReactNode } from 'react';
import { useMediaQuery } from '@/lib/useMediaQuery';
import { useThemeStore } from '@/state/theme-store';
import { applyTheme, DARK_QUERY, resolveTheme, type ResolvedTheme, type ThemeChoice } from './theme';

/**
 * Keeps the document in the theme the person is actually in.
 *
 * The very first paint is handled by a small script in the page itself, long
 * before any of this has loaded. This picks up from there and covers the rest
 * of the app's life: somebody changing the setting, and somebody changing
 * their system theme while the app is open.
 */
export function ThemeProvider({ children }: { children: ReactNode }): ReactNode {
  const choice = useThemeStore((state) => state.choice);
  const prefersDark = useMediaQuery(DARK_QUERY);
  const resolved = resolveTheme(choice, prefersDark);

  useEffect(() => {
    applyTheme(document.documentElement, resolved);
  }, [resolved]);

  return children;
}

/**
 * The theme, as a screen needs to know it.
 *
 * Returns both halves on purpose: a settings screen shows which of the three
 * was chosen, while a chart needs to know which of the two is on the screen
 * right now.
 */
export function useTheme(): {
  choice: ThemeChoice;
  resolved: ResolvedTheme;
  setChoice: (choice: ThemeChoice) => void;
} {
  const choice = useThemeStore((state) => state.choice);
  const setChoice = useThemeStore((state) => state.setChoice);
  const prefersDark = useMediaQuery(DARK_QUERY);

  return { choice, resolved: resolveTheme(choice, prefersDark), setChoice };
}
