import { ArrowRight } from "./validationUi";

// "About this study": the first screen. The research goal and what the survey asks of the participant.

export function CoverageIntro({ onContinue }: { onContinue: () => void }) {
  return (
    <div className="h-full overflow-y-auto px-6 py-10 sm:py-14 flex items-center justify-center">
      <div className="mx-auto max-w-3xl animate-fadeSlideIn">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-indigo-400 mb-4">
          Welcome
        </p>
        <h1 className="text-[1.6rem] sm:text-[2rem] font-light text-slate-800 leading-tight tracking-[-0.015em]">
          About this study
        </h1>

        <div className="mt-6 space-y-4 text-[15px] text-slate-600 leading-relaxed">
          <p>
            We're developing better ways to understand how people's tasks at
            work are changing as they use AI tools.
          </p>
          <p>
            To do that, we assembled a list of the tasks that make up each
            occupation. This stage is where we check those lists with the people
            who actually do the work.
          </p>
          <p>
            As an expert in your occupation, you'll review a set of tasks for
            your occupation and tell us, for each one, whether it is really part
            of your occupation's work — and if so, how often it is done and how
            important it is. Throughout, we're asking about{" "}
            <span className="font-medium text-slate-700">
              the occupation as a whole
            </span>{" "}
            — the work as it is done across employers, settings and
            specializations — rather than about your own job in particular.
          </p>
          <p>
            You don't have to go through all tasks in one sitting. Your answers
            are saved as you go — close the tab whenever you need to, then
            reopen the same link and you'll pick up on the task you left off on.
          </p>
        </div>

        <div className="mt-10 flex justify-end pb-4">
          <button
            onClick={onContinue}
            className="inline-flex items-center gap-2 px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-full transition-all active:scale-[0.98] shadow-sm shadow-indigo-200"
          >
            Continue
            <ArrowRight />
          </button>
        </div>
      </div>
    </div>
  );
}
