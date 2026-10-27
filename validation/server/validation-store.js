// Per-participant records of the validation survey: fit (the screener), assignment, draft, response.
// Postgres when VALIDATION_DATABASE_URL is set (tables created on first use, see SCHEMA); otherwise one
// JSON file per participant under validation/tools/out/. The inputs (inventories.json) stay files.
import fs from 'fs/promises';
import path from 'path';
import pg from 'pg';
import {
  COVERAGE_FIT_DIR,
  COVERAGE_ASSIGN_DIR,
  COVERAGE_RESPONSE_DIR,
  COVERAGE_DRAFT_DIR,
} from './paths.js';

for (const dir of [COVERAGE_ASSIGN_DIR, COVERAGE_RESPONSE_DIR, COVERAGE_FIT_DIR, COVERAGE_DRAFT_DIR]) {
  await fs.mkdir(dir, { recursive: true });
}

const DB_URL = process.env.VALIDATION_DATABASE_URL || '';
export const usingDb = !!DB_URL;

let pool = null;
function db() {
  if (!pool) {
    pool = new pg.Pool({ connectionString: DB_URL, ssl: { rejectUnauthorized: false }, max: 5 });
    // An idle client dropped by the server emits 'error'; without a listener that crashes the process.
    pool.on('error', (err) => console.warn('[validation-store] idle pg client error (recovered):', err.message));
  }
  return pool;
}

// The tables, created if missing on first use. `data` holds the whole record; the other columns are
// indexes the queries below filter or sort on.
const SCHEMA = `
  CREATE TABLE IF NOT EXISTS coverage_fit (
    external_id text PRIMARY KEY,
    data        jsonb NOT NULL,
    fit         boolean NOT NULL
  );
  CREATE TABLE IF NOT EXISTS coverage_assignment (
    external_id text PRIMARY KEY,
    data        jsonb NOT NULL,
    soc         text NOT NULL,
    occupation  text
  );
  CREATE INDEX IF NOT EXISTS coverage_assignment_soc ON coverage_assignment (soc);
  CREATE TABLE IF NOT EXISTS coverage_response (
    external_id  text PRIMARY KEY,
    data         jsonb NOT NULL,
    soc          text,
    occupation   text,
    submitted_at timestamptz NOT NULL DEFAULT now()
  );
  CREATE TABLE IF NOT EXISTS coverage_draft (
    external_id text PRIMARY KEY,
    data        jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
  );`;
let schemaReady = null;
/** The pool, once the tables exist. */
async function ready() {
  if (!schemaReady) {
    schemaReady = db().query(SCHEMA).catch((err) => { schemaReady = null; throw err; });
  }
  await schemaReady;
  return db();
}

// ── file store ───────────────────────────────────────────────────────────────
async function readJsonFile(dir, id) {
  try {
    return JSON.parse(await fs.readFile(path.join(dir, `${id}.json`), 'utf-8'));
  } catch (err) {
    if (err.code === 'ENOENT') return null;
    throw err;
  }
}
async function writeJsonFile(dir, id, record) {
  await fs.mkdir(dir, { recursive: true });
  await fs.writeFile(path.join(dir, `${id}.json`), JSON.stringify(record, null, 2));
}
/** Every readable record in a directory as {id, data}; a missing directory or unreadable file is skipped. */
async function readAllJsonFiles(dir) {
  let names;
  try { names = await fs.readdir(dir); } catch { return []; }
  const out = [];
  for (const n of names.filter((x) => x.endsWith('.json'))) {
    try { out.push({ id: n.slice(0, -5), data: JSON.parse(await fs.readFile(path.join(dir, n), 'utf-8')) }); }
    catch { /* skip unreadable */ }
  }
  return out;
}

// ── postgres helpers ──────────────────────────────────────────────────────────
async function selectData(table, id) {
  const r = await (await ready()).query(`SELECT data FROM ${table} WHERE external_id = $1`, [id]);
  return r.rows[0] ? r.rows[0].data : null;
}
async function upsert(table, id, record, extraCols = {}) {
  const entries = Object.entries(extraCols);
  const cols = ['external_id', 'data', ...entries.map(([c]) => c)];
  const vals = [id, JSON.stringify(record), ...entries.map(([, v]) => v)];
  const ph = vals.map((_, i) => `$${i + 1}`);
  const updates = ['data = EXCLUDED.data', ...entries.map(([c]) => `${c} = EXCLUDED.${c}`)];
  await (await ready()).query(
    `INSERT INTO ${table} (${cols.join(', ')}) VALUES (${ph.join(', ')})
     ON CONFLICT (external_id) DO UPDATE SET ${updates.join(', ')}`,
    vals,
  );
}

