/**
 * Deciding which theme the app is in, and remembering the answer.
 *
 * Three choices are on offer and only two can be drawn, so there is always a
 * resolving step: "system" is not a theme, it is a question asked of the
 * device. Everything here is a plain function over values that get passed in,
 * which is what makes it testable without a browser.
 */

/** What somebody can choose. */
export type ThemeChoice = 'system' | 'light' | 'dark';

/** What can actually be drawn. */
export type ResolvedTheme = 'light' | 'dark';

/**
 * Where the choice is kept.
 *
 * The same string is written into index.html, which needs it before any of
 * this code has loaded. A test compares the two so they cannot drift apart.
 */
export const THEME_STORAGE_KEY = 'lumen.theme';

/** The question the device is asked when the choice is "system". */
export const DARK_QUERY = '(prefers-color-scheme: dark)';

/** What somebody who has never chosen gets: whatever their device is set to. */
export const DEFAULT_CHOICE: ThemeChoice = 'system';

const CHOICES: readonly ThemeChoice[] = ['system', 'light', 'dark'];

/** Whether a value read from storage is one of the three real choices. */
export function isThemeChoice(value: unknown): value is ThemeChoice {
  return typeof value === 'string' && (CHOICES as readonly string[]).includes(value);
}

/**
 * Turn a choice into a theme that can be drawn.
 *
 * @param choice What was chosen, or asked of the system.
 * @param systemPrefersDark What the device says, for when the choice defers.
 */
export function resolveTheme(choice: ThemeChoice, systemPrefersDark: boolean): ResolvedTheme {
  if (choice === 'system') return systemPrefersDark ? 'dark' : 'light';
  return choice;
}

/**
 * Read the remembered choice, falling back to following the device.
 *
 * Anything unrecognised is treated as never having chosen. A stored value
 * from an older version of the app should quietly stop applying rather than
 * put the app in a state it has no styles for.
 */
export function readStoredChoice(storage: Pick<Storage, 'getItem'>): ThemeChoice {
  try {
    const stored = storage.getItem(THEME_STORAGE_KEY);
    return isThemeChoice(stored) ? stored : DEFAULT_CHOICE;
  } catch {
    // Private browsing, or storage switched off. Following the device is a
    // perfectly good answer and is not worth an error.
    return DEFAULT_CHOICE;
  }
}

/** Remember a choice, and shrug if the browser will not let us. */
export function storeChoice(storage: Pick<Storage, 'setItem'>, choice: ThemeChoice): void {
  try {
    storage.setItem(THEME_STORAGE_KEY, choice);
  } catch {
    // Not being able to remember is a smaller problem than refusing to
    // switch, so the switch happens either way.
  }
}

/**
 * Put the resolved theme on the document.
 *
 * Sets the browser's own colour scheme as well as ours, so that things the
 * app does not draw — scrollbars, form controls, the flash between pages —
 * match the theme instead of staying stubbornly light.
 */
export function applyTheme(root: HTMLElement, theme: ResolvedTheme): void {
  root.setAttribute('data-theme', theme);
  root.style.colorScheme = theme;
}
