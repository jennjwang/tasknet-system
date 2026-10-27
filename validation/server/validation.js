import express from 'express';
import fs from 'fs/promises';
import path from 'path';
import { VALIDATION_INPUTS_DIR } from './paths.js';
import {
  getFit, putFit, getAssignment, putAssignment, putResponse, getDraft, putDraft,
  deleteDraft, listResponses, listAssignments, responseIds, failedAttentionIds, hasResponse, usingDb,
} from './validation-store.js';
import { subsetSize, statementLoads, pickSubset } from './coverage-subset.js';
// Shared with the analysis tools, so both agree on which ids are test rows.
import { testReason } from '../tools/lib/io.mjs';

console.log('[validation] storage:', usingDb ? 'postgres (VALIDATION_DATABASE_URL)' : 'files (validation/tools/out)');

const router = express.Router();

function safeExternalId(v) {
  if (typeof v !== 'string') return '';
  return v.replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 64);
}

// inventories.json: one task list per occupation, keyed by O*NET-SOC (built by tools/adapters/build_inputs.py).
async function loadInventories() {
  const raw = await fs.readFile(path.join(VALIDATION_INPUTS_DIR, 'inventories.json'), 'utf-8');
  const data = JSON.parse(raw);
  if (!data || typeof data.occupations !== 'object') {
    throw new Error('bad inventories.json: expected { occupations: {<soc>: {...}} } '
      + '(rebuild it with validation/tools/adapters/build_inputs.py)');
  }
  // COVERAGE_MAX_TASKS caps every list, for trying the flow on a short one.
  const cap = parseInt(process.env.COVERAGE_MAX_TASKS || '', 10);
  if (Number.isFinite(cap) && cap > 0) {
    for (const o of Object.values(data.occupations)) {
      if (Array.isArray(o.statements)) o.statements = o.statements.slice(0, cap);
      if (Array.isArray(o.sample)) o.sample = o.sample.slice(0, cap);
    }
  }
  return data;
}

/** The list for a SOC code, or null. hasOwnProperty, so "constructor" cannot resolve through the prototype. */
const listForCode = (inv, code) => {
  const key = typeof code === 'string' ? code.trim() : '';
  return key && Object.prototype.hasOwnProperty.call(inv.occupations, key)
    ? inv.occupations[key]
    : null;
};

/** The statements shown for an occupation: the build's `sample` if it has one, else the whole list. */
const shownList = (entry) => (entry.sample && entry.sample.length ? entry.sample : entry.statements);

/** The planted attention-check statements, as a Set. */
const attentionCheckSet = (inv) => new Set(
  (Array.isArray(inv.attentionChecks) ? inv.attentionChecks : []).filter((c) => typeof c === 'string' && c.trim()),
);

// Attention checks are statements that plainly belong to another occupation, spliced into the list; marking
// one Relevant means the participant wasn't reading. About one per 15 statements, each in its own band of
// the middle 70% of the list, at a position hashed from the participant id so a reload doesn't move it.
function spliceAttentionChecks(statements, pool, externalId) {
  if (!pool.length || statements.length < 6) return statements;

  let h = 0;
  for (const c of externalId) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const next = () => (h = (h * 1103515245 + 12345) >>> 0);

  const count = Math.max(1, Math.min(pool.length, Math.floor(statements.length / 15)));
  const lo = Math.max(1, Math.floor(statements.length * 0.15));
  const hi = Math.max(lo, Math.floor(statements.length * 0.85));
  const band = (hi - lo) / count;

  const out = statements.slice();
  // Insert back-to-front so earlier indices stay valid as the array grows.
  for (let i = count - 1; i >= 0; i--) {
    const start = Math.floor(lo + band * i);
    const width = Math.max(1, Math.floor(band));
    out.splice(start + (next() % width), 0, pool[i]);
  }
  return out;
}

