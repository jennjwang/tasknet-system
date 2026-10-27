// Client for the validation survey's API (validation/server/validation.js).
import type { TaskRating } from "../components/CoverageTaskCards";
import type { TaskPerformanceRating } from "../components/CoverageTaskPerformance";

export interface CoverageAssignment {
  assignmentId: string;
  occupation: string;
  statements: string[]; // attention checks included, unflagged
  // Already submitted (or cut for a failed attention check — deliberately indistinguishable):
  // show the thank-you screen, never a second pass through the survey.
  completed?: boolean;
}

interface FitResult {
  fit: boolean;
  reason: string | null;
}

// The screener: fit is true when a list is published for the selected O*NET code.
export async function judgeFit(
  externalId: string,
  selectedCode: string,
  selectedTitle: string,
): Promise<FitResult> {
  const res = await fetch("/api/validation/coverage/fit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ externalId, selectedCode, selectedTitle }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function assignCoverage(
  externalId: string,
): Promise<CoverageAssignment> {
  const res = await fetch("/api/validation/coverage/assign", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ externalId }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// Reports one relevance answer for attention grading. Called for EVERY task so the planted
// checks stay indistinguishable on the wire; returns true exactly when this answer marked an
// attention check Relevant and the study should quietly end. Fails soft: a network hiccup must
// never interrupt a real rater, so any failure reads as "carry on".
export async function checkAttention(
  externalId: string,
  statement: string,
  relevant: boolean,
): Promise<boolean> {
  try {
    const res = await fetch("/api/validation/coverage/attn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ externalId, statement, relevant }),
    });
    if (!res.ok) return false;
    const d = await res.json();
    return d.cut === true;
  } catch {
    return false;
  }
}

export async function submitCoverageResponse(
  externalId: string,
  ratings: TaskRating[],
  taskPerformance: TaskPerformanceRating | null,
  meta: {
    startedAt?: string;
    elapsedMs?: number;
    occupation?: unknown;
    screener?: { title: string; duties: string } | null;
    notes?: string;
    feedback?: string;
    feedbackRatings?: { burden: number | null; clarity: number | null };
    events?: unknown[];
  } = {},
): Promise<void> {
  const res = await fetch("/api/validation/coverage/response", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      externalId,
      ratings,
      taskPerformance: taskPerformance ?? null,
      startedAt: meta.startedAt ?? null,
      elapsedMs: meta.elapsedMs ?? null,
      occupation: meta.occupation ?? null,
      screener: meta.screener ?? null,
      notes: meta.notes ?? null,
      feedback: meta.feedback ?? null,
      feedbackRatings: meta.feedbackRatings ?? null,
      events: meta.events ?? null,
    }),
  });
  if (!res.ok) throw new Error(await res.text());
}
