import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';
import type { ReactNode } from 'react';
import { Session, type SessionView, type User } from '@/api/session';
import { SessionProvider, useSession } from './SessionProvider';

/**
 * Tests for how the rest of the app sees the session.
 *
 * The two that matter are the cache being emptied when the person changes,
 * and the reason a session ended surviving — those are the two failures a
 * person would actually notice, and one of them is somebody else's journal.
 */

function personCalled(id: string): User {
  return { user_id: id, email: `${id}@example.com`, display_name: id, status: 'ACTIVE' };
}

function sessionFor(id: string): SessionView {
  return { access_token: 't', expires_in: 900, token_type: 'Bearer', user: personCalled(id) };
}

function harness(session: Session) {
  const cache = new QueryClient();
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={cache}>
        <SessionProvider session={session}>{children}</SessionProvider>
      </QueryClientProvider>
    );
  }
  return { cache, Wrapper };
}

describe('the session, as the app sees it', () => {
  it('starts with nobody signed in', () => {
    const session = new Session(async () => sessionFor('usr_1'));
    const { Wrapper } = harness(session);

    const { result } = renderHook(() => useSession(), { wrapper: Wrapper });

    expect(result.current.signedIn).toBe(false);
    expect(result.current.scope).toBe('local');
  });

  it('follows somebody signing in', async () => {
    const session = new Session(async () => sessionFor('usr_1'));
    const { Wrapper } = harness(session);
    const { result } = renderHook(() => useSession(), { wrapper: Wrapper });

    act(() => session.begin(sessionFor('usr_1')));

    await waitFor(() => expect(result.current.user?.user_id).toBe('usr_1'));
  });

  it('remembers why a session ended', async () => {
    // "Your session expired" and "we could not reach the service" are
    // different sentences, and the person is about to be shown one of them.
    const session = new Session(async () => sessionFor('usr_1'));
    const { Wrapper } = harness(session);
    const { result } = renderHook(() => useSession(), { wrapper: Wrapper });
    act(() => session.begin(sessionFor('usr_1')));

    act(() => session.end('unreachable'));

    await waitFor(() => expect(result.current.endedBecause).toBe('unreachable'));
  });

  it('empties the cache when a different person signs in', async () => {
    const session = new Session(async () => sessionFor('usr_1'));
    const { cache, Wrapper } = harness(session);
    renderHook(() => useSession(), { wrapper: Wrapper });
    act(() => session.begin(sessionFor('usr_1')));
    cache.setQueryData(['usr_1', 'episodes'], [{ id: 'ep_1' }]);

    act(() => session.begin(sessionFor('usr_2')));

    await waitFor(() => expect(cache.getQueryData(['usr_1', 'episodes'])).toBeUndefined());
  });

  it('refuses to be used outside the provider', () => {
    // A component quietly believing nobody is signed in would show an empty
    // history instead of a person's own, which is worse than a crash.
    expect(() => renderHook(() => useSession())).toThrow(/outside a SessionProvider/);
  });
});
