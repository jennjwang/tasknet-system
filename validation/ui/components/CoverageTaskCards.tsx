import { useEffect, useState } from "react";
import SCALES from "../lib/scales.json";

// Per-task rating pass, three screens per statement:
//   1. relevance → frequency (7-point O*NET scale) → importance (5-point)
//   2. duration (9-point) + the two evaluability scales
//   3. training (SVP, 9-point)
// Scales collapse to their pick once answered, so the next question has room.
// "Not relevant" skips screens 2-3. One rating per statement.

export interface TaskRating {
  statement: string;
  // null = never answered; false = an explicit "not relevant".
  relevant: boolean | null;
  frequency: number | null; // 1-7 when relevant, else null
  importance: number | null; // 1-5 when relevant, else null
  standards: Verdict | null; // evaluability: standards stateable in advance?
  verifiability: Verdict | null; // evaluability: success verifiable afterwards?
  duration: number | null; // 1-9 when relevant, else null
  training: number | null; // 1-9 when relevant, else null
}

// A task with no ratings: `relevant: false` is the rater's explicit "not part of the work", `null` a task
// they never reached. Keep them distinct: endorsement is computed from the first.
export function blankRating(statement: string, relevant: boolean | null): TaskRating {
  return {
    statement, relevant, frequency: null, importance: null,
    standards: null, verifiability: null, duration: null, training: null,
  };
}

// Task evaluability — can good performance be specified up front, and can it be
// established afterwards. Stored as verdict keys rather than numbers.
export type Verdict = "yes" | "partly" | "no";

export const EVALUABILITY_QUESTIONS: {
  key: "standards" | "verifiability";
  question: string;
  hint?: string; // fine print under the question
}[] = [
  {
    key: "standards",
    question:
      "Can the standards for successful performance on this task be clearly stated in advance?",
  },
  {
    key: "verifiability",
    question:
      "Using only the information available when the task is performed, could another person in this occupation determine whether it was performed successfully?",
    // The evaluator is a peer in the occupation, and "performed" (not "completed") covers ongoing work
    // such as supervising.
    hint: "This information may include the work product, observations of the process, or records and metrics.",
  },
];

// Badge numbers: 1-4 are relevance, frequency, importance, duration; the evaluability rows take 5..;
// training is last.
const TRAINING_Q_NO = 5 + EVALUABILITY_QUESTIONS.length;

// Display order No, Partly, Yes (the floor of the scale first); stored values are the keys.
export const VERDICTS: { value: Verdict; label: string }[] = [
  { value: "no", label: "No" },
  { value: "partly", label: "Partly" },
  { value: "yes", label: "Yes" },
];

// The rating scales (validation/ui/lib/scales.json, shared with tools/coverage/export.mjs). The stored
// rating is index + 1. Duration bands double in length; 1 workday = 8 hours. Training is the SVP bands.
const FREQUENCY_OPTIONS = SCALES.frequency;
const IMPORTANCE_OPTIONS = SCALES.importance;
const DURATION_OPTIONS = SCALES.duration;
const TRAINING_OPTIONS = SCALES.training;

function scaleBtn(active: boolean) {
  return `w-full text-left px-4 py-3 rounded-xl border text-sm transition active:scale-[0.99] ${
    active
      ? "border-indigo-400 bg-indigo-50 text-indigo-700 ring-2 ring-indigo-200 font-medium"
      : "border-slate-200 bg-white text-slate-600 hover:border-indigo-200 hover:bg-indigo-50/40"
  }`;
}