// ── results feed ─────────────────────────────────────────────────────────────
// The submitted responses as JSON (test ids dropped), for an access code in ?viewer= listed in
// VALIDATION_VIEWERS; with none configured it answers 503.
const VIEWERS = new Set((process.env.VALIDATION_VIEWERS || '')
  .split(',').map((s) => s.trim()).filter(Boolean));

/** The gate. Returns null when it has already answered the request. */
function viewerOr403(req, res) {
  const viewer = safeExternalId(req.query && req.query.viewer);
  if (!VIEWERS.size) {
    res.status(503).json({ error: 'no viewers configured (set VALIDATION_VIEWERS)' });
    return null;
  }
  if (!viewer || !VIEWERS.has(viewer)) {
    res.status(403).json({ error: 'this link is missing a valid access code' });
    return null;
  }
  return viewer;
}

/** Participant rows, test rows dropped, for the results feed. */
async function participantResponses() {
  const all = await listResponses();
  const rows = all.filter((r) => testReason(r.externalId) === null);
  return { rows, excluded: all.length - rows.length };
}

router.get('/api/validation/results', async (req, res) => {
  if (!viewerOr403(req, res)) return;
  try {
    const inv = await loadInventories();
    const { rows, excluded } = await participantResponses();

    const raters = rows.map((r) => {
      const soc = (r.selfIdOccupation && r.selfIdOccupation.selectedCode) || r.soc || null;
      const list = soc ? listForCode(inv, soc) : null;
      return {
        // A prefix only: enough to tell raters apart without sharing the full id.
        id: String(r.externalId || '').slice(0, 8),
        submitted: r.submittedAt || null,
        minutes: Number.isFinite(r.elapsedMs) ? Math.round(r.elapsedMs / 60000) : null,
        occupation: r.occupation || (list && list.title) || null,
        soc,
        in_cohort: !!list,
        n: r.n ?? null,
        n_answered: r.nAnswered ?? null,
        complete: r.complete ?? null,
        relevant: r.relevantCount ?? null,
        endorsement: r.relevanceShare ?? null,
        burden: (r.feedbackRatings && r.feedbackRatings.burden) ?? null,
        clarity: (r.feedbackRatings && r.feedbackRatings.clarity) ?? null,
        attention_passed: (r.attentionChecks && r.attentionChecks.passed) ?? null,
        screener_title: (r.screener && r.screener.title) || null,
        picked: (r.selfIdOccupation && r.selfIdOccupation.selectedTitle) || null,
        notes: r.reviewNotes || null,
        feedback: r.feedback || null,
      };
    });
    const med = (xs) => {
      const v = xs.filter((x) => x != null).sort((a, b) => a - b);
      return v.length ? v[Math.floor(v.length / 2)] : null;
    };
    const byOcc = {};
    for (const x of raters) {
      const k = x.soc || '(none)';
      byOcc[k] = byOcc[k] || { soc: k, title: x.picked || x.occupation || k, n: 0, complete: 0, in_cohort: x.in_cohort };
      byOcc[k].n += 1;
      if (x.complete) byOcc[k].complete += 1;
    }
    // Per-task fill: how many participants answered each statement (failed attention checks excluded).
    // Fielding is done when every task reaches COVERAGE_RATERS_PER_TASK.
    const answeredBySoc = {};
    for (const r of rows) {
      const soc = r.soc;
      if (!soc || !Array.isArray(r.ratings)) continue;
      if (r.attentionChecks && r.attentionChecks.passed === false) continue;
      const m = (answeredBySoc[soc] = answeredBySoc[soc] || new Map());
      for (const t of r.ratings) {
        if (t && typeof t.statement === 'string' && t.relevant !== null) {
          m.set(t.statement, (m.get(t.statement) || 0) + 1);
        }
      }
    }
    const target = parseInt(process.env.COVERAGE_RATERS_PER_TASK || '', 10);
    const taskFill = Object.values(inv.occupations).map((o) => {
      const list = shownList(o);
      const m = answeredBySoc[o.soc] || new Map();
      const counts = list.map((s) => m.get(s) || 0).sort((a, b) => a - b);
      return {
        soc: o.soc,
        title: o.title,
        tasks: list.length,
        target: Number.isFinite(target) && target > 0 ? target : null,
        min: counts[0] ?? 0,
        median: counts.length ? counts[Math.floor(counts.length / 2)] : 0,
        max: counts[counts.length - 1] ?? 0,
        at_target: Number.isFinite(target) && target > 0 ? counts.filter((c) => c >= target).length : null,
      };
    });
    res.json({
      generated_at: new Date().toISOString(),
      routable: Object.values(inv.occupations).map((o) => ({ soc: o.soc, title: o.title, tasks: o.statements.length })),
      task_fill: taskFill,
      summary: {
        n: raters.length,
        excluded,
        complete: raters.filter((x) => x.complete === true).length,
        partial: raters.filter((x) => x.complete === false).length,
        in_cohort: raters.filter((x) => x.in_cohort).length,
        attention_passed: raters.filter((x) => x.attention_passed === true).length,
        median_minutes: med(raters.map((x) => x.minutes)),
        median_endorsement: med(raters.map((x) => x.endorsement)),
      },
      by_occupation: Object.values(byOcc).sort((a, b) => b.n - a.n),
      raters,
    });
  } catch (err) {
    console.error('[validation] results failed:', err);
    res.status(500).json({ error: 'results failed' });
  }
});

