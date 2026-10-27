import { useState, useEffect, useRef } from "react";
import { EVALUABILITY_QUESTIONS, VERDICTS } from "./CoverageTaskCards";
import SCALES from "../lib/scales.json";
import { ArrowRight } from "./validationUi";

// Instructions: one card per rating type (relevance, frequency, importance, evaluability, duration,
// training, task performance), stepped through with tabs or Next. Continue unlocks once all were shown.

function Scale({ items }: { items: string[] }) {
  return (
    <ul className="mt-3 space-y-1.5">
      {items.map((label, i) => (
        <li key={i} className="flex items-center gap-3 text-sm text-slate-600">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-indigo-50 text-xs font-semibold text-indigo-600">
            {i + 1}
          </span>
          {label}
        </li>
      ))}
    </ul>
  );
}

const SECTIONS: { title: string; body: React.ReactNode }[] = [
  {
    title: "Relevance",
    body: (
      <>
        <p className="mt-2 text-sm text-slate-600 leading-relaxed">
          First, determine whether the task is relevant to the occupation. Read
          each task carefully before deciding.
        </p>
        <p className="mt-2 text-sm font-medium text-slate-700 leading-relaxed">
          Is this task relevant to the occupation?
        </p>
        <ul className="mt-3 list-disc space-y-1.5 pl-5 text-sm text-slate-600 leading-relaxed">
          <li>
            If the task is{" "}
            <span className="font-medium text-slate-700">not relevant</span> to
            the occupation, mark it{" "}
            <span className="font-medium text-slate-700">Not relevant</span> —
            you don't need to rate anything else.
          </li>
          <li>
            If the task is part of the occupation, mark it{" "}
            <span className="font-medium text-slate-700">Relevant</span> and
            answer the remaining questions for it.
          </li>
        </ul>
        <p className="mt-3 text-sm text-slate-600 leading-relaxed">
          <span className="font-medium text-slate-700">
            The question is the occupation, not you.
          </span>{" "}
          Work that others in this occupation routinely do is relevant even if it
          is no part of your own job — &ldquo;I don't do this&rdquo; is not a
          reason to mark a task Not relevant.
        </p>
      </>
    ),
  },
  {
    title: "Frequency",
    body: (
      <>
        <p className="mt-2 text-sm font-medium text-slate-700 leading-relaxed">
          How often is this task performed in the occupation?
        </p>
        <Scale items={SCALES.frequency} />
        <p className="mt-3 text-sm text-slate-600 leading-relaxed">
          For example, "Interact with potential customers" might be performed
          once per week or less in one occupation, but hourly or more often in
          another.
        </p>
      </>
    ),
  },
  {
    title: "Importance",
    body: (
      <>
        <p className="mt-2 text-sm font-medium text-slate-700 leading-relaxed">
          How important is this task to successful performance of the
          occupation?
        </p>
        <Scale items={SCALES.importance} />
        <p className="mt-3 text-sm text-slate-600 leading-relaxed">
          For example, "Develop objectives and strategies to guide the
          organization" might be very important in one occupation but less so in
          another, where "Provide performance feedback to subordinates" is what
          matters most.
        </p>
      </>
    ),
  },
  {
    title: "Task evaluability",
    body: (
      <>
        <p className="mt-2 text-sm text-slate-600 leading-relaxed">
          Two questions per task — whether good performance can be specified up
          front, and whether it can be verified afterwards. Each is answered{" "}
          {VERDICTS.map((v, i) => (
            <span key={v.value}>
              {i > 0 && (i === VERDICTS.length - 1 ? ", or " : ", ")}
              <span className="font-medium text-slate-700">{v.label}</span>
            </span>
          ))}
          .
        </p>
        <ul className="mt-3 list-disc space-y-2 pl-5">
          {EVALUABILITY_QUESTIONS.map((q) => (
            <li key={q.key} className="text-sm text-slate-600 leading-relaxed">
              {q.question}
            </li>
          ))}
        </ul>
      </>
    ),
  },
  {
    title: "Duration",
    body: (
      <>
        <p className="mt-2 text-sm font-medium text-slate-700 leading-relaxed">
          How long would it typically take a worker in this occupation to
          complete this task once?
        </p>
        <Scale items={SCALES.duration} />
        <p className="mt-3 text-sm text-slate-600 leading-relaxed">
          Think of one full workday as 8 hours.
        </p>
      </>
    ),
  },
  {
    title: "Training and experience",
    body: (
      <>
        <p className="mt-2 text-sm text-slate-600 leading-relaxed">
          Consider someone who is newly hired into this occupation and{" "}
          <span className="font-semibold text-slate-700">
            has not previously performed this task
          </span>
          .
        </p>
        <p className="mt-2 text-sm font-medium text-slate-700 leading-relaxed">
          How much training and experience would this person typically need
          before they could perform the task independently at the level normally
          expected in the occupation?
        </p>
        <Scale items={SCALES.training} />
        <p className="mt-3 text-sm text-slate-600 leading-relaxed">
          This includes related work experience, organized classroom study or
          other structured instruction provided by the employer, and on-the-job
          training.
        </p>
      </>
    ),
  },
  {
    title: "Task performance",
    body: (
      <>
        <p className="mt-2 text-sm text-slate-600 leading-relaxed">
          Consider two workers in the same occupation and work setting:
        </p>
        <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm text-slate-600 leading-relaxed">
          <li>
            a <span className="font-medium text-slate-700">typical worker</span>,
            whose overall performance is at the level normally expected; and
          </li>
          <li>
            the{" "}
            <span className="font-medium text-slate-700">
              high-performing worker
            </span>
            , whose overall performance is among the top 10% of workers in
            the occupation.
          </li>
        </ul>
        <p className="mt-3 text-sm font-medium text-slate-700 leading-relaxed">
          On which tasks does the high-performing worker typically outperform
          the typical worker?
        </p>
        <p className="mt-1.5 text-xs text-slate-400">
          Select all that apply — you'll see your relevant tasks in one list.
        </p>
      </>
    ),
  },
];

