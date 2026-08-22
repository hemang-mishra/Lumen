import { describe, expect, it } from 'vitest';
import { resolveDensity } from './density';

/**
 * Tests for the one rule that decides how tightly a surface is packed.
 *
 * The third one is the point of the whole thing: a phone showing a run trace
 * is still a phone, and a 32px target cannot be hit with a thumb whatever is
 * being displayed.
 */

describe('choosing a density', () => {
  it('keeps writing surfaces comfortable on a desktop', () => {
    expect(resolveDensity('reflect', true)).toBe('comfortable');
  });

  it('keeps writing surfaces comfortable on a phone', () => {
    expect(resolveDensity('reflect', false)).toBe('comfortable');
  });

  it('packs inspect surfaces tightly where there is a mouse', () => {
    expect(resolveDensity('inspect', true)).toBe('compact');
  });

  it('leaves inspect surfaces comfortable on a touch device', () => {
    expect(resolveDensity('inspect', false)).toBe('comfortable');
  });
});
