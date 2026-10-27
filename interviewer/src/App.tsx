import { useEffect, useState } from "react";
import { useWorkflowStore } from "./store";
import { Welcome } from "./components/Welcome";
import { BackgroundInterview } from "./components/interview/BackgroundInterview";
import { Screener } from "./components/interview/Screener";
import { TaskSelection } from "./components/tasks/TaskSelection";
import { OccupationSelect } from "./components/OccupationSelect";
import { StudyComplete } from "./components/StudyComplete";
import { ScreenOut } from "./components/ScreenOut";
import { LoadingDots } from "./components/ui";
import {
  fetchAppConfig,
  fetchOccupationTitle,
  fetchScreenerEnabled,
  checkScreenStatus,
  fetchSession,
  saveSession,
  saveSessionBeacon,
} from "./lib/api";

// Paste is blocked by default so participants type their own answers. Append
// ?paste=1 to the URL to allow copy/paste (e.g. for testing).
const ALLOW_PASTE =
  new URLSearchParams(window.location.search).get("paste") === "1";

// Phases with their own save (StudyComplete and ScreenOut save on mount), or nothing to save yet.
const NO_AUTOSAVE = new Set(["setup", "screen-out", "occupation-select", "study-complete"]);
// Leaving mid-way through these asks "Leave site?" first.
const PROTECTED_PHASES = new Set(["background", "task-selection"]);

