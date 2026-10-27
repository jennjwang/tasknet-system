// Build interviewer/data/onet-related.json — a map of O*NET-SOC code -> ranked list of
// related O*NET-SOC codes, from O*NET's official "Related Occupations" dataset.
//
// We keep only the PRIMARY relatedness tiers (Primary-Short + Primary-Long),
// dropping the looser "Supplemental" tier, and preserve O*NET's Index order so
// the most-related occupation comes first. Titles/definitions are NOT stored
// here — the runtime joins these codes against data/onet-soc.json, which is the
// single source of truth for occupation text (and drops any related code not in
// that list, e.g. taxonomy subcodes we don't carry).
//
// Usage:
//   node interviewer/scripts/build_onet_related.mjs                 # download DB, build (from the repo root)
//   node interviewer/scripts/build_onet_related.mjs <path/to.txt>   # use a local Related Occupations.txt
//
// The output is committed to the repo (like data/onet-soc.json) so the app needs
// no network at runtime; re-run this only to refresh against a newer O*NET release.

import fs from 'fs/promises';
import path from 'path';
import os from 'os';
import { DATA_DIR } from '../lib/paths.js';
import { execFileSync } from 'child_process';

const OUT = path.join(DATA_DIR, 'onet-related.json');
const DB_URL = 'https://www.onetcenter.org/dl_files/database/db_29_1_text.zip';
const MEMBER = 'db_29_1_text/Related Occupations.txt';
const PRIMARY_TIERS = new Set(['Primary-Short', 'Primary-Long']);

async function getRelatedText(localPath) {
  if (localPath) return fs.readFile(localPath, 'utf-8');
  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'onet-'));
  const zip = path.join(tmp, 'db.zip');
  console.log(`Downloading ${DB_URL} ...`);
  execFileSync('curl', ['-sSL', '-o', zip, DB_URL, '--max-time', '120']);
  execFileSync('unzip', ['-o', zip, MEMBER, '-d', tmp]);
  return fs.readFile(path.join(tmp, MEMBER), 'utf-8');
}

function build(text) {
  const lines = text.split(/\r?\n/).filter(Boolean);
  const header = lines.shift(); // O*NET-SOC Code \t Related O*NET-SOC Code \t Relatedness Tier \t Index
  if (!/O\*NET-SOC Code/.test(header)) throw new Error('unexpected header: ' + header);
  // code -> [{ related, index }]
  const acc = new Map();
  for (const line of lines) {
    const [code, related, tier, index] = line.split('\t');
    if (!PRIMARY_TIERS.has(tier)) continue;
    if (!acc.has(code)) acc.set(code, []);
    acc.get(code).push({ related, index: parseInt(index, 10) });
  }
  const out = {};
  for (const [code, rows] of acc) {
    rows.sort((a, b) => a.index - b.index);
    out[code] = rows.map((r) => r.related);
  }
  return out;
}

const map = build(await getRelatedText(process.argv[2]));
await fs.writeFile(OUT, JSON.stringify(map) + '\n');
const codes = Object.keys(map);
const total = codes.reduce((n, c) => n + map[c].length, 0);
console.log(`Wrote ${OUT}: ${codes.length} occupations, ${total} primary relations (avg ${(total / codes.length).toFixed(1)}/occ)`);
