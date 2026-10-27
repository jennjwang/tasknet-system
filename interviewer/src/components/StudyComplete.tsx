import { useSaveOnMount } from "../lib/useSaveOnMount";

export function StudyComplete() {
  // The last save: it records the completed task review and the completion itself
  // (there is no completion redirect).
  useSaveOnMount();

  return (
    <div className="min-h-screen bg-white flex flex-col items-center justify-center px-5 sm:px-8 py-16">
      <div className="absolute top-0 left-0 right-0 h-56 bg-gradient-to-b from-indigo-50/30 to-transparent pointer-events-none" />

      <div className="relative max-w-2xl w-full text-center">
        <div className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-emerald-50 border-2 border-emerald-200 mb-8">
          <svg
            className="w-7 h-7 text-emerald-500"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <polyline points="20 6 9 17 4 12" />
          </svg>
        </div>

        <h1 className="text-[1.85rem] font-light text-slate-800 leading-snug tracking-tight mb-4">
          Thank you for your response.
        </h1>
        <p className="text-sm text-slate-500 leading-relaxed">
          Your answers have been recorded. You can close this tab.
        </p>
      </div>
    </div>
  );
}
