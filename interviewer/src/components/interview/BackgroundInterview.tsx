import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useWorkflowStore } from "../../store";
import { plannerNext } from "../../lib/api";
import { LoadingDots } from "../ui";
import { OPENING_PLACEHOLDER, OPENING_QUESTION, OUTRO_TEXT } from "../../settings";
import { FADE_MS, QuestionCard, wait } from "./QuestionCard";

// The interview (phase 'background'), on the shared question card. The adaptive
// planner drives every question after the opener via /api/planner-next. The
// opener alone is client-side (shown verbatim, the same for every participant);
// every answer is captured in the backgroundTranscript, the sole grounding for
// downstream steps. With the screener on, its two answers are already in the
// transcript, and the interview resumes from them (see the resume effect below).



export function BackgroundInterview() {
  const { setPhase, addBackgroundTurn, screener, screenerEnabled } = useWorkflowStore(
    useShallow((s) => ({
      setPhase: s.setPhase,
      addBackgroundTurn: s.addBackgroundTurn,
      screener: s.screener,
      screenerEnabled: s.screenerEnabled,
    })),
  );

  // Adaptive planner: every question after the opener is chosen by
  // /api/planner-next, which processes each answer server-side (extracts tasks,
  // strikes covered spine topics, decides spine vs emergent vs stop).
  const plannerStateRef = useRef<unknown>(null);
  const plannerTurnsRef = useRef<{ question: string; answer: string }[]>([]);
  // What the planner has actually done, for the header bar: how many spine topics
  // are still uncovered (out of spineTotal), which phase it's in, and how many
  // emergent turns have run. Turn count alone can't tell you — the planner ends
  // anywhere from 9 to 17 turns, so a fixed denominator freezes or jumps.
  const [plannerProgress, setPlannerProgress] = useState<{
    remaining: number;
    total: number;
    phase: string;
    emergentTurns: number;
  } | null>(null);

  /** Fold a planner reply into the progress state. */
  const notePlannerProgress = (r: {
    phase?: string;
    spineTotal?: number;
    state?: unknown;
  }) => {
    const st = (r.state ?? {}) as { remaining?: unknown[] };
    setPlannerProgress((prev) => ({
      remaining: Array.isArray(st.remaining) ? st.remaining.length : 0,
      total: r.spineTotal || prev?.total || 8,
      phase: r.phase || "spine",
      emergentTurns:
        (prev?.emergentTurns ?? 0) + (r.phase === "emergent" ? 1 : 0),
    }));
  };

  // The current question shown after the opener (set from the planner's reply);
  // null means the opener is showing.
  const [followUpQ, setFollowUpQ] = useState<string | null>(null);

  const [isEvaluating, setIsEvaluating] = useState(false);
  // The planner call for the next question failed. The participant's answer is
  // already recorded; the screen shows a plain apology and a Retry that re-asks
  // the planner with the same turns (see requestNextQuestion).
  const [plannerError, setPlannerError] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [questionVisible, setQuestionVisible] = useState(true);

  // Intro screens, shown ONCE per participant, up front: with the screener on,
  // the screener showed them (a screener record means they've been seen), so the
  // interview skips them.
  const [showIntro, setShowIntro] = useState(!screener);

  // Closing thank-you screen, shown after the last question
  const [showOutro, setShowOutro] = useState(false);

  const finishOutro = () => {
    setIsSubmitting(true);
    // With the screener on, the occupation self-ID already happened before the
    // interview (it was the gate), so go straight to task selection. With it
    // off, the self-ID screen still runs here, as it always did.
    setPhase(screenerEnabled ? "task-selection" : "occupation-select");
  };

  const question = followUpQ ?? OPENING_QUESTION;
  const placeholder = followUpQ ? "Your answer…" : OPENING_PLACEHOLDER;

  // Continuous progress (0–1) for the header bar, from what the planner has
  // actually covered rather than a turn count. A turn count can't work: real
  // interviews end anywhere from 9 to 17 turns, so a fixed denominator either
  // freezes (62% of interviews ran past 13, sitting at the clamp for ~3 questions)
  // or jumps at the end. Bands: spine 0.05→0.70 by topics struck, emergent
  // 0.70→0.90 easing out (its length is decided by the self-stop, so it can only
  // be approached, never completed), shadow 0.95, outro 1. Monotonic throughout.
  const spineDone = plannerProgress
    ? (plannerProgress.total - plannerProgress.remaining) / plannerProgress.total
    : 0;
  const interviewProgress = !plannerProgress
    ? 0.05
    : plannerProgress.phase === "shadow"
      ? 0.95
      : plannerProgress.phase === "emergent"
        ? 0.7 + 0.2 * (1 - Math.exp(-plannerProgress.emergentTurns / 3))
        : 0.05 + 0.65 * spineDone;
  const progress = showIntro ? 0 : showOutro ? 1 : interviewProgress;

  // The interview ends when the planner is done: fade out, show the outro.
  const finishInterview = async () => {
    setQuestionVisible(false);
    await wait(FADE_MS);
    setFollowUpQ(null);
    setShowOutro(true);
    setQuestionVisible(true);
  };

  // Planner turn: record the answer, ask /api/planner-next for the next question
  // (it processes the answer server-side: extracts tasks, strikes covered spine
  // topics, decides spine vs emergent vs stop), and show it — or finish when the
  // planner is done (the shadow catch-all was the last question).
  const advance = async (answer: string) => {
    const isFollowUp = plannerTurnsRef.current.length > 0;
    addBackgroundTurn({
      field: "planner",
      question,
      answer,
      isFollowUp,
      timestamp: Date.now(),
    });
    plannerTurnsRef.current.push({ question, answer });
    await requestNextQuestion();
  };

  // Ask /api/planner-next for the question after the turns recorded so far.
  // Called after each answer, and again by the Retry button when it failed.
  const requestNextQuestion = async () => {
    setPlannerError(false);
    setQuestionVisible(false);
    setIsEvaluating(true);
    try {
      console.log(
        `[planner] turn ${plannerTurnsRef.current.length} → calling /api/planner-next`,
      );
      const r = await plannerNext(
        plannerTurnsRef.current,
        plannerStateRef.current,
      );
      console.log(
        `[planner] ← phase=${r.phase} done=${r.done} stop=${r.stopReason ?? "-"} q=${JSON.stringify(r.question)}`,
      );
      plannerStateRef.current = r.state;
      notePlannerProgress(r);
      if (r.done || !r.question) {
        await finishInterview();
        return;
      }
      await wait(FADE_MS);
      setFollowUpQ(r.question);
      setIsEvaluating(false);
      setQuestionVisible(true);
    } catch (e) {
      // Show the participant a plain apology and a Retry instead of silently ending
      // the interview. The developer detail goes to the console only: in local dev a
      // 404 here almost always means the API server wasn't restarted (it doesn't
      // hot-reload) -- Ctrl-C the whole `npm run dev`, not just the browser.
      console.error(
        "[planner] /api/planner-next FAILED (in dev, a 404 usually means the API server needs a full restart):",
        e,
      );
      setPlannerError(true);
      setIsEvaluating(false);
      setQuestionVisible(true);
    }
  };

  // RESUME across a reload, and the start after the screener. Hydration restores
  // the saved transcript into the store, but this component remounts with fresh
  // refs (plannerTurnsRef = []) and would otherwise re-ask the opener — appending
  // DUPLICATE opening turns and restarting the planner. If saved planner turns
  // exist, rebuild the refs from them and jump straight to the NEXT question. The
  // server re-derives coverage from the turns (state = null), so it skips
  // already-covered topics.
  const didResumeRef = useRef(false);
  useEffect(() => {
    if (didResumeRef.current) return;
    didResumeRef.current = true;
    const prior = useWorkflowStore
      .getState()
      .backgroundTranscript.filter(
        (t) => t.field === "planner" && t.answer?.trim(),
      );
    if (prior.length === 0) return; // fresh interview — normal opener flow
    plannerTurnsRef.current = prior.map((t) => ({
      question: t.question,
      answer: t.answer,
    }));
    setQuestionVisible(false);
    setIsEvaluating(true);
    (async () => {
      try {
        const r = await plannerNext(plannerTurnsRef.current, null);
        plannerStateRef.current = r.state;
        notePlannerProgress(r);
        if (r.done || !r.question) {
          await finishInterview();
          return;
        }
        setFollowUpQ(r.question);
      } catch (e) {
        // Resume failed — fall back to the normal opener so the participant
        // isn't stuck (worst case is the prior restart behavior, not a dead end).
        console.error("[planner] resume failed:", e);
      } finally {
        setIsEvaluating(false);
        setQuestionVisible(true);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (isSubmitting) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-white gap-4">
        <LoadingDots dot="w-2.5 h-2.5 bg-indigo-300" />
        <p className="text-sm text-slate-500">Building your task list…</p>
      </div>
    );
  }

  return (
    <QuestionCard
      heading="Interview"
      progress={progress}
      intro={showIntro}
      onIntroDone={() => setShowIntro(false)}
      question={question}
      placeholder={placeholder}
      questionVisible={questionVisible}
      isEvaluating={isEvaluating}
      slowLabels={["Thinking it through…", "Following up on that…"]}
      onSubmit={advance}
      outro={showOutro ? { text: OUTRO_TEXT, onContinue: finishOutro } : null}
      error={plannerError ? { onRetry: requestNextQuestion } : null}
    />
  );
}
