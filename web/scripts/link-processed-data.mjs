#!/usr/bin/env node
/**
 * Point the frontend at the real ETL output instead of the mock.
 *
 *   npm run link-data           # symlink public/data -> ../../data/processed
 *   npm run link-data -- --copy # copy instead (for CI / Windows)
 *   npm run link-data -- --undo # restore the mock
 *
 * The frontend reads exactly three files - players.json, meta.json,
 * teams.json - and does not care where they came from. This script only
 * changes where `public/data` points.
 */

import { cpSync, existsSync, lstatSync, mkdirSync, rmSync, symlinkSync, readdirSync } from 'node:fs';
import { dirname, join, resolve, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB = resolve(HERE, '..');
const TARGET = join(WEB, 'public', 'data');
const PROCESSED = resolve(WEB, '..', 'data', 'processed');
const REQUIRED = ['players.json', 'meta.json', 'teams.json'];

const argv = process.argv.slice(2);
const has = (f) => argv.includes(f);

const clear = () => {
  if (existsSync(TARGET) || lstatSafe(TARGET)) rmSync(TARGET, { recursive: true, force: true });
};
function lstatSafe(p) {
  try {
    return lstatSync(p);
  } catch {
    return null;
  }
}

if (has('--undo')) {
  clear();
  mkdirSync(TARGET, { recursive: true });
  console.log('unlinked. run `npm run mock` to regenerate the mock dataset.');
  process.exit(0);
}

if (!existsSync(PROCESSED)) {
  console.error(`No ETL output at ${PROCESSED}.`);
  console.error('Run the Python pipeline (etl/build.py) first, or `npm run mock` to stay on mock data.');
  process.exit(1);
}

const present = new Set(readdirSync(PROCESSED));
const missing = REQUIRED.filter((f) => !present.has(f));
if (missing.length) {
  console.error(`${PROCESSED} is missing: ${missing.join(', ')}`);
  process.exit(1);
}

clear();
if (has('--copy')) {
  mkdirSync(TARGET, { recursive: true });
  for (const f of REQUIRED) cpSync(join(PROCESSED, f), join(TARGET, f));
  console.log(`copied ${REQUIRED.join(', ')} -> public/data/`);
} else {
  symlinkSync(relative(dirname(TARGET), PROCESSED), TARGET, 'dir');
  console.log(`public/data -> ${PROCESSED}`);
}
