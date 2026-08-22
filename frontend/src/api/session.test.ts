import { describe, expect, it, vi } from 'vitest';
import { LumenError } from './errors';
import { Session, type SessionView, type User } from './session';

/**
 * Tests for holding a session and renewing it exactly once.
 *
 * The single-flight behaviour is the one worth the most here. Ten requests
 * failing together at the moment a token expires is the ordinary case, not an
 * edge case, and ten renewals would mean nine wasted round trips and a race
 * over which answer wins.
 */

const SOMEBODY: User = {
  user_id: 'usr_1',
  email: 'somebody@example.com',
  display_name: 'Somebody',
  status: 'ACTIVE',
};

function sessionView(token: string, user: User = SOMEBODY): SessionView {
  return { access_token: token, expires_in: 900, token_type: 'Bearer', user };
}

describe('holding a session', () => {
  it('starts with nobody signed in', () => {
    const session = new Session(async () => sessionView('t'));

    expect(session.isSignedIn).toBe(false);
    expect(session.accessToken).toBeNull();
    expect(session.user).toBeNull();
  });

  it('remembers the token and who it belongs to', () => {
    const session = new Session(async () => sessionView('t'));

    session.begin(sessionView('first'));

    expect(session.accessToken).toBe('first');
    expect(session.user?.email).toBe('somebody@example.com');
  });

  it('forgets everything when the session ends', () => {
    const session = new Session(async () => sessionView('t'));
    session.begin(sessionView('first'));

    session.end('requested');

    expect(session.accessToken).toBeNull();
    expect(session.user).toBeNull();
  });
});

describe('telling anybody who is listening', () => {
  it('announces a sign-in and a sign-out', () => {
    const session = new Session(async () => sessionView('t'));
    const heard: string[] = [];
    session.subscribe((event) => heard.push(event.type));

    session.begin(sessionView('first'));
    session.end('requested');

    expect(heard).toEqual(['signed-in', 'signed-out']);
  });

  it('says why the session ended, because those are different sentences', () => {
    const session = new Session(async () => sessionView('t'));
    const reasons: string[] = [];
    session.subscribe((event) => {
      if (event.type === 'signed-out') reasons.push(event.reason);
    });
    session.begin(sessionView('first'));

    session.end('expired');

    expect(reasons).toEqual(['expired']);
  });

  it('says nothing when there was no session to end', () => {
    // Ten requests all discovering an expired session at once must produce
    // one announcement between them, not ten sign-out messages.
    const session = new Session(async () => sessionView('t'));
    const heard: string[] = [];
    session.subscribe((event) => heard.push(event.type));

    session.end('expired');
    session.end('expired');

    expect(heard).toEqual([]);
  });

  it('stops telling somebody who has stopped listening', () => {
    const session = new Session(async () => sessionView('t'));
    const heard: string[] = [];
    const stop = session.subscribe((event) => heard.push(event.type));

    stop();
    session.begin(sessionView('first'));

    expect(heard).toEqual([]);
  });

  it('survives a listener that throws', () => {
    // Whoever is listening is reacting to the news, not part of delivering it.
    const session = new Session(async () => sessionView('t'));
    const heard: string[] = [];
    session.subscribe(() => {
      throw new Error('a screen blew up');
    });
    session.subscribe((event) => heard.push(event.type));

    expect(() => session.begin(sessionView('first'))).not.toThrow();
    expect(heard).toEqual(['signed-in']);
  });
});

describe('renewing', () => {
  it('replaces the token with the new one', async () => {
    const session = new Session(async () => sessionView('second'));
    session.begin(sessionView('first'));

    const token = await session.renewOnce();

    expect(token).toBe('second');
    expect(session.accessToken).toBe('second');
  });

  it('renews once however many callers ask at the same moment', async () => {
    const renew = vi.fn(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5));
      return sessionView('second');
    });
    const session = new Session(renew);
    session.begin(sessionView('first'));

    const answers = await Promise.all(
      Array.from({ length: 10 }, () => session.renewOnce()),
    );

    expect(renew).toHaveBeenCalledTimes(1);
    expect(answers).toEqual(Array.from({ length: 10 }, () => 'second'));
  });

  it('is willing to renew again after one has finished', async () => {
    const renew = vi.fn(async () => sessionView('again'));
    const session = new Session(renew);
    session.begin(sessionView('first'));

    await session.renewOnce();
    await session.renewOnce();

    expect(renew).toHaveBeenCalledTimes(2);
  });

  it('ends the session once when the renewal is refused', async () => {
    const heard: string[] = [];
    const session = new Session(async () => {
      throw new LumenError('not_authenticated', 'this session has ended');
    });
    session.subscribe((event) => heard.push(event.type));
    session.begin(sessionView('first'));

    const answers = await Promise.all([session.renewOnce(), session.renewOnce()]);

    expect(answers).toEqual([null, null]);
    expect(heard).toEqual(['signed-in', 'signed-out']);
  });

  it('says the service was unreachable rather than that the session expired', async () => {
    // A person who has just lost what they were reading deserves to know
    // which of the two happened.
    const reasons: string[] = [];
    const session = new Session(async () => {
      throw new LumenError('network', 'the service could not be reached');
    });
    session.subscribe((event) => {
      if (event.type === 'signed-out') reasons.push(event.reason);
    });
    session.begin(sessionView('first'));

    await session.renewOnce();

    expect(reasons).toEqual(['unreachable']);
  });

  it('reports whether one is happening right now', async () => {
    const session = new Session(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5));
      return sessionView('second');
    });

    const pending = session.renewOnce();
    expect(session.isRenewing).toBe(true);

    await pending;
    expect(session.isRenewing).toBe(false);
  });
});
