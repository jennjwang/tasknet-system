// Balanced random task subsets. With COVERAGE_COHORT_SIZE (N) workers and COVERAGE_RATERS_PER_TASK (k), each
// worker rates about T·k/N of the T statements: the least-loaded ones, ties broken at random, so every task
// ends at k raters rather than merely averaging k.

/** How many statements one participant rates. Full list unless both knobs are set and k < N. */
export function subsetSize(total, ratersPerTask, cohortSize) {
  if (!Number.isFinite(total) || total <= 0) return 0;
  const k = ratersPerTask;
  const n = cohortSize;
  if (!Number.isFinite(k) || !Number.isFinite(n) || k <= 0 || n <= 0 || k >= n) return total;
  return Math.min(total, Math.max(1, Math.round((total * k) / n)));
}

/**
 * Per-statement rater load from the existing assignments. An assignment counts if the worker submitted, or if
 * it is open and younger than openTtlMs. The TTL is a hold, not a lock: once it lapses the tasks are dealt to
 * the next worker, but the lapsed worker keeps their (sticky) list and can still submit, so the worst case is
 * k+1 raters on a task rather than a task left short by a dropout. The clock runs from assignedAt.
 */
export function statementLoads(base, assignments, { now = Date.now(), openTtlMs = 72 * 3600 * 1000 } = {}) {
  const loads = new Map(base.map((s) => [s, 0]));
  for (const a of assignments) {
    if (!a) continue;
    const at = a.assignedAt ? Date.parse(a.assignedAt) : NaN;
    const active = a.responded === true || (Number.isFinite(at) && now - at < openTtlMs);
    if (!active) continue;
    // No stored subset means the worker was served the whole list.
    for (const s of a.statements || base) if (loads.has(s)) loads.set(s, loads.get(s) + 1);
  }
  return loads;
}

/** Pick m statements, least-loaded first with random ties, returned in inventory order. */
export function pickSubset(base, loads, m, rand = Math.random) {
  if (m >= base.length) return base.slice();
  const ranked = base.map((s) => ({ s, load: loads.get(s) ?? 0, r: rand() }));
  ranked.sort((a, b) => (a.load - b.load) || (a.r - b.r));
  const chosen = new Set(ranked.slice(0, m).map((x) => x.s));
  return base.filter((s) => chosen.has(s));
}
