import { describe, expect, it } from 'vitest';
import { isLumenError, LumenError, toLumenError, toNetworkError } from './errors';

/**
 * Tests for reading what the service said went wrong.
 *
 * The service keeps four kinds of failure apart on purpose, and the whole
 * value of this layer is that a screen can still tell them apart afterwards.
 * Most of these tests are about not flattening something.
 */

describe('reading a failure', () => {
  it('keeps a missing record and a refused request apart', () => {
    const missing = toLumenError(404, { error: 'not_found', detail: 'no episode with id ep_1' });
    const refused = toLumenError(400, { error: 'bad_request', detail: 'that is not an export' });

    expect(missing.kind).toBe('not_found');
    expect(refused.kind).toBe('bad_request');
  });

  it('carries which record was missing, so a screen can say which', () => {
    const failure = toLumenError(404, {
      error: 'not_found',
      detail: 'no episode with id ep_1',
      kind: 'episode',
      id: 'ep_1',
    });

    expect(failure.missingKind).toBe('episode');
    expect(failure.missingId).toBe('ep_1');
  });

  it('carries which part of the system is not running', () => {
    const failure = toLumenError(503, {
      error: 'unavailable',
      detail: 'listening is unavailable: no model configured',
      what: 'listening',
    });

    expect(failure.kind).toBe('unavailable');
    expect(failure.unavailablePart).toBe('listening');
  });

  it('repeats the service’s own words rather than inventing a sentence', () => {
    const failure = toLumenError(400, { error: 'bad_request', detail: 'that is not an export' });

    expect(failure.message).toBe('that is not an export');
  });

  it('has something to say when the body has nothing in it', () => {
    const failure = toLumenError(500, null);

    expect(failure.kind).toBe('server');
    expect(failure.message).toContain('500');
  });

  it('falls back to the status when the body is not ours', () => {
    // Something in front of the service answering with HTML, for instance.
    const failure = toLumenError(502, '<html>Bad Gateway</html>');

    expect(failure.kind).toBe('server');
    expect(failure.status).toBe(502);
  });

  it('reads an unnamed refusal from its status', () => {
    expect(toLumenError(401, {}).kind).toBe('not_authenticated');
    expect(toLumenError(403, {}).kind).toBe('forbidden');
    expect(toLumenError(409, {}).kind).toBe('conflict');
    expect(toLumenError(422, {}).kind).toBe('bad_request');
    expect(toLumenError(429, {}).kind).toBe('too_many_attempts');
  });

  it('trusts the name the service gave over the status it used', () => {
    const failure = toLumenError(400, { error: 'conflict', detail: 'already adopted' });

    expect(failure.kind).toBe('conflict');
  });
});

describe('never reaching the service at all', () => {
  it('is a different kind of failure from being refused', () => {
    const failure = toNetworkError(new TypeError('Failed to fetch'));

    expect(failure.kind).toBe('network');
    expect(failure.status).toBe(0);
    expect(failure.message).toContain('Failed to fetch');
  });

  it('copes with something that is not an error being thrown', () => {
    expect(toNetworkError('gone').message).toContain('gone');
  });
});

describe('deciding whether to try again', () => {
  it('tries again for things that might resolve themselves', () => {
    expect(new LumenError('network', 'x').worthRetrying).toBe(true);
    expect(new LumenError('unavailable', 'x').worthRetrying).toBe(true);
    expect(new LumenError('server', 'x').worthRetrying).toBe(true);
  });

  it('does not for things that will fail identically forever', () => {
    expect(new LumenError('not_found', 'x').worthRetrying).toBe(false);
    expect(new LumenError('bad_request', 'x').worthRetrying).toBe(false);
    expect(new LumenError('forbidden', 'x').worthRetrying).toBe(false);
  });
});

describe('recognising one of ours', () => {
  it('says yes to a Lumen failure and no to anything else', () => {
    expect(isLumenError(new LumenError('server', 'x'))).toBe(true);
    expect(isLumenError(new Error('x'))).toBe(false);
    expect(isLumenError('x')).toBe(false);
  });
});
