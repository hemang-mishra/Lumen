import type { ReactNode } from 'react';
import { isLumenError } from '@/api/errors';
import { cn } from '@/lib/cn';
import { Button } from './Button';

/**
 * The four ways a list can have nothing in it, kept apart.
 *
 * Still loading, nothing exists yet, a filter excluded everything, and could
 * not be fetched are four different facts. Rendering all four as an empty box
 * is a wrong answer that looks right, and it is the single easiest way to
 * lose a distinction the service went to trouble to keep.
 *
 * The sentences are required rather than defaulted, so writing this component
 * into a screen forces somebody to decide what each state actually says. A
 * shared default would be four identical sentences everywhere, which is the
 * same failure with extra steps.
 *
 * There are no illustrations here and there never will be. An empty state is
 * a sentence and, where there is something to do about it, a button.
 */

/** Which of the states a list is in. */
export type ListStatus = 'loading' | 'ready' | 'empty' | 'filtered-empty' | 'failed';

/** What this particular list says in each state. All four are required. */
export interface StateSentences {
  /** While it is being fetched. */
  loading: string;
  /** Nothing of this kind exists yet. */
  empty: string;
  /** Things exist, but the current filters exclude all of them. */
  filteredEmpty: string;
  /** It could not be fetched. The reason is added from the failure itself. */
  failed: string;
}

export interface StateBoundaryProps {
  status: ListStatus;
  sentences: StateSentences;
  /** What went wrong, when something did. Its own words are shown too. */
  failure?: unknown;
  /** Offered when fetching failed. */
  onRetry?: () => void;
  /** Offered when a filter is what emptied the list. */
  onClearFilters?: () => void;
  /** The list itself, shown only when there is something to show. */
  children: ReactNode;
  className?: string;
}

export function StateBoundary({
  status,
  sentences,
  failure,
  onRetry,
  onClearFilters,
  children,
  className,
}: StateBoundaryProps) {
  if (status === 'ready') return <>{children}</>;

  const message = messageFor(status, sentences, failure);
  const action = actionFor(status, onRetry, onClearFilters);

  return (
    <div
      className={cn(
        'flex flex-col items-start gap-3 py-8 text-[length:var(--density-text)]',
        status === 'failed' ? 'text-critical' : 'text-text-secondary',
        className,
      )}
      // A list that fills in on its own should say so, rather than changing
      // silently behind somebody who cannot see it.
      aria-live={status === 'loading' ? 'polite' : undefined}
      aria-busy={status === 'loading' ? true : undefined}
      role={status === 'failed' ? 'alert' : undefined}
      data-state={status}
    >
      <p>{message}</p>
      {action}
    </div>
  );
}

/** The sentence for a state, with the failure's own reason added to it. */
export function messageFor(
  status: Exclude<ListStatus, 'ready'>,
  sentences: StateSentences,
  failure?: unknown,
): string {
  if (status === 'loading') return sentences.loading;
  if (status === 'empty') return sentences.empty;
  if (status === 'filtered-empty') return sentences.filteredEmpty;

  // What failed, and then what the service said about it. The second half is
  // what tells somebody whether to try again or to go and fix something.
  const because = reasonFrom(failure);
  return because ? `${sentences.failed} ${because}` : sentences.failed;
}

/** The service's own words about a failure, where there are any. */
export function reasonFrom(failure: unknown): string {
  if (isLumenError(failure)) return failure.message;
  if (failure instanceof Error) return failure.message;
  return '';
}

function actionFor(
  status: ListStatus,
  onRetry?: () => void,
  onClearFilters?: () => void,
): ReactNode {
  if (status === 'failed' && onRetry) {
    return (
      <Button variant="secondary" onClick={onRetry}>
        Try again
      </Button>
    );
  }
  if (status === 'filtered-empty' && onClearFilters) {
    return (
      <Button variant="ghost" onClick={onClearFilters}>
        Clear the filters
      </Button>
    );
  }
  return null;
}
