// Coverage scoring. Reads the task-rating responses (Postgres when VALIDATION_DATABASE_URL is set, else
// validation/tools/out/) and reports, per occupation (each rated its own list):
//
//   PRIMARY     endorsement rate (share of the list marked relevant) and endorsed count. The participant
//               is the unit of analysis, so CIs are over participants, never over task rows.
//   ITEM        per statement, share of raters calling it relevant, Wilson CI.
//   RELIABILITY chance-corrected agreement (Krippendorff's alpha) and rank agreement (Spearman).
//   BURDEN      completion time and the self-reported burden/clarity items.
//
// Evaluability and top-worker differentiation print descriptively only.
//
// Usage (from the repo root; add --env-file=interviewer/.env to read Postgres):
//   node validation/tools/coverage/coverage.mjs
//   MIN_MINUTES=5 node validation/tools/coverage/coverage.mjs
import {
  loadCoverageResponses,
  loadCoverageFits,
  loadCoverageAssignments,
  isTestId,
} from '../lib/io.mjs';
import {
  wilsonCI,
  bootstrapMeanCI,
  krippendorffAlpha,
  spearman,
} from '../lib/stats.mjs';

// ── pre-specified exclusions ──────────────────────────────────────────────────
// Fix these before looking at outcomes.
const MIN_MINUTES = Number(process.env.MIN_MINUTES ?? 5);
const GRADED = ['frequency', 'importance', 'duration', 'training'];
const VERDICT = ['standards', 'verifiability'];

const pct = (x) => (Number.isFinite(x) ? `${(x * 100).toFixed(1)}%` : '—');
const f2 = (x) => (Number.isFinite(x) ? x.toFixed(2) : '—');
const bar = (x, w = 20) => '#'.repeat(Math.round((x || 0) * w));
const head = (t) => console.log(`\n${t}\n${'─'.repeat(t.length)}`);

