import { ArrowRight } from "./validationUi";

// "Before you start": rate the occupation as a whole, not your own job. It changes what every rating
// means, so it gets its own screen, right before the first task.

export function CoverageScope({ onContinue }: { onContinue: () => void }) {
  return (
    <div className="h-full overflow-y-auto px-6 py-10 sm:py-14 flex items-center justify-center">
      <div className="mx-auto max-w-3xl animate-fadeSlideIn">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-indigo-400 mb-4">
          Before you start
        </p>
        <h1 className="text-[1.6rem] sm:text-[2rem] font-light text-slate-800 leading-tight tracking-[-0.015em]">
          Please rate the occupation in general — not your own job.
        </h1>

        <div className="mt-6 space-y-4 text-[15px] text-slate-600 leading-relaxed">
          <p>
            The tasks that follow ask about the occupation as a whole. Please
            answer for it as it is practised across employers, work settings and
            specializations — not only as you practice it yourself. In
            particular:
          </p>
          <ul className="list-disc space-y-3 pl-5">
            <li>
              A task that is a common part of the work counts as{" "}
              <span className="font-medium text-slate-700">relevant</span> even
              if you never do it yourself — because it sits with a
              specialization you don't hold or a setting you don't work in.
            </li>
            <li>
              The reverse too: something you happen to do that is not typical of
              the occupation is{" "}
              <span className="font-medium text-slate-700">not relevant</span>,
              however much of your own week it takes.
            </li>
          </ul>
        </div>

        <div className="mt-10 flex justify-end pb-4">
          <button
            onClick={onContinue}
            className="inline-flex items-center gap-2 px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-full transition-all active:scale-[0.98] shadow-sm shadow-indigo-200"
          >
            Start rating
            <ArrowRight />
          </button>
        </div>
      </div>
    </div>
  );
}
