import { useState, type ReactNode } from 'react';
import { Copy, Check } from 'lucide-react';
import { Link } from 'react-router-dom';
import { cn } from '@/lib/cn';
import { copyText } from '@/lib/copy';
import { metaLine } from '@/lib/format';
import { Icon } from '@/components/Icon';

/**
 * How any record from the graph appears, anywhere in the app.
 *
 * The record's own words are the heading. Always. An identifier like
 * `obs_2026_06_11_01_003` tells a person nothing about their own history, and
 * a screen that leads with one is asking them to go and look it up somewhere
 * else. The identifier is still here — quiet, monospace, and copyable in one
 * click — because it is what somebody needs when they are debugging, and
 * hiding it entirely would have replaced one failure with another.
 *
 * The meta row is middot-separated text rather than a row of chips. Four
 * chips beside every record turns a list into a wall of boxes, and none of
 * those four facts is a state anybody is scanning for.
 *
 * The type makes this hard to get wrong: what the record says is required, so
 * there is no way to render one of these with an identifier as its heading.
 */

export interface RecordLineProps {
  /** What the record says, in its own words. Required, and the heading. */
  says: string;
  /** Facts about it: its kind, its date, its strength, its status. */
  meta?: ReadonlyArray<string | null | undefined>;
  /** The record's identifier. Shown quietly, copied on click. */
  id?: string;
  /** Where opening this record leads, if anywhere. */
  to?: string;
  /** Something extra at the end of the meta row — a chip for a real state. */
  trailing?: ReactNode;
  className?: string;
}

export function RecordLine({ says, meta = [], id, to, trailing, className }: RecordLineProps) {
  const facts = metaLine(meta);

  return (
    <div className={cn('flex flex-col gap-1', className)}>
      <div className="text-[length:var(--density-text)] leading-[var(--density-line)] text-text">
        {to ? (
          <Link to={to} className="text-text underline-offset-2 hover:underline">
            {says}
          </Link>
        ) : (
          says
        )}
      </div>

      {(facts || trailing) && (
        <div className="flex flex-wrap items-center gap-2 text-[length:var(--type-meta)] leading-[var(--type-meta-line)] text-text-secondary">
          {facts ? <span>{facts}</span> : null}
          {trailing}
        </div>
      )}

      {id ? <CopyableId id={id} /> : null}
    </div>
  );
}

/**
 * The identifier, and one click to take a copy of it.
 *
 * The confirmation is a change of icon and a word, not a message that floats
 * over the corner of the screen — a person copying an id is looking at the
 * id.
 */
export function CopyableId({ id, className }: { id: string; className?: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    const worked = await copyText(id);
    if (!worked) return;
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  return (
    <button
      type="button"
      onClick={copy}
      aria-label={copied ? `${id} copied` : `Copy ${id}`}
      className={cn(
        'inline-flex w-fit cursor-pointer items-center gap-1 rounded-chip px-1 py-0.5',
        'font-mono text-[length:var(--type-meta)] text-text-tertiary',
        'hover:bg-[var(--state-hover)] active:bg-[var(--state-press)]',
        'transition-colors duration-[var(--dur-micro)]',
        className,
      )}
    >
      <span>{id}</span>
      <Icon as={copied ? Check : Copy} className="size-4" />
      {copied ? <span className="sr-only">copied</span> : null}
    </button>
  );
}
