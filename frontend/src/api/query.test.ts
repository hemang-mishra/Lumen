import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';
import { LumenError } from './errors';
import { bindCacheToSession, createQueryClient, keyFor, NOBODY_IN_PARTICULAR, scopeOf, worthRetrying } from './query';
import { Session, type SessionView, type User } from './session';

/**
 * Tests for the cache, and for whose it is.
 *
 * The last group is the important one. Two accounts on one browser may never
 * see each other's records — not after a refetch, and not for the fraction of
 * a second before one lands.
 */

function personCalled(id: string): User {
  return { user_id: id, email: `${id}@example.com`, display_name: id, status: 'ACTIVE' };
}

function sessionFor(id: string): SessionView {
  return { access_token: 't', expires_in: 900, token_type: 'Bearer', user: personCalled(id) };
}

describe('the query client', () => {
  it('is built with defaults rather than left bare', () => {
    const client = createQueryClient();

    expect(client.getDefaultOptions().queries?.staleTime).toBeGreaterThan(0);
  });
});

describe('deciding whether to try again', () => {
  it('gives up on a failure that will never resolve itself', () => {
    expect(worthRetrying(new LumenError('not_found', 'nothing there'))).toBe(false);
  });

  it('tries again when the service could not be reached', () => {
    expect(worthRetrying(new LumenError('network', 'unreachable'))).toBe(true);
  });

  it('tries once more for something thrown that is not ours', () => {
    expect(worthRetrying(new Error('who knows'))).toBe(true);
  });
});

describe('what a cached answer is filed under', () => {
  it('starts with whose data it is', () => {
    expect(keyFor('usr_1', 'episodes', 'ep_1')).toEqual(['usr_1', 'episodes', 'ep_1']);
  });

  it('is filed under nobody in particular when there is no sign-in', () => {
    const session = new Session(async () => sessionFor('usr_1'));

    expect(scopeOf(session)).toBe(NOBODY_IN_PARTICULAR);
  });

  it('is filed under whoever is signed in', () => {
    const session = new Session(async () => sessionFor('usr_1'));
    session.begin(sessionFor('usr_1'));

    expect(scopeOf(session)).toBe('usr_1');
  });
});

describe('tying the cache to the session', () => {
  it('empties everything when somebody signs out', () => {
    const cache = new QueryClient();
    const session = new Session(async () => sessionFor('usr_1'));
    session.begin(sessionFor('usr_1'));
    bindCacheToSession(cache, session);
    cache.setQueryData(['usr_1', 'episodes'], [{ id: 'ep_1' }]);

    session.end('requested');

    expect(cache.getQueryData(['usr_1', 'episodes'])).toBeUndefined();
  });

  it('empties everything when a different person signs in', () => {
    // The one that is easy to forget, and the one that would show a
    // stranger's journal on a shared laptop.
    const cache = new QueryClient();
    const session = new Session(async () => sessionFor('usr_1'));
    session.begin(sessionFor('usr_1'));
    bindCacheToSession(cache, session);
    cache.setQueryData(['usr_1', 'episodes'], [{ id: 'ep_1' }]);

    session.begin(sessionFor('usr_2'));

    expect(cache.getQueryData(['usr_1', 'episodes'])).toBeUndefined();
  });

  it('leaves the cache alone when the same person renews', () => {
    const cache = new QueryClient();
    const session = new Session(async () => sessionFor('usr_1'));
    session.begin(sessionFor('usr_1'));
    bindCacheToSession(cache, session);
    cache.setQueryData(['usr_1', 'episodes'], [{ id: 'ep_1' }]);

    session.begin(sessionFor('usr_1'));

    expect(cache.getQueryData(['usr_1', 'episodes'])).toBeDefined();
  });

  it('stops watching when asked to', () => {
    const cache = new QueryClient();
    const session = new Session(async () => sessionFor('usr_1'));
    const stop = bindCacheToSession(cache, session);
    cache.setQueryData(['local', 'episodes'], [{ id: 'ep_1' }]);

    stop();
    session.begin(sessionFor('usr_2'));

    expect(cache.getQueryData(['local', 'episodes'])).toBeDefined();
  });
});
