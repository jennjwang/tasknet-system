import { useEffect, useMemo, useRef, useState } from "react";
import { CoverageTaskCards, TaskRating, blankRating } from "./CoverageTaskCards";
import { CoverageTaskPerformance, TaskPerformanceRating } from "./CoverageTaskPerformance";
import { CoverageReview } from "./CoverageReview";
import { CoverageFeedback, FeedbackRatings } from "./CoverageFeedback";
import { Frame, Centered, resolveExternalId } from "./validationUi";
import { CoverageIntro } from "./CoverageIntro";
import { CoverageAbout } from "./CoverageAbout";
import { CoverageScope } from "./CoverageScope";
import { OccupationScreener } from "./OccupationScreener";
import {
  ValidationOccupationSelect,
  OccupationPick,
} from "./ValidationOccupationSelect";
import {
  loadBestDraft, saveDraft, saveDraftRemote, clearDraft, AnswerEvent,
} from "../lib/coverageDraft";
import {
  assignCoverage,
  submitCoverageResponse,
  checkAttention,
  judgeFit,
  CoverageAssignment,
} from "../lib/coverageApi";

// The validation survey. Standalone entry (rendered by interviewer/src/main.tsx on ?phase=validation); it
// does not use the interview's phase machine or store.
//
// Flow: about this study → occupation screener (title + duties) → occupation pick, which routes to that
// occupation's list (no list = screened out) → instructions → scope → rate each task (relevance, then
// frequency, importance, duration, evaluability, training) → review → task performance (where top
// workers outperform) → feedback → submit.

type Stage =
  | "intro"
  | "about"
  | "scope"
  | "screen"
  | "occupation"
  | "judging"
  | "assigning"
  | "cards"
  | "review"
  | "taskPerformance"
  | "feedback"
  | "done"
  | "screened-out"
  | "error";

// One selection on a rating card, before the parent stamps its time (see AnswerEvent).
type Answer = Omit<AnswerEvent, "atMs" | "iso">;

// assign's refusal for an occupation with no published list: the participant was screened out.
const NO_LIST = /no task list for the selected occupation/i;

