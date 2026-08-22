import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '@/test/render';
import { SECTIONS } from '@/shell/sections';
import { AppRoutes, landingPath, routableSections } from './routes';

/**
 * Tests for which addresses this app answers.
 *
 * The first group is the honest-navigation rule seen from the other side: a
 * screen that has not been built must not be reachable by typing its address
 * either, or the navigation is telling one story and the router another.
 */

describe('what is reachable', () => {
  it('routes only to sections that have a screen', () => {
    const claimed = SECTIONS.filter((section) => section.ready).map((section) => section.id);
    const reachable = routableSections().map((section) => section.id);

    expect(reachable.every((id) => claimed.includes(id))).toBe(true);
  });

  it('lands on the first built section, or on the placeholder', () => {
    expect(landingPath([{ ...SECTIONS[0]!, ready: true }])).toBe(SECTIONS[0]!.path);
    expect(landingPath([{ ...SECTIONS[0]!, ready: false }])).toBe('/home');
  });

  it('says there is nothing at an address it does not answer', () => {
    renderWithProviders(<AppRoutes />, { route: '/not-a-place' });

    expect(screen.getByRole('heading', { name: /nothing at this address/i })).toBeInTheDocument();
    expect(screen.getByText('/not-a-place')).toBeInTheDocument();
  });

  it('does not answer the address of a screen nobody has built', () => {
    const unbuilt = SECTIONS.find((section) => !section.ready)!;

    renderWithProviders(<AppRoutes />, { route: unbuilt.path });

    expect(screen.getByRole('heading', { name: /nothing at this address/i })).toBeInTheDocument();
  });

  it('shows the design system on its own unlisted address', () => {
    renderWithProviders(<AppRoutes />, { route: '/kitchen-sink' });

    expect(screen.getByRole('heading', { name: 'Kitchen sink' })).toBeInTheDocument();
  });

  it('says plainly what is built and what is not', () => {
    renderWithProviders(<AppRoutes />, { route: '/home' });

    expect(screen.getByRole('heading', { name: /foundation is in place/i })).toBeInTheDocument();
    expect(screen.getByText('Today')).toBeInTheDocument();
  });
});
