/**
 * Where the service is, and whether it has sign-in switched on.
 *
 * Both are settled when the app is built rather than looked up when it
 * starts. A lookup would buy one build for every environment at the cost of a
 * request before anything can be drawn, which is the opposite of what a phone
 * opening this needs.
 */

/** The settings the app was built with, in the shape the client wants them. */
export interface ApiSettings {
  /** The address of the Python service, with no trailing slash. */
  baseUrl: string;
  /** Whether this deployment expects people to sign in. */
  authEnabled: boolean;
}

/** Used when nothing is configured: the service as it runs on a laptop. */
export const DEFAULT_BASE_URL = 'http://localhost:8000';

/**
 * Read the settings out of whatever the build put them in.
 *
 * Takes the environment as an argument so a test can hand in its own instead
 * of rebuilding the app to check a different one.
 */
export function readSettings(env: Record<string, unknown>): ApiSettings {
  const configured = typeof env.VITE_LUMEN_API_URL === 'string' ? env.VITE_LUMEN_API_URL : '';
  return {
    baseUrl: withoutTrailingSlash(configured || DEFAULT_BASE_URL),
    // Off unless switched on. A deployment that forgot to configure sign-in
    // should open unprotected on somebody's laptop, not lock everybody out
    // of a service that cannot answer a sign-in yet.
    authEnabled: env.VITE_AUTH_ENABLED === 'true' || env.VITE_AUTH_ENABLED === true,
  };
}

/** A base address and a path should not meet at a double slash. */
export function withoutTrailingSlash(url: string): string {
  return url.replace(/\/+$/, '');
}

/** The settings this build is actually running with. */
export const settings: ApiSettings = readSettings(import.meta.env as Record<string, unknown>);
