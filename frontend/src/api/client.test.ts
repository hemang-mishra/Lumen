import { describe, expect, it, vi } from 'vitest';
import { createLumenClient, unwrap } from './client';
import { LumenError } from './errors';
import type { SessionView, User } from './session';

/**
 * Tests for the one way this app talks to the service.
 *
 * Requests are made against a stand-in rather than a real service, so what is
 * being checked is what the client *sends* and how it reacts — which is
 * precisely where the interesting rules live: what carries the session
 * cookie, what carries the token, and what happens to ten requests that all
 * fail at once because a session expired.
 */

const SOMEBODY: User = {
  user_id: 'usr_1',
  email: 'somebody@example.com',
  display_name: 'Somebody',
  status: 'ACTIVE',
};

const A_SESSION: SessionView = {
  access_token: 'renewed',
  expires_in: 900,
  token_type: 'Bearer',
  user: SOMEBODY,
};

const BASE = 'http://service.test';

/** A stand-in service that answers whatever a test tells it to. */
function serviceThat(answers: (request: Request) => Response | Promise<Response>) {
  const seen: Request[] = [];
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init);
    seen.push(request);
    return answers(request);
  }) as unknown as typeof globalThis.fetch;
  return { fetch, seen };
}

/** Run something that is expected to fail, and hand back what it threw. */
async function failureOf(call: Promise<unknown>): Promise<LumenError> {
  try {
    await call;
  } catch (thrown) {
    return thrown as LumenError;
  }
  throw new Error('this was expected to fail and did not');
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('reading an answer', () => {
  it('hands back the body when the service is happy', async () => {
    const { fetch } = serviceThat(() => json({ status: 'ok' }));
    const { api } = createLumenClient({ baseUrl: BASE, authEnabled: false, fetch });

    const health = await unwrap(api.GET('/health', {}));

    expect(health).toEqual({ status: 'ok' });
  });

  it('throws what the service said, not a status code', async () => {
    const { fetch } = serviceThat(() =>
      json({ error: 'not_found', detail: 'no episode with id ep_1', kind: 'episode', id: 'ep_1' }, 404),
    );
    const { api } = createLumenClient({ baseUrl: BASE, authEnabled: false, fetch });

    const failure = await failureOf(
      unwrap(
        api.GET('/graph/episodes/{episode_id}', { params: { path: { episode_id: 'ep_1' } } }),
      ),
    );

    expect(failure).toBeInstanceOf(LumenError);
    expect(failure.kind).toBe('not_found');
    expect(failure.missingId).toBe('ep_1');
  });

  it('says the service could not be reached when it never answered', async () => {
    const fetch = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    }) as unknown as typeof globalThis.fetch;
    const { api } = createLumenClient({ baseUrl: BASE, authEnabled: false, fetch });

    const failure = await failureOf(unwrap(api.GET('/health', {})));

    expect(failure.kind).toBe('network');
  });
});

describe('what carries the session cookie', () => {
  it('sends it when renewing, because that is what the cookie is for', async () => {
    const { fetch, seen } = serviceThat(() => json(A_SESSION));
    const { api } = createLumenClient({ baseUrl: BASE, authEnabled: true, fetch });

    await unwrap(api.POST('/auth/refresh', {}));

    expect(seen[0]?.credentials).toBe('include');
  });

  it('does not send it with an ordinary read', async () => {
    // The longest-lived credential in the system has no business being
    // attached to a request for a list of episodes.
    const { fetch, seen } = serviceThat(() => json({ status: 'ok' }));
    const { api } = createLumenClient({ baseUrl: BASE, authEnabled: true, fetch });

    await unwrap(api.GET('/health', {}));

    expect(seen[0]?.credentials).not.toBe('include');
  });
});

describe('carrying the token', () => {
  it('puts it on every request once somebody is signed in', async () => {
    const { fetch, seen } = serviceThat(() => json({ status: 'ok' }));
    const client = createLumenClient({ baseUrl: BASE, authEnabled: true, fetch });
    client.session.begin({ ...A_SESSION, access_token: 'first' });

    await unwrap(client.api.GET('/health', {}));

    expect(seen[0]?.headers.get('Authorization')).toBe('Bearer first');
  });

  it('sends nothing when nobody is signed in', async () => {
    const { fetch, seen } = serviceThat(() => json({ status: 'ok' }));
    const client = createLumenClient({ baseUrl: BASE, authEnabled: true, fetch });

    await unwrap(client.api.GET('/health', {}));

    expect(seen[0]?.headers.get('Authorization')).toBeNull();
  });

  it('adds nothing at all when this deployment has no sign-in', async () => {
    // With sign-in switched off the whole mechanism is inert, which is what
    // makes that a real supported mode rather than dead code.
    const { fetch, seen } = serviceThat(() => json({ status: 'ok' }));
    const client = createLumenClient({ baseUrl: BASE, authEnabled: false, fetch });
    client.session.begin(A_SESSION);

    await unwrap(client.api.GET('/health', {}));

    expect(seen[0]?.headers.get('Authorization')).toBeNull();
  });
});

