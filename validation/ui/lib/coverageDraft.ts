// Draft of an in-progress survey, so a participant can close the tab and pick up where they left off.
// Kept in two places:
//   localStorage — synchronous and works offline; keyed by participant id.
//   the server   — POST /api/validation/coverage/draft, so a participant can resume on another device
//                  (only when the id is in the URL; see resolveExternalId). Failures are swallowed.
// loadBestDraft() takes whichever copy is newer. Both are cleared on submit.

import type { TaskRating } from "../components/CoverageTaskCards";
import type { TaskPerformanceRating } from "../components/CoverageTaskPerformance";
import type { FeedbackRatings } from "../components/CoverageFeedback";
import type { OccupationPick } from "../components/ValidationOccupationSelect";

const VERSION = 1;
const KEY = (externalId: string) => `coverage_draft_v${VERSION}:${externalId}`;

// One selection, stamped relative to study start. Enough to reconstruct how long
// each question and each task statement took: sort by atMs and diff.
export interface AnswerEvent {
  task: number; // index into the assigned statements
  statement: string;
  field: string; // relevance | frequency | importance | duration | standards | verifiability | training
  value: string | number | boolean;
  atMs: number; // ms since the study started
  iso: string;
}

interface CoverageDraft {
  stage: string;
  cardAnswers: (TaskRating | null)[]; // per assigned statement; null = not yet rated
  cardIdx: number; // where they were in the rating pass
  ratings: TaskRating[]; // set once the pass is finished
  screener: { title: string; duties: string } | null; // the screener answers
  occupation: OccupationPick | null; // their self-identified occupation
  reviewNotes: string;
  feedback: string;
  feedbackRatings: FeedbackRatings;
  performance: TaskPerformanceRating | null;
  events: AnswerEvent[];
  savedAt: string;
}

function loadDraft(externalId: string): CoverageDraft | null {
  if (!externalId) return null;
  try {
    const raw = window.localStorage.getItem(KEY(externalId));
    if (!raw) return null;
    const d = JSON.parse(raw);
    return d && typeof d.stage === "string" ? (d as CoverageDraft) : null;
  } catch {
    return null; // corrupt or unavailable storage — start fresh rather than break
  }
}

export function saveDraft(externalId: string, draft: Omit<CoverageDraft, "savedAt">) {
  if (!externalId) return;
  try {
    window.localStorage.setItem(
      KEY(externalId),
      JSON.stringify({ ...draft, savedAt: new Date().toISOString() }),
    );
  } catch {
    // Private mode / quota — losing resume is acceptable, blocking the study isn't.
  }
}

export function clearDraft(externalId: string) {
  if (!externalId) return;
  // Cancel a pending sync too: firing after submit would re-insert the draft the server just deleted, and
  // reopening the link would then resume a finished survey.
  pending = null;
  if (syncTimer) {
    clearTimeout(syncTimer);
    syncTimer = null;
  }
  try {
    window.localStorage.removeItem(KEY(externalId));
  } catch {
    /* nothing to do */
  }
}

// ── server mirror ───────────────────────────────────────────────────────────
// Debounced with a ceiling: send after SYNC_MS of quiet, and at least every MAX_WAIT_MS of continuous
// activity (a plain debounce never fires for someone answering steadily). Also flushed on pagehide.
const SYNC_MS = 4000;
const MAX_WAIT_MS = 20000;
let syncTimer: ReturnType<typeof setTimeout> | null = null;
let lastSentAt = 0;
let pending: { externalId: string; draft: Omit<CoverageDraft, "savedAt"> } | null = null;
let flushBound = false;

function send() {
  if (syncTimer) {
    clearTimeout(syncTimer);
    syncTimer = null;
  }
  if (!pending) return;
  const body = JSON.stringify({ externalId: pending.externalId, draft: pending.draft });
  pending = null;
  lastSentAt = Date.now();
  // keepalive caps the body at 64 KB and a long pass's draft exceeds it, so use it only for small bodies.
  const small = body.length < 60_000;
  void fetch("/api/validation/coverage/draft", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
    keepalive: small,
  }).catch(() => {
    /* offline or rejected — localStorage already has it */
  });
}

export function saveDraftRemote(externalId: string, draft: Omit<CoverageDraft, "savedAt">) {
  if (!externalId) return;
  pending = { externalId, draft };
  if (!flushBound && typeof window !== "undefined") {
    flushBound = true;
    // pagehide fires on tab close and on mobile backgrounding, where unload does not.
    window.addEventListener("pagehide", send);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") send();
    });
  }
  if (lastSentAt && Date.now() - lastSentAt >= MAX_WAIT_MS) {
    send();          // continuous activity: the ceiling has been reached, do not wait for quiet
    return;
  }
  if (!lastSentAt) {
    send();          // first change of the pass: get something on the server immediately
    return;
  }
  if (syncTimer) clearTimeout(syncTimer);
  syncTimer = setTimeout(send, SYNC_MS);
}

async function loadDraftRemote(externalId: string): Promise<CoverageDraft | null> {
  if (!externalId) return null;
  try {
    const res = await fetch(
      `/api/validation/coverage/draft?externalId=${encodeURIComponent(externalId)}`,
    );
    if (!res.ok) return null;
    const d = (await res.json())?.draft;
    return d && typeof d.stage === "string" ? (d as CoverageDraft) : null;
  } catch {
    return null; // unreachable — fall back to whatever is local
  }
}

/** The newer of the local and server drafts. Either may be absent. */
export async function loadBestDraft(externalId: string): Promise<CoverageDraft | null> {
  const local = loadDraft(externalId);
  const remote = await loadDraftRemote(externalId);
  if (!remote) return local;
  if (!local) return remote;
  // Compares wall clocks across devices; a tie or an unparseable stamp keeps the local copy.
  const t = (d: CoverageDraft) => Date.parse(d.savedAt || "") || 0;
  return t(remote) > t(local) ? remote : local;
}
