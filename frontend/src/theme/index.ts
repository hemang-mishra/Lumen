/** Everything about which theme and density the app is in. */
export { ThemeProvider, useTheme } from './ThemeProvider';
export { DensityScope } from './DensityScope';
export {
  applyTheme,
  DARK_QUERY,
  DEFAULT_CHOICE,
  isThemeChoice,
  readStoredChoice,
  resolveTheme,
  storeChoice,
  THEME_STORAGE_KEY,
  type ResolvedTheme,
  type ThemeChoice,
} from './theme';
export {
  FINE_POINTER_QUERY,
  resolveDensity,
  type Density,
  type SurfaceKind,
} from './density';
