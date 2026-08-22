import { describe, expect, it } from 'vitest';
import { DEFAULT_BASE_URL, readSettings, withoutTrailingSlash } from './config';

/** Tests for the two settings the app is built with. */

describe('reading the settings', () => {
  it('uses the configured address', () => {
    expect(readSettings({ VITE_LUMEN_API_URL: 'https://lumen.example' }).baseUrl).toBe(
      'https://lumen.example',
    );
  });

  it('falls back to the service as it runs on a laptop', () => {
    expect(readSettings({}).baseUrl).toBe(DEFAULT_BASE_URL);
  });

  it('ignores an address that was set to nothing', () => {
    expect(readSettings({ VITE_LUMEN_API_URL: '' }).baseUrl).toBe(DEFAULT_BASE_URL);
  });

  it('does not let a base address and a path meet at a double slash', () => {
    expect(readSettings({ VITE_LUMEN_API_URL: 'https://lumen.example/' }).baseUrl).toBe(
      'https://lumen.example',
    );
    expect(withoutTrailingSlash('https://lumen.example///')).toBe('https://lumen.example');
  });

  it('treats sign-in as off unless it was switched on', () => {
    // A deployment that forgot to configure sign-in should open on somebody's
    // laptop, not lock everybody out of a service that cannot answer one.
    expect(readSettings({}).authEnabled).toBe(false);
    expect(readSettings({ VITE_AUTH_ENABLED: 'false' }).authEnabled).toBe(false);
    expect(readSettings({ VITE_AUTH_ENABLED: 'true' }).authEnabled).toBe(true);
    expect(readSettings({ VITE_AUTH_ENABLED: true }).authEnabled).toBe(true);
  });
});