router.post('/api/validation/coverage/assign', async (req, res) => {
  const externalId = safeExternalId(req.body && req.body.externalId);
  if (!externalId) return res.status(400).json({ error: 'externalId required' });
  // Only screened participants get a list; one whose occupation has no list is refused below.
  let fit;
  try {
    fit = await getFit(externalId);
    if (!fit) return res.status(403).json({ error: 'not screened' });
  } catch (err) {
    console.error('[coverage] fit-gate read failed:', err);
    return res.status(500).json({ error: 'screen check failed' });
  }
  try {
    const inv = await loadInventories();
    let assignment = await getAssignment(externalId);
    if (!assignment) {
      // Route on the screened SOC stored in the fit record.
      const entry = listForCode(inv, fit.selectedCode);
      if (!entry) {
        return res.status(403).json({
          error: 'no task list for the selected occupation',
          reason: 'This study is only rating occupations we publish a task list for.',
        });
      }
      assignment = {
        externalId,
        soc: entry.soc,
        slug: entry.slug,
        occupation: entry.title,
        assignedAt: new Date().toISOString(),
      };
      await putAssignment(externalId, assignment);
    }
    // Sticky: an assignment whose occupation was later dropped from inventories.json cannot be served.
    const entry = listForCode(inv, assignment.soc);
    if (!entry) {
      return res.status(409).json({
        error: 'assigned list no longer exists',
        reason: `This participant was assigned '${assignment.soc}', which is not a published occupation list.`,
      });
    }
    // Which statements this participant rates: a balanced subset (coverage-subset.js), stored on the
    // assignment so a reload serves the same list. Peers count while they have submitted or their open
    // assignment is younger than COVERAGE_ASSIGN_TTL_HOURS; test ids never count. COVERAGE_FULL_SOCS lists
    // occupations that always get the full list.
    const base = shownList(entry);
    let served = base;
    const fullSocs = new Set((process.env.COVERAGE_FULL_SOCS || '').split(/[,;\s]+/).map((s) => s.trim()).filter(Boolean));
    if (Array.isArray(assignment.statements) && assignment.statements.length) {
      served = assignment.statements; // sticky: the subset this participant already saw
    } else if (!fullSocs.has(entry.soc)) {
      const k = parseInt(process.env.COVERAGE_RATERS_PER_TASK || '', 10);
      const cohort = parseInt(process.env.COVERAGE_COHORT_SIZE || '', 10);
      const m = subsetSize(base.length, k, cohort);
      if (m < base.length) {
        const [peers, responded, failedAttn] = await Promise.all([
          listAssignments(entry.soc), responseIds(), failedAttentionIds(),
        ]);
        // Peers who failed an attention check hold no slots: their ratings are excluded from analysis.
        const rows = peers
          .filter((a) => a && a.externalId !== externalId && testReason(a.externalId) === null
            && a.attnCut !== true && !failedAttn.has(a.externalId))
          .map((a) => ({ ...a, responded: responded.has(a.externalId) }));
        const ttlH = parseFloat(process.env.COVERAGE_ASSIGN_TTL_HOURS || '') || 72;
        const loads = statementLoads(base, rows, { openTtlMs: ttlH * 3600 * 1000 });
        served = pickSubset(base, loads, m);
        assignment = {
          ...assignment,
          statements: served,
          subset: { ratersPerTask: k, cohortSize: cohort, m, of: base.length },
        };
        await putAssignment(externalId, assignment);
      }
    }
    // One pass per participant: anyone who submitted or was cut gets the thank-you screen.
    const completed = assignment.attnCut === true || (await hasResponse(externalId));
    // The client is not told which statements are attention checks.
    const statements = spliceAttentionChecks(served, [...attentionCheckSet(inv)], externalId);
    res.json({ assignmentId: externalId, occupation: entry.title, statements, completed });
  } catch (err) {
    console.error('[coverage] assign failed:', err);
    res.status(500).json({ error: 'assign failed' });
  }
});

