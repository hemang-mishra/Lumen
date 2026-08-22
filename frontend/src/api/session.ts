import type { components } from './schema';

/**
 * Holding a session, and renewing it exactly once when it expires.
 *
 * Three things here are security decisions rather than convenience.
 *
 * The short-lived token lives in a variable and nowhere else. Not in local
 * storage, not in a cookie a script can read — anything a script on the page
 * can reach is readable by any script that gets onto the page, and what it
 * would reach is somebody's private history.
 *
 * The renewable half is never touched by this code at all. It is a cookie the
 * browser will not show us; our only involvement is asking the service to
 * renew, with credentials, and reading back what it hands over.
 *
 * When a session expires, ten requests fail at the same moment. They all wait
 * on one renewal rather than starting ten of their own, and if that renewal
 * fails the person is signed out once rather than ten times.
 */

/** Somebody, as the service describes them. */
export type User = components['schemas']['UserView'];

/** What the service hands back when a session starts or is renewed. */
export type SessionView = components['schemas']['SessionView'];

/** Why a session ended, which is a different sentence in each case. */
export type SignOutReason =
  /** They asked to sign out. */
  | 'requested'
  /** It expired and could not be renewed. */
  | 'expired'
  /** The service could not be reached to find out. */
  | 'unreachable';

/** Something that happened to the session, for anyone who needs to react. */
export type SessionEvent =
  | { type: 'signed-in'; user: User }
  | { type: 'signed-out'; reason: SignOutReason };

/** Told when the session changes. */
export type SessionListener = (event: SessionEvent) => void;

/** How a session is renewed. Supplied from outside so this stays testable. */
export type RenewCall = () => Promise<SessionView>;

/**
 * The one place the current session lives.
 *
 * Deliberately not a React thing. Requests are made from places that are not
 * components, and a token that only existed inside the component tree would
 * have to be threaded through all of them.
 */
export class Session {
  private token: string | null = null;
  private who: User | null = null;
  private renewing: Promise<string | null> | null = null;
  private readonly listeners = new Set<SessionListener>();

  /**
   * @param renew How to ask the service for a new token. Injected rather
   *   than built here, because the thing that makes requests already needs
   *   this object and the two cannot each construct the other.
   */
  constructor(private readonly renew: RenewCall) {}

  /** The token to send with a request, if there is one. */
  get accessToken(): string | null {
    return this.token;
  }

  /** Who is signed in, if anybody. */
  get user(): User | null {
    return this.who;
  }

  /** Whether there is a session at all. */
  get isSignedIn(): boolean {
    return this.token !== null;
  }

  /** Start a session from what the service handed back. */
  begin(view: SessionView): void {
    this.token = view.access_token;
    this.who = view.user;
    this.announce({ type: 'signed-in', user: view.user });
  }

  /**
   * End the session and forget everything about it.
   *
   * Silent when there was nothing to end, so that ten requests discovering
   * an expired session at the same moment produce one announcement between
   * them rather than ten.
   */
  end(reason: SignOutReason): void {
    if (this.token === null && this.who === null) return;
    this.token = null;
    this.who = null;
    this.announce({ type: 'signed-out', reason });
  }

  /**
   * Renew the session, or join the renewal already under way.
   *
   * Returns the new token, or nothing at all if the session is really over.
   * Everybody who asks while one renewal is in flight gets that same
   * renewal's answer.
   */
  async renewOnce(): Promise<string | null> {
    if (this.renewing) return this.renewing;

    this.renewing = this.attemptRenewal().finally(() => {
      this.renewing = null;
    });
    return this.renewing;
  }

  /** Whether a renewal is happening right now. */
  get isRenewing(): boolean {
    return this.renewing !== null;
  }

  /** Be told when the session starts or ends. Returns how to stop being told. */
  subscribe(listener: SessionListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private async attemptRenewal(): Promise<string | null> {
    try {
      const view = await this.renew();
      this.token = view.access_token;
      this.who = view.user;
      return this.token;
    } catch (failure) {
      // A renewal that fails because the service is down is a different
      // sentence from one that fails because the session is genuinely over,
      // and the person is about to be shown one of them.
      this.end(reasonFor(failure));
      return null;
    }
  }

  private announce(event: SessionEvent): void {
    // A listener that throws must not take the session with it. Whoever is
    // listening is reacting to the news, not part of delivering it.
    for (const listener of [...this.listeners]) {
      try {
        listener(event);
      } catch {
        // Deliberately ignored: see above.
      }
    }
  }
}

/** Why a failed renewal ended the session. */
function reasonFor(failure: unknown): SignOutReason {
  const kind = (failure as { kind?: string } | null)?.kind;
  return kind === 'network' || kind === 'unavailable' ? 'unreachable' : 'expired';
}
