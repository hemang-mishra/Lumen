/**
 * Generated from the API description. Do not edit.
 *
 * Regenerate with: npm run types:generate
 */

/** Every message /chat/ws can send. */
export type ChatFrameKind =
  | 'audio.reply'
  | 'context.ready'
  | 'error'
  | 'reply.delta'
  | 'reply.done'
  | 'turn.accepted';

/** Every message /events/ws can send. */
export type ActivityEventKind =
  | 'job_failed'
  | 'job_ran'
  | 'run_finished'
  | 'run_started';