// ── in-progress draft ────────────────────────────────────────────────────────
// A copy of the client's localStorage draft, so a participant can resume on another device. The client
// treats failures as non-fatal (coverageDraft.ts).
router.post('/api/validation/coverage/draft', async (req, res) => {
  const externalId = safeExternalId(req.body && req.body.externalId);
  if (!externalId) return res.status(400).json({ error: 'externalId required' });
  const draft = req.body && req.body.draft;
  if (!draft || typeof draft !== 'object') return res.status(400).json({ error: 'draft required' });
  try {
    if (!(await getFit(externalId))) return res.status(403).json({ error: 'not screened' });
    await putDraft(externalId, { ...draft, savedAt: new Date().toISOString() });
    res.json({ ok: true });
  } catch (err) {
    console.error('[coverage] draft save failed:', err);
    res.status(500).json({ error: 'draft save failed' });
  }
});

router.get('/api/validation/coverage/draft', async (req, res) => {
  const externalId = safeExternalId(req.query && req.query.externalId);
  if (!externalId) return res.status(400).json({ error: 'externalId required' });
  try {
    if (!(await getFit(externalId))) return res.status(403).json({ error: 'not screened' });
    res.json({ draft: (await getDraft(externalId)) || null });
  } catch (err) {
    console.error('[coverage] draft load failed:', err);
    res.status(500).json({ error: 'draft load failed' });
  }
});

