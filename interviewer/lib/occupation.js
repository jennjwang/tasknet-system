// Which occupation the participant is in: the screener's sufficiency check and the occupation shortlist.
//
// The shortlist serves the screener, the occupation-select screen and the validation survey. One LLM call sees the whole interview transcript and the full O*NET-SOC list and ranks the top N
// occupations (N is per call: the screener asks for 5 so a real target rarely screens themselves out, the
// validation screen keeps 3). Every returned item is resolved against the real list, so a hallucinated code
// never reaches the participant. The list is then:
//   1. reordered by consensus: after the #1 match, matches O*NET also lists as related to it come first;
//   2. widened with the #1 match's other O*NET-related occupations (data/onet-related.json), up to unionCap;
//   3. optionally (preferAgreement) re-tiered by evidence, see orderByEvidence.
// It is a recall tool: the participant makes the final pick, with "show different options" (excludeCodes
// plus an optional hint) and free search as fallbacks. Fail-open: any error returns [].

import fs from 'fs/promises';
import path from 'path';
import { client, SMALL_MODEL, DATA_DIR } from './config.js';
import { loadPrompt, fill } from './prompts.js';

const SHORTLIST_PROMPT = loadPrompt('occupation/shortlist.txt');
const SCREENER_CHECK_PROMPT = loadPrompt('occupation/screener_check.txt');

const OCCUPATION_MODEL = process.env.OCCUPATION_MODEL || 'gpt-5.4';
const SOC_PATH = path.join(DATA_DIR, 'onet-soc.json');
const RELATED_PATH = path.join(DATA_DIR, 'onet-related.json');
// Default number of LLM matches, and the bound on a caller's topN (past a handful the model pads).
const TOP_N = parseInt(process.env.OCCUPATION_TOP_N || '3', 10);
const TOP_N_MAX = 8;
// Default length after adding related occupations; a caller's unionCap of 0 means uncapped.
const UNION_CAP = parseInt(process.env.OCCUPATION_UNION_CAP || '8', 10);

// OpenAI reasoning models reject `temperature`.
const isReasoning = (m) => /^(o1|o3|o4|gpt-5)/.test(m);

// A JSON file read once per process; a failed read clears the cache so the next call retries.
function cachedJson(file) {
  let promise = null;
  return () => {
    if (!promise) promise = fs.readFile(file, 'utf-8').then(JSON.parse).catch((err) => { promise = null; throw err; });
    return promise;
  };
}
const loadSoc = cachedJson(SOC_PATH);           // [{code, title, definition}]
const loadRelated = cachedJson(RELATED_PATH);   // {code: [relatedCode, ...]}, most related first

// Full list (code + title) for the screen's "search all occupations" box; [] on failure.
export async function listOccupations() {
  try {
    return (await loadSoc()).map((o) => ({ code: o.code, title: o.title }));
  } catch (err) {
    console.error('[occupation-candidates] listOccupations', err);
    return [];
  }
}

function transcriptText(backgroundTranscript = []) {
  return backgroundTranscript
    .filter((t) => t && typeof t.answer === 'string' && t.answer.trim())
    .map((t) => `Q: ${(t.question || '').trim()}\nA: ${t.answer.trim()}`)
    .join('\n');
}

// Re-tier a candidate list by how much is known about THIS participant; the anchor keeps slot 1:
//   anchor → match ∩ O*NET-related → match with a `why` → related → match with no `why`.
// A `why` means the model read the participant's words and asserted a fit; a related card is there only for
// being adjacent to the anchor, so when the anchor is wrong they are wrong with it and must not bury the one
// pick that broke from it. Nothing is dropped.
export function orderByEvidence(candidates) {
  if (!Array.isArray(candidates) || candidates.length < 2) return candidates;
  const tier = (m) =>
    m.source === 'match' && m.consensus ? 0
    : m.source === 'match' && m.why     ? 1
    : m.source === 'related'            ? 2
    :                                     3;
  const [anchor, ...rest] = candidates;
  rest.sort((a, b) => tier(a) - tier(b));   // stable: the model's order holds within a tier
  return [anchor, ...rest];
}