export function CoverageAbout({ onContinue }: { onContinue: () => void }) {
  const [active, setActive] = useState(0);
  // Continue unlocks only once every rating card has been shown.
  const [seen, setSeen] = useState<Set<number>>(() => new Set([0]));

  useEffect(() => {
    setSeen((prev) => (prev.has(active) ? prev : new Set(prev).add(active)));
  }, [active]);

  // On phones the tab strip scrolls; Next must bring the newly active chip into view.
  const chipRefs = useRef<(HTMLButtonElement | null)[]>([]);
  useEffect(() => {
    chipRefs.current[active]?.scrollIntoView({
      block: "nearest", inline: "nearest", behavior: "smooth",
    });
  }, [active]);

  const allSeen = seen.size === SECTIONS.length;
  const isLast = active === SECTIONS.length - 1;

  return (
    // Center with m-auto on the child: items-center on a scrolling container clips the top of tall content.
    <div className="h-full overflow-y-auto px-5 sm:px-6 py-8 sm:py-14 flex">
      <div className="m-auto w-full max-w-3xl animate-fadeSlideIn">
        <h1 className="text-[1.6rem] sm:text-[2rem] font-light text-slate-800 leading-tight tracking-[-0.015em]">
          Instructions
        </h1>

        <div className="mt-6 space-y-4 text-[15px] text-slate-600 leading-relaxed">
          <p>
            For each task, you will rate its relevance, frequency, importance,
            ease of evaluation, time required, and training needed. Then, across
            the tasks you marked relevant, you will identify where the
            high-performing worker outperforms the typical worker.
          </p>
        </div>

        {/* Named tabs — jump to any rating type; a check marks the ones read. On phones the
            seven pills wrapped into three ragged rows and shoved the card below the fold, so
            below `sm` they're ONE horizontally scrollable row (the standard mobile tab strip;
            the edge fade-out from the bleed margins cues that it scrolls). */}
        <div className="mt-6 sm:mt-8 flex gap-1.5 overflow-x-auto flex-nowrap sm:flex-wrap sm:overflow-visible -mx-5 px-5 sm:mx-0 sm:px-0 pb-1 sm:pb-0 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {SECTIONS.map((s, i) => (
            <button
              key={i}
              ref={(el) => { chipRefs.current[i] = el; }}
              type="button"
              onClick={() => setActive(i)}
              className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium whitespace-nowrap transition ${
                i === active
                  ? "bg-indigo-600 text-white shadow-sm shadow-indigo-200"
                  : seen.has(i)
                    ? "bg-indigo-50 text-indigo-600 hover:bg-indigo-100"
                    : "bg-slate-100 text-slate-500 hover:bg-slate-200"
              }`}
            >
              {seen.has(i) && i !== active && (
                <svg
                  viewBox="0 0 16 16"
                  className="h-3 w-3"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <polyline points="3.5 8.5 6.5 11.5 12.5 5" />
                </svg>
              )}
              {s.title}
            </button>
          ))}
        </div>

        <div className="mt-3 rounded-2xl border border-slate-200 p-4 sm:p-5">
          <div key={active} className="animate-fadeSlideIn">
            <h2 className="text-sm font-semibold text-slate-800">
              {SECTIONS[active].title}
            </h2>
            {SECTIONS[active].body}
          </div>
        </div>

        {/* Next walks the cards in order; on the last one it becomes Continue. */}
        <div className="mt-6 flex items-center justify-between gap-4 pb-4">
          <button
            type="button"
            onClick={() => setActive(active - 1)}
            disabled={active === 0}
            className="text-sm font-medium text-slate-400 hover:text-slate-600 disabled:invisible transition"
          >
            Back
          </button>
          <div className="flex items-center gap-4">
            {isLast && !allSeen && (
              <p className="text-xs text-slate-400">
                Please review all the rating types before continuing.
              </p>
            )}
            <button
              onClick={() => (isLast ? onContinue() : setActive(active + 1))}
              disabled={isLast && !allSeen}
              className="inline-flex items-center gap-2 px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-30 disabled:cursor-not-allowed text-white text-sm font-medium rounded-full transition-all active:scale-[0.98] shadow-sm shadow-indigo-200"
            >
              {isLast ? "Continue" : "Next"}
              <ArrowRight />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
