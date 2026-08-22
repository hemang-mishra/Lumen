import createClient, { type Client, type Middleware } from 'openapi-fetch';
import type { paths } from './schema';
import { settings, type ApiSettings } from './config';
import { toLumenError, toNetworkError, LumenError } from './errors';
import { Session, type SessionView } from './session';

/**
 * The one way this app talks to the Python service.
 *
 * Everything a screen calls goes through here, which is what makes the three
 * things below true everywhere rather than in the places somebody remembered.
 *
 * Every request carries the current token. Requests to sign in, renew or sign
 * out are the only ones sent with the session cookie, because that cookie is
 * the credential that outlives everything else and it has no business being
 * attached to a request for a list of episodes.
 *
 * A request refused because the session expired renews once and is tried
 * again, and everything else that failed at the same moment waits on that one
 * renewal.
 */

/** The path prefix that owns sessions, and the only one sent credentials. */
const AUTH_PREFIX = '/auth';

/** Where a session is renewed. */
const REFRESH_PATH = '/auth/refresh';

/** Marks a request that has already been retried, so it cannot loop. */
const RETRIED_HEADER = 'x-lumen-retried';

/** How a client is put together. */
export interface LumenClientOptions extends ApiSettings {
  /** How requests are actually made. Replaced wholesale in tests. */
  fetch?: typeof globalThis.fetch;
}

/** A configured client and the session it carries. */
export interface LumenClient {
  /** The typed endpoints, generated from the service's own description. */
  api: Client<paths>;
  /** The session those requests are made under. */
  session: Session;
}

/**
 * Build a client.
 *
 * A factory rather than a single shared object, because tests need their own
 * and because two clients pointed at two services is a perfectly reasonable
 * thing to want one day.
 */
export function createLumenClient(options: LumenClientOptions): LumenClient {
  const doFetch = options.fetch ?? globalThis.fetch.bind(globalThis);
  const session = new Session(() => renew(options.baseUrl, doFetch));
  const api = createClient<paths>({ baseUrl: options.baseUrl, fetch: doFetch });

  api.use(credentialsForSessionRequests());
  if (options.authEnabled) {
    api.use(carriesTheToken(session));
    api.use(renewsAnExpiredSession(session, doFetch));
  }

  return { api, session };
}

/**
 * Read the answer to a request, or throw what went wrong.
 *
 * The generated client reports a failure as a value rather than throwing,
 * which is precise but leaves every caller to remember to check. Screens ask
 * for data through this instead, so a failure arrives as an exception the
 * data layer already knows how to handle, carrying what the service actually
 * said rather than a status code.
 */
export async function unwrap<D>(
  call: Promise<{ data?: D; error?: unknown; response: Response }>,
): Promise<D> {
  let result: { data?: D; error?: unknown; response: Response };
  try {
    result = await call;
  } catch (failure) {
    // Never reached the service at all: no answer to read, and a different
    // thing to tell somebody than a refusal.
    if (failure instanceof LumenError) throw failure;
    throw toNetworkError(failure);
  }

  if (result.error !== undefined || !result.response.ok) {
    throw toLumenError(result.response.status, result.error);
  }
  return result.data as D;
}

/**
 * Send the session cookie, but only where it belongs.
 *
 * The service is on another address, so a cookie is not sent cross-origin
 * unless the request asks for it. Asking on every request would attach the
 * longest-lived credential in the system to every list and every search.
 */
function credentialsForSessionRequests(): Middleware {
  return {
    onRequest({ request }) {
      if (!pathOf(request.url).startsWith(AUTH_PREFIX)) return undefined;
      return new Request(request, { credentials: 'include' });
    },
  };
}

/** Put the current token on every request that has one to put. */
function carriesTheToken(session: Session): Middleware {
  return {
    onRequest({ request }) {
      const token = session.accessToken;
      if (!token) return undefined;
      request.headers.set('Authorization', `Bearer ${token}`);
      return request;
    },
  };
}

/**
 * Renew once when the service says the session has expired, and try again.
 *
 * The copy of the request is taken before it is sent, because a request that
 * has been sent has had its body read and cannot be sent a second time.
 */
function renewsAnExpiredSession(session: Session, doFetch: typeof globalThis.fetch): Middleware {
  const unsent = new Map<string, Request>();

  return {
    onRequest({ request, id }) {
      unsent.set(id, request.clone());
      return undefined;
    },

    onError({ id }) {
      unsent.delete(id);
      return undefined;
    },

    async onResponse({ request, response, id }) {
      const original = unsent.get(id);
      unsent.delete(id);

      if (response.status !== 401) return undefined;
      // Renewing is itself a request, and a renewal refused must be allowed
      // to be refused rather than start another renewal.
      if (pathOf(request.url).startsWith(AUTH_PREFIX)) return undefined;
      // One retry. A second refusal is a real refusal.
      if (request.headers.get(RETRIED_HEADER) || !original) return undefined;

      const token = await session.renewOnce();
      if (!token) return undefined;

      const retry = new Request(original, {});
      retry.headers.set('Authorization', `Bearer ${token}`);
      retry.headers.set(RETRIED_HEADER, '1');
      return doFetch(retry);
    },
  };
}

/** Ask the service for a new token, using the cookie it will not show us. */
async function renew(baseUrl: string, doFetch: typeof globalThis.fetch): Promise<SessionView> {
  let response: Response;
  try {
    response = await doFetch(`${baseUrl}${REFRESH_PATH}`, {
      method: 'POST',
      credentials: 'include',
      headers: { Accept: 'application/json' },
    });
  } catch (failure) {
    throw toNetworkError(failure);
  }

  if (!response.ok) {
    throw toLumenError(response.status, await readBody(response));
  }
  return (await response.json()) as SessionView;
}

/** Whatever the service put in a failed answer, if anything readable. */
async function readBody(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

/** The path part of a URL, without the host it is on. */
function pathOf(url: string): string {
  try {
    return new URL(url).pathname;
  } catch {
    return url;
  }
}

/** The client this app runs with. */
export const lumen: LumenClient = createLumenClient(settings);
