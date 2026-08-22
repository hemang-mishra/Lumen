import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The contrast floor, checked on the palette itself.
 *
 * The browser run checks the one page every component is on; this checks the
 * colours before anything is drawn with them, so a new combination is caught
 * the moment it is written rather than the first time somebody puts it on a
 * screen.
 *
 * The one that keeps being got wrong is a coloured word on its own faint
 * tint — a caution chip is caution-coloured text on a caution-coloured
 * background, and the two are much closer together than either is to the page.
 */

/** What ordinary text has to clear. */
const FLOOR = 4.5;

const css = readFileSync(join(process.cwd(), 'src', 'styles', 'tokens.css'), 'utf8');

/** Every plain colour defined in one theme's block. */
function paletteOf(selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  const block = css.slice(start, css.indexOf('\n}', start));
  const found: Record<string, string> = {};
  for (const [, name, value] of block.matchAll(/^\s{2}(--[a-z0-9-]+):\s*([^;]+);/gm)) {
    found[name!] = value!.trim();
  }
  return found;
}

const LIGHT = paletteOf(':root');
const DARK = { ...LIGHT, ...paletteOf(":root[data-theme='dark']") };

interface Rgb {
  r: number;
  g: number;
  b: number;
  a: number;
}

function parse(value: string): Rgb {
  const hex = /^#([0-9a-f]{6})$/i.exec(value);
  if (hex) {
    const digits = hex[1]!;
    return {
      r: parseInt(digits.slice(0, 2), 16),
      g: parseInt(digits.slice(2, 4), 16),
      b: parseInt(digits.slice(4, 6), 16),
      a: 1,
    };
  }
  const rgba = /^rgba?\(([^)]+)\)$/.exec(value);
  if (rgba) {
    const parts = rgba[1]!.split(',').map((part) => Number(part.trim()));
    return { r: parts[0]!, g: parts[1]!, b: parts[2]!, a: parts[3] ?? 1 };
  }
  throw new Error(`cannot read the colour ${value}`);
}

/** A translucent colour as it actually appears over what is behind it. */
function flatten(top: Rgb, bottom: Rgb): Rgb {
  return {
    r: top.r * top.a + bottom.r * (1 - top.a),
    g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a),
    a: 1,
  };
}

function luminance({ r, g, b }: Rgb): number {
  const channel = (value: number) => {
    const scaled = value / 255;
    return scaled <= 0.04045 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(front: Rgb, back: Rgb): number {
  const [high, low] = [luminance(front), luminance(back)].sort((a, b) => b - a);
  return (high! + 0.05) / (low! + 0.05);
}

/** How readable one token is on another, in one theme. */
function ratio(palette: Record<string, string>, front: string, back: string): number {
  const ground = parse(palette[back]!);
  const text = parse(palette[front]!);
  return contrast(flatten(text, ground), ground);
}

const THEMES: ReadonlyArray<[string, Record<string, string>]> = [
  ['light', LIGHT],
  ['dark', DARK],
];

const GROUNDS = ['--canvas', '--surface', '--surface-sunken'];
const TEXTS = ['--text', '--text-secondary', '--text-tertiary'];

describe.each(THEMES)('the %s palette', (_name, palette) => {
  it.each(TEXTS)('reads %s on every surface', (text) => {
    for (const ground of GROUNDS) {
      expect(ratio(palette, text, ground), `${text} on ${ground}`).toBeGreaterThanOrEqual(FLOOR);
    }
  });

  it.each(['--positive', '--caution', '--critical', '--accent'])(
    'reads %s against the page',
    (colour) => {
      expect(ratio(palette, colour, '--canvas')).toBeGreaterThanOrEqual(FLOOR);
    },
  );

  it.each([
    ['--positive', '--positive-quiet'],
    ['--caution', '--caution-quiet'],
    ['--critical', '--critical-quiet'],
    ['--accent', '--accent-quiet'],
  ])('reads %s on its own faint tint', (colour, tint) => {
    // A filled chip: the word and the background are the same hue, which is
    // where contrast quietly disappears.
    const ground = flatten(parse(palette[tint]!), parse(palette['--canvas']!));
    const text = parse(palette[colour]!);

    expect(contrast(flatten(text, ground), ground)).toBeGreaterThanOrEqual(FLOOR);
  });

  it('reads the label on a filled accent button', () => {
    expect(ratio(palette, '--accent-contrast', '--accent')).toBeGreaterThanOrEqual(FLOOR);
  });
});
