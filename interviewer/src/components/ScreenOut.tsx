import { useWorkflowStore } from "../store";
import { useSaveOnMount } from "../lib/useSaveOnMount";

export function ScreenOut() {
  const screening = useWorkflowStore((s) => s.screening);

  // Persist the partial response (including the screen-out reason) so
  // researchers can audit screen-outs. The FULL export, not a hand-picked subset:
  // it carries externalId, which the server needs to name the file
  // <externalId>_<sessionId>.json -- without it a screen-out landed in
  // <sessionId>.json, detached from the participant's other saves.
  useSaveOnMount(() => {
    const state = useWorkflowStore.getState();
    return {
      ...state.getExportData(),
      screenedOut: true,
      // Set by whichever gate routed here: the pre-interview occupation
      // screener ('occupation-mismatch') or a failed attention check.
      screenOutReason: state.screening.screenOutReason ?? "attention-check-failed",
    };
  });

  // An occupation mismatch is about FIT, not about anything the participant did
  // wrong — they answered honestly and picked their own occupation, and the study
  // happens to be recruiting a different one. Saying "your responses don't
  // qualify" reads as an accusation and is the kind of thing that turns into a
  // one-star review and a support message. A failed attention check stays vague
  // on purpose: spelling out the mechanism teaches the next participant how to
  // beat it.
  const mismatch = screening.screenOutReason === "occupation-mismatch";
  const heading = mismatch
    ? "This study is looking for a different role."
    : "You don't qualify for this study.";
  const body = mismatch
    ? "We're recruiting people in a particular set of occupations, and the one you selected isn't among them this time. Thank you for taking the time."
    : "Thanks for taking the time.";

  return (
    <div className="min-h-screen bg-white flex flex-col items-center justify-center px-5 sm:px-8 py-16">
      <div className="absolute top-0 left-0 right-0 h-56 bg-gradient-to-b from-slate-50 to-transparent pointer-events-none" />

      <div className="relative max-w-md w-full text-center">
        {/* An info "i": being screened out is neither an error nor a success,
            so it reads as neither a warning nor a tick. */}
        <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-slate-100 border border-slate-200 mb-7">
          <svg
            className="w-6 h-6 text-slate-400"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="11" x2="12" y2="16.5" />
            <line x1="12" y1="7.75" x2="12.01" y2="7.75" />
          </svg>
        </div>

        <h1 className="text-[1.6rem] font-light text-slate-800 leading-snug tracking-tight mb-3">
          {heading}
        </h1>
        <p className="text-sm text-slate-500 leading-relaxed mb-8">{body}</p>

        <p className="text-sm text-slate-400">
          Your response has been recorded. You can close this tab.
        </p>
      </div>
    </div>
  );
}
