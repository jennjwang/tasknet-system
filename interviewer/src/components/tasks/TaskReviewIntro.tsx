import { ArrowRightIcon } from "../ui";

// The task review's opening screen: what this step is for, and why editing helps.
export function TaskReviewIntro({ onStart }: { onStart: () => void }) {
  return (
    <div className="flex flex-col h-full bg-transparent relative overflow-hidden">
      {/* Top gradient is rendered by the parent (App.tsx) so it spans the full viewport. */}
      <div className="relative z-10 flex flex-1 items-center justify-center px-5 sm:px-8 py-10 overflow-y-auto">
        <div className="max-w-lg w-full">
          <p
            className="text-[10px] font-semibold uppercase tracking-[0.18em] text-indigo-400 mb-9 animate-fadeSlideUp"
            style={{ animationDelay: "0ms" }}
          >
            Task Review
          </p>
          <h2
            className="text-[1.4rem] sm:text-[1.65rem] font-light text-slate-800 leading-snug tracking-tight animate-fadeSlideUp"
            style={{ animationDelay: "80ms" }}
          >
            Let's check what we heard.
          </h2>
          <p
            className="text-slate-500 mt-6 text-[15px] leading-[1.7] animate-fadeSlideUp"
            style={{ animationDelay: "160ms" }}
          >
            From your interview, we pulled together the tasks below. This step
            is just to make sure we captured your work correctly.
          </p>
          <p
            className="text-slate-500 mt-4 text-[15px] leading-[1.7] animate-fadeSlideUp"
            style={{ animationDelay: "220ms" }}
          >
            Go through each one: confirm the tasks you actually do, drop the
            ones you don't, and reword anything that doesn't quite match how
            you'd describe it.
          </p>
          <div
            className="mt-6 px-5 py-4 rounded-xl border border-indigo-100 bg-indigo-50/60 animate-fadeSlideUp"
            style={{ animationDelay: "280ms" }}
          >
            <p className="text-sm font-semibold text-indigo-700 mb-1.5">
              Why edit the tasks
            </p>
            <p className="text-sm text-slate-600 leading-relaxed">
              No one understands your work better than you do. Correcting and
              rewording these tasks so they match what you actually do — and how
              you'd describe it — is exactly what helps us get your role right.
            </p>
          </div>
          <button
            onClick={onStart}
            className="mt-10 sm:mt-14 inline-flex items-center gap-2 px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-full transition-all active:scale-[0.98] shadow-sm shadow-indigo-200 animate-fadeSlideUp"
            style={{ animationDelay: "420ms" }}
          >
            Start
            <ArrowRightIcon strokeWidth={2.2} head="13 6 19 12 13 18" />
          </button>
        </div>
      </div>
    </div>
  );
}