router.post('/api/validation/coverage/response', async (req, res) => {
  const externalId = safeExternalId(req.body && req.body.externalId);
  if (!externalId) return res.status(400).json({ error: 'externalId required' });
  const { ratings, taskPerformance, elapsedMs, startedAt, occupation, screener, notes, feedback, feedbackRatings, events } = req.body || {};
  if (!Array.isArray(ratings)) {
    return res.status(400).json({ error: 'ratings required' });
  }
  try {
    const assignment = await getAssignment(externalId);
    if (!assignment) return res.status(400).json({ error: 'no assignment for participant' });
    // Ratings for relevant tasks: frequency 1-7, importance 1-5, the two evaluability verdicts
    // (yes/partly/no), duration 1-9, training 1-9. Clamped to range; null when not relevant.
    const clip = (v, lo, hi) => (Number.isFinite(+v) ? Math.min(hi, Math.max(lo, Math.round(+v))) : null);
    const VERDICTS = new Set(['yes', 'partly', 'no']);
    const verdict = (v) => (VERDICTS.has(v) ? v : null);

    const clean = ratings
      .filter((r) => r && typeof r.statement === 'string')
      .map((r) => {
        const relevant = r.relevant === true;
        // An unreached task stays relevant:null, distinct from an explicit "not relevant" (false).
        const answered = r.relevant === true || r.relevant === false;
        return {
          statement: r.statement,
          relevant: answered ? relevant : null,
          frequency: relevant ? clip(r.frequency, 1, 7) : null,
          importance: relevant ? clip(r.importance, 1, 5) : null,
          standards: relevant ? verdict(r.standards) : null,
          verifiability: relevant ? verdict(r.verifiability) : null,
          duration: relevant ? clip(r.duration, 1, 9) : null,
          training: relevant ? clip(r.training, 1, 9) : null,
        };
      });
    // Grade the planted attention checks, then strip them from the ratings (they are not tasks).
    const inv = await loadInventories();
    const checkTexts = attentionCheckSet(inv);
    const checks = [...checkTexts].map((statement) => {
      const row = clean.find((r) => r.statement === statement);
      // A check counts only if answered; an unreached one is neither passed nor failed.
      const answered = !!row && row.relevant !== null;
      return {
        statement,
        shown: answered,
        markedRelevant: answered ? row.relevant : null,
        // Passed = saw it and said it wasn't part of the occupation.
        passed: answered ? row.relevant === false : null,
      };
    });
    const seen = checks.filter((c) => c.shown);
    const attentionChecks = {
      items: checks,
      shown: seen.length,
      failed: seen.filter((c) => !c.passed).length,
      // Overall pass = every check they were actually shown was rejected.
      passed: seen.length > 0 ? seen.every((c) => c.passed) : null,
    };
    // attnCut on the assignment is authoritative: a cut participant's ratings may lack the failed row.
    if (assignment.attnCut === true) {
      attentionChecks.passed = false;
      attentionChecks.cut = true;
    }
    const rated = clean.filter((r) => !checkTexts.has(r.statement));
    const relevantCount = rated.filter((r) => r.relevant).length;
    // Complete = every task has a relevance verdict.
    const nAnswered = rated.filter((r) => r.relevant !== null).length;
    const complete = rated.length > 0 && nAnswered === rated.length;
    // The tasks on which a top worker outperforms a typical one (optional).
    let taskPerformanceRating = null;
    if (taskPerformance && typeof taskPerformance === 'object') {
      const perTask = Array.isArray(taskPerformance.perTask)
        ? taskPerformance.perTask
            .filter((t) => t && typeof t.statement === 'string')
            .map((t) => ({
              statement: t.statement,
              outperforms: t.outperforms === true,
            }))
        : [];
      taskPerformanceRating = { perTask };
    }
    // Answer log: one entry per selection, for response-time analysis. Capped at 5000 entries.
    const FIELDS = new Set([
      'relevance', 'frequency', 'importance', 'duration',
      'standards', 'verifiability', 'training',
    ]);
    const answerEvents = Array.isArray(events)
      ? events
          .filter((e) => e && FIELDS.has(e.field) && typeof e.statement === 'string')
          .slice(0, 5000)
          .map((e) => ({
            task: Number.isFinite(+e.task) ? +e.task : null,
            statement: e.statement,
            field: e.field,
            value: typeof e.value === 'object' ? null : e.value,
            atMs: Number.isFinite(+e.atMs) ? Math.max(0, Math.round(+e.atMs)) : null,
            iso: typeof e.iso === 'string' ? e.iso : null,
          }))
      : [];

    const record = {
      externalId,
      soc: assignment.soc ?? null,
      occupation: assignment.occupation,
      selfIdOccupation: occupation && typeof occupation === 'object' ? occupation : null,
      // The participant's own job title and duties from the screener.
      screener: screener && typeof screener === 'object'
        ? {
            title: typeof screener.title === 'string' ? screener.title.trim().slice(0, 300) : null,
            duties: typeof screener.duties === 'string' ? screener.duties.trim().slice(0, 4000) : null,
          }
        : null,
      ratings: rated,
      attentionChecks,
      taskPerformance: taskPerformanceRating,
      // Free-text: tasks the participant found unclear or wrongly formulated.
      reviewNotes: typeof notes === 'string' ? notes.trim().slice(0, 4000) : null,
      // Free-text: what they made of the questions (optional, asked last).
      feedback: typeof feedback === 'string' ? feedback.trim().slice(0, 4000) || null : null,
      answerEvents,
      // Two 1-5 items on the final screen: perceived burden and clarity.
      feedbackRatings: feedbackRatings && typeof feedbackRatings === 'object'
        ? {
            burden: clip(feedbackRatings.burden, 1, 5),
            clarity: clip(feedbackRatings.clarity, 1, 5),
          }
        : null,
      n: rated.length,
      nAnswered,
      complete,
      relevantCount,
      // Share of the answered tasks, so an unfinished pass is not read as rejections.
      relevanceShare: nAnswered > 0 ? relevantCount / nAnswered : null,
      elapsedMs: Number.isFinite(elapsedMs) ? elapsedMs : null,
      startedAt: startedAt || null,
      submittedAt: new Date().toISOString(),
    };
    await putResponse(externalId, record);
    // A leftover draft is harmless.
    try {
      await deleteDraft(externalId);
    } catch (err) {
      console.warn('[coverage] draft cleanup failed:', err.message);
    }
    res.json({ ok: true });
  } catch (err) {
    console.error('[coverage] response failed:', err);
    res.status(500).json({ error: 'response failed' });
  }
});

