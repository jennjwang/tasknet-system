// Loaders for the analysis tools: the records the server wrote, from Postgres when VALIDATION_DATABASE_URL is
// set, else from the file store (server/paths.js).
import fs from 'fs/promises';
import path from 'path';
import {
  VALIDATION_OUT_DIR as OUT_DIR, COVERAGE_RESPONSE_DIR, COVERAGE_FIT_DIR, COVERAGE_ASSIGN_DIR,
} from '../../server/paths.js';

export { OUT_DIR };
const STORE_DIRS = {
  coverage_response: COVERAGE_RESPONSE_DIR,
  coverage_fit: COVERAGE_FIT_DIR,
  coverage_assignment: COVERAGE_ASSIGN_DIR,
};

// Test rows, which never reach an analysis: an id starting with "test", or with dev, anon (a link with no id,
// so a visitor rather than a recruited rater) or e2e followed by - or _. Anything else is a participant.
const RESERVED_PREFIX = /^(anon|e2e|dev)[-_]/i;
const BARE_TEST = /^test/i;

/** Why this id is not a participant, or null if it is one. */
export function testReason(externalId) {
  const id = String(externalId ?? '').trim();
  if (!id) return 'no id';
  if (BARE_TEST.test(id)) return 'reserved prefix (test)';
  const m = RESERVED_PREFIX.exec(id);
  if (m) return `reserved prefix (${m[0]})`;
  return null;
}

export const isTestId = (externalId) => testReason(externalId) !== null;

// A path for a log line: relative to the current directory when inside it.
export const short = (p) => {
  const rel = path.relative(process.cwd(), p);
  return rel.startsWith('..') ? p : rel;
};
const readJson = async (filePath) => JSON.parse(await fs.readFile(filePath, 'utf-8'));

async function jsonFilesIn(dir) {
  try {
    return (await fs.readdir(dir)).filter((n) => n.endsWith('.json'));
  } catch (err) {
    if (err.code === 'ENOENT') return [];
    throw err;
  }
}

// Every record in one file-store folder, with externalId taken from the file name when absent.
async function loadFromFiles(table) {
  const dir = STORE_DIRS[table];
  const out = [];
  for (const name of await jsonFilesIn(dir)) {
    out.push({ externalId: name.slice(0, -5), ...(await readJson(path.join(dir, name))) });
  }
  return out;
}

async function loadCoverageFromDb(table) {
  const { default: pg } = await import('pg');
  const pool = new pg.Pool({
    connectionString: process.env.VALIDATION_DATABASE_URL,
    ssl: { rejectUnauthorized: false },
    max: 3,
  });
  try {
    const r = await pool.query(`SELECT external_id, data FROM ${table}`);
    return r.rows.map((row) => ({ externalId: row.external_id, ...row.data }));
  } finally {
    await pool.end();
  }
}

const load = (table) =>
  (process.env.VALIDATION_DATABASE_URL ? loadCoverageFromDb(table) : loadFromFiles(table));

export const loadCoverageResponses = () => load('coverage_response');
export const loadCoverageFits = () => load('coverage_fit');
export const loadCoverageAssignments = () => load('coverage_assignment');
