/**
 * Turns the socket message names in the API description into TypeScript.
 *
 * The generator that produces everything else only understands requests and
 * responses, so the message names a socket can send travel in a section of
 * their own. This reads that section and writes it out as string unions, so a
 * screen switching on a message name gets the same compile-time checking as
 * everything else.
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const schemaPath = resolve(here, '..', 'openapi.json');
const outputPath = resolve(here, '..', 'src', 'api', 'sockets.d.ts');

// The same name the Python side writes this section under.
const SECTION = 'x-lumen-socket-events';

// Each socket's address, and what to call its messages in TypeScript.
const NAMES = {
  '/chat/ws': 'ChatFrameKind',
  '/events/ws': 'ActivityEventKind',
};

const schema = JSON.parse(readFileSync(schemaPath, 'utf8'));
const sockets = schema[SECTION];

if (!sockets) {
  console.error(`${schemaPath} has no "${SECTION}" section. Regenerate it with:`);
  console.error('  uv run python -m lumen.api.schema_dump');
  process.exit(1);
}

const blocks = Object.entries(NAMES).map(([path, name]) => {
  const kinds = sockets[path];
  if (!kinds) {
    console.error(`${schemaPath} describes no messages for ${path}.`);
    process.exit(1);
  }
  const union = kinds.map((kind) => `  | '${kind}'`).join('\n');
  return `/** Every message ${path} can send. */\nexport type ${name} =\n${union};\n`;
});

const banner = `/**
 * Generated from the API description. Do not edit.
 *
 * Regenerate with: npm run types:generate
 */
`;

writeFileSync(outputPath, `${banner}\n${blocks.join('\n')}`);
console.log(`wrote ${outputPath}`);
