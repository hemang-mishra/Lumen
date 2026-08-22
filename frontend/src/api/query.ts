import { QueryClient, type QueryKey } from '@tanstack/react-query';
import { isLumenError } from './errors';
import type { Session } from './session';

/**
 * The cache of everything the service has told us, and who it belongs to.
 *
 * Every cached answer is filed under the person it was fetched for, and the
 * whole cache is emptied when that person changes or leaves. Two accounts on
 * one laptop may never see each other's records — not after a refetch, and
 * not for the fraction of a second before one lands.
 */

/** What the cache is filed under when nobody has to sign in. */
export const NOBODY_IN_PARTICULAR = 'local';

/** How long an answer is treated as still true before it is fetched again. */
const FRESH_FOR = 30_000;

/**
 * Build a query client with the behaviour every screen should get.
 *
 * Retrying is the interesting part: a request that failed because the record
 * does not exist will fail identically forever, and retrying it three times
 * only makes a screen take three times as long to say so. Only failures that
 * could plausibly resolve themselves are tried again.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: FRESH_FOR,
        retry: (attempt, failure) => attempt < 2 && worthRetrying(failure),
        refetchOnWindowFocus: false,
        refetchOnReconnect: true,
      },
      mutations: { retry: false },
    },
  });
}

/** Whether a failure is the kind that might work if tried again. */
export function worthRetrying(failure: unknown): boolean {
  if (isLumenError(failure)) return failure.worthRetrying;
  // Something that is not one of ours got thrown. One more attempt is
  // cheap and might get past whatever it was.
  return true;
}

/**
 * The key an answer is cached under.
 *
 * Everything starts with whose data it is, so emptying one person's cache is
 * possible without a list of every kind of thing the app caches.
 */
export function keyFor(scope: string, ...parts: ReadonlyArray<unknown>): QueryKey {
  return [scope, ...parts];
}

/**
 * Keep the cache honest about who it belongs to.
 *
 * Emptied when somebody signs out, and emptied when somebody different signs
 * in — the second is the one that is easy to forget and the one that would
 * show a stranger's journal.
 *
 * @returns How to stop watching.
 */
export function bindCacheToSession(client: QueryClient, session: Session): () => void {
  let holder = session.user?.user_id ?? NOBODY_IN_PARTICULAR;

  return session.subscribe((event) => {
    if (event.type === 'signed-out') {
      holder = NOBODY_IN_PARTICULAR;
      client.clear();
      return;
    }
    if (event.user.user_id !== holder) {
      holder = event.user.user_id;
      client.clear();
    }
  });
}

/**
 * Who the cache is currently filed under.
 *
 * A screen builds its keys from this, so the moment the session changes, the
 * keys change too and nothing already fetched can be read by the next person.
 */
export function scopeOf(session: Session): string {
  return session.user?.user_id ?? NOBODY_IN_PARTICULAR;
}
