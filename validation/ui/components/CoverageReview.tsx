import { ArrowRight } from "./validationUi";

// Review screen, after the rating cards and before task performance: the tasks marked Relevant, each
// removable (×). A removed task is recorded as not relevant.

export function CoverageReview({
  statements,
  onRemove,
  onContinue,
  onBack,
  notes = "",
  onNotesChange,
}: {
  statements: string[];
  onRemove: (statement: string) => void;
  onContinue: () => void;
  onBack?: () => void; // back to the rating cards
  notes?: string;
  onNotesChange?: (v: string) => void; // required free-text: unclear / mis-worded tasks
}) {
  // Required: the wording question must be answered before continuing.
  const canContinue = notes.trim().length > 0;
  return (
    <div className="h-full overflow-y-auto px-6 py-10 sm:py-14">
      <div className="mx-auto max-w-3xl animate-fadeSlideIn">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-indigo-400">
          Review
        </p>
        <h1 className="mt-3 text-[1.5rem] sm:text-[1.85rem] font-light text-slate-800 leading-snug tracking-tight">
          Relevant Tasks
        </h1>
        <p className="mt-2 text-sm text-slate-500 leading-relaxed">
          These are the tasks you found relevant to the occupation. Hover over a
          task and click the × to remove any that don't belong.
        </p>

        {statements.length > 0 ? (
          <div className="mt-6 columns-1 sm:columns-2 gap-2.5">
            {statements.map((s, i) => (
              <div
                key={`${s}-${i}`}
                className="group relative mb-2.5 break-inside-avoid px-4 py-3 rounded-xl bg-white border border-slate-200 hover:border-indigo-200 hover:shadow-sm transition"
              >
                <p className="text-sm text-slate-800 leading-snug pr-6">{s}</p>
                <button
                  type="button"
                  onClick={() => onRemove(s)}
                  aria-label="Remove task"
                  title="Remove task"
                  className="absolute top-2 right-2 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity w-7 h-7 sm:w-5 sm:h-5 flex items-center justify-center rounded-full text-slate-400 sm:text-slate-300 hover:text-red-400 hover:bg-red-50"
                >
                  <svg
                    viewBox="0 0 16 16"
                    className="w-3 h-3"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                  >
                    <line x1="4" y1="4" x2="12" y2="12" />
                    <line x1="12" y1="4" x2="4" y2="12" />
                  </svg>
                </button>
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-6 text-sm text-slate-400">
            You've removed all tasks — none will be counted as relevant. You can
            still continue.
          </p>
        )}

        <div className="mt-10 rounded-2xl border border-indigo-100 bg-indigo-50/50 p-5 sm:p-6">
          <p className="text-base font-medium text-slate-800 leading-relaxed">
            Would you or your colleagues have trouble understanding any of these
            tasks?
          </p>
          <textarea
            value={notes}
            onChange={(e) => onNotesChange?.(e.target.value)}
            rows={3}
            placeholder="Note any tasks that are unclear or worded poorly, or if they all read fine."
            className="mt-3 w-full resize-none rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 placeholder:text-slate-300 focus:border-indigo-300 focus:outline-none focus:ring-2 focus:ring-indigo-100"
          />
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
          <button
            onClick={onContinue}
            disabled={!canContinue}
            className="inline-flex items-center gap-2 px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-30 disabled:cursor-not-allowed text-white text-sm font-medium rounded-full transition-all active:scale-[0.98] shadow-sm shadow-indigo-200"
          >
            Continue
            <ArrowRight />
          </button>
        </div>
      </div>
    </div>
  );
}