// Ranked candidates for a transcript. Each item: {code, title, definition, why, source, consensus?}.
//   excludeCodes : codes already shown ("show different options")
//   hint         : optional participant steer ("none fit because… more technical")
export async function occupationCandidates(
  backgroundTranscript,
  { excludeCodes = [], hint = '', topN = TOP_N, preferAgreement = false, unionCap = UNION_CAP } = {},
) {
  try {
    const wantN = Math.max(1, Math.min(TOP_N_MAX, Number(topN) || TOP_N));
    const role = transcriptText(backgroundTranscript);
    if (!role) return [];
    const soc = await loadSoc();
    const byCode = new Map(soc.map((o) => [o.code, o]));
    // Lets a returned item with a mistyped code be recovered by its title.
    const byTitle = new Map(soc.map((o) => [o.title.toLowerCase().trim(), o]));
    const exclude = new Set(excludeCodes);

    // The system message (instructions + the full ~11.6k-token list, never filtered) is identical on every
    // call so it is billed at the cached-input rate; exclusions and the hint go in the user message.
    const list = soc.map((o) => `${o.code} ${o.title}`).join('\n');
    const system = fill(SHORTLIST_PROMPT, { n: wantN, occupation_list: list });
    const excludeBlock = excludeCodes.length
      ? `\nThe participant has already rejected these — do NOT return them:\n${excludeCodes.join(', ')}\n`
      : '';
    const hintBlock = hint && hint.trim()
      ? `\nThe participant says none of the previous options fit, because: "${hint.trim()}". Weight this strongly.\n`
      : '';
    const user = `Worker interview transcript:\n${role}\n${excludeBlock}${hintBlock}`;

    const params = {
      model: OCCUPATION_MODEL,
      response_format: { type: 'json_object' },
      messages: [
        { role: 'system', content: system },
        { role: 'user', content: user },
      ],
    };
    if (!isReasoning(OCCUPATION_MODEL)) params.temperature = 0;

    const resp = await client.chat.completions.create(params);
    const parsed = JSON.parse(resp.choices[0].message.content);
    const ranked = Array.isArray(parsed.ranked) ? parsed.ranked : [];

    // Resolve by code, then by exact title; title and definition always come from the canonical list.
    const seen = new Set();
    let out = [];
    for (const r of ranked) {
      const occ = byCode.get(r && r.code) || byTitle.get((r && r.title || '').toLowerCase().trim());
      if (!occ || seen.has(occ.code) || exclude.has(occ.code)) continue;
      seen.add(occ.code);
      out.push({ code: occ.code, title: occ.title, definition: occ.definition, why: (r.why || '').trim(), source: 'match' });
      if (out.length >= wantN) break;
    }

    let relatedMap = {};
    try {
      relatedMap = await loadRelated();
    } catch (err) {
      console.error('[occupation-candidates] related', err);
    }

    // 1. Consensus: after the anchor, matches O*NET also relates to it, then the model-only matches.
    if (out.length > 1) {
      const relOfTop = new Set(relatedMap[out[0].code] || []);
      const [anchor, ...rest] = out;
      for (const m of rest) m.consensus = relOfTop.has(m.code);
      out = [anchor, ...rest.filter((m) => m.consensus), ...rest.filter((m) => !m.consensus)];
    }

    // 2. Add the anchor's other related occupations. The screener passes unionCap 0: its list scrolls, so
    //    truncating could only lose the participant's real occupation.
    const cap = unionCap > 0 ? Math.max(unionCap, wantN) : Infinity;
    if (out.length && out.length < cap) {
      for (const code of relatedMap[out[0].code] || []) {
        const occ = byCode.get(code);
        if (!occ || seen.has(code) || exclude.has(code)) continue;
        seen.add(code);
        out.push({ code: occ.code, title: occ.title, definition: occ.definition, why: '', source: 'related', consensus: false });
        if (out.length >= cap) break;
      }
    }

    // 3. Evidence-first order, opt-in.
    return preferAgreement ? orderByEvidence(out) : out;
  } catch (err) {
    console.error('[occupation-candidates]', err);
    return [];
  }
}

// Is there enough in the screener's two answers to identify an occupation? The shortlist never admits it had
// nothing to go on ("idk" still yields five confident picks), so this catches it first and returns one follow-up
// nudging for a concrete example. Throws on a failed call; the route fails open.
export async function checkScreenerAnswers(title, responsibilities) {
  const completion = await client.chat.completions.create({
    model: SMALL_MODEL,
    temperature: 0,
    response_format: { type: 'json_object' },
    messages: [
      { role: 'system', content: SCREENER_CHECK_PROMPT },
      { role: 'user', content: `Job title: "${title}"\n\nMain responsibilities: "${responsibilities}"` },
    ],
  });
  const parsed = JSON.parse(completion.choices[0].message.content);
  const sufficient = parsed.sufficient !== false;
  const followUp =
    typeof parsed.followUp === 'string' && parsed.followUp.trim()
      ? parsed.followUp.trim()
      : 'Could you give me one concrete example — something you actually worked on in the last week?';
  return { sufficient, followUp: sufficient ? null : followUp };
}