// ── attention-check cut ──────────────────────────────────────────────────────
// The client reports every relevance answer (so it can't tell which are checks); the answer is {cut:true}
// when an attention check is marked Relevant, recorded on the assignment as attnCut. Fails open.
router.post('/api/validation/coverage/attn', async (req, res) => {
  const externalId = safeExternalId(req.body && req.body.externalId);
  if (!externalId) return res.status(400).json({ error: 'externalId required' });
  const statement = typeof (req.body && req.body.statement) === 'string' ? req.body.statement : '';
  const relevant = (req.body && req.body.relevant) === true;
  try {
    if (!(await getFit(externalId))) return res.status(403).json({ error: 'not screened' });
    const inv = await loadInventories();
    const cut = attentionCheckSet(inv).has(statement) && relevant;
    if (cut) {
      const assignment = await getAssignment(externalId);
      if (assignment && assignment.attnCut !== true) {
        await putAssignment(externalId, {
          ...assignment, attnCut: true, attnCutAt: new Date().toISOString(),
        });
      }
    }
    res.json({ cut });
  } catch (err) {
    console.error('[coverage] attn check failed (failing open):', err);
    res.json({ cut: false });
  }
});

router.post('/api/validation/coverage/fit', async (req, res) => {
  const externalId = safeExternalId(req.body && req.body.externalId);
  if (!externalId) return res.status(400).json({ error: 'externalId required' });
  const selectedCode = typeof (req.body && req.body.selectedCode) === 'string' ? req.body.selectedCode.trim() : '';
  const selectedTitle = typeof (req.body && req.body.selectedTitle) === 'string' ? req.body.selectedTitle.trim() : '';
  if (!selectedCode) return res.status(400).json({ error: 'selectedCode required' });
  try {
    const inv = await loadInventories();
    const targets = Object.values(inv.occupations);
    if (!targets.length) return res.status(500).json({ error: 'inventories.json has no occupations' });

    // A participant passes when there is a list for the occupation they picked, and rates that list.
    const matched = listForCode(inv, selectedCode);
    const fit = !!matched;
    const rec = {
      externalId,
      targetCodes: targets.map((o) => o.soc),
      matchedCode: matched ? matched.soc : null,
      matchedTitle: matched ? matched.title : null,
      selectedCode,
      selectedTitle,
      fit,
      reason: fit
        ? `Selected occupation has a published task list (${matched.title}).`
        : 'No task list is published for the selected occupation.',
      at: new Date().toISOString(),
    };
    await putFit(externalId, rec);
    res.json({ fit, reason: rec.reason });
  } catch (err) {
    console.error('[coverage] fit failed:', err);
    res.status(500).json({ error: 'fit failed' });
  }
});

export default router;
