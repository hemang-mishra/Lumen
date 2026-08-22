import { useState, type ReactNode } from 'react';
import * as Collapsible from '@radix-ui/react-collapsible';
import { ChevronRight } from 'lucide-react';
import { cn } from '@/lib/cn';
import { Icon } from './Icon';

/**
 * Something folded away, with a label saying what is inside it.
 *
 * This is the workhorse of the whole product. The rule everywhere is that the
 * default view is calm and everything else is one deliberate expansion away —
 * so raw payloads, every property of a record, the candidates that were not
 * chosen, all of it lives behind one of these.
 *
 * The label says what is inside rather than saying "more". "Everything it
 * holds" and "What went in" tell somebody whether it is worth opening;
 * "Show more" makes them open it to find out.
 */

export interface DisclosureProps {
  /** What is inside, in words. Not "more". */
  label: string;
  /** A count or a hint beside the label, when one helps decide. */
  hint?: string;
  /** Open to begin with. Rare — the point is that things start folded. */
  defaultOpen?: boolean;
  children: ReactNode;
  className?: string;
}

export function Disclosure({
  label,
  hint,
  defaultOpen = false,
  children,
  className,
}: DisclosureProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <Collapsible.Root open={open} onOpenChange={setOpen} className={cn('w-full', className)}>
      <Collapsible.Trigger
        className={cn(
          'flex w-full cursor-pointer items-center gap-2 rounded-control px-2 py-2 text-left',
          'text-[length:var(--density-text)] text-text-secondary',
          'hover:bg-[var(--state-hover)] active:bg-[var(--state-press)]',
          'transition-colors duration-[var(--dur-micro)]',
        )}
      >
        <Icon
          as={ChevronRight}
          className={cn(
            'transition-transform duration-[var(--dur-standard)] ease-[var(--ease-enter)]',
            open ? 'rotate-90' : '',
          )}
        />
        <span className="font-medium text-text">{label}</span>
        {hint ? <span className="text-[length:var(--type-meta)]">{hint}</span> : null}
      </Collapsible.Trigger>

      <Collapsible.Content className="overflow-hidden">
        <div className="px-2 pt-2 pb-3">{children}</div>
      </Collapsible.Content>
    </Collapsible.Root>
  );
}
