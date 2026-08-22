import type { ReactNode } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { cn } from '@/lib/cn';
import { IconButton } from './Button';

/**
 * One component for everything that appears over the page.
 *
 * A sheet that rises from the bottom on a phone and a panel in the middle on
 * a desktop are the same thing wearing two layouts, and building them as two
 * components means two sets of focus handling, two ways of closing, and one
 * of them quietly worse than the other.
 *
 * Which form it takes is decided in the stylesheet rather than in code, so
 * there is no moment on load where it is the wrong one and then corrects
 * itself.
 *
 * A title is required. An overlay with no heading is unannounced for anybody
 * arriving at it with a screen reader, who then has no idea what has just
 * taken over the page.
 */

export interface OverlayProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** What this overlay is. Shown, and announced when it opens. */
  title: string;
  /** A line under the title, where the title alone is not enough. */
  description?: string;
  children: ReactNode;
  /** Buttons along the bottom. */
  footer?: ReactNode;
  className?: string;
}

export function Overlay({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  className,
}: OverlayProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay
          className={cn(
            'fixed inset-0 z-40 bg-[var(--scrim)]',
            'data-[state=open]:animate-in data-[state=open]:fade-in',
          )}
        />
        <Dialog.Content
          className={cn(
            'fixed z-50 flex flex-col gap-4 bg-surface-raised text-text shadow-[var(--shadow-2)]',
            // A phone: full width, along the bottom, within the safe area so
            // it clears the home indicator.
            'inset-x-0 bottom-0 max-h-[85vh] rounded-t-sheet p-[var(--card-padding)]',
            'pb-[calc(var(--card-padding)+env(safe-area-inset-bottom))]',
            // A desktop: a panel in the middle.
            'sm:inset-x-auto sm:bottom-auto sm:top-1/2 sm:left-1/2 sm:w-[min(560px,92vw)]',
            'sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-sheet sm:pb-[var(--card-padding)]',
            className,
          )}
        >
          <div className="flex items-start justify-between gap-4">
            <div className="flex flex-col gap-1">
              <Dialog.Title className="text-[length:var(--type-title)] leading-[var(--type-title-line)]">
                {title}
              </Dialog.Title>
              {description ? (
                <Dialog.Description className="text-[length:var(--type-meta)] text-text-secondary">
                  {description}
                </Dialog.Description>
              ) : null}
            </div>
            <Dialog.Close asChild>
              <IconButton icon={X} label="Close" />
            </Dialog.Close>
          </div>

          <div className="overflow-y-auto">{children}</div>

          {footer ? <div className="flex justify-end gap-2">{footer}</div> : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
