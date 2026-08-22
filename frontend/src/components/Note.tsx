import type { ReactNode } from 'react';
import { AlertTriangle, Info, OctagonAlert } from 'lucide-react';
import { cn } from '@/lib/cn';
import { Icon } from './Icon';

/**
 * A sentence saying something is being held back, cut short, or has failed.
 *
 * This exists because of one rule the whole product is built on: nothing is
 * silently withheld. A record gated until its subject comes up, a list cut by
 * a limit, a graph slice that stopped at three hops — each is stated where it
 * happened, with the reason, rather than left as a gap the reader has no way
 * of noticing.
 *
 * It stays on the screen. A message that disappears after four seconds is not
 * a statement about what is missing; it is a message somebody may not have
 * been looking at.
 */

export type NoteTone = 'info' | 'caution' | 'critical';

export interface NoteProps {
  tone?: NoteTone;
  /** What is being held back or what went wrong, in a sentence. */
  children: ReactNode;
  /** Something to do about it, where there is anything to do. */
  action?: ReactNode;
  className?: string;
}

const tones: Record<NoteTone, { text: string; glyph: typeof Info; word: string }> = {
  info: { text: 'text-text-secondary', glyph: Info, word: 'Note' },
  caution: { text: 'text-caution', glyph: AlertTriangle, word: 'Held back' },
  critical: { text: 'text-critical', glyph: OctagonAlert, word: 'Failed' },
};

export function Note({ tone = 'info', children, action, className }: NoteProps) {
  const { text, glyph, word } = tones[tone];

  return (
    <div
      className={cn(
        'flex items-start gap-2 text-[length:var(--type-meta)] leading-[var(--type-meta-line)]',
        text,
        className,
      )}
    >
      {/* The icon is labelled rather than decorative, so the meaning the
          colour carries is also available as a word. */}
      <Icon as={glyph} label={word} className="mt-0.5" />
      <div className="flex flex-col gap-1">
        <span>{children}</span>
        {action}
      </div>
    </div>
  );
}
