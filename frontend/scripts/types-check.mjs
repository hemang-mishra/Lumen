/**
 * Fails if the generated types no longer match the API description.
 *
 * The other half of this check lives in the Python test suite, which fails if
 * the description no longer matches the service. Together the two mean a
 * field renamed in Python cannot reach a screen as an undefined: one of them
 * breaks first, whichever end the change was made at.
 */
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const scratch = mkdtempSync(join(tmpdir(), 'lumen-types-'));

/** Compare a committed file against what the generator produces right now. */
function compare(committed, fresh, label) {
  const a = readFileSync(committed, 'utf8');
  const b = readFileSync(fresh, 'utf8');
  if (a === b) return true;
  console.error(`${label} is out of date. Regenerate it in the same change:`);
  console.error('  npm run types:generate');
  return false;
}

try {
  const freshSchema = join(scratch, 'schema.d.ts');
  execFileSync(
    'npx',
    ['openapi-typescript', join(root, 'openapi.json'), '-o', freshSchema],
    { stdio: 'inherit', cwd: root },
  );

  const ok = compare(join(root, 'src', 'api', 'schema.d.ts'), freshSchema, 'src/api/schema.d.ts');
  if (!ok) process.exit(1);

  // The socket names are small enough to compare by rebuilding them here
  // rather than shelling out a second time.
  const schema = JSON.parse(readFileSync(join(root, 'openapi.json'), 'utf8'));
  const declared = readFileSync(join(root, 'src', 'api', 'sockets.d.ts'), 'utf8');
  for (const kinds of Object.values(schema['x-lumen-socket-events'] ?? {})) {
    for (const kind of kinds) {
      if (!declared.includes(`'${kind}'`)) {
        console.error(`src/api/sockets.d.ts is missing the message "${kind}".`);
        console.error('  npm run types:generate');
        process.exit(1);
      }
    }
  }

  console.log('Generated types match the API description.');
} finally {
  rmSync(scratch, { recursive: true, force: true });
}