// ── coverage fit (the screener) ───────────────────────────────────────────────
export async function getFit(id) {
  return usingDb ? selectData('coverage_fit', id) : readJsonFile(COVERAGE_FIT_DIR, id);
}
export async function putFit(id, record) {
  return usingDb
    ? upsert('coverage_fit', id, record, { fit: record.fit === true })
    : writeJsonFile(COVERAGE_FIT_DIR, id, record);
}

// ── coverage assignment (sticky per id) ───────────────────────────────────────
export async function getAssignment(id) {
  return usingDb ? selectData('coverage_assignment', id) : readJsonFile(COVERAGE_ASSIGN_DIR, id);
}
export async function putAssignment(id, record) {
  return usingDb
    ? upsert('coverage_assignment', id, record, { soc: record.soc, occupation: record.occupation ?? null })
    : writeJsonFile(COVERAGE_ASSIGN_DIR, id, record);
}
/** Every assignment to one occupation's list, the input to subset balancing. */
export async function listAssignments(soc) {
  if (usingDb) {
    const r = await (await ready()).query('SELECT external_id, data FROM coverage_assignment WHERE soc = $1', [soc]);
    return r.rows.map((row) => ({ externalId: row.external_id, ...row.data }));
  }
  return (await readAllJsonFiles(COVERAGE_ASSIGN_DIR)).map((f) => f.data).filter((a) => a && a.soc === soc);
}

/** The ids that have submitted a response, so balancing can tell a finished assignment from an open one. */
export async function responseIds() {
  if (usingDb) {
    const r = await (await ready()).query('SELECT external_id FROM coverage_response');
    return new Set(r.rows.map((row) => row.external_id));
  }
  try {
    const names = await fs.readdir(COVERAGE_RESPONSE_DIR);
    return new Set(names.filter((n) => n.endsWith('.json')).map((n) => n.slice(0, -5)));
  } catch {
    return new Set();
  }
}

/** Ids whose response failed its attention checks. Their ratings are excluded from analysis, so they hold no
 *  slots in balancing either. */
export async function failedAttentionIds() {
  if (usingDb) {
    const r = await (await ready()).query(
      `SELECT external_id FROM coverage_response WHERE data->'attentionChecks'->>'passed' = 'false'`,
    );
    return new Set(r.rows.map((row) => row.external_id));
  }
  return new Set((await readAllJsonFiles(COVERAGE_RESPONSE_DIR))
    .filter(({ data: r }) => r && r.attentionChecks && r.attentionChecks.passed === false)
    .map(({ id, data: r }) => r.externalId ?? id));
}

/** Every submitted response, for the gated results view. */
export async function listResponses() {
  if (usingDb) {
    const r = await (await ready()).query('SELECT external_id, data FROM coverage_response ORDER BY submitted_at');
    return r.rows.map((row) => ({ externalId: row.external_id, ...row.data }));
  }
  return (await readAllJsonFiles(COVERAGE_RESPONSE_DIR)).map((f) => f.data);
}

// ── coverage draft (in progress, for resuming on another device) ─────────────
// Its own table, so one coverage_response row is always one submitted rater.
export async function getDraft(id) {
  if (!usingDb) return readJsonFile(COVERAGE_DRAFT_DIR, id);
  return selectData('coverage_draft', id);
}
export async function putDraft(id, record) {
  if (!usingDb) return writeJsonFile(COVERAGE_DRAFT_DIR, id, record);
  await (await ready()).query(
    `INSERT INTO coverage_draft (external_id, data, updated_at) VALUES ($1, $2, now())
     ON CONFLICT (external_id) DO UPDATE SET data = EXCLUDED.data, updated_at = now()`,
    [id, JSON.stringify(record)],
  );
}
/** Dropped once the response is stored. */
export async function deleteDraft(id) {
  if (!usingDb) {
    try { await fs.unlink(path.join(COVERAGE_DRAFT_DIR, `${id}.json`)); } catch { /* already gone */ }
    return;
  }
  await (await ready()).query('DELETE FROM coverage_draft WHERE external_id = $1', [id]);
}

// ── coverage response ─────────────────────────────────────────────────────────
/** Whether this participant has submitted a response. */
export async function hasResponse(id) {
  if (usingDb) {
    const r = await (await ready()).query('SELECT 1 FROM coverage_response WHERE external_id = $1', [id]);
    return r.rows.length > 0;
  }
  try {
    await fs.access(path.join(COVERAGE_RESPONSE_DIR, `${id}.json`));
    return true;
  } catch {
    return false;
  }
}

export async function putResponse(id, record) {
  return usingDb
    ? upsert('coverage_response', id, record, { soc: record.soc ?? null, occupation: record.occupation ?? null })
    : writeJsonFile(COVERAGE_RESPONSE_DIR, id, record);
}
