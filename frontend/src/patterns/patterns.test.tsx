import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/render';
import { JournalText } from './JournalText';
import { RecordLine } from './RecordLine';

/**
 * Tests for the two patterns that are Lumen's own.
 *
 * Both exist because of a specific failure. A record shown as an identifier
 * tells a person nothing about their own history; journal text rendered as
 * markup lets an imported file decide what the page does.
 */

describe('a record line', () => {
  it('leads with what the record says', () => {
    renderWithProviders(
      <RecordLine
        says="I put off the review again."
        meta={['observation', '11 Jun 2026']}
        id="obs_2026_06_11_01_003"
      />,
    );

    expect(screen.getByText('I put off the review again.')).toBeInTheDocument();
  });

  it('keeps the identifier quiet rather than making it the heading', () => {
    const { container } = renderWithProviders(
      <RecordLine says="I put off the review again." id="obs_2026_06_11_01_003" />,
    );

    const first = container.querySelector('div')?.textContent ?? '';
    expect(first.indexOf('I put off')).toBeLessThan(first.indexOf('obs_2026'));
  });

  it('joins the facts about it with middots', () => {
    renderWithProviders(
      <RecordLine says="Something" meta={['observation', '11 Jun 2026', 'high']} />,
    );

    expect(screen.getByText('observation · 11 Jun 2026 · high')).toBeInTheDocument();
  });

  it('leaves out the facts it does not have', () => {
    renderWithProviders(<RecordLine says="Something" meta={['observation', null, undefined]} />);

    expect(screen.getByText('observation')).toBeInTheDocument();
  });

  it('copies the identifier when it is clicked', async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    renderWithProviders(<RecordLine says="Something" id="obs_1" />);

    await userEvent.click(screen.getByRole('button', { name: 'Copy obs_1' }));

    expect(writeText).toHaveBeenCalledWith('obs_1');
    expect(await screen.findByRole('button', { name: 'obs_1 copied' })).toBeInTheDocument();
  });

  it('says nothing about having copied when it could not', async () => {
    vi.stubGlobal('navigator', {});
    Object.defineProperty(document, 'execCommand', {
      value: () => {
        throw new Error('no');
      },
      configurable: true,
    });
    renderWithProviders(<RecordLine says="Something" id="obs_1" />);

    await userEvent.click(screen.getByRole('button', { name: 'Copy obs_1' }));

    expect(screen.getByRole('button', { name: 'Copy obs_1' })).toBeInTheDocument();
  });

  it('links to the record where there is somewhere to go', () => {
    renderWithProviders(<RecordLine says="Something" to="/episodes/ep_1" />);

    expect(screen.getByRole('link', { name: 'Something' })).toHaveAttribute(
      'href',
      '/episodes/ep_1',
    );
  });
});

describe('journal text', () => {
  it('shows what was written, markup and all, as text', () => {
    // An imported export can contain anything at all, and this is the one
    // place in the app that displays whatever a file happened to hold.
    const written = 'I wrote <script>alert(1)</script> in my journal.';
    const { container } = renderWithProviders(<JournalText>{written}</JournalText>);

    expect(screen.getByText(written)).toBeInTheDocument();
    expect(container.querySelector('script')).toBeNull();
  });

  it('keeps the line breaks somebody wrote', () => {
    const { container } = renderWithProviders(<JournalText>{'one\n\ntwo'}</JournalText>);

    expect(container.textContent).toBe('one\n\ntwo');
  });
});
