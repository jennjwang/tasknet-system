import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useWorkflowStore } from "../store";
import { fetchOccupationCandidates, fetchOccupations, saveSession, screenerSelect } from "../lib/api";
import type { OccupationCandidate } from "../types";
import { MATCH_COUNT } from "../settings";
import { LoadingDots } from "./ui";


// O*NET-SOC definitions are written as verb-led duty sentences, so a single
// paragraph reads like a run-on task dump. Split on sentence boundaries so each
// card shows a short bullet list instead.
function definitionBullets(definition: string): string[] {
  return definition
    .split(/(?<=\.)\s+/)
    .map((s) => s.trim().replace(/\.$/, ""))
    .filter(Boolean);
}

// Occupation self-ID screen: the participant picks the O*NET-SOC occupation they
// best identify with. It offers the full union the classifier returns — the LLM's
// ranked matches over all 1016 occupations plus O*NET's related occupations for the
// top one — as a scrollable list, strongest agreement first. If none of them fit,
// they can give a one-line hint and "show others" (which excludes what they've
// already seen), or search all 1016 by name. The pick plus the full trail (sets
// shown, rejected codes, hints) is stored on the session.
//
// This screen sits in one of two places, and its role differs:
//   screener ON  — right after the two screener questions, BEFORE the interview,
//                  and the pick IS THE GATE: the model only proposes candidates,
//                  the participant decides, and /api/screener/select rules on
//                  their choice (pass → interview, else → screen-out).
//   screener OFF — right after the interview, purely descriptive, then on to
//                  task selection.
export function OccupationSelect() {
  const {
    sessionId,
    backgroundTranscript,
    getExportData,
    setPhase,
    setOccupationSelection,
    screenerEnabled,
    screener,
    setScreener,
    setScreening,
    externalId,
  } = useWorkflowStore(
    useShallow((s) => ({
      sessionId: s.sessionId,
      backgroundTranscript: s.backgroundTranscript,
      getExportData: s.getExportData,
      setPhase: s.setPhase,
      setOccupationSelection: s.setOccupationSelection,
      screenerEnabled: s.screenerEnabled,
      screener: s.screener,
      setScreener: s.setScreener,
      setScreening: s.setScreening,
      externalId: s.externalId,
    })),
  );

  const [candidates, setCandidates] = useState<OccupationCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [hintText, setHintText] = useState("");
  const [mode, setMode] = useState<"candidates" | "search">("candidates");
  const [allOcc, setAllOcc] = useState<OccupationCandidate[]>([]);
  const [query, setQuery] = useState("");

  // Accumulated across "show different options" — persisted with the selection.
  const shownSets = useRef<OccupationCandidate[][]>([]);
  const rejectedCodes = useRef<string[]>([]);
  const hints = useRef<string[]>([]);

  async function fetchCandidates(excludeCodes: string[], hint: string) {
    setLoading(true);
    try {
      const cand = await fetchOccupationCandidates({
        backgroundTranscript,
        excludeCodes,
        hint,
        topN: MATCH_COUNT,
        // Orders the scroll by evidence (see orderByEvidence in
        // lib/occupation.js). Nothing is dropped — this only decides
        // how far someone has to scroll to find themselves.
        preferAgreement: true,
        // Uncapped: the list is a scroll, so every occupation O*NET relates to
        // the top match is offered rather than truncated at a fixed length.
        unionCap: 0,
      });
      setCandidates(cand);
      if (cand.length) shownSets.current.push(cand);
      else { await ensureAllOcc(); setMode("search"); }   // fail-open → search
    } catch {
      setCandidates([]);
      await ensureAllOcc();
      setMode("search");
    } finally {
      setLoading(false);
      setHintText("");
    }
  }

  // Fetch candidates once on mount.
  useEffect(() => {
    fetchCandidates([], "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function ensureAllOcc() {
    if (allOcc.length) return;
    setAllOcc(await fetchOccupations());
  }

  function showDifferent() {
    rejectedCodes.current.push(...candidates.map((c) => c.code));
    const h = hintText.trim();
    if (h) hints.current.push(h);
    fetchCandidates(rejectedCodes.current.slice(), h);
  }

  async function choose(code: string, title: string, fromSearch: boolean) {
    if (submitting) return;
    setSubmitting(true);
    setOccupationSelection({
      selectedCode: code,
      selectedTitle: title,
      fromSearch,
      shownSets: shownSets.current,
      rejectedCodes: rejectedCodes.current,
      hints: hints.current,
      selectedAt: Date.now(),
    });

    // Screener placement: the pick is the gate. Rule on it BEFORE saving the
    // session, so a rejected participant's session records the screen-out.
    if (screenerEnabled) {
      let pass = true;
      try {
        const v = await screenerSelect({
          code,
          selectedTitle: title,
          fromSearch,
          title: screener?.title ?? "",
          responsibilities: screener?.responsibilities ?? "",
          externalId,
          sessionId,
        });
        pass = v.pass;
      } catch (e) {
        // Fail OPEN: a gate we couldn't reach must not reject a real recruit.
        // The server-side record is authoritative, so an admitted mismatch is
        // recoverable in analysis; a wrongly rejected participant is not.
        console.error("[screener] select gate failed — admitting:", e);
      }
      setScreener({
        title: screener?.title ?? "",
        responsibilities: screener?.responsibilities ?? "",
        selectedCode: code,
        selectedTitle: title,
        pass,
        at: Date.now(),
      });
      if (!pass) {
        setScreening({ screenedOut: true, screenOutReason: "occupation-mismatch" });
        setPhase("screen-out");
        return; // ScreenOut saves the partial session itself
      }
    }

    try {
      await saveSession(sessionId, getExportData());
    } catch {
      // Best-effort — proceed regardless.
    }
    // Screener on: the interview is next. Off: the interview already happened.
    setPhase(screenerEnabled ? "background" : "task-selection");
  }

  const filtered = query.trim()
    ? allOcc
        .filter((o) => `${o.title} ${o.code}`.toLowerCase().includes(query.trim().toLowerCase()))
        .slice(0, 60)
    : allOcc.slice(0, 60);

  // Loading state mirrors the study's other loaders: a bare centered loader with
  // a caption and NO heading/subcopy (those belong to the picked-screen content).
  if (loading) {
    return (
      <div className="min-h-screen bg-white flex flex-col items-center justify-center px-5 sm:px-8 py-12 sm:py-16">
        <div className="absolute top-0 left-0 right-0 h-56 bg-gradient-to-b from-indigo-50/30 to-transparent pointer-events-none" />
        <div className="relative flex flex-col items-center gap-5">
          <LoadingDots dot="w-2 h-2 bg-indigo-300" />
          <p className="text-slate-400 text-sm animate-fadeSlideIn">
            Finding the best matches…
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-white flex flex-col items-center justify-center px-5 sm:px-8 py-12 sm:py-16">
      <div className="absolute top-0 left-0 right-0 h-56 bg-gradient-to-b from-indigo-50/30 to-transparent pointer-events-none" />

      <div className="relative max-w-2xl w-full">
        <h1 className="text-[1.5rem] sm:text-[1.85rem] font-light text-slate-800 leading-snug tracking-tight mb-2">
          Which occupation best describes your job?
        </h1>
        <p className="text-sm text-slate-500 leading-relaxed mb-10">
          {screenerEnabled
            ? "Based on what you've told us, here are the closest matches — scroll to see them all. Read each description and pick the one you most identify with."
            : "Based on your interview, here are the closest matches — scroll to see them all. Read each description and pick the one you most identify with."}
        </p>

        {mode === "candidates" ? (
          <>
            {/* The WHOLE union is offered, in a scroll rather than a hard top-N cut:
                with a cut, a genuine target whose occupation fell below it would take
                the closest visible card, and where the pick is the gate that ends
                their session. */}
            <div className="scroll-visible mt-6 space-y-3 max-h-[58vh] overflow-y-auto pr-3">
              {candidates.map((c) => (
                <button
                  key={c.code}
                  type="button"
                  disabled={submitting}
                  onClick={() => choose(c.code, c.title, false)}
                  className="w-full text-left p-4 rounded-xl border border-slate-200 bg-white hover:border-indigo-400 hover:bg-indigo-50/40 transition disabled:opacity-60"
                >
                  <span className="block font-medium text-slate-800">{c.title}</span>
                  {c.definition && (
                    <ul className="mt-2 list-disc pl-4 space-y-1 text-xs text-slate-500 leading-relaxed">
                      {definitionBullets(c.definition).map((b, i) => (
                        <li key={i}>{b}</li>
                      ))}
                    </ul>
                  )}
                </button>
              ))}
            </div>

            <div className="mt-6 border-t border-slate-100 pt-5 space-y-3">
              <label className="block text-sm font-medium text-slate-700">
                Not quite right? Tell us what would fit better and we'll suggest others.
              </label>
              <input
                type="text"
                value={hintText}
                onChange={(e) => setHintText(e.target.value)}
                placeholder="e.g. more technical, I'm more of a manager, it's more creative"
                className="w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-800 placeholder:text-slate-400 focus:border-indigo-300 focus:outline-none focus:ring-2 focus:ring-indigo-100"
              />
              <button
                type="button"
                onClick={showDifferent}
                className="px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium transition"
              >
                Show others
              </button>
            </div>
          </>
        ) : (
          <div className="mt-6">
            <input
              type="text"
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search all occupations…"
              className="w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-800 placeholder:text-slate-400 focus:border-indigo-300 focus:outline-none focus:ring-2 focus:ring-indigo-100"
            />
            <div className="scroll-visible mt-3 max-h-[55vh] overflow-y-auto space-y-1.5 pr-3">
              {filtered.map((o) => (
                <button
                  key={o.code}
                  type="button"
                  disabled={submitting}
                  onClick={() => choose(o.code, o.title, true)}
                  className="w-full text-left px-4 py-2.5 rounded-lg border border-slate-200 bg-white hover:border-indigo-400 hover:bg-indigo-50/40 transition text-sm text-slate-700 disabled:opacity-60"
                >
                  {o.title}
                </button>
              ))}
              {!filtered.length && (
                <p className="text-sm text-slate-400 px-1 py-2">No matching occupations.</p>
              )}
            </div>
            {candidates.length > 0 && (
              <button
                type="button"
                onClick={() => { setMode("candidates"); setQuery(""); }}
                className="mt-4 text-sm font-medium text-indigo-600 hover:text-indigo-700"
              >
                ← Back to suggestions
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
