import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Tests for the rules that hold the design system together.
 *
 * These are the two that cannot be enforced by a type or noticed in review.
 * A colour defined only in the dark block works perfectly until somebody
 * opens the app in light and finds an unstyled control, which is usually
 * months later and never by the person who wrote it.
 */

const here = join(process.cwd(), 'src', 'styles');
const tokens = readFileSync(join(here, 'tokens.css'), 'utf8');
const density = readFileSync(join(here, 'density.css'), 'utf8');

/** Every custom property defined inside a block, by the block's selector. */
function definedIn(css: string, selector: string): Set<string> {
  const start = css.indexOf(`${selector} {`);
  if (start === -1) throw new Error(`no ${selector} block in the stylesheet`);
  const end = css.indexOf('\n}', start);
  const block = css.slice(start, end);
  return new Set([...block.matchAll(/^\s{2}(--[a-z0-9-]+):/gm)].map((match) => match[1]!));
}

describe('the token file', () => {
  const light = definedIn(tokens, ':root');
  const dark = definedIn(tokens, ":root[data-theme='dark']");

  it('defines the light theme as the base of every name', () => {
    // The rule that stops light rotting: if a name only exists in dark, the
    // light theme has a hole in it that nobody will notice for months.
    const onlyInDark = [...dark].filter((name) => !light.has(name));

    expect(onlyInDark).toEqual([]);
  });

  it('redefines names in dark rather than inventing them', () => {
    expect(dark.size).toBeGreaterThan(0);
    expect([...dark].every((name) => light.has(name))).toBe(true);
  });

  it('gives both themes a full set of surfaces, text and meaning colours', () => {
    for (const required of [
      '--canvas',
      '--surface',
      '--surface-raised',
      '--surface-sunken',
      '--text',
      '--text-secondary',
      '--text-tertiary',
      '--accent',
      '--positive',
      '--caution',
      '--critical',
    ]) {
      expect(light.has(required), `light is missing ${required}`).toBe(true);
      expect(dark.has(required), `dark is missing ${required}`).toBe(true);
    }
  });

  it('keeps the spacing scale to the twelve steps and nothing between', () => {
    const spacing = [...light].filter((name) => name.startsWith('--space-'));

    expect(spacing.sort()).toEqual(
      [
        '--space-0',
        '--space-12',
        '--space-16',
        '--space-2',
        '--space-20',
        '--space-24',
        '--space-32',
        '--space-4',
        '--space-40',
        '--space-56',
        '--space-72',
        '--space-8',
        '--space-96',
      ].sort(),
    );
  });

  it('keeps the type scale to six sizes', () => {
    const sizes = [...light].filter(
      (name) => name.startsWith('--type-') && !name.endsWith('-line'),
    );

    expect(sizes).toHaveLength(6);
  });

  it('has exactly three durations', () => {
    const durations = [...light].filter((name) => name.startsWith('--dur-'));

    expect(durations.sort()).toEqual(['--dur-large', '--dur-micro', '--dur-standard']);
  });

  it('has two shadows and no more', () => {
    const shadows = [...light].filter((name) => name.startsWith('--shadow-'));

    expect(shadows.sort()).toEqual(['--shadow-1', '--shadow-2']);
  });

  it('drops shadows in dark, where they do not read', () => {
    expect(tokens).toMatch(/--shadow-1:\s*none/);
  });
});

describe('the density file', () => {
  it('defines both densities and no third', () => {
    const selectors = [...density.matchAll(/\[data-density='([a-z]+)'\]/g)].map(
      (match) => match[1],
    );

    expect([...new Set(selectors)].sort()).toEqual(['comfortable', 'compact']);
  });

  it('gives compact a smaller control than the 44px touch target', () => {
    expect(density).toMatch(/\[data-density='compact'\][\s\S]*?--control-height:\s*32px/);
    expect(density).toMatch(/\[data-density='comfortable'\][\s\S]*?--control-height:\s*44px/);
  });
});
