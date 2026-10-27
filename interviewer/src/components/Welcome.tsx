import { useWorkflowStore } from "../store";
import { STUDY_DESCRIPTION } from "../settings";
import { ArrowRightIcon } from "./ui";

// The consent form participants are asked to read (VITE_CONSENT_URL in interviewer/.env, read at build time).
// None ships with the app; unset, the screen shows no consent link or agreement line.
const CONSENT_URL: string | undefined = import.meta.env.VITE_CONSENT_URL;

export function Welcome() {
  const setPhase = useWorkflowStore((s) => s.setPhase);
  // Consent first, then the screener (intro → two questions → occupation
  // selector) when a recruiting target is configured, else straight to the
  // interview. App fetches the flag on mount, so it's settled by the time the
  // participant has read the consent copy.
  const screenerEnabled = useWorkflowStore((s) => s.screenerEnabled);

  return (
    <div className="min-h-screen bg-white relative overflow-hidden flex flex-col">
      <div className="absolute top-0 left-0 right-0 h-72 bg-gradient-to-b from-indigo-50/40 to-transparent pointer-events-none" />

      {/* Welcome card */}
      <div className="relative z-10 flex-1 flex items-center justify-center px-5 sm:px-6 py-10 sm:py-12">
        <div className="max-w-2xl w-full">
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-indigo-400 mb-4">
            Welcome
          </p>
          <h1 className="text-[1.6rem] sm:text-[2rem] font-light text-slate-800 leading-tight tracking-[-0.015em] mb-6">
            About this study
          </h1>

          <div className="space-y-4 text-[15px] text-slate-600 leading-relaxed">
            {STUDY_DESCRIPTION.map((text, i) => <p key={i}>{text}</p>)}
            {CONSENT_URL && <p>Before you begin, please review the consent form.</p>}
          </div>

          {CONSENT_URL && (<>
          <a
            href={CONSENT_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-6 inline-flex items-center gap-2 px-5 py-2.5 border border-slate-300 text-slate-700 hover:border-indigo-400 hover:text-indigo-700 hover:bg-indigo-50/40 text-sm font-medium rounded-full transition-all active:scale-[0.98]"
          >
            <svg
              className="w-4 h-4"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="8" y1="13" x2="16" y2="13" />
              <line x1="8" y1="17" x2="16" y2="17" />
            </svg>
            Review consent form
            <svg
              className="w-3.5 h-3.5 opacity-60"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
              <polyline points="15 3 21 3 21 9" />
              <line x1="10" y1="14" x2="21" y2="3" />
            </svg>
          </a>

          <p className="mt-12 text-xs text-slate-500 leading-relaxed">
            By clicking “Begin interview,” you confirm that you have read and
            agree to the{" "}
            <a
              href={CONSENT_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="text-indigo-600 hover:text-indigo-700 underline font-medium"
            >
              consent form
            </a>
            .
          </p>
          </>)}

          <button
            onClick={() => setPhase(screenerEnabled ? "screen" : "background")}
            className="mt-4 inline-flex items-center gap-2 px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-full transition-all active:scale-[0.98] shadow-sm shadow-indigo-200"
          >
            Begin interview
            <ArrowRightIcon strokeWidth={2.2} head="13 6 19 12 13 18" />
          </button>
        </div>
      </div>
    </div>
  );
}
