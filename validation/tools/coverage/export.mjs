// Flattens the coverage responses to CSV for analysis in Excel / R / Stata. Writes to validation/tools/out/:
//   ratings.csv       one row per task rating   — the analysis file
//   participants.csv  one row per participant   — join on external_id
//   events.csv        one row per answer event  — timing, in the order answered
// Each graded item is written as the stored code and as the label the participant saw (`*_label`).
//
// Usage (from the repo root; add --env-file=interviewer/.env to read Postgres):
//   node validation/tools/coverage/export.mjs
//   node validation/tools/coverage/export.mjs --all     # include test rows
import fs from 'fs/promises';
import path from 'path';
import {
  loadCoverageResponses, loadCoverageFits, isTestId, testReason, short, OUT_DIR,
} from '../lib/io.mjs';

// The scales the participant saw (shared with the UI). The stored value is the 1-based index.
const SCALES = JSON.parse(await fs.readFile(new URL('../../ui/lib/scales.json', import.meta.url), 'utf-8'));
const label = (field, v) => (v == null ? '' : SCALES[field][v - 1] ?? '');

// RFC 4180: the free text contains commas, quotes and newlines.
const cell = (v) => {
  if (v == null || v === '') return '';
  const s = String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};
const toCsv = (cols, rows) =>
  [cols.join(','), ...rows.map((r) => cols.map((c) => cell(r[c])).join(','))].join('\n') + '\n';

async function main() {
  const includeTests = process.argv.includes('--all');
  const [responses, fits] = await Promise.all([loadCoverageResponses(), loadCoverageFits()]);
  const fitById = new Map(fits.map((f) => [f.externalId, f]));
  const rows = responses
    .filter((r) => Array.isArray(r.ratings) && (includeTests || !isTestId(r.externalId)))
    .sort((a, b) => (a.submittedAt < b.submittedAt ? -1 : 1));

  // Print every excluded id and why, so a misclassified participant is visible.
  if (!includeTests) {
    const dropped = responses
      .filter((r) => Array.isArray(r.ratings) && isTestId(r.externalId))
      .map((r) => `${r.externalId} (${testReason(r.externalId)})`);
    console.log(`${rows.length} participants; ${dropped.length} excluded as non-participants`
      + (dropped.length ? ':' : '.'));
    for (const d of dropped) console.log(`    - ${d}`);
    console.log('  --all includes them.');
  }

  const participants = rows.map((r) => {
    const fit = fitById.get(r.externalId);
    return {
      external_id: r.externalId,
      soc: r.soc ?? '',
      occupation: r.occupation ?? '',
      submitted_at: r.submittedAt,
      minutes: r.elapsedMs ? (r.elapsedMs / 60000).toFixed(2) : '',
      n_statements: r.n,
      n_answered: r.nAnswered,
      complete: r.complete,
      relevant_count: r.relevantCount,
      // Share of the ANSWERED tasks, so an unfinished pass is not read as rejections.
      endorsement_rate: r.relevanceShare != null ? r.relevanceShare.toFixed(4) : '',
      burden_1_5: r.feedbackRatings?.burden ?? '',
      clarity_1_5: r.feedbackRatings?.clarity ?? '',
      attention_passed: r.attentionChecks?.passed ?? '',
      occupation_fit: fit?.fit ?? '',
      screener_title: r.screener?.title ?? '',
      screener_duties: r.screener?.duties ?? '',
      picked_occupation: r.selfIdOccupation?.selectedTitle ?? fit?.selectedTitle ?? '',
      feedback_task_wording: r.reviewNotes ?? '',
      feedback_questions: r.feedback ?? '',
    };
  });

  const ratings = [];
  for (const r of rows) {
    // A task absent from the top-worker screen is blank, not false.
    const perf = new Map((r.taskPerformance?.perTask ?? []).map((x) => [x.statement, x.outperforms]));
    r.ratings.forEach((t, i) => {
      ratings.push({
        external_id: r.externalId,
        soc: r.soc ?? '',
        position: i + 1,
        statement: t.statement,
        // '' for never answered, distinct from 0 (not relevant).
        relevant: t.relevant === null || t.relevant === undefined ? '' : (t.relevant ? 1 : 0),
        frequency: t.frequency ?? '',
        frequency_label: label('frequency', t.frequency),
        importance: t.importance ?? '',
        importance_label: label('importance', t.importance),
        duration: t.duration ?? '',
        duration_label: label('duration', t.duration),
        training: t.training ?? '',
        training_label: label('training', t.training),
        articulability: t.standards ?? '',
        verifiability: t.verifiability ?? '',
        top_worker_outperforms: perf.has(t.statement) ? (perf.get(t.statement) ? 1 : 0) : '',
      });
    });
  }

  const events = [];
  for (const r of rows) {
    const log = [...(r.answerEvents ?? [])].sort((a, b) => a.atMs - b.atMs);
    log.forEach((e, i) => {
      const prev = i ? log[i - 1].atMs : null;
      events.push({
        external_id: r.externalId,
        seq: i + 1,
        task_position: e.task != null ? e.task + 1 : '',
        statement: e.statement ?? '',
        field: e.field,
        value: typeof e.value === 'boolean' ? (e.value ? 1 : 0) : e.value,
        at_ms: e.atMs,
        iso: e.iso,
        // Time on this answer, including reading the question. Blank for the first event and for gaps over
        // two minutes (breaks).
        secs_since_prev:
          prev == null || e.atMs - prev > 120000 ? '' : ((e.atMs - prev) / 1000).toFixed(2),
      });
    });
  }

  const files = [
    ['participants.csv', participants],
    ['ratings.csv', ratings],
    ['events.csv', events],
  ];
  await fs.mkdir(OUT_DIR, { recursive: true });
  for (const [name, data] of files) {
    if (!data.length) continue;
    await fs.writeFile(path.join(OUT_DIR, name), toCsv(Object.keys(data[0]), data));
    console.log(`${String(data.length).padStart(5)} rows -> ${short(path.join(OUT_DIR, name))}`);
  }
  console.log(`\n${rows.length} participants${includeTests ? ' (test rows included)' : ''}.`);
  console.log('Join ratings/events to participants on external_id.');
}

main().catch((e) => { console.error(e); process.exit(1); });
