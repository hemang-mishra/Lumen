import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { bindCacheToSession, scopeOf } from '@/api/query';
import type { Session, SignOutReason, User } from '@/api/session';

/**
 * Who is signed in, as the rest of the app sees it.
 *
 * The session itself is a plain object that lives outside React, because
 * requests are made from places that are not components. This is the thin
 * layer that lets a component re-render when it changes, and the place where
 * the cache is tied to it: when somebody signs out, or a different person
 * signs in, everything fetched for the previous one is thrown away before
 * anything can read it.
 *
 * Why a session ended is kept, because "your session expired" and "we could
 * not reach the service" are different sentences and a person who has just
 * lost what they were reading deserves the right one.
 */

interface SessionContextValue {
  /** Who is signed in, or nobody. */
  user: User | null;
  /** Whether there is a session at all. */
  signedIn: boolean;
  /** Why the last session ended, if one did. */
  endedBecause: SignOutReason | null;
  /** What everything cached is filed under. */
  scope: string;
  /** The session itself, for the few places that need to act on it. */
  session: Session;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({
  session,
  children,
}: {
  session: Session;
  children: ReactNode;
}) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<User | null>(session.user);
  const [endedBecause, setEndedBecause] = useState<SignOutReason | null>(null);

  useEffect(() => bindCacheToSession(queryClient, session), [queryClient, session]);

  useEffect(
    () =>
      session.subscribe((event) => {
        if (event.type === 'signed-in') {
          setUser(event.user);
          setEndedBecause(null);
          return;
        }
        setUser(null);
        setEndedBecause(event.reason);
      }),
    [session],
  );

  const value = useMemo<SessionContextValue>(
    () => ({
      user,
      signedIn: user !== null,
      endedBecause,
      scope: scopeOf(session),
      session,
    }),
    [user, endedBecause, session],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

/**
 * The current session.
 *
 * Throws when used outside the provider rather than handing back an empty
 * one. A component that quietly believes nobody is signed in would show an
 * empty history instead of a person's own, which is a worse failure than a
 * crash during development.
 */
export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error('useSession was used outside a SessionProvider');
  return value;
}
