// Final screen: two 1-5 items about the experience, then free text on the questions themselves (the
// review screen asks about the wording of the tasks). All three are optional.

export interface FeedbackRatings {
  burden: number | null; // 1 = not at all demanding, 5 = extremely
  clarity: number | null; // 1 = confusing, 5 = very clear
}

const ITEMS: {
  key: keyof FeedbackRatings;
  question: string;
  options: string[]; // index + 1 is the stored value
}[] = [
  {
    key: "burden",
    question: "How mentally demanding was this survey?",
    options: ["Not at all", "Slightly", "Moderately", "Very", "Extremely"],
  },
  {
    key: "clarity",
    question: "How clear were the questions?",
    options: [
      "Not at all clear",
      "Slightly clear",
      "Moderately clear",
      "Very clear",
      "Completely clear",
    ],
  },
];

// Every point carries its own word, so the direction of the scale comes from the
// labels rather than from position — the two items here run in opposite valence
// (high burden is bad, high clarity is good), which bare numbers hide.
function ScaleRow({
  options,
  value,
  onChange,
}: {
  options: string[];
  value: number | null;
  onChange: (v: number) => void;
}) {
  return (
    <div className="ml-7 mt-3 flex flex-col gap-2 sm:flex-row">
      {options.map((label, i) => {
        const v = i + 1;
        return (
          <button
            key={v}
            type="button"
            onClick={() => onChange(v)}
            className={`flex-1 rounded-xl border px-3 py-3 text-sm leading-snug transition active:scale-[0.99] ${
              value === v
                ? "border-indigo-400 bg-indigo-50 text-indigo-700 ring-2 ring-indigo-200 font-medium"
                : "border-slate-200 bg-white text-slate-600 hover:border-indigo-200 hover:bg-indigo-50/40"
            }`}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

export function CoverageFeedback({
  value,
  onChange,
  ratings,
  onRatingsChange,
  onSubmit,
  onBack,
  submitting = false,
}: {
  value: string;
  onChange: (v: string) => void;
  ratings: FeedbackRatings;
  onRatingsChange: (r: FeedbackRatings) => void;
  onSubmit: () => void;
  onBack?: () => void;
  submitting?: boolean;
}) {
  // Nothing gates the submit: a participant who has no comment should not have to
  // invent one to get paid.
  return (
    <div className="h-full overflow-y-auto px-6 py-10 sm:py-14">
      <div className="mx-auto max-w-3xl animate-fadeSlideIn">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-indigo-400">
          Last step
        </p>
        <h1 className="mt-3 text-[1.5rem] sm:text-[1.85rem] font-light text-slate-800 leading-snug tracking-tight">
          How were the questions?
        </h1>

        <div className="mt-7 space-y-7">
          {ITEMS.map((item, i) => (
            <div key={item.key}>
              <div className="flex items-start gap-2">
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-[11px] font-semibold text-indigo-600">
                  {i + 1}
                </span>
                <p className="text-base text-slate-700 leading-relaxed">
                  {item.question}
                </p>
              </div>
              <ScaleRow
                options={item.options}
                value={ratings[item.key]}
                onChange={(v) => onRatingsChange({ ...ratings, [item.key]: v })}
              />
            </div>
          ))}

          <div>
            <div className="flex items-start gap-2">
              <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-[11px] font-semibold text-indigo-600">
                {ITEMS.length + 1}
              </span>
              <p className="text-base text-slate-700 leading-relaxed">
                Was anything confusing, hard to answer, or missing an option you
                needed?
              </p>
            </div>
            <textarea
              value={value}
              onChange={(e) => onChange(e.target.value)}
              rows={4}
              placeholder="Anything that gave you pause, or that the questions worked fine."
              className="ml-7 mt-3 w-[calc(100%-1.75rem)] resize-none rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 placeholder:text-slate-300 focus:border-indigo-300 focus:outline-none focus:ring-2 focus:ring-indigo-100"
            />
          </div>
        </div>

        <div className="mt-8 flex items-center justify-between pb-4">
          {onBack ? (
            <button
              onClick={onBack}
              disabled={submitting}
              className="text-sm font-medium text-slate-400 hover:text-slate-600 disabled:opacity-40 transition"
            >
              Back
            </button>
          ) : (
            <span />
          )}
          <button
            onClick={onSubmit}
            disabled={submitting}
            className="inline-flex items-center gap-2 px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-30 disabled:cursor-not-allowed text-white text-sm font-medium rounded-full transition-all active:scale-[0.98] shadow-sm shadow-indigo-200"
          >
            {submitting ? "Submitting…" : "Submit"}
          </button>
        </div>
      </div>
    </div>
  );
}
