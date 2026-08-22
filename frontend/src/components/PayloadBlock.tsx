import { useState } from 'react';
import { cn } from '@/lib/cn';
import { Button } from './Button';

/**
 * Raw machine output — a stage's input, a stage's answer, a stored payload.
 *
 * It sits on the sunken surface, which is the one place a container is
 * allowed to be darker than what it is inside. That is what says "this is
 * content within something" rather than "this is another panel".
 *
 * It is capped and scrolls in its own box. A stage payload can be tens of
 * thousands of pixels tall, and a page that grows to that height is a page
 * where nothing below the payload can be reached.
 */

/** How tall a payload gets before it starts scrolling instead of growing. */
const CAPPED_AT = '340px';

export interface PayloadBlockProps {
  /** The text to show. Objects are formatted before they get here. */
  children: string;
  /** What this payload is, for anybody who cannot see the layout. */
  label?: string;
  className?: string;
}

export function PayloadBlock({ children, label, className }: PayloadBlockProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <pre
        // Focusable and marked as a region, because a box that scrolls must
        // be reachable by keyboard or its contents are unreadable without a
        // mouse.
        tabIndex={0}
        role="region"
        aria-label={label ?? 'payload'}
        className={cn(
          'm-0 overflow-auto rounded-card bg-surface-sunken p-3',
          'font-mono text-[length:var(--type-meta)] leading-[var(--type-meta-line)] text-text',
          'whitespace-pre-wrap [overflow-wrap:anywhere]',
        )}
        style={{ maxHeight: expanded ? 'none' : CAPPED_AT }}
      >
        {children}
      </pre>
      <div>
        <Button variant="ghost" onClick={() => setExpanded((was) => !was)}>
          {expanded ? 'Collapse it' : 'Show all of it'}
        </Button>
      </div>
    </div>
  );
}

/**
 * Format anything for a payload block.
 *
 * Text is left exactly as it is; anything else is laid out as readable JSON.
 * Something that cannot be laid out at all is described rather than silently
 * shown as "[object Object]", which tells a reader nothing and looks like a
 * bug in the payload rather than in the display of it.
 */
export function asPayloadText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value === null || value === undefined) return '';
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return '(this payload could not be displayed)';
  }
}
