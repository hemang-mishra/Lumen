/**
 * Turning what the service says went wrong into something a screen can say.
 *
 * The service is careful about this: a missing record, a request it cannot
 * work with, a store that is not running and an expired session are four
 * different answers, and it returns them as four different shapes. Collapsing
 * them into a status code here would throw that away, and every screen would
 * end up printing "something went wrong".
 *
 * A failure to reach the service at all is included as a kind of its own,
 * because "we could not reach it" and "it answered, and the answer was no"
 * are different sentences and a person deserves the right one.
 */

/** The kinds of failure a screen can meaningfully tell apart. */
export type FailureKind =
  | 'bad_request'
  | 'not_authenticated'
  | 'forbidden'
  | 'not_found'
  | 'conflict'
  | 'too_many_attempts'
  | 'unavailable'
  | 'server'
  | 'network';

/** The envelope the service puts a failure in. */
interface Envelope {
  error?: unknown;
  detail?: unknown;
  /** On a missing record: what kind of thing was looked for, and by what name. */
  kind?: unknown;
  id?: unknown;
  /** On something not running: which part of the system it was. */
  what?: unknown;
}

/**
 * Something the service refused or could not do.
 *
 * Carries the pieces a screen might want to show — what kind of failure it
 * was, which record was missing, which store is down — rather than only a
 * message, so a screen can decide how to say it rather than repeat a sentence
 * written for a developer.
 */
export class LumenError extends Error {
  /** Which kind of failure this is. Screens switch on this. */
  readonly kind: FailureKind;
  /** The status the service answered with; zero when it never answered. */
  readonly status: number;
  /** What kind of record was missing, when that is what happened. */
  readonly missingKind?: string;
  /** Which record was missing, when that is what happened. */
  readonly missingId?: string;
  /** Which part of the system is not running, when that is what happened. */
  readonly unavailablePart?: string;

  constructor(
    kind: FailureKind,
    message: string,
    details: { status?: number; missingKind?: string; missingId?: string; unavailablePart?: string } = {},
  ) {
    super(message);
    this.name = 'LumenError';
    this.kind = kind;
    this.status = details.status ?? 0;
    this.missingKind = details.missingKind;
    this.missingId = details.missingId;
    this.unavailablePart = details.unavailablePart;
  }

  /** Whether trying the same thing again could plausibly work. */
  get worthRetrying(): boolean {
    return this.kind === 'network' || this.kind === 'unavailable' || this.kind === 'server';
  }
}

/**
 * Read a failed response into something a screen can use.
 *
 * The status decides the kind, not the body, because a body can be missing,
 * be HTML from something in front of the service, or be half a sentence from
 * a connection that dropped. The body is only ever used to make the message
 * better.
 */
export function toLumenError(status: number, body: unknown): LumenError {
  const envelope: Envelope = isObject(body) ? body : {};
  const detail = asText(envelope.detail);
  const kind = kindFor(status, asText(envelope.error));

  return new LumenError(kind, detail || defaultMessage(kind, status), {
    status,
    missingKind: asText(envelope.kind) || undefined,
    missingId: asText(envelope.id) || undefined,
    unavailablePart: asText(envelope.what) || undefined,
  });
}

/** A request that never got an answer at all. */
export function toNetworkError(cause: unknown): LumenError {
  const because = cause instanceof Error ? cause.message : String(cause);
  return new LumenError('network', `the service could not be reached: ${because}`);
}

/** Whether something thrown is one of ours. */
export function isLumenError(value: unknown): value is LumenError {
  return value instanceof LumenError;
}

function kindFor(status: number, named: string): FailureKind {
  // The service names its own failures, and its name is more precise than
  // the status: 400 covers both "this file is not an export" and other
  // things a caller can fix differently.
  const byName: Record<string, FailureKind> = {
    bad_request: 'bad_request',
    not_authenticated: 'not_authenticated',
    forbidden: 'forbidden',
    not_found: 'not_found',
    conflict: 'conflict',
    too_many_attempts: 'too_many_attempts',
    unavailable: 'unavailable',
  };
  const claimed = byName[named];
  if (claimed) return claimed;

  if (status === 400 || status === 422) return 'bad_request';
  if (status === 401) return 'not_authenticated';
  if (status === 403) return 'forbidden';
  if (status === 404) return 'not_found';
  if (status === 409) return 'conflict';
  if (status === 429) return 'too_many_attempts';
  if (status === 503) return 'unavailable';
  return 'server';
}

function defaultMessage(kind: FailureKind, status: number): string {
  const messages: Record<FailureKind, string> = {
    bad_request: 'the service could not work with that request',
    not_authenticated: 'this session has ended',
    forbidden: 'this account is not allowed to do that',
    not_found: 'there is nothing by that name',
    conflict: 'something else changed this first',
    too_many_attempts: 'that was tried too many times in a row',
    unavailable: 'part of the service is not running',
    server: `the service failed unexpectedly (${status})`,
    network: 'the service could not be reached',
  };
  return messages[kind];
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value : '';
}
