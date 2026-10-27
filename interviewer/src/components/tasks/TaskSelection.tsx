import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import {
  generateTasksFromInterview,
  extractInterviewTasks,
  recordScreenOut,
} from "../../lib/api";
import { useWorkflowStore } from "../../store";
import { TaskItem } from "../../types";
import {
  ATTENTION_CHECK_COUNT,
  ATTENTION_CHECKS,
  DONE_THRESHOLD,
  GENERATION_CEILING,
  NUDGE_AFTER_UNEDITED,
} from "../../settings";
import { LoadingDots, PencilIcon } from "../ui";
import { buildAttentionCheckItems, isKept, levenshtein, spliceAttentionChecks } from "./taskItems";
import { TaskReviewIntro } from "./TaskReviewIntro";
import { TaskReviewCard } from "./TaskReviewCard";
import { ReviewAndAddScreen } from "./ReviewAndAddScreen";

// The task review (phase 'task-selection'): generate the participant's task list from the interview, walk
// them through it one card at a time (attention checks mixed in), then let them add what's missing.

export function TaskSelection() {
  const {
    backgroundTranscript,
    occupationSelection,
    setSelectedTasks,
    setTaskItems,
    setInterviewExtractedTasks,
    setPhase,
    screening,
    setScreening,
    externalId,
  } = useWorkflowStore(
    useShallow((s) => ({
      backgroundTranscript: s.backgroundTranscript,
      occupationSelection: s.occupationSelection,
      setSelectedTasks: s.setSelectedTasks,
      setTaskItems: s.setTaskItems,
      setInterviewExtractedTasks: s.setInterviewExtractedTasks,
      setPhase: s.setPhase,
      screening: s.screening,
      setScreening: s.setScreening,
      externalId: s.externalId,
    })),
  );

  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [currentIdx, setCurrentIdx] = useState(0);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [showProcessing, setShowProcessing] = useState(true);
  const [showIntro, setShowIntro] = useState(false);
  const [showEditPopup, setShowEditPopup] = useState(false);
  const editPopupTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Participant-typed tasks captured on the all-done screen. Each entry carries
  // the moment it was added, so the timestamp in edits[] is when the participant
  // typed it, not when they hit Submit.
  const [extraTasks, setExtraTasks] = useState<
    { name: string; addedAt: number }[]
  >([]);
  const [extraInput, setExtraInput] = useState("");

  // Load the tasks behind the processing screen: once on mount, and again from
  // the error screen's Retry. `retry` starts from a clean deck (a failed pass can
  // leave a partial one) and skips the intro, which the participant has read.
  const load = async (retry = false) => {
    if (retry) {
      setTasks([]);
      setCurrentIdx(0);
      claimedIdxRef.current = -1;
      setLoadState("loading");
      setShowProcessing(true);
    }
    // Keep the "Processing your interview…" screen up for the whole
    // extraction + generation pass, with a 1.2s floor so it never flashes.
    const minDisplay = new Promise((r) => setTimeout(r, 1200));
    // Grounding role for task generation / occupation-matched retrieval. Prefer
    // the O*NET occupation the participant self-identified with on the prior
    // occupation-select screen (a clean, canonical title); fall back to the
    // opening turn of the interview if they somehow skipped it.
    const jobTitle =
      occupationSelection?.selectedTitle?.trim() ||
      backgroundTranscript.find((t) => t.answer?.trim())?.answer ||
      "";
    try {
      // Extract interview tasks first (grounding for generation).
      let interviewTasks: string[] = [];
      try {
        interviewTasks = await extractInterviewTasks(backgroundTranscript);
        setInterviewExtractedTasks(interviewTasks);
      } catch (e) {
        console.warn(
          "[task-selection] extract failed, generating without grounding:",
          e,
        );
      }

      // Generate the full rewritten task list while the processing screen
      // stays up, so the participant only reaches the cards once every task is
      // ready. We show ALL the generated tasks — no max/min cap — so nothing
      // they described gets dropped. (When attention checks are on, they
      // splice in on top below.)
      let realCount = 0;
      const onTask = (name: string, meta?: { source?: "interview" | "gap" }) => {
        realCount += 1;
        setTasks((prev) => [
          ...prev,
          {
            name,
            originalName: name,
            source: meta?.source,
            status: "unreviewed",
          },
        ]);
        if (realCount === 1) setLoadState("ready");
      };
      await generateTasksFromInterview(
        jobTitle,
        interviewTasks,
        onTask,
        GENERATION_CEILING,
        backgroundTranscript,
        occupationSelection?.selectedCode,
      );
      // If the stream returned zero tasks (model fluke), surface an error
      // state so the participant sees something rather than a frozen loader.
      // No attention checks then: a deck of only checks is not a task list.
      if (realCount === 0) {
        setLoadState("error");
      } else {
        // Splice the attention checks into the generated list at even intervals.
        const attnItems = buildAttentionCheckItems(ATTENTION_CHECKS, ATTENTION_CHECK_COUNT);
        if (attnItems.length > 0) {
          setTasks((prev) => spliceAttentionChecks(prev, attnItems));
        }
      }
    } catch (e) {
      console.error("Task load failed", e);
      setLoadState("error");
    } finally {
      // Leave the processing screen only once generation has finished (or
      // failed), and never before the 1.2s minimum.
      await minDisplay;
      setShowProcessing(false);
      if (!retry) setShowIntro(true);
    }
  };

  const didLoadRef = useRef(false);
  useEffect(() => {
    if (didLoadRef.current) return;
    didLoadRef.current = true;
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const saveEdit = (idx: number, newName: string) => {
    const trimmed = newName.trim();
    if (!trimmed) return;
    // Dismiss the edit reminder as soon as any real edit is saved.
    if (trimmed !== tasks[idx]?.name) {
      if (editPopupTimerRef.current) clearTimeout(editPopupTimerRef.current);
      setShowEditPopup(false);
    }
    const now = Date.now();
    const next = tasks.map((t, i) => {
      if (i !== idx) return t;
      // No-op edit (e.g. user opened the editor and saved unchanged) — don't record anything.
      if (trimmed === t.name) return t;
      const stepDistance = levenshtein(t.name, trimmed);
      const editEntry = {
        from: t.name,
        to: trimmed,
        charsChanged: stepDistance,
        timestamp: now,
      };
      const changed = trimmed !== t.originalName;
      return {
        ...t,
        name: trimmed,
        status: changed ? "edited" : t.status,
        edits: [...(t.edits ?? []), editEntry],
      };
    });
    setTasks(next);
  };

  // The card this advance() call is answering, claimed synchronously so a second call for the SAME card
  // cannot be processed. Without it a fast double-click silently SKIPS A CARD: both clicks answer card k
  // (one render closure) while the pointer moves twice, and card k+1 is saved as shown-but-never-answered
  // (see __tests__/task-review-advance.test.tsx). A ref, not state, because the guard has to hold WITHIN a
  // tick — two clicks in the same frame both read the pre-render value of any state.
  const claimedIdxRef = useRef(-1);

  const advance = (answer: "yes" | "no") => {
    const idx = currentIdx;
    if (claimedIdxRef.current === idx) return;   // already answering this card — a duplicate fire
    claimedIdxRef.current = idx;
    const reviewedTask = tasks[idx];

    // Per-task dwell time: shownAt was stamped when this card became active.
    const answeredAt = Date.now();
    setTasks((prev) =>
      prev.map((t, i) => {
        if (i !== idx) return t;
        const timed = {
          ...t,
          answeredAt,
          timeSpentMs: t.shownAt != null ? answeredAt - t.shownAt : undefined,
        };
        if (answer === "no") return { ...timed, status: "removed" };
        return {
          ...timed,
          status: t.status === "edited" ? "edited" : "confirmed",
        };
      }),
    );

    // Hard block: if this was an attention check answered "yes", count it as a fail.
    // Once fails exceed the configured threshold, route to the screen-out phase.
    if (reviewedTask?.isAttentionCheck && answer === "yes") {
      const nextFails = screening.attnCheckFails + 1;
      setScreening({ attnCheckFails: nextFails });
      if (nextFails > screening.attnCheckMaxFails) {
        setScreening({
          screenedOut: true,
          screenOutReason: "attention-check-failed",
        });
        // Keyed on the ?id= external id, which is what App's mount-time
        // checkScreenStatus looks up — so a reload can't hand them a clean
        // slate.
        if (externalId) {
          const sid = useWorkflowStore.getState().sessionId;
          recordScreenOut(externalId, sid, "attention-check-failed").catch(
            () => {},
          );
        }
        setTaskItems(
          tasks.map((t, i) =>
            i !== idx
              ? t
              : {
                  ...t,
                  status: "confirmed",
                  answeredAt,
                  timeSpentMs:
                    t.shownAt != null ? answeredAt - t.shownAt : undefined,
                },
          ),
        );
        setPhase("screen-out");
        return;
      }
    }

    // idx + 1, never i => i + 1: the pointer must land on the successor of the card THIS call answered.
    // The nudge lives outside any state updater because side effects there run twice under StrictMode.
    const next = idx + 1;
    // Re-show the edit reminder on every card after the threshold, until
    // the participant edits at least one task.
    if (next >= NUDGE_AFTER_UNEDITED) {
      const hasEdited = tasks.some((t) => (t.edits?.length ?? 0) > 0);
      if (!hasEdited) {
        if (editPopupTimerRef.current) clearTimeout(editPopupTimerRef.current);
        setShowEditPopup(true);
        editPopupTimerRef.current = setTimeout(() => setShowEditPopup(false), 4000);
      } else {
        setShowEditPopup(false);
      }
    }
    setCurrentIdx(next);
  };

  const addExtraTask = (raw: string) => {
    const t = raw.trim();
    if (!t) return;
    if (extraTasks.some((e) => e.name === t)) return; // dedup against what they've already added
    setExtraTasks((prev) => [...prev, { name: t, addedAt: Date.now() }]);
    setExtraInput("");
  };

  const removeExtraTask = (idx: number) => {
    setExtraTasks((prev) => prev.filter((_, i) => i !== idx));
  };

  // Finalize the response.
  const finalize = () => {
    // Tasks the participant typed in get appended as confirmed, flagged as participant-added.
    const extraItems: TaskItem[] = extraTasks.map((e) => ({
      name: e.name,
      originalName: e.name,
      status: "confirmed",
      addedByParticipant: true,
      edits: [
        {
          from: "",
          to: e.name,
          charsChanged: e.name.length,
          timestamp: e.addedAt,
        },
      ],
    }));

    const allTasks = [...tasks, ...extraItems];
    // Attention checks stay in taskItems with their status, never in selectedTasks.
    setSelectedTasks(allTasks.filter(isKept).map((t) => t.name));
    setTaskItems(allTasks);
    setPhase("study-complete");
  };

  // Real tasks only: an attention check answered "yes" is "confirmed" but is never
  // carried into selectedTasks (see finalize), so it must not inflate the count.
  const confirmedCount = tasks.filter(isKept).length;
  const canEarlyExit = currentIdx >= DONE_THRESHOLD && confirmedCount >= 1;
  const isExhausted = currentIdx >= tasks.length && loadState === "ready";
  const currentTask = tasks[currentIdx];
  const loading =
    loadState === "loading" ||
    (loadState === "ready" && !currentTask && !isExhausted);

  // Per-task timing: stamp shownAt the first time a card becomes the active
  // question (answeredAt + timeSpentMs are set in advance()). cardActive gates
  // out the processing/intro screens where no card is visible; the
  // shownAt == null guard makes it idempotent across re-renders.
  const cardActive = !showProcessing && !showIntro && !!currentTask && !isExhausted;
  useEffect(() => {
    if (!cardActive) return;
    setTasks((prev) =>
      prev.map((t, i) =>
        i === currentIdx && t.shownAt == null
          ? { ...t, shownAt: Date.now() }
          : t,
      ),
    );
  }, [cardActive, currentIdx]);

  if (showProcessing) {
    return (
      <div className="flex flex-col h-full items-center justify-center gap-5 px-8">
        <LoadingDots dot="w-2 h-2 bg-indigo-300" />
        <p className="text-slate-400 text-sm animate-fadeSlideIn">
          Processing your interview…
        </p>
      </div>
    );
  }

  if (showIntro) {
    return <TaskReviewIntro onStart={() => setShowIntro(false)} />;
  }

  return (
    <div className="flex flex-col h-full bg-transparent relative overflow-hidden">
      {/* Top gradient is rendered by the parent (App.tsx) so it spans the full viewport. */}

      {/* Edit reminder toast — slides in once after NUDGE_AFTER_UNEDITED unedited cards */}
      <div
        className={`absolute bottom-8 left-6 right-6 z-50 transition-all duration-500 ${showEditPopup ? "opacity-100 translate-y-0" : "opacity-0 translate-y-4 pointer-events-none"}`}
      >
        <div className="bg-slate-800 text-white rounded-2xl px-5 py-4 shadow-lg flex items-start gap-3">
          <PencilIcon className="w-4 h-4 mt-0.5 shrink-0 text-indigo-300" />
          <p className="text-sm leading-relaxed">
            <span className="font-semibold text-white">
              Quick reminder: Use the pencil to edit.
            </span>{" "}
            <span className="text-slate-300">
              Rewrite any task that doesn't quite fit.
            </span>
          </p>
        </div>
      </div>

      {/* Header */}
      <div
        className={`relative z-10 px-5 sm:px-8 pt-7 sm:pt-8 pb-5 shrink-0 w-full mx-auto ${
          isExhausted ? "max-w-[1000px]" : "max-w-[780px]"
        }`}
      >
        <div className="flex items-center justify-between mb-6">
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-indigo-400">
            Task Review
          </p>
        </div>
        <div className="w-full h-1.5 bg-slate-100 rounded-full overflow-hidden">
          {/* Linear progress over the full generated list (variable length).
              isExhausted pegs to 100% on the last card. */}
          <div
            className="h-full rounded-full bg-gradient-to-r from-indigo-400 to-violet-400 transition-all duration-500 ease-out"
            style={{
              width: `${isExhausted || tasks.length === 0 ? 100 : Math.min((currentIdx / tasks.length) * 100, 100)}%`,
            }}
          />
        </div>
      </div>

      {/* Task area */}
      <div
        className={`relative z-10 flex-1 flex flex-col min-h-0 overflow-y-auto w-full mx-auto
        ${isExhausted ? "justify-start pt-6 pb-[48vh] px-5 sm:px-8 max-w-[860px]" : "px-5 sm:px-8 max-w-[780px]"}`}
      >
        {loading ? (
          <div className="flex-1 flex items-center justify-center">
            <LoadingDots dot="w-2 h-2 bg-indigo-200" />
          </div>
        ) : loadState === "error" ? (
          <div className="text-center space-y-3">
            <p className="text-slate-500 text-sm">
              Something went wrong on our side. Please wait a moment and try again.
            </p>
            <button
              onClick={() => load(true)}
              className="text-indigo-500 text-sm underline"
            >
              Retry
            </button>
          </div>
        ) : isExhausted ? (
          <ReviewAndAddScreen
            confirmedTasks={tasks.filter(isKept)}
            extraTasks={extraTasks}
            extraInput={extraInput}
            onExtraInputChange={setExtraInput}
            onAddExtra={addExtraTask}
            onRemoveExtra={removeExtraTask}
            onRemoveConfirmed={(idx) =>
              setTasks((prev) => {
                const target = prev.filter(isKept)[idx];
                return prev.filter((t) => t !== target);
              })
            }
            onSubmit={finalize}
          />
        ) : currentTask ? (
          <div className="w-full max-w-[600px] mx-auto h-full">
            <TaskReviewCard
              key={currentIdx}
              task={currentTask}
              taskIdx={currentIdx}
              onSaveEdit={saveEdit}
              onAdvance={advance}
            />
          </div>
        ) : null}
      </div>

      {/* Footer — early-exit hint, only shown mid-flow (the exhausted screen has its own primary button) */}
      {canEarlyExit && !isExhausted && (
        <div className="relative z-10 px-5 sm:px-8 pb-8 pt-4 border-t border-slate-100 shrink-0 w-full max-w-[780px] mx-auto">
          <button
            onClick={finalize}
            className="w-full text-sm text-slate-400 hover:text-slate-600 transition py-1"
          >
            Done — continue with {confirmedCount} task
            {confirmedCount !== 1 ? "s" : ""}
          </button>
        </div>
      )}
    </div>
  );
}
