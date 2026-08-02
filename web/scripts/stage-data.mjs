#!/usr/bin/env node
/**
 * Build-time staging of the ETL output into `web/public/data`.
 *
 * Runs automatically as npm `prebuild`, so `npm run build` is self-sufficient
 * on Vercel and in CI without any prior step having to remember to copy files.
 *
 * WHY THIS EXISTS SEPARATELY FROM link-processed-data.mjs:
 * that script is the *developer* convenience — it symlinks public/data at the
 * repo's data/processed so a local ETL run shows up instantly. A symlink is
 * exactly the wrong thing for a hosted build: `web/public/data` is gitignored,
 * so on a fresh clone (Vercel, CI) it does not exist at all and Vite would
 * publish a site with no data. This script materialises real files.
 *
 * It is deliberately idempotent and non-destructive toward the dev symlink: if
 * public/data already resolves to a directory containing the required JSON,
 * it is left alone. So a local `npm run build` does not blow away your symlink.
 *
 * Headshots matter as much as the JSON. They are the plot marks, and they must
 * be same-origin — cdn.nba.com sends no Access-Control-Allow-Origin header and
 * WebGL refuses to texture a cross-origin image, so an un-mirrored build
 * degrades to silhouettes. See etl/sources/headshots.py.
 */

import {
  cpSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  statSync
} from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB = resolve(HERE, '..');
const TARGET = join(WEB, 'public', 'data');
const PROCESSED = resolve(WEB, '..', 'data', 'processed');

const REQUIRED = ['players.json', 'meta.json', 'teams.json'];

const die = (msg) => {
  console.error(`\n  stage-data: ${msg}\n`);
  process.exit(1);
};

/** Does `dir` already hold a usable dataset? */
const isSatisfied = (dir) => {
  try {
    if (!statSync(dir).isDirectory()) return false;
  } catch {
    return false;
  }
  const present = new Set(readdirSync(dir));
  return REQUIRED.every((f) => present.has(f));
};

// A live symlink (or an already-populated directory) from the dev workflow is
// good enough — don't clobber it.
if (isSatisfied(TARGET)) {
  const kind = lstatSync(TARGET).isSymbolicLink() ? 'symlink' : 'directory';
  console.log(`stage-data: public/data already resolves (${kind}) — leaving it.`);
  process.exit(0);
}

if (!existsSync(PROCESSED)) {
  die(
    `no ETL output at ${PROCESSED}.\n` +
      `  data/processed is committed to the repo, so on a hosted build this\n` +
      `  means the checkout is incomplete (a sparse or shallow clone that\n` +
      `  excluded it). Locally, run:  python -m etl.build --verify`
  );
}

const present = new Set(readdirSync(PROCESSED));
const missing = REQUIRED.filter((f) => !present.has(f));
if (missing.length) die(`${PROCESSED} is missing: ${missing.join(', ')}`);

mkdirSync(TARGET, { recursive: true });
for (const f of REQUIRED) cpSync(join(PROCESSED, f), join(TARGET, f));

const headshots = join(PROCESSED, 'headshots');
let faces = 0;
if (existsSync(headshots)) {
  cpSync(headshots, join(TARGET, 'headshots'), { recursive: true });
  faces = readdirSync(join(TARGET, 'headshots')).length;
} else {
  // Not fatal: the chart falls back to placeholder marks rather than breaking.
  console.warn('stage-data: no mirrored headshots — chart will use placeholders.');
}

const players = JSON.parse(readFileSync(join(TARGET, 'players.json'), 'utf8'));

console.log(
  `stage-data: staged ${REQUIRED.length} JSON files ` +
    `(${players.length} players) + ${faces} headshots -> public/data/`
);
