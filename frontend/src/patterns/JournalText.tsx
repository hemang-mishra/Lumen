import { cn } from '@/lib/cn';

/**
 * Somebody's own writing, shown as they wrote it.
 *
 * Larger and more loosely set than the rest of the interface, and capped at a
 * comfortable line length, because this is the one thing on the screen that
 * is actually read rather than scanned.
 *
 * It is rendered as text and never as markup, and that is a safety rule
 * before it is a typographic one. An imported export can contain anything at
 * all, and the one place in this app that displays whatever a file happened
 * to hold is the one place that must not interpret it.
 *
 * Line breaks are kept, and a very long unbroken string — a pasted URL, a
 * stretch with no spaces — wraps rather than pushing the page sideways.
 */

export interface JournalTextProps {
  /** What was written. Plain text, always. */
  children: string;
  /** Whether it should be held to the reading measure. */
  measured?: boolean;
  className?: string;
}

export function JournalText({ children, measured = true, className }: JournalTextProps) {
  return (
    <p
      className={cn(
        'text-[length:var(--type-reading)] leading-[var(--type-reading-line)] text-text',
        'whitespace-pre-wrap [overflow-wrap:anywhere]',
        measured ? 'max-w-[var(--measure-reading)]' : '',
        className,
      )}
    >
      {children}
    </p>
  );
}
