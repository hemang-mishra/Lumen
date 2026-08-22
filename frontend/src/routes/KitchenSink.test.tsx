import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import { KitchenSink } from './KitchenSink';

/**
 * Tests for the page the design system is reviewed on.
 *
 * The browser run does the looking — both themes, both densities, a phone
 * width, an accessibility pass. This checks the thing a browser run cannot:
 * that every part is actually on the page, so a component cannot quietly
 * stop being reviewed by falling off it.
 */

describe('the kitchen sink', () => {
  it('shows all three buttons and both destructive forms', () => {
    renderWithProviders(<KitchenSink />);

    for (const name of ['Primary', 'Secondary', 'Ghost', 'With an icon']) {
      expect(screen.getByRole('button', { name })).toBeInTheDocument();
    }
    expect(screen.getAllByRole('button', { name: 'Erase everything' })).toHaveLength(2);
  });

  it('shows a chip in every tone, each with its word', () => {
    renderWithProviders(<KitchenSink />);

    for (const word of ['observation', 'settled', 'needs attention', 'failed', 'needs you']) {
      expect(screen.getByText(word)).toBeInTheDocument();
    }
  });

  it('shows all four ways a list can be empty, said four different ways', () => {
    renderWithProviders(<KitchenSink />);

    expect(screen.getByText('Looking for runs.')).toBeInTheDocument();
    expect(screen.getByText('Nothing has been processed yet.')).toBeInTheDocument();
    expect(screen.getByText('No run matches these filters.')).toBeInTheDocument();
    expect(screen.getByText(/The runs could not be loaded\./)).toBeInTheDocument();
  });

  it('shows a record by what it says rather than by its id', () => {
    renderWithProviders(<KitchenSink />);

    expect(
      screen.getByText('I put off the review again and told myself it was timing.'),
    ).toBeInTheDocument();
  });

  it('shows the table and the card form of the same rows', () => {
    renderWithProviders(<KitchenSink />);

    const table = screen.getByRole('table', { name: 'Recent runs' });
    const cards = screen.getByRole('list', { name: 'Recent runs' });

    expect(within(table).getByText('Tuesday evening, after the review')).toBeInTheDocument();
    expect(within(cards).getByText('Tuesday evening, after the review')).toBeInTheDocument();
  });

  it('opens the overlay', async () => {
    renderWithProviders(<KitchenSink />);

    await userEvent.click(screen.getByRole('button', { name: 'Open it' }));

    expect(screen.getByRole('dialog', { name: 'A sheet, or a dialog' })).toBeInTheDocument();
  });
});
