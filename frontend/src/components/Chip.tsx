import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

/**
 * A small outlined label for a state.
 *
 * Two rules decide when a chip may be coloured, and both come from the same
 * idea: colour says what condition something is in, never what kind of thing
 * it is. Fifteen kinds of record cannot be fifteen colours, so a kind gets a
 * word; a state — settled, failed, needs attention — may get one of four.
 *
 * The colour is never the only thing carrying the meaning. A chip always has
 * its word in it, so somebody who cannot separate the two colours loses
 * nothing at all.
 *
 * Chips are outlines. The one filled chip in the app is the "needs you"
 * state, which is filled precisely because it is the one thing meant to pull
 * a person's eye across a screen.
 */

export type ChipTone = 'neutral' | 'positive' | 'caution' | 'critical' | 'accent';

export interface ChipProps {
  /** The word. Not optional — a coloured shape on its own means nothing. */
  children: ReactNode;
  tone?: ChipTone;
  /** Reserved for the one state that is asking somebody to do something. */
  filled?: boolean;
  className?: string;
}

const outlines: Record<ChipTone, string> = {
  neutral: 'text-text-secondary border-border-strong',
  positive: 'text-positive border-positive',
  caution: 'text-caution border-caution',
  critical: 'text-critical border-critical',
  accent: 'text-accent border-accent',
};

const fills: Record<ChipTone, string> = {
  neutral: 'text-text bg-[var(--state-hover)] border-transparent',
  positive: 'text-positive bg-[var(--positive-quiet)] border-transparent',
  caution: 'text-caution bg-[var(--caution-quiet)] border-transparent',
  critical: 'text-critical bg-[var(--critical-quiet)] border-transparent',
  accent: 'text-accent bg-accent-quiet border-transparent',
};

export function Chip({ children, tone = 'neutral', filled = false, className }: ChipProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-chip border px-2 py-0.5',
        'text-[length:var(--type-meta)] leading-[var(--type-meta-line)] font-medium',
        filled ? fills[tone] : outlines[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
