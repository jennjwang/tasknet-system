import { useState } from "react";

// Task-performance stage, shown AFTER the rating cards + review, on the tasks the
// participant marked Relevant. A single screen: the two workers being compared,
// then one cross-task question — pick the tasks where top workers outperform.

export interface TaskPerformanceRating {
  perTask: {
    statement: string;
    outperforms: boolean; // top workers typically outperform typical workers on this task
  }[];
}

// The two workers being compared — the whole point of the intro screen.
function ComparisonSetup() {
  return (
    <div className="mt-4 rounded-2xl border border-indigo-100 bg-indigo-50/60 px-7 py-6">
      <ul className="space-y-3 text-[15px] text-slate-600 leading-relaxed">
        <li className="flex gap-3.5">
          <span className="mt-[9px] h-2 w-2 shrink-0 rounded-full bg-slate-400" />
          <span>
            a <span className="font-medium text-slate-700">typical worker</span>,
            whose overall performance is at the level normally expected; and
          </span>
        </li>
        <li className="flex gap-3.5">
          <span className="mt-[9px] h-2 w-2 shrink-0 rounded-full bg-slate-400" />
          <span>
            the{" "}
            <span className="font-medium text-slate-700">
              high-performing worker
            </span>
            , whose overall performance is among the top 10% of workers in
            the occupation.
          </span>
        </li>
      </ul>
    </div>
  );
}

// Square marker — signals multi-select, unlike a single-choice scale.
function CheckBox({ on }: { on: boolean }) {
  return (
    <span
      className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded transition ${
        on ? "bg-indigo-600 text-white" : "border-2 border-slate-300"
      }`}
    >
      {on && (
        <svg
          viewBox="0 0 16 16"
          className="h-2.5 w-2.5"
          fill="none"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polyline points="3.5 8.5 6.5 11.5 12.5 5" />
        </svg>
      )}
    </span>
  );
}

export function CoverageTaskPerformance({
  statements,
  onDone,
  onBack,
  submitting = false,
}: {
  statements: string[]; // the RELEVANT (checked) task statements
  onDone: (r: TaskPerformanceRating) => void;
  onBack?: () => void; // back to the relevant-tasks review screen
  submitting?: boolean;
}) {
  // Indices into `statements` that the participant marked as outperformed-on.
  const [picked, setPicked] = useState<number[]>([]);

  const total = statements.length;

  const finish = () => {
    if (submitting) return;
    onDone({
      perTask: statements.map((s, i) => ({
        statement: s,
        outperforms: picked.includes(i),
      })),
    });
  };

  const togglePicked = (i: number) =>
    setPicked((prev) =>
      prev.includes(i)
        ? prev.filter((x) => x !== i)
        : [...prev, i].sort((a, b) => a - b),
    );

  // ── One screen: the two workers, then the question over every relevant task ──
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex flex-1 flex-col items-center overflow-y-auto px-6 sm:px-10 py-10">
        <div className="my-auto w-full max-w-3xl animate-fadeSlideIn">
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-indigo-400">
            Task performance
          </p>

          <h1 className="mt-3 text-[1.5rem] sm:text-[1.85rem] font-light text-slate-800 leading-snug tracking-tight">
            Where does expertise show up?
          </h1>

          <p className="mt-4 text-[15px] text-slate-600 leading-relaxed">
            Consider two workers in the same occupation and work setting:
          </p>

          <ComparisonSetup />

          <div className="mt-8">
            <p className="text-base font-medium text-slate-800 leading-relaxed">
              On which tasks does the high-performing worker typically
              outperform the typical worker?
            </p>
            <p className="mt-1.5 text-xs text-slate-400">
              Select all that apply.
            </p>
          </div>

          <div className="mt-5 space-y-2">
            {statements.map((s, i) => {
              const on = picked.includes(i);
              return (
                <button
                  key={i}
                  type="button"
                  onClick={() => togglePicked(i)}
                  className={`flex w-full items-start gap-3 rounded-xl border px-4 py-3 text-left text-sm leading-relaxed transition active:scale-[0.99] ${
                    on
                      ? "border-indigo-400 bg-indigo-50/70 text-indigo-800 ring-2 ring-indigo-200 font-medium"
                      : "border-slate-200 bg-white text-slate-600 hover:border-indigo-200 hover:bg-indigo-50/30"
                  }`}
                >
                  <CheckBox on={on} />
                  <span>{s}</span>
                </button>
              );
            })}
          </div>

          <div className="mt-8 flex items-center justify-between pb-4">
            {onBack ? (
              <button
                onClick={onBack}
                className="text-sm font-medium text-slate-400 hover:text-slate-600 transition"
              >
                Back
              </button>
            ) : (
              <span />
            )}
            <div className="flex items-center gap-4">
              <p className="text-xs text-slate-400">
                {picked.length} of {total} selected
              </p>
              <button
                onClick={finish}
                disabled={submitting}
                className="inline-flex items-center gap-2 px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-30 disabled:cursor-not-allowed text-white text-sm font-medium rounded-full transition-all active:scale-[0.98] shadow-sm shadow-indigo-200"
              >
                {submitting ? "Submitting…" : "Submit"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