describe('a session that expires mid-use', () => {
  /** A service that refuses the old token, renews once, and accepts the new one. */
  function serviceThatExpires() {
    let renewals = 0;
    const { fetch, seen } = serviceThat((request) => {
      const path = new URL(request.url).pathname;
      if (path === '/auth/refresh') {
        renewals += 1;
        return json(A_SESSION);
      }
      if (request.headers.get('Authorization') === 'Bearer renewed') {
        return json({ status: 'ok' });
      }
      return json({ error: 'not_authenticated', detail: 'this session has ended' }, 401);
    });
    return { fetch, seen, renewals: () => renewals };
  }

  it('renews once and tries the request again', async () => {
    const { fetch, renewals } = serviceThatExpires();
    const client = createLumenClient({ baseUrl: BASE, authEnabled: true, fetch });
    client.session.begin({ ...A_SESSION, access_token: 'expired' });

    const health = await unwrap(client.api.GET('/health', {}));

    expect(health).toEqual({ status: 'ok' });
    expect(renewals()).toBe(1);
  });

  it('renews once for ten requests that fail together', async () => {
    // The ordinary case, not an edge case: a token expires while a screen
    // has several requests in the air.
    const { fetch, renewals } = serviceThatExpires();
    const client = createLumenClient({ baseUrl: BASE, authEnabled: true, fetch });
    client.session.begin({ ...A_SESSION, access_token: 'expired' });

    const answers = await Promise.all(
      Array.from({ length: 10 }, () => unwrap(client.api.GET('/health', {}))),
    );

    expect(answers).toHaveLength(10);
    expect(renewals()).toBe(1);
  });

  it('gives up after one retry rather than looping', async () => {
    // A service that refuses even the fresh token must not produce an
    // endless exchange of renewals.
    let renewals = 0;
    const { fetch } = serviceThat((request) => {
      if (new URL(request.url).pathname === '/auth/refresh') {
        renewals += 1;
        return json(A_SESSION);
      }
      return json({ error: 'not_authenticated', detail: 'no' }, 401);
    });
    const client = createLumenClient({ baseUrl: BASE, authEnabled: true, fetch });
    client.session.begin({ ...A_SESSION, access_token: 'expired' });

    const failure = await failureOf(unwrap(client.api.GET('/health', {})));

    expect(failure.kind).toBe('not_authenticated');
    expect(renewals).toBe(1);
  });

  it('ends the session once when the renewal itself is refused', async () => {
    const heard: string[] = [];
    const { fetch } = serviceThat((request) =>
      new URL(request.url).pathname === '/auth/refresh'
        ? json({ error: 'not_authenticated', detail: 'the session is over' }, 401)
        : json({ error: 'not_authenticated', detail: 'no' }, 401),
    );
    const client = createLumenClient({ baseUrl: BASE, authEnabled: true, fetch });
    client.session.subscribe((event) => heard.push(event.type));
    client.session.begin({ ...A_SESSION, access_token: 'expired' });

    await Promise.all(
      Array.from({ length: 5 }, () =>
        unwrap(client.api.GET('/health', {})).catch(() => null),
      ),
    );

    expect(heard).toEqual(['signed-in', 'signed-out']);
  });

  it('does not try to renew a refused sign-in', async () => {
    // A sign-in that Google turned away is refused, not expired, and asking
    // for a renewal in the middle of one would be an endless loop.
    let renewals = 0;
    const { fetch } = serviceThat((request) => {
      if (new URL(request.url).pathname === '/auth/refresh') renewals += 1;
      return json({ error: 'not_authenticated', detail: 'no' }, 401);
    });
    const client = createLumenClient({ baseUrl: BASE, authEnabled: true, fetch });

    await unwrap(client.api.GET('/auth/me', {})).catch(() => null);

    expect(renewals).toBe(0);
  });

  it('leaves a body-carrying request repeatable', async () => {
    // A request that has been sent has had its body read, so the retry has
    // to be made from a copy taken beforehand.
    const bodies: string[] = [];
    const { fetch } = serviceThat(async (request) => {
      const path = new URL(request.url).pathname;
      if (path === '/auth/refresh') return json(A_SESSION);
      bodies.push(await request.text());
      if (request.headers.get('Authorization') === 'Bearer renewed') {
        return json({ signal: 'ok' }, 200);
      }
      return json({ error: 'not_authenticated', detail: 'expired' }, 401);
    });
    const client = createLumenClient({ baseUrl: BASE, authEnabled: true, fetch });
    client.session.begin({ ...A_SESSION, access_token: 'expired' });

    await unwrap(
      client.api.POST('/query/formulate', { body: { text: 'what did I say about this' } }),
    ).catch(() => null);

    expect(bodies).toHaveLength(2);
    expect(bodies[0]).toBe(bodies[1]);
  });
});
