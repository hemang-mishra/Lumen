import { act, render, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { setMediaQueries } from '@/test/setup';
import { useThemeStore } from '@/state/theme-store';
import { DARK_QUERY } from './theme';
import { ThemeProvider, useTheme } from './ThemeProvider';
import { DensityScope } from './DensityScope';
import { FINE_POINTER_QUERY } from './density';

/**
 * Tests for keeping the document in the theme somebody is actually in.
 *
 * The page settles the first paint by itself; this picks up from there and
 * covers the rest of the app's life — somebody changing the setting, and
 * somebody changing their system theme while the app is open.
 */

describe('the theme provider', () => {
  it('puts the resolved theme on the document', async () => {
    setMediaQueries({ [DARK_QUERY]: true });
    act(() => useThemeStore.getState().setChoice('system'));

    render(<ThemeProvider>{null}</ThemeProvider>);

    await waitFor(() =>
      expect(document.documentElement.getAttribute('data-theme')).toBe('dark'),
    );
  });

  it('follows a choice over what the device says', async () => {
    setMediaQueries({ [DARK_QUERY]: true });
    render(<ThemeProvider>{null}</ThemeProvider>);

    act(() => useThemeStore.getState().setChoice('light'));

    await waitFor(() =>
      expect(document.documentElement.getAttribute('data-theme')).toBe('light'),
    );
  });

  it('renders whatever is inside it', () => {
    const { getByText } = render(
      <ThemeProvider>
        <p>a screen</p>
      </ThemeProvider>,
    );

    expect(getByText('a screen')).toBeInTheDocument();
  });
});

describe('asking what theme we are in', () => {
  it('reports the choice and the theme it resolves to', () => {
    setMediaQueries({ [DARK_QUERY]: true });
    act(() => useThemeStore.getState().setChoice('system'));

    const { result } = renderHook(() => useTheme());

    expect(result.current.choice).toBe('system');
    expect(result.current.resolved).toBe('dark');
  });

  it('changes the choice', () => {
    const { result } = renderHook(() => useTheme());

    act(() => result.current.setChoice('dark'));

    expect(result.current.choice).toBe('dark');
  });
});

describe('scoping a density', () => {
  it('marks the container so everything inside inherits it', () => {
    setMediaQueries({ [FINE_POINTER_QUERY]: true });

    const { container } = render(
      <DensityScope kind="inspect">
        <p>a run trace</p>
      </DensityScope>,
    );

    expect(container.firstElementChild).toHaveAttribute('data-density', 'compact');
    expect(container.firstElementChild).toHaveAttribute('data-surface', 'inspect');
  });

  it('stays comfortable on a touch device, whatever is being shown', () => {
    setMediaQueries({ [FINE_POINTER_QUERY]: false });

    const { container } = render(
      <DensityScope kind="inspect">
        <p>a run trace</p>
      </DensityScope>,
    );

    expect(container.firstElementChild).toHaveAttribute('data-density', 'comfortable');
  });
});