export function CoverageStudy() {
  const externalId = useMemo(resolveExternalId, []);
  const [stage, setStage] = useState<Stage>("intro");
  const [assignment, setAssignment] = useState<CoverageAssignment | null>(null);
  const [ratings, setRatings] = useState<TaskRating[]>([]);
  const [screenerInfo, setScreenerInfo] = useState<{ title: string; duties: string } | null>(null);
  const [occupation, setOccupation] = useState<OccupationPick | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [reviewNotes, setReviewNotes] = useState("");
  const [feedback, setFeedback] = useState("");
  const [feedbackRatings, setFeedbackRatings] = useState<FeedbackRatings>({
    burden: null,
    clarity: null,
  });
  // Answer log: one entry per selection, for response-time analysis. `atMs` is
  // relative to study start so it survives clock skew; `iso` keeps the wall time.
  const [events, setEvents] = useState<AnswerEvent[]>([]);
  // Local draft: where they are in the rating pass, so a break doesn't lose it.
  const [cardAnswers, setCardAnswers] = useState<(TaskRating | null)[]>([]);
  const [cardIdx, setCardIdx] = useState(0);
  const [restored, setRestored] = useState(false); // a draft was picked up on load
  const [performance, setPerformance] = useState<TaskPerformanceRating | null>(null);
  const [startedAt] = useState(() => new Date().toISOString());
  // Save after every change once the rating pass has started. Cheap (a few KB) and
  // synchronous, so it survives a tab close as well as a refresh.
  const RESUMABLE = ["cards", "review", "taskPerformance", "feedback"];
  useEffect(() => {
    if (!RESUMABLE.includes(stage)) return;
    const draft = {
      stage, cardAnswers, cardIdx, ratings, reviewNotes, feedback, performance, events,
      screener: screenerInfo, feedbackRatings, occupation,
    };
    saveDraft(externalId, draft);       // immediate, local
    saveDraftRemote(externalId, draft); // debounced, cross-device
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, cardAnswers, cardIdx, ratings, reviewNotes, feedback, performance, events, screenerInfo, feedbackRatings, occupation]);

  const logAnswer = (e: Answer) => {
    const now = new Date();
    setEvents((prev) => [
      ...prev,
      { ...e, atMs: now.getTime() - new Date(startedAt).getTime(), iso: now.toISOString() },
    ]);
  };

  // ── attention-check early cut ──────────────────────────────────────────────
  // Every relevance answer is reported to the server, which alone knows the planted checks. On
  // {cut:true} the participant finishes the check's card, and when they advance past it the study
  // ends on the normal thank-you screen with no mention of the check. Refs, not state, because the
  // verdict arrives after this closure's state has gone stale.
  const cutAtRef = useRef<number | null>(null); // card index of the failed check, once verdicted
  const cutDoneRef = useRef(false);
  const cardIdxRef = useRef(0);
  const cardAnswersRef = useRef<(TaskRating | null)[]>([]);
  const finishCut = () => {
    if (cutDoneRef.current) return;
    cutDoneRef.current = true;
    const listed = assignment ? assignment.statements : [];
    submitAll(
      listed.map((s, i) => cardAnswersRef.current[i] ?? blankRating(s, null)),
      null,
      true, // quiet: thank-you screen first, no flash of the next card
    );
  };
  const onCardAnswer = (e: Answer) => {
    logAnswer(e);
    if (e.field !== "relevance" || cutAtRef.current !== null) return;
    void checkAttention(externalId, e.statement, e.value === true).then((cut) => {
      if (!cut || cutAtRef.current !== null) return;
      cutAtRef.current = e.task;
      // Slow verdict, fast participant: if they have already moved past the check's card,
      // the between-cards moment this waits for has been and gone — end now.
      if (cardIdxRef.current > e.task) finishCut();
    });
  };

  const handlePass = (info: { title: string; duties: string }) => {
    setScreenerInfo(info);
    setStage("occupation");
  };

  const handleOccupation = async (pick: OccupationPick) => {
    setOccupation(pick);
    setStage("judging");
    try {
      // Is a list published for the picked occupation?
      const verdict = await judgeFit(
        externalId,
        pick.selectedCode,
        pick.selectedTitle,
      );
      if (!verdict.fit) {
        setStage("screened-out");
        return;
      }
      setStage("assigning");
      const a = await assignCoverage(externalId);
      // Already finished (or cut): the thank-you screen, never a second pass.
      if (a.completed) {
        setStage("done");
        return;
      }
      setAssignment(a);
      setStage("about");   // instructions, then the first task
    } catch (e) {
      const msg = String((e as Error)?.message || e);
      if (NO_LIST.test(msg)) {
        setStage("screened-out");
        return;
      }
      setError(msg);
      setStage("error");
    }
  };

  // After the rating cards: keep the ratings and go to the task-performance stage on
  // the Relevant tasks. If nothing was Relevant, there is no task-performance step — submit.
  const handleCardsDone = (rs: TaskRating[]) => {
    setRatings(rs);
    // The resume position applies to the first load only; Back from review lands on the last task.
    setRestored(false);
    // A pending cut whose check was the LAST card lands here instead of onProgress:
    // submit straight to the thank-you screen, skipping review/performance/feedback.
    if (cutAtRef.current !== null) {
      if (!cutDoneRef.current) {
        cutDoneRef.current = true;
        submitAll(rs, null, true); // quiet: straight to the thank-you screen
      }
      return;
    }
    if (rs.some((r) => r.relevant)) setStage("review");
    else submitAll(rs, null);
  };

  // Review-screen removal: drop a task from the relevant set (it won't be
  // time-allocated or counted; recorded as not relevant).
  const removeRelevant = (statement: string) => {
    setRatings((prev) =>
      prev.map((r) => (r.statement === statement ? blankRating(r.statement, false) : r)),
    );
  };

  // Resume a saved draft (local or server copy, whichever is newer): re-fetch the sticky
  // assignment and drop the participant back where they were.
  useEffect(() => {
    (async () => {
      const draft = await loadBestDraft(externalId);
      // Fetched even with no draft, so a finished participant reopens onto the thank-you
      // screen. A fresh participant has no fit record yet, so this throws and the intro proceeds.
      try {
        const a = await assignCoverage(externalId);
        if (a.completed) {
          setStage("done");
          return;
        }
        if (!draft) return;
        setAssignment(a);
        setCardAnswers(draft.cardAnswers ?? []);
        setCardIdx(draft.cardIdx ?? 0);
        setRatings(draft.ratings ?? []);
        setReviewNotes(draft.reviewNotes ?? "");
        setFeedback(draft.feedback ?? "");
        setPerformance(draft.performance ?? null);
        setEvents(draft.events ?? []);
        setScreenerInfo(draft.screener ?? null);
        setFeedbackRatings(draft.feedbackRatings ?? { burden: null, clarity: null });
        setOccupation(draft.occupation ?? null);
        setRestored(true);
        setStage(draft.stage as Stage);
      } catch (e) {
        // Already screened out: reopening the link lands back on the screen-out.
        if (NO_LIST.test(String((e as Error)?.message || e))) {
          setStage("screened-out");
          return;
        }
        // Not screened yet, assignment gone, or unreachable: the normal flow proceeds and
        // a draft with no usable assignment is dropped.
        if (draft) clearDraft(externalId);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const submitAll = async (
    rs: TaskRating[],
    tp: TaskPerformanceRating | null,
    // quiet (the attention-cut path): show the thank-you screen first and submit behind it,
    // swallowing failure — the cut is already recorded on the assignment.
    quiet = false,
  ) => {
    if (quiet) setStage("done");
    setSubmitting(true);
    try {
      await submitCoverageResponse(externalId, rs, tp, {
        startedAt,
        elapsedMs: Date.now() - new Date(startedAt).getTime(),
        occupation,
        screener: screenerInfo,
        notes: reviewNotes,
        feedback,
        feedbackRatings,
        events,
      });
      clearDraft(externalId);
      setStage("done");
    } catch (e) {
      if (quiet) {
        console.warn("[coverage] quiet submit failed (cut already recorded):", e);
      } else {
        setError(String((e as Error)?.message || e));
        setStage("error");
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (stage === "intro") {
    return (
      <Frame>
        <CoverageIntro onContinue={() => setStage("screen")} />
      </Frame>
    );
  }

  // Instructions come after screening, immediately before the first task.
  if (stage === "about") {
    return (
      <Frame>
        <CoverageAbout onContinue={() => setStage("scope")} />
      </Frame>
    );
  }

  // Last screen before card 1 (see CoverageScope).
  if (stage === "scope") {
    return (
      <Frame>
        <CoverageScope onContinue={() => setStage("cards")} />
      </Frame>
    );
  }

  if (stage === "screen") {
    return (
      <Frame>
        <OccupationScreener onPass={handlePass} />
      </Frame>
    );
  }

  if (stage === "occupation" && screenerInfo) {
    return (
      <Frame>
        <ValidationOccupationSelect
          title={screenerInfo.title}
          duties={screenerInfo.duties}
          onSelect={handleOccupation}
        />
      </Frame>
    );
  }

  if (stage === "judging") {
    return (
      <Frame>
        <Centered>
          <p className="text-sm text-slate-400">Checking your responses…</p>
        </Centered>
      </Frame>
    );
  }

  if (stage === "assigning") {
    return (
      <Frame>
        <Centered>
          <p className="text-sm text-slate-400">Loading…</p>
        </Centered>
      </Frame>
    );
  }

  if (stage === "screened-out") {
    // Worded as the study looking for other roles, not as a judgement of the participant.
    return (
      <Frame>
        <Centered>
          <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-slate-100 border border-slate-200 mb-7">
            <svg
              className="w-6 h-6 text-slate-400"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="11" x2="12" y2="16.5" />
              <line x1="12" y1="7.75" x2="12.01" y2="7.75" />
            </svg>
          </div>
          <h1 className="text-[1.6rem] font-light text-slate-800 leading-snug tracking-tight mb-3">
            This study is looking for a different role.
          </h1>
          <p className="text-sm text-slate-500 leading-relaxed mb-8">
            We're recruiting people in a particular set of occupations, and the
            one you selected isn't among them this time. Thank you for taking
            the time.
          </p>
          <p className="text-sm text-slate-400">
            Your response has been recorded. You can close this tab.
          </p>
        </Centered>
      </Frame>
    );
  }

  if (stage === "error") {
    return (
      <Frame>
        <Centered>
          <h1 className="text-2xl font-light text-slate-800">
            Something went wrong
          </h1>
          <p className="mt-3 text-sm text-slate-500">{error}</p>
        </Centered>
      </Frame>
    );
  }

  if (stage === "done") {
    return (
      <Frame>
        <Centered>
          <h1 className="text-2xl font-light text-slate-800">Thank you!</h1>
          <p className="mt-3 text-sm text-slate-500">
            Your responses have been recorded.
          </p>
          <p className="mt-3 text-sm text-slate-500">
            You may close this window.
          </p>
        </Centered>
      </Frame>
    );
  }

  if (stage === "cards" && assignment) {
    return (
      <Frame>
        <CoverageTaskCards
          statements={assignment.statements}
          // The occupation they picked (the assigned list's title as a fallback).
          occupation={occupation?.selectedTitle || assignment.occupation}
          submitting={submitting}
          initial={
            ratings.length ? ratings : cardAnswers.length ? cardAnswers : undefined
          }
          startAt={restored ? cardIdx : undefined}
          onProgress={(answers, at) => {
            cardAnswersRef.current = answers;
            cardIdxRef.current = at;
            setCardAnswers(answers);
            setCardIdx(at);
            // A pending cut fires the moment they advance PAST the failed check's card — its
            // completed rating is in this snapshot, so the submitted response includes it.
            if (cutAtRef.current !== null && at > cutAtRef.current) finishCut();
          }}
          onAnswer={onCardAnswer}
          onDone={handleCardsDone}
        />
      </Frame>
    );
  }

  if (stage === "review") {
    return (
      <Frame>
        <CoverageReview
          statements={ratings.filter((r) => r.relevant).map((r) => r.statement)}
          onRemove={removeRelevant}
          onBack={() => setStage("cards")}
          notes={reviewNotes}
          onNotesChange={setReviewNotes}
          onContinue={() => {
            if (ratings.some((r) => r.relevant)) setStage("taskPerformance");
            else submitAll(ratings, null);
          }}
        />
      </Frame>
    );
  }

  if (stage === "taskPerformance") {
    return (
      <Frame full>
        <CoverageTaskPerformance
          statements={ratings.filter((r) => r.relevant).map((r) => r.statement)}
          submitting={submitting}
          onBack={() => setStage("review")}
          onDone={(tp) => {
            setPerformance(tp);
            setStage("feedback");
          }}
        />
      </Frame>
    );
  }

  if (stage === "feedback") {
    return (
      <Frame>
        <CoverageFeedback
          value={feedback}
          onChange={setFeedback}
          ratings={feedbackRatings}
          onRatingsChange={setFeedbackRatings}
          submitting={submitting}
          onBack={() => setStage("taskPerformance")}
          onSubmit={() => submitAll(ratings, performance)}
        />
      </Frame>
    );
  }

  return null;
}