async function main() {
  const [responses, fits, assignments] = await Promise.all([
    loadCoverageResponses(),
    loadCoverageFits(),
    loadCoverageAssignments(),
  ]);
  const notTest = (x) => !isTestId(x.externalId);
  const real = responses.filter((r) => Array.isArray(r.ratings) && notTest(r));

  head('SAMPLE');
  console.log(`  screened   ${fits.filter(notTest).length}`);
  console.log(`  assigned   ${assignments.filter(notTest).length}`);
  console.log(`  submitted  ${real.length}`);

  const kept = [];
  for (const r of real) {
    const mins = (r.elapsedMs || 0) / 60000;
    const why = [];
    if (mins < MIN_MINUTES) why.push(`under ${MIN_MINUTES} min (${mins.toFixed(1)})`);
    if (r.attentionChecks?.passed === false) why.push('attention check');
    if (why.length) console.log(`    – excluded ${String(r.externalId).slice(0, 8)}…  ${why.join('; ')}`);
    else kept.push(r);
  }
  console.log(`  analysed   ${kept.length}`);
  if (!kept.length) return console.log('\nNothing left to analyse.');

  // One group per occupation, labelled "<title> (<soc>)".
  const groups = new Map();
  for (const r of kept) {
    const k = r.soc ? `${r.occupation || r.soc} (${r.soc})` : '(no occupation)';
    groups.set(k, [...(groups.get(k) || []), r]);
  }

  head('PRIMARY — per occupation (unit of analysis = participant)');
  for (const [occ, rs] of groups) {
    const rate = bootstrapMeanCI(rs.map((r) => r.relevantCount / r.n));
    const cnt = bootstrapMeanCI(rs.map((r) => r.relevantCount));
    console.log(`  ${occ}  (n=${rs.length} participants, ${[...new Set(rs.map((r) => r.n))].join('/')} statements)`);
    console.log(`    endorsement rate  ${pct(rate.mean)}  [${pct(rate.lo)}, ${pct(rate.hi)}]`);
    console.log(`    endorsed count    ${f2(cnt.mean)}   [${f2(cnt.lo)}, ${f2(cnt.hi)}]`);
    console.log(`    per participant   ${rs.map((r) => `${r.relevantCount}/${r.n}`).join('  ')}`);
  }

  head('ITEM LEVEL — share of raters calling each statement relevant');
  for (const [occ, rs] of groups) {
    console.log(`  ${occ}`);
    const items = new Map();
    for (const r of rs) {
      for (const t of r.ratings) {
        const e = items.get(t.statement) || { yes: 0, n: 0 };
        e.n++;
        if (t.relevant) e.yes++;
        items.set(t.statement, e);
      }
    }
    const sorted = [...items].sort((a, b) => a[1].yes / a[1].n - b[1].yes / b[1].n);
    for (const [stmt, e] of sorted) {
      const ci = wilsonCI(e.yes, e.n);
      const share = e.yes / e.n;
      console.log(
        `    ${`${e.yes}/${e.n}`.padEnd(6)}${pct(share).padStart(6)} ` +
          `[${pct(ci.lo)}, ${pct(ci.hi)}]  ${bar(share).padEnd(20)} ${stmt.slice(0, 50)}`,
      );
    }
    console.log(
      `    -> rejected by a majority: ${sorted.filter(([, e]) => e.yes / e.n < 0.5).length} of ${sorted.length}`,
    );
  }

  head("RELIABILITY — Krippendorff's alpha (chance corrected)");
  for (const [occ, rs] of groups) {
    if (rs.length < 2) {
      console.log(`  ${occ}: needs 2+ participants`);
      continue;
    }
    console.log(`  ${occ}  (${rs.length} raters)`);
    const units = (field, relevantOnly) => {
      const m = new Map();
      for (const r of rs) {
        for (const t of r.ratings) {
          if (relevantOnly && !t.relevant) continue;
          const v = field === 'relevance' ? (t.relevant ? 1 : 0) : t[field];
          if (v == null) continue;
          if (!m.has(t.statement)) m.set(t.statement, []);
          m.get(t.statement).push(v);
        }
      }
      return [...m.values()];
    };
    console.log(`    relevance      alpha ${f2(krippendorffAlpha(units('relevance', false), 'nominal'))}  (nominal)`);
    for (const g of GRADED) {
      const u = units(g, true);
      console.log(`    ${g.padEnd(14)} alpha ${f2(krippendorffAlpha(u, 'ordinal'))}  (ordinal, ${u.length} statements)`);
    }
    for (const v of VERDICT) {
      console.log(`    ${v.padEnd(14)} alpha ${f2(krippendorffAlpha(units(v, true), 'nominal'))}  (nominal)`);
    }

    // Raters can use different parts of a scale while ordering the tasks the same way.
    console.log('    rank agreement (mean pairwise Spearman, pairs with 4+ shared tasks):');
    for (const g of GRADED) {
      const rhos = [];
      for (let i = 0; i < rs.length; i++) {
        for (let j = i + 1; j < rs.length; j++) {
          const pick = (r) =>
            new Map(r.ratings.filter((t) => t.relevant && t[g] != null).map((t) => [t.statement, t[g]]));
          const A = pick(rs[i]);
          const B = pick(rs[j]);
          const shared = [...A.keys()].filter((s) => B.has(s));
          if (shared.length < 4) continue;
          const rho = spearman(shared.map((s) => A.get(s)), shared.map((s) => B.get(s)));
          if (Number.isFinite(rho)) rhos.push(rho);
        }
      }
      const mean = rhos.length ? rhos.reduce((a, b) => a + b, 0) / rhos.length : NaN;
      console.log(`      ${g.padEnd(12)} rho ${f2(mean)}  (${rhos.length} pairs)`);
    }
  }

  head('BURDEN');
  for (const [occ, rs] of groups) {
    const mins = rs.map((r) => (r.elapsedMs || 0) / 60000).sort((a, b) => a - b);
    const med = mins[Math.floor(mins.length / 2)];
    console.log(`  ${occ}  median ${f2(med)} min  (range ${f2(mins[0])}–${f2(mins.at(-1))})`);
    console.log(`    burden 1-5   ${rs.map((r) => r.feedbackRatings?.burden ?? '—').join(', ')}`);
    console.log(`    clarity 1-5  ${rs.map((r) => r.feedbackRatings?.clarity ?? '—').join(', ')}`);
    console.log(`    ~${f2(med / (rs[0]?.n || 1))} min per statement`);
  }

  // Time per question: the gap from the previous answer, so it includes reading the question. Gaps over
  // 2 min are dropped as idle.
  head('TIME PER ITEM (from the answer-event log)');
  for (const [occ, rs] of groups) {
    const withEvents = rs.filter((r) => (r.answerEvents || []).length > 1);
    if (!withEvents.length) {
      console.log(`  ${occ}: no answer-event log`);
      continue;
    }
    console.log(`  ${occ}  (${withEvents.length} of ${rs.length} participants have event logs)`);
    const byField = {};
    let idle = 0;
    for (const r of withEvents) {
      const ev = r.answerEvents;
      for (let i = 1; i < ev.length; i++) {
        const g = ev[i].atMs - ev[i - 1].atMs;
        if (g < 0 || g > 120000) { idle++; continue; }
        (byField[ev[i].field] ||= []).push(g);
      }
    }
    const rank = Object.entries(byField)
      .map(([k, v]) => {
        v.sort((a, b) => a - b);
        return { k, n: v.length, med: v[Math.floor(v.length / 2)] / 1000,
                 total: v.reduce((a, b) => a + b, 0) / 1000 };
      })
      .sort((a, b) => b.total - a.total);
    const grand = rank.reduce((a, b) => a + b.total, 0);
    console.log('    question         n   median    total   share of answering time');
    for (const x of rank) {
      console.log(
        `    ${x.k.padEnd(15)} ${String(x.n).padStart(3)}  ${f2(x.med).padStart(6)}s ${f2(x.total).padStart(7)}s  ` +
          `${pct(x.total / grand).padStart(6)} ${bar(x.total / grand, 30)}`,
      );
    }
    if (idle) console.log(`    (${idle} gaps over 2 min dropped as idle)`);

    const spans = [], notRel = [];
    for (const r of withEvents) {
      const byTask = {};
      for (const e of r.answerEvents) (byTask[e.task] ||= []).push(e);
      let prevEnd = r.answerEvents[0].atMs;
      for (const es of Object.values(byTask)) {
        const lead = es[0].atMs - prevEnd;
        const span = es.at(-1).atMs - es[0].atMs;
        prevEnd = es.at(-1).atMs;
        if (lead < 0 || lead > 120000) continue;
        (es.length > 1 ? spans : notRel).push(span + lead);
      }
    }
    const med = (a) => { a.sort((x, y) => x - y); return a.length ? a[Math.floor(a.length / 2)] / 1000 : NaN; };
    console.log(`    median all-in per RELEVANT task  ${f2(med(spans))}s  (n=${spans.length})`);
    console.log(`    median all-in per rejected task  ${f2(med(notRel))}s  (n=${notRel.length})`);
  }

  head('SECONDARY (descriptive — see caveats)');
  for (const [occ, rs] of groups) {
    console.log(`  ${occ}`);
    for (const v of VERDICT) {
      const t = {};
      for (const r of rs) for (const x of r.ratings.filter((y) => y.relevant)) t[x[v]] = (t[x[v]] || 0) + 1;
      console.log(`    ${v.padEnd(14)} ${JSON.stringify(t)}  (n=${Object.values(t).reduce((a, b) => a + b, 0)})`);
    }
    const tp = rs.flatMap((r) => r.taskPerformance?.perTask || []);
    const out = tp.filter((x) => x.outperforms).length;
    console.log(`    top worker outperforms on ${out}/${tp.length} = ${pct(out / tp.length)} of relevant tasks`);
  }

  head('CAVEATS');
  console.log('  · Relevance measures PRECISION, not recall: nothing asks what the');
  console.log('    list MISSES, so "covers the occupation" is only half tested.');
  console.log('  · Verdict items skew hard to "yes": expect healthy raw agreement and');
  console.log('    poor alpha once chance corrected.');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
