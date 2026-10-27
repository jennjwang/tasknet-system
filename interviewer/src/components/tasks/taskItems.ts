// Task-list helpers for the task review: attention-check cards and the kept/confirmed tests.
import { TaskItem } from "../../types";

// Build TaskItem cards for `count` attention checks, a random subset of the pool.
export function buildAttentionCheckItems(pool: string[], count: number): TaskItem[] {
  if (count <= 0) return [];
  const shuffled = [...pool];
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }
  return shuffled.slice(0, count).map((name) => ({
    name,
    originalName: name,
    status: "unreviewed" as const,
    isAttentionCheck: true,
  }));
}

// Intersperse attention checks among the real tasks at even intervals, never at
// index 0 (the first card orients the participant) and preferably not trailing.
// Real-task order is preserved.
export function spliceAttentionChecks(
  real: TaskItem[],
  checks: TaskItem[],
): TaskItem[] {
  if (checks.length === 0) return real;
  if (real.length <= 1) return [...real, ...checks];
  const interval = Math.max(1, Math.floor(real.length / (checks.length + 1)));
  const out: TaskItem[] = [];
  let ci = 0;
  for (let i = 0; i < real.length; i++) {
    out.push(real[i]);
    if (ci < checks.length && (i + 1) % interval === 0 && i + 1 < real.length) {
      out.push(checks[ci++]);
    }
  }
  // Safety net: append any checks the interval math didn't place.
  while (ci < checks.length) out.push(checks[ci++]);
  return out;
}

// Confirmed or edited — the participant said they do it.
export const isConfirmed = (t: TaskItem) => t.status === "confirmed" || t.status === "edited";
// The participant's real confirmed work: never an attention check (one answered
// "yes" is "confirmed" but must not pollute the list).
export const isKept = (t: TaskItem) => isConfirmed(t) && !t.isAttentionCheck;

// Levenshtein distance — counts insertions, deletions, AND substitutions.
// Recorded per edit (TaskEdit.charsChanged) in the saved edit history.
export function levenshtein(a: string, b: string): number {
  if (a === b) return 0;
  if (!a.length) return b.length;
  if (!b.length) return a.length;
  const m = a.length,
    n = b.length;
  const prev = new Array<number>(n + 1);
  const curr = new Array<number>(n + 1);
  for (let j = 0; j <= n; j++) prev[j] = j;
  for (let i = 1; i <= m; i++) {
    curr[0] = i;
    for (let j = 1; j <= n; j++) {
      const cost = a.charCodeAt(i - 1) === b.charCodeAt(j - 1) ? 0 : 1;
      curr[j] = Math.min(
        curr[j - 1] + 1, // insertion
        prev[j] + 1, // deletion
        prev[j - 1] + cost, // substitution
      );
    }
    for (let j = 0; j <= n; j++) prev[j] = curr[j];
  }
  return prev[n];
}