export default function App() {
  const phase = useWorkflowStore((s) => s.phase);
  const sessionId = useWorkflowStore((s) => s.sessionId);
  const externalId = useWorkflowStore((s) => s.externalId);
  const invitedSoc = useWorkflowStore((s) => s.invitedSoc);
  const getExportData = useWorkflowStore((s) => s.getExportData);
  const setScreening = useWorkflowStore((s) => s.setScreening);
  const setPhase = useWorkflowStore((s) => s.setPhase);
  const hydrateFromSnapshot = useWorkflowStore((s) => s.hydrateFromSnapshot);
  const setScreenerEnabled = useWorkflowStore((s) => s.setScreenerEnabled);
  const setInvitedTitle = useWorkflowStore((s) => s.setInvitedTitle);

  // Brief loading window while we check whether this participant has been
  // screened out before. Prevents a flash of the welcome screen for a
  // refresh-after-fail.
  const [bootstrapping, setBootstrapping] = useState<boolean>(!!externalId);
  // Fetching the saved session snapshot to rehydrate the store. True until the
  // GET /api/session call resolves, regardless of whether a snapshot was found.
  const [hydrating, setHydrating] = useState<boolean>(true);

  // Rehydrate from a previously-saved session snapshot. Runs once on mount,
  // before any participant interaction. If the localStorage-pinned sessionId
  // matches an existing snapshot on the server, restore phase + state so the
  // participant lands where they left off after a reload — otherwise a reload
  // would re-run the occupation gate, and a different pick could screen out
  // someone who already passed. sessionId is pinned in localStorage per ?id=, so
  // a participant can only resume their own session. The autosave below waits
  // for this, and the server refuses to overwrite a real snapshot with empty
  // state, so a fast-closing tab during this fetch can't clobber the file.
  useEffect(() => {
    let cancelled = false;
    fetchSession(sessionId, externalId)
      .then((snap) => {
        if (cancelled || !snap.found || !snap.data) return;
        // Don't restore a completed/terminal session — start fresh instead.
        const restoredPhase = snap.data.phase;
        if (restoredPhase === "study-complete" || restoredPhase === "screen-out") return;
        hydrateFromSnapshot(snap.data);
      })
      .finally(() => {
        if (!cancelled) setHydrating(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // If this load carries an ?id=, check whether it was previously screened out.
  // If so, jump straight to the screen-out phase, so refreshing the page can't
  // bypass it — otherwise a participant could reload and re-run the occupation
  // gate until a pick happened to pass.
  useEffect(() => {
    if (!externalId) return;
    let cancelled = false;
    checkScreenStatus(externalId)
      .then((status) => {
        if (cancelled || !status.screenedOut) return;
        // Carry the reason through so ScreenOut shows the right copy — a
        // mismatch reads as "wrong study for you", a failed check doesn't.
        setScreening({ screenedOut: true, screenOutReason: status.reason ?? null });
        setPhase("screen-out");
      })
      .finally(() => {
        if (!cancelled) setBootstrapping(false);
      });
    return () => {
      cancelled = true;
    };
  }, [externalId, setScreening, setPhase]);

  // Server-side settings, once on mount: the attention-check threshold, and whether a recruiting target is
  // configured (which decides where the occupation selector sits and whether it gates).
  useEffect(() => {
    fetchAppConfig().then((cfg) => setScreening({ attnCheckMaxFails: cfg.attnCheckMaxFails }));
    fetchScreenerEnabled().then(setScreenerEnabled);
  }, [setScreening, setScreenerEnabled]);

  // Resolve the ?soc= code to an occupation title for the interview's intro
  // screens. Fetched here, on mount, rather than in the interview: the intro is
  // the first thing shown after consent, and a lookup started there would land
  // mid-animation and swap the copy while the participant is reading it.
  useEffect(() => {
    if (!invitedSoc) return;
    let cancelled = false;
    fetchOccupationTitle(invitedSoc).then((t) => {
      if (!cancelled) setInvitedTitle(t);
    });
    return () => {
      cancelled = true;
    };
  }, [invitedSoc, setInvitedTitle]);

  // Block pasting into any text field so participants must type their own
  // answers rather than dropping in pre-written or AI-generated text. A single
  // capture-phase listener covers every <input>, <textarea>, and
  // contentEditable element across the app without touching each component.
  useEffect(() => {
    if (ALLOW_PASTE) return;
    const blockPaste = (e: ClipboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (!t) return;
      if (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    document.addEventListener("paste", blockPaste, true);
    return () => document.removeEventListener("paste", blockPaste, true);
  }, []);

  // Autosave, 800ms after the last change to anything the export carries. Each
  // slice is subscribed on its own so the effect re-runs only when one changes.
  const backgroundTranscript = useWorkflowStore((s) => s.backgroundTranscript);
  const taskItems = useWorkflowStore((s) => s.taskItems);
  const selectedTasks = useWorkflowStore((s) => s.selectedTasks);
  useEffect(() => {
    // Not until hydration has settled, so a fresh-store save can't race the
    // hydrate fetch (the server also refuses empty overwrites).
    if (NO_AUTOSAVE.has(phase) || hydrating) return;
    const handle = setTimeout(() => {
      saveSession(sessionId, getExportData()).catch(() => {});
    }, 800);
    return () => clearTimeout(handle);
  }, [phase, sessionId, hydrating, getExportData, backgroundTranscript, taskItems, selectedTasks]);

  // Flush the latest state on page unload via sendBeacon, so a tab closed inside
  // the autosave's 800ms window still keeps its last action. `pagehide` fires
  // reliably on mobile/iOS where `beforeunload` doesn't, so listen to both;
  // beforeunload also asks "Leave site?" mid-study.
  useEffect(() => {
    const flush = () => saveSessionBeacon(sessionId, getExportData());
    const promptOnLeave = (e: BeforeUnloadEvent) => {
      flush();
      if (PROTECTED_PHASES.has(useWorkflowStore.getState().phase)) {
        e.preventDefault();
        e.returnValue = ""; // required by older browsers to surface the prompt
      }
    };
    window.addEventListener("beforeunload", promptOnLeave);
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("beforeunload", promptOnLeave);
      window.removeEventListener("pagehide", flush);
    };
  }, [sessionId, getExportData]);

  if (bootstrapping || hydrating) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-white">
        <LoadingDots dot="w-2 h-2 bg-indigo-200" />
      </div>
    );
  }

  switch (phase) {
    case "setup":
      return <Welcome />;
    // The screener and the interview run on the same question card (QuestionCard).
    case "screen":
    case "background":
      return (
        <div className="flex app-screen w-screen overflow-hidden">
          <div className="flex-1 min-w-0">
            {phase === "screen" ? <Screener /> : <BackgroundInterview />}
          </div>
        </div>
      );
    case "task-selection":
      return (
        <div className="relative flex app-screen w-screen overflow-hidden bg-white">
          {/* Gradient spans the full viewport so it doesn't get clipped to the centered column. */}
          <div className="absolute top-0 left-0 right-0 h-48 bg-gradient-to-b from-indigo-50/40 to-transparent pointer-events-none" />
          <div className="relative w-full max-w-[1040px] mx-auto h-full">
            <TaskSelection />
          </div>
        </div>
      );
    case "occupation-select":
      return <OccupationSelect />;
    case "screen-out":
      return <ScreenOut />;
    default:
      return <StudyComplete />;
  }
}