// One rating scale: shows the full option list, then COLLAPSES to just the picked
// option once selected; clicking that option re-opens the list to change it.
function ScaleGroup({
  items,
  value,
  onChange,
}: {
  items: string[];
  value: number | null;
  onChange: (v: number) => void;
}) {
  // Start collapsed when a value is already set (e.g. revisiting an answered task
  // via Back), so it shows the picked option rather than the full list.
  const [open, setOpen] = useState(() => value == null);
  if (value != null && !open) {
    return (
      <div className="ml-7 mt-3">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className={`${scaleBtn(true)} flex items-center justify-between`}
        >
          <span>{items[value - 1]}</span>
          <span className="text-xs font-medium text-indigo-400">Change</span>
        </button>
      </div>
    );
  }
  return (
    <div className="ml-7 mt-3 space-y-2">
      {items.map((label, i) => {
        const v = i + 1;
        return (
          <button
            key={v}
            type="button"
            onClick={() => {
              onChange(v);
              setOpen(false);
            }}
            className={scaleBtn(value === v)}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

// One column per VERDICTS entry.
const MATRIX_COLS = "grid grid-cols-[1fr_repeat(3,4rem)] items-start gap-x-2";

// Both evaluability questions as one grid: they share a scale, so a matrix shows
// that and keeps the screen short. Below `sm` it falls back to stacked lists.
function EvaluabilityMatrix({
  answers,
  onChange,
}: {
  answers: Partial<Record<string, Verdict>>;
  onChange: (key: string, v: Verdict) => void;
}) {
  return (
    <div className="hidden sm:block">
      <div
        className={`${MATRIX_COLS} items-end border-b border-slate-200 pb-2`}
      >
        <span />
        {VERDICTS.map((c) => (
          <span
            key={c.value}
            className="text-center text-[11px] font-semibold uppercase tracking-[0.06em] text-slate-400 leading-tight"
          >
            {c.label}
          </span>
        ))}
      </div>

      {EVALUABILITY_QUESTIONS.map((q, i) => (
        <div
          key={q.key}
          className={`${MATRIX_COLS} border-b border-slate-100 py-4`}
        >
          <div className="flex items-start gap-2 pr-6">
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-[11px] font-semibold text-indigo-600">
              {i + 5}
            </span>
            <div>
              <p className="text-sm text-slate-700 leading-relaxed">
                {q.question}
              </p>
              {q.hint && (
                <p className="mt-1.5 text-[13px] text-slate-500 leading-relaxed">
                  {q.hint}
                </p>
              )}
            </div>
          </div>
          {VERDICTS.map((c) => {
            const on = answers[q.key] === c.value;
            return (
              <button
                key={c.value}
                type="button"
                aria-label={`${q.question} — ${c.label}`}
                aria-pressed={on}
                onClick={() => onChange(q.key, c.value)}
                className="group flex justify-center rounded-lg py-0.5 transition hover:bg-indigo-50/50"
              >
                <span
                  className={`flex h-5 w-5 items-center justify-center rounded-full border-2 transition ${
                    on
                      ? "border-indigo-500 bg-indigo-500"
                      : "border-slate-300 group-hover:border-indigo-300"
                  }`}
                >
                  {on && <span className="h-2 w-2 rounded-full bg-white" />}
                </span>
              </button>
            );
          })}
        </div>
      ))}
    </div>
  );
}

// Screens 1..3 — see the file header. "Not relevant" skips 2-3 entirely.
const LAST_PART = 3;

// The screen a revisited task opens on: the furthest one it has complete answers for (screen 1 for a
// "not relevant" or untouched task), using the same conditions as `canContinue`.
function lastPartOf(r: TaskRating | undefined): number {
  if (!r || !r.relevant) return 1;
  if (r.frequency === null || r.importance === null) return 1;
  const part2Done =
    EVALUABILITY_QUESTIONS.every((q) => r[q.key] != null) &&
    r.duration !== null;
  return part2Done ? LAST_PART : 2;
}

export function CoverageTaskCards({
  statements,
  occupation = "your occupation",
  onDone,
  submitting = false,
  initial,
  startAt,
  onProgress,
  onAnswer,
}: {
  statements: string[];
  occupation?: string;
  onDone: (ratings: TaskRating[]) => void;
  submitting?: boolean;
  initial?: (TaskRating | null)[]; // resume with these answers; null = not yet rated
  startAt?: number; // task to open on (defaults to the last answered one)
  onProgress?: (answers: (TaskRating | null)[], idx: number) => void; // for the local draft
  // Fired on every selection, for response-time analysis: which questions and
  // which task statements take longest. The parent stamps the time.
  onAnswer?: (e: {
    task: number;
    statement: string;
    field: string;
    value: string | number | boolean;
  }) => void;
}) {
  // Resume mode: rehydrate answers and land where they left off instead of
  // restarting the pass.
  const resume = !!(initial && initial.length > 0);
  const initialAnswers: Record<number, TaskRating> = {};
  if (resume)
    initial!.forEach((r, i) => {
      if (r) initialAnswers[i] = r;
    });
  // Clamped at BOTH ends. A resumed cardIdx is not validated against the re-fetched assignment, so a list
  // that got shorter between sessions (a re-route, or COVERAGE_MAX_TASKS changing) would index past
  // the end and render an undefined statement.
  const startIdx = Math.min(
    statements.length - 1,
    Math.max(0, startAt ?? (resume ? statements.length - 1 : 0)),
  );
  const start = initialAnswers[startIdx];

  const [idx, setIdx] = useState(startIdx);
  const [answers, setAnswers] =
    useState<Record<number, TaskRating>>(initialAnswers);
  const [choice, setChoice] = useState<"yes" | "no" | null>(
    start ? (start.relevant ? "yes" : "no") : null,
  );
  const [frequency, setFrequency] = useState<number | null>(
    start?.relevant ? start.frequency : null,
  );
  const [importance, setImportance] = useState<number | null>(
    start?.relevant ? start.importance : null,
  );
  const [duration, setDuration] = useState<number | null>(
    start?.relevant ? start.duration : null,
  );
  const [training, setTraining] = useState<number | null>(
    start?.relevant ? start.training : null,
  );
  const [evalAns, setEvalAns] = useState<Partial<Record<string, Verdict>>>(
    start?.relevant
      ? {
          standards: start.standards ?? undefined,
          verifiability: start.verifiability ?? undefined,
        }
      : {},
  );

  // Same rule on the way IN as on the way Back: a resumed draft, or the last task
  // reopened via Back from the review screen, opens on the screen they left off at.
  const [part, setPart] = useState(() => lastPartOf(start));

  const total = statements.length;
  const statement = statements[idx];

  const canContinue =
    part === 2
      ? EVALUABILITY_QUESTIONS.every((q) => evalAns[q.key] != null) &&
        duration !== null
      : part === LAST_PART
        ? training !== null
        : choice === "no" ||
          (choice === "yes" && frequency !== null && importance !== null);

  const answered = (field: string, value: string | number | boolean) =>
    onAnswer?.({ task: idx, statement, field, value });

  // Hand the parent a snapshot (sparse array + position) for the local draft.
  const report = (map: Record<number, TaskRating>, at: number) =>
    onProgress?.(
      statements.map((_, i) => map[i] ?? null),
      at,
    );

  // Load a task's saved answer (or a partial draft) back into the working state —
  // used when navigating between tasks so nothing is lost going back and forth.
  const hydrate = (r: TaskRating | undefined) => {
    if (r && r.relevant) {
      setChoice("yes");
      setFrequency(r.frequency);
      setImportance(r.importance);
      setDuration(r.duration);
      setTraining(r.training);
      setEvalAns({
        standards: r.standards ?? undefined,
        verifiability: r.verifiability ?? undefined,
      });
    } else {
      setChoice(r ? "no" : null);
      setFrequency(null);
      setImportance(null);
      setDuration(null);
      setTraining(null);
      setEvalAns({});
    }
  };

  // Snapshot the current (possibly partial) selections so revisiting restores them.
  const currentDraft = (): TaskRating | undefined => {
    if (choice === "yes")
      return {
        statement,
        relevant: true,
        frequency,
        importance,
        duration,
        training,
        standards: evalAns.standards ?? null,
        verifiability: evalAns.verifiability ?? null,
      };
    if (choice === "no") return blankRating(statement, false);
    return undefined; // untouched — don't record
  };

  // Report the open card's partial answers on every selection, so a closed tab loses nothing; a resume
  // rehydrates them via `initial[startAt]`.
  useEffect(() => {
    const draft = currentDraft();
    if (!draft) return; // untouched card — nothing new to save
    report({ ...answers, [idx]: draft }, idx);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [choice, frequency, importance, duration, training, evalAns]);

  const advance = () => {
    if (!canContinue || submitting) return;
    // Relevant tasks walk screens 2..3 before moving on.
    if (choice === "yes" && part < LAST_PART) {
      setPart(part + 1);
      return;
    }
    // canContinue on screen 1 means a choice was made, so this is the finished rating.
    const nextAnswers = { ...answers, [idx]: currentDraft()! };
    if (idx + 1 >= total) {
      onDone(statements.map((s, i) => nextAnswers[i] ?? blankRating(s, null)));
      return;
    }
    setAnswers(nextAnswers);
    setIdx(idx + 1);
    setPart(1);
    hydrate(nextAnswers[idx + 1]); // restore if this task was answered before
    report(nextAnswers, idx + 1);
  };

  const back = () => {
    if (submitting) return;
    if (part > 1) {
      setPart(part - 1);
      return;
    }
    if (idx === 0) return;
    const draft = currentDraft();
    if (draft) setAnswers((a) => ({ ...a, [idx]: draft })); // keep this task's progress
    const prev = idx - 1;
    setIdx(prev);
    setPart(lastPartOf(answers[prev]));
    hydrate(answers[prev]);
    report(draft ? { ...answers, [idx]: draft } : answers, prev);
  };

  const choiceBtn = (active: boolean) =>
    `flex-1 px-5 py-4 rounded-xl border text-sm font-medium transition active:scale-[0.99] ${
      active
        ? "border-indigo-400 bg-indigo-50 text-indigo-700 ring-2 ring-indigo-200"
        : "border-slate-200 bg-white text-slate-600 hover:border-indigo-200 hover:bg-indigo-50/40"
    }`;

  return (
    <div className="flex flex-col h-full">
      {/* Progress header — pinned to the top of the screen */}
      <div className="shrink-0 px-6 pt-8 pb-2">
        <div className="mx-auto flex w-full max-w-3xl items-center gap-8">
          <p className="shrink-0 text-[10px] font-semibold uppercase tracking-[0.18em] text-indigo-400">
            Task {idx + 1} of {total}
          </p>
          <div className="h-1.5 flex-1 rounded-full bg-slate-100 overflow-hidden">
            <div
              className="h-full bg-indigo-400 transition-[width] duration-300"
              style={{ width: `${((idx + 1) / total) * 100}%` }}
            />
          </div>
        </div>
      </div>

      {/* Question + answers — scrolls if the scales run long */}
      <div className="flex flex-1 items-center justify-center px-6 pb-10 overflow-y-auto">
        <div className="w-full max-w-3xl my-auto py-6">
          {/* The task itself, in its own labeled card so it reads as the thing to evaluate. */}
          <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-5">
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-indigo-400">
              The task
            </p>
            <p className="mt-2 text-[1.35rem] font-light text-slate-800 leading-snug">
              {statement}
            </p>
          </div>

          {/* One screen at a time — slides in from the right on arrival */}
          <div key={`${idx}-${part}`} className="animate-swipeIn">
            {part === 1 ? (
              <>
                {/* Step 1 — the relevance question stands in for the "Relevance" label */}
                <div className="mt-8">
                  <div className="flex items-start gap-2">
                    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-[11px] font-semibold text-indigo-600">
                      1
                    </span>
                    <p className="text-base text-slate-700">
                      Is this task relevant to the occupation of{" "}
                      {occupation.toLowerCase()}?
                    </p>
                  </div>
                  <div className="ml-7 mt-3 flex gap-4">
                    <button
                      type="button"
                      className={choiceBtn(choice === "yes")}
                      onClick={() => {
                        setChoice("yes");
                        answered("relevance", true);
                      }}
                    >
                      Relevant
                    </button>
                    <button
                      type="button"
                      className={choiceBtn(choice === "no")}
                      onClick={() => {
                        setChoice("no");
                        answered("relevance", false);
                        setFrequency(null);
                        setImportance(null);
                        setDuration(null);
                        setTraining(null);
                        setEvalAns({});
                      }}
                    >
                      Not relevant
                    </button>
                  </div>
                </div>

                {choice === "yes" && (
                  <div className="mt-8 space-y-8">
                    <div className="animate-fadeSlideIn">
                      <div className="flex items-start gap-2">
                        <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-[11px] font-semibold text-indigo-600">
                          2
                        </span>
                        <p className="text-base text-slate-700">
                          How often is this task performed in the occupation?
                        </p>
                      </div>
                      <ScaleGroup
                        items={FREQUENCY_OPTIONS}
                        value={frequency}
                        onChange={(v) => {
                          setFrequency(v);
                          answered("frequency", v);
                        }}
                      />
                    </div>

                    {frequency !== null && (
                      <div className="animate-fadeSlideIn">
                        <div className="flex items-start gap-2">
                          <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-[11px] font-semibold text-indigo-600">
                            3
                          </span>
                          <p className="text-base text-slate-700">
                            How important is this task to performance of the
                            occupation?
                          </p>
                        </div>
                        <ScaleGroup
                          items={IMPORTANCE_OPTIONS}
                          value={importance}
                          onChange={(v) => {
                            setImportance(v);
                            answered("importance", v);
                          }}
                        />
                      </div>
                    )}
                  </div>
                )}
              </>
            ) : part === 2 ? (
              <>
                <div className="mt-8">
                  <div className="flex items-start gap-2">
                    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-[11px] font-semibold text-indigo-600">
                      4
                    </span>
                    <p className="text-base text-slate-700">
                      How long would it typically take a worker in this
                      occupation to complete this task once?
                    </p>
                  </div>
                  <ScaleGroup
                    items={DURATION_OPTIONS}
                    value={duration}
                    onChange={(v) => {
                      setDuration(v);
                      answered("duration", v);
                    }}
                  />
                </div>

                {duration !== null && (
                  <div className="mt-10 animate-fadeSlideIn">
                    <div className="space-y-8">
                      <p className="text-base text-slate-700 leading-relaxed">
                        Now consider how someone in the occupation would judge
                        whether this task was performed successfully.
                      </p>
                      <EvaluabilityMatrix
                        answers={evalAns}
                        onChange={(key, v) => {
                          setEvalAns((prev) => ({ ...prev, [key]: v }));
                          answered(key, v);
                        }}
                      />

                      {/* Narrow screens: the grid is too tight, so stack them. */}
                      <div className="space-y-8 sm:hidden">
                        {EVALUABILITY_QUESTIONS.map((q, i) => (
                          <div key={q.key}>
                            <div className="flex items-start gap-2">
                              <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-[11px] font-semibold text-indigo-600">
                                {i + 5}
                              </span>
                              <div>
                                <p className="text-base text-slate-700 leading-relaxed">
                                  {q.question}
                                </p>
                                {q.hint && (
                                  <p className="mt-1.5 text-[13px] text-slate-500 leading-relaxed">
                                    {q.hint}
                                  </p>
                                )}
                              </div>
                            </div>
                            <div className="mt-3 flex flex-wrap gap-2">
                              {VERDICTS.map((v) => (
                                <button
                                  key={v.value}
                                  type="button"
                                  onClick={() => {
                                    setEvalAns((prev) => ({
                                      ...prev,
                                      [q.key]: v.value,
                                    }));
                                    answered(q.key, v.value);
                                  }}
                                  className={`flex-1 rounded-xl border px-4 py-3 text-sm transition active:scale-[0.99] ${
                                    evalAns[q.key] === v.value
                                      ? "border-indigo-400 bg-indigo-50 text-indigo-700 ring-2 ring-indigo-200 font-medium"
                                      : "border-slate-200 bg-white text-slate-600 hover:border-indigo-200 hover:bg-indigo-50/40"
                                  }`}
                                >
                                  {v.label}
                                </button>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="mt-8">
                <div className="flex items-start gap-2">
                  <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-[11px] font-semibold text-indigo-600">
                    {TRAINING_Q_NO}
                  </span>
                  <div>
                    <p className="text-base text-slate-700">
                      Consider someone who is newly hired into this occupation
                      and{" "}
                      <span className="font-semibold">
                        has not previously performed this task
                      </span>
                      . How much training and experience would this person
                      typically need before they could perform the task
                      independently at the level normally expected in the
                      occupation?
                    </p>
                    <p className="mt-1.5 text-[13px] text-slate-500 leading-relaxed">
                      This includes related work experience, organized classroom
                      study or other structured instruction provided by the
                      employer, and on-the-job training.
                    </p>
                  </div>
                </div>
                <ScaleGroup
                  items={TRAINING_OPTIONS}
                  value={training}
                  onChange={(v) => {
                    setTraining(v);
                    answered("training", v);
                  }}
                />
              </div>
            )}
          </div>

          <div className="mt-10 flex items-center justify-between">
            {idx > 0 || part > 1 ? (
              <button
                onClick={back}
                disabled={submitting}
                className="text-sm font-medium text-slate-400 hover:text-slate-600 disabled:opacity-40 transition"
              >
                Back
              </button>
            ) : (
              <span />
            )}
            <button
              onClick={advance}
              disabled={!canContinue || submitting}
              className="inline-flex items-center gap-2 px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-30 disabled:cursor-not-allowed text-white text-sm font-medium rounded-full transition-all active:scale-[0.98] shadow-sm shadow-indigo-200"
            >
              {submitting ? "Submitting…" : "Continue"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
