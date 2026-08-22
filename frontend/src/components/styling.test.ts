import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The rule that says a component may not invent a colour or a spacing value.
 *
 * A design system holds together for as long as everything asks for values by
 * name. It falls apart one hurried `#2a2a2a` at a time, and no reviewer
 * catches every one of them — so this walks every source file instead.
 *
 * A literal colour is the serious case: it will be wrong in one of the two
 * themes, and nobody will find out until somebody opens the app in the other
 * one. Off-scale spacing is milder but adds up to a screen where nothing
 * quite lines up with anything else.
 */

const SOURCE = join(process.cwd(), 'src');

/** Files whose whole job is to define the values everything else asks for. */
const ALLOWED = new Set(['src/styles/tokens.css', 'src/styles/density.css']);

/**
 * The spacing steps that exist.
 *
 * Tailwind's numbers are quarters of a rem, so these are the ones that land
 * on the 4px scale: 1 is 4px, 3 is 12px, 6 is 24px, and so on.
 */
const ON_SCALE = new Set(['0', '0.5', '1', '2', '3', '4', '5', '6', '8', '10', '14', '18', '24']);

/** A colour written out rather than asked for by name. */
const LITERAL_COLOUR = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/;

/** A spacing utility with a number after it: p-4, gap-3, mt-8. */
const SPACING_UTILITY = /\b(?:p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|gap-x|gap-y)-([0-9.]+)\b/g;

function sourceFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((entry) => {
    const path = join(directory, entry);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.(tsx?|css)$/.test(entry) && !/\.test\.tsx?$/.test(entry) ? [path] : [];
  });
}

function relative(path: string): string {
  return path.slice(process.cwd().length + 1);
}

const files = sourceFiles(SOURCE)
  .filter((path) => !ALLOWED.has(relative(path)))
  .filter((path) => !path.endsWith('.d.ts'));

describe('every component asks for values by name', () => {
  it('finds source files to check', () => {
    expect(files.length).toBeGreaterThan(10);
  });

  it('holds no colour written out by hand', () => {
    const offenders = files
      .map((path) => ({ path: relative(path), text: readFileSync(path, 'utf8') }))
      .filter(({ text }) => LITERAL_COLOUR.test(withoutComments(text)))
      .map(({ path }) => path);

    expect(offenders).toEqual([]);
  });

  it('uses no spacing value that is not on the scale', () => {
    const offenders: string[] = [];

    for (const path of files) {
      const text = withoutComments(readFileSync(path, 'utf8'));
      for (const match of text.matchAll(SPACING_UTILITY)) {
        if (!ON_SCALE.has(match[1]!)) offenders.push(`${relative(path)}: ${match[0]}`);
      }
    }

    expect(offenders).toEqual([]);
  });
});

/**
 * The same text with its comments taken out.
 *
 * Comments explain the values; they are allowed to name one. It is the code
 * that may not.
 */
function withoutComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
}
