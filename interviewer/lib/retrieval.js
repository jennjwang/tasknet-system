// Retrieval grounding for the upper-level task generator (off by default).
//
// The corpus (CORPUS_PATH) is task statements grouped by occupation. The participant is matched to a corpus
// occupation (by O*NET code, then exact title, then one LLM call over the occupation names), and that
// occupation's statements are added to the generator prompt as coverage/shape exemplars. No embeddings.
//
// Fail-open: if retrieval is off, the corpus is missing, or any call throws, retrieveExemplarBlock returns
// an empty block and the generator runs without exemplars.

import fs from 'fs/promises';
import { loadPrompt } from './prompts.js';
import { client } from './config.js';
import { envPath } from './paths.js';

const CORPUS_MATCH_PROMPT = loadPrompt('occupation/corpus_match.txt');

const CORPUS_PATH = envPath('CORPUS_PATH', '');
const ENABLED = process.env.RETRIEVAL_ENABLED === 'true' && Boolean(CORPUS_PATH);
if (process.env.RETRIEVAL_ENABLED === 'true' && !CORPUS_PATH) {
  console.warn('[retrieval] RETRIEVAL_ENABLED=true but CORPUS_PATH is unset; retrieval is off');
}
// The match call only returns a name, so a fast model is enough.
const MATCH_MODEL = process.env.MATCH_MODEL || process.env.SMALL_MODEL || 'gpt-4o-mini';
const RETRIEVAL_K = parseInt(process.env.RETRIEVAL_K || '20', 10);
const RETRIEVAL_TOP_OCCUPATIONS = parseInt(process.env.RETRIEVAL_TOP_OCCUPATIONS || '1', 10);

// The corpus, loaded once and indexed by occupation name and by O*NET-SOC code. Accepts
// [{occupation, code?, statements[]}], {occupation: statements[]} or {occupation: {code?, statements[]}}.
// A failed load clears the promise so the next call retries.
let corpusPromise = null;
function loadCorpus() {
  if (!corpusPromise) {
    corpusPromise = (async () => {
      const data = JSON.parse(await fs.readFile(CORPUS_PATH, 'utf-8'));
      const byOcc = new Map();
      const byCode = new Map();
      const add = (occupation, code, statements) => {
        if (typeof occupation !== 'string' || !Array.isArray(statements)) return;
        const stmts = statements.filter((s) => typeof s === 'string' && s.trim());
        byOcc.set(occupation, stmts);
        if (typeof code === 'string' && code.trim()) byCode.set(code.trim(), { occupation, statements: stmts });
      };
      if (Array.isArray(data)) {
        for (const row of data) if (row) add(row.occupation, row.code, row.statements);
      } else if (data && typeof data === 'object') {
        for (const [occ, val] of Object.entries(data)) {
          if (Array.isArray(val)) add(occ, undefined, val);
          else if (val && typeof val === 'object') add(occ, val.code, val.statements);
        }
      }
      return { byOcc, byCode };
    })().catch((err) => { corpusPromise = null; throw err; });
  }
  return corpusPromise;
}

// LLM matches keyed by normalised job title; a title nearly always maps to the same occupation.
const matchCache = new Map();
const profileKey = (p) => `${p.jobTitle || ''}`.toLowerCase().replace(/\s+/g, ' ').trim();

// One LLM call: pick up to `n` names from `occupations`; names not in the list are dropped.
async function matchOccupations(profile, occupations, n) {
  const list = occupations.map((o, i) => `${i + 1}. ${o}`).join('\n');
  const resp = await client.chat.completions.create({
    model: MATCH_MODEL,
    temperature: 0,
    response_format: { type: 'json_object' },
    messages: [
      { role: 'system', content: CORPUS_MATCH_PROMPT },
      {
        role: 'user',
        content:
          `OCCUPATIONS (choose ONLY from this list, copying names EXACTLY):\n${list}\n\n` +
          `PARTICIPANT:\nJob title: ${profile.jobTitle || ''}\n\n` +
          `Return the ${n} best-matching occupation name(s) as JSON.`,
      },
    ],
  });
  const parsed = JSON.parse(resp.choices[0].message.content);
  const picks = Array.isArray(parsed.occupations) ? parsed.occupations : [];
  const valid = new Set(occupations);
  return picks.filter((o) => typeof o === 'string' && valid.has(o)).slice(0, n);
}

// The matched occupations and up to `k` of their statements. Tries, in order: profile.occupationCode
// (set when the participant picked an occupation), an exact match of profile.jobTitle, then the LLM.
async function retrieveTaskStatements(
  profile,
  { k = RETRIEVAL_K, topOccupations = RETRIEVAL_TOP_OCCUPATIONS } = {},
) {
  const { byOcc, byCode } = await loadCorpus();
  if (byOcc.size === 0) return { occupations: [], statements: [] };

  const code = (profile.occupationCode || '').trim();
  if (code && byCode.has(code)) {
    const { occupation, statements } = byCode.get(code);
    return { occupations: [occupation], statements: statements.slice(0, k) };
  }

  const title = (profile.jobTitle || '').trim().toLowerCase();
  if (title) {
    for (const occ of byOcc.keys()) {
      if (occ.toLowerCase() === title) return { occupations: [occ], statements: byOcc.get(occ).slice(0, k) };
    }
  }

  const key = profileKey(profile);
  let matched = matchCache.get(key);
  if (!matched) {
    matched = await matchOccupations(profile, [...byOcc.keys()], topOccupations);
    matchCache.set(key, matched);
  }
  const statements = matched.flatMap((occ) => byOcc.get(occ) || []).slice(0, k);
  return { occupations: matched, statements };
}

// The prompt block to splice into the generator's user message ('' when off, empty, or on error).
export async function retrieveExemplarBlock(profile) {
  if (!ENABLED) return { block: '', occupations: [], count: 0 };
  try {
    const { occupations, statements } = await retrieveTaskStatements(profile);
    if (statements.length === 0) return { block: '', occupations, count: 0 };
    const block =
      `\nREAL TASK STATEMENTS FROM A SIMILAR OCCUPATION (${occupations.join(', ')}) — ` +
      `reference for COVERAGE and SHAPE only:\n` +
      statements.map((s) => `- ${s}`).join('\n') +
      `\n\nUse these as (a) a checklist of categories someone in this role typically does and ` +
      `(b) a model for phrasing and granularity. DO NOT copy them verbatim, DO NOT include any ` +
      `that don't apply to THIS participant, and DO NOT reintroduce activities the participant ` +
      `already mentioned. The participant's own responsibilities and typical week always win.\n`;
    return { block, occupations, count: statements.length };
  } catch (err) {
    console.error('[retrieval]', err.message);
    return { block: '', occupations: [], count: 0 };
  }
}
