import { create } from 'zustand';
import { v4 as uuidv4 } from 'uuid';
import { Phase, TaskItem, BackgroundTurn, OccupationSelection, ScreenerOutcome } from './types';

// Eligibility state for the two gates that can end a run early: the
// pre-interview occupation screener and the attention checks spliced into the
// task list. Participants are identified by the ?id= external id alone, so
// nothing here carries a recruitment-platform identifier.
export interface Screening {
  // Threshold from /api/config (ATTN_CHECK_MAX_FAILS). Fails STRICTLY above
  // this route to screen-out, so 0 means the first failure ends the run.
  attnCheckMaxFails: number;
  attnCheckFails: number;
  screenedOut: boolean;
  screenOutReason: string | null;
}

interface WorkflowStore {
  sessionId: string;
  // Participant ID — captured from the ?id= URL param, and the only identity
  // the study has: it keys the saved session, the screener record, and the
  // screen-out. Null when the link doesn't carry one (dev/preview loads).
  externalId: string | null;
  // O*NET-SOC code the participant was RECRUITED for, from the ?soc= URL param
  // (e.g. ?soc=29-1141.00). Purely a nudge: the interview's intro screens name
  // the occupation so someone who holds more than one job answers about the one
  // we invited them for. It doesn't preselect anything and the gate still rules
  // on whatever they pick. Null when the link doesn't carry one.
  invitedSoc: string | null;
  // Title for `invitedSoc`, resolved from the SOC list on mount. Null until it
  // resolves, and stays null for a code that isn't in the list — the intro copy
  // falls back to its occupation-less wording either way, so a failed lookup
  // costs the nudge, not the screen.
  invitedTitle: string | null;
  setInvitedTitle: (title: string | null) => void;
  phase: Phase;
  // Millisecond epoch when the store first initialized (≈ page load). Used as
  // a session-start proxy for time-spent analysis.
  sessionStartedAt: number;
  // Millisecond epoch the participant first entered each phase. Re-entries (if
  // the participant goes back and forward) keep the FIRST entry, since that's
  // what matters for time-per-phase analysis.
  phaseEnteredAt: Partial<Record<Phase, number>>;

  // Eligibility gates — the attention-check tally and the screen-out verdict.
  screening: Screening;
  setScreening: (p: Partial<Screening>) => void;
  // Whether the server has a recruiting target configured (SCREENER_TARGET_SOC).
  // Decides the whole shape of the run: ON = consent → intro → screener questions
  // → occupation selector (the gate) → interview → task selection; OFF = consent
  // → interview → occupation self-ID → task selection.
  screenerEnabled: boolean;
  setScreenerEnabled: (on: boolean) => void;

  // Phase 1 — background
  backgroundTranscript: BackgroundTurn[];
  addBackgroundTurn: (turn: BackgroundTurn) => void;

  // Phase 2 — task selection
  selectedTasks: string[];
  taskItems: TaskItem[];
  // Activities explicitly named by the participant in the background interview,
  // extracted by /api/extract-interview-tasks and used to ground the task
  // generator. Persisted in the session JSON so analysts can audit grounding.
  interviewExtractedTasks: string[];
  setSelectedTasks: (tasks: string[]) => void;
  setTaskItems: (items: TaskItem[]) => void;
  setInterviewExtractedTasks: (tasks: string[]) => void;

  setPhase: (phase: Phase) => void;
  // Participant occupation self-ID, captured on the final 'occupation-select' screen.
  occupationSelection: OccupationSelection | null;
  setOccupationSelection: (sel: OccupationSelection) => void;
  // Pre-interview screener outcome, captured on the 'screen' phase. Recorded for
  // passes too, so admitted-on-low-confidence cases are visible in the session.
  // (The server keeps its own authoritative record under sessions/screener/.)
  screener: ScreenerOutcome | null;
  setScreener: (s: ScreenerOutcome) => void;
  // The session as saved: everything the server and the analysis read.
  getExportData: () => Record<string, unknown>;
  // Restore persistent state from a previously-saved snapshot (returned by
  // GET /api/session): phase plus everything getExportData serializes.
  hydrateFromSnapshot: (data: Record<string, unknown>) => void;
}

const urlParams = new URLSearchParams(window.location.search);
const PHASES: readonly Phase[] = [
  'setup', 'screen', 'background', 'occupation-select', 'task-selection',
  'study-complete', 'screen-out',
];
const isPhase = (p: unknown): p is Phase => typeof p === 'string' && (PHASES as readonly string[]).includes(p);
// Restrict the URL-supplied external ID to filename-safe chars so the server
// can splice it into a path without escaping concerns. Empty after sanitizing → null.
const rawExternalId = urlParams.get('id');
const initialExternalId = rawExternalId
  ? (rawExternalId.replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 64) || null)
  : null;
// Recruited-for occupation (?soc=). Accepted only in canonical O*NET-SOC form
// (##-####.##) so a malformed link degrades to "no nudge" rather than showing a
// participant a code we can't resolve to a title.
const rawSoc = urlParams.get('soc');
const initialInvitedSoc = rawSoc && /^\d{2}-\d{4}\.\d{2}$/.test(rawSoc.trim()) ? rawSoc.trim() : null;

// attnCheckMaxFails is a placeholder until /api/config resolves; the rest is
// the "nothing has gone wrong yet" state every run starts in.
const initialScreening: Screening = {
  attnCheckMaxFails: 0,
  attnCheckFails: 0,
  screenedOut: false,
  screenOutReason: null,
};

const SESSION_START_MS = Date.now();

// Persist sessionId across page reloads so an accidental browser back/refresh
// can resume against the same server-side JSON. Keyed by the ?id= external id
// so each invited participant gets their own slot: two people sharing a browser
// (or one person opening a second invite) start separate sessions instead of
// silently writing to the same sessionId. Links without an ?id= share the
// 'anon' slot, which is only ever hit by dev/preview loads.
function getOrCreateSessionId(): string {
  try {
    const key = `wf-graph-session-${initialExternalId || 'anon'}`;
    const stored = window.localStorage?.getItem(key);
    if (stored) return stored;
    const fresh = uuidv4();
    window.localStorage?.setItem(key, fresh);
    return fresh;
  } catch {
    // localStorage can throw in private windows / SSR — fall back to a
    // process-lifetime UUID.
    return uuidv4();
  }
}

export const useWorkflowStore = create<WorkflowStore>((set, get) => ({
  sessionId: getOrCreateSessionId(),
  externalId: initialExternalId,
  invitedSoc: initialInvitedSoc,
  invitedTitle: null,
  setInvitedTitle: (invitedTitle) => set({ invitedTitle }),
  phase: 'setup',
  sessionStartedAt: SESSION_START_MS,
  // Every run starts on the welcome screen, stamped with the session start.
  phaseEnteredAt: { setup: SESSION_START_MS },

  screening: initialScreening,
  setScreening: (p) => set(state => ({ screening: { ...state.screening, ...p } })),

  backgroundTranscript: [],
  addBackgroundTurn: (turn) =>
    set(state => ({ backgroundTranscript: [...state.backgroundTranscript, turn] })),

  // All start empty — populated when the participant finishes the
  // TaskSelection screen.
  selectedTasks: [],
  taskItems: [],
  interviewExtractedTasks: [],
  setSelectedTasks: (selectedTasks) => set({ selectedTasks }),
  setTaskItems: (taskItems) => set({ taskItems }),
  setInterviewExtractedTasks: (interviewExtractedTasks) => set({ interviewExtractedTasks }),

  setPhase: (phase) => set(state => ({
    phase,
    // Stamp the first-entry time only — re-entries keep the original so the
    // "time spent on this phase" duration isn't reset by a back-and-forth.
    phaseEnteredAt: state.phaseEnteredAt[phase]
      ? state.phaseEnteredAt
      : { ...state.phaseEnteredAt, [phase]: Date.now() },
  })),
  occupationSelection: null,
  setOccupationSelection: (occupationSelection) => set({ occupationSelection }),
  screener: null,
  setScreener: (screener) => set({ screener }),
  screenerEnabled: false,
  setScreenerEnabled: (screenerEnabled) => set({ screenerEnabled }),

  getExportData: () => {
    const { sessionId, externalId, invitedSoc, phase, screening, backgroundTranscript, selectedTasks, taskItems, interviewExtractedTasks, occupationSelection, screener, sessionStartedAt, phaseEnteredAt } = get();
    // Completion signal — `study-complete` lands in phaseEnteredAt the moment
    // the participant arrives at the thanks screen. Surfacing it as a top-level
    // boolean (plus an ISO timestamp) so analysts can split completers from
    // abandoners without parsing the phaseEnteredAt map.
    const completedAtMs = phaseEnteredAt?.['study-complete'];
    const completed = typeof completedAtMs === 'number';
    const completedAt = completed ? new Date(completedAtMs).toISOString() : null;
    return {
      sessionId,
      externalId,
      // What we recruited them for — pairs with occupationSelection so analysts
      // can see when someone's self-ID differs from the invite.
      invitedSoc,
      phase,
      completed,
      completedAt,
      // Attention-check tally + screen-out verdict. Kept in the export because
      // it's the only place an abandoned-vs-ejected run can be told apart.
      screening,
      backgroundTranscript,
      interviewExtractedTasks,
      selectedTasks,
      taskItems,
      occupationSelection,
      screener,
      sessionStartedAt,
      phaseEnteredAt,
      exportedAt: new Date().toISOString(),
    };
  },

  hydrateFromSnapshot: (d) => set(state => {
    const next: Partial<WorkflowStore> = {};

    if (typeof d.externalId === 'string' && state.externalId === null) {
      next.externalId = d.externalId;
    }
    // A resumed link may have dropped the ?soc= param — restore it so the nudge
    // survives a reload, but never let a snapshot override a live URL param.
    if (typeof d.invitedSoc === 'string' && state.invitedSoc === null) {
      next.invitedSoc = d.invitedSoc;
    }
    if (Array.isArray(d.backgroundTranscript)) next.backgroundTranscript = d.backgroundTranscript as BackgroundTurn[];
    if (Array.isArray(d.selectedTasks)) next.selectedTasks = d.selectedTasks as string[];
    if (Array.isArray(d.taskItems)) next.taskItems = d.taskItems as TaskItem[];
    if (Array.isArray(d.interviewExtractedTasks)) next.interviewExtractedTasks = d.interviewExtractedTasks as string[];
    if (d.occupationSelection && typeof d.occupationSelection === 'object') next.occupationSelection = d.occupationSelection as OccupationSelection;
    if (d.screener && typeof d.screener === 'object') next.screener = d.screener as ScreenerOutcome;
    if (d.phaseEnteredAt && typeof d.phaseEnteredAt === 'object') {
      next.phaseEnteredAt = d.phaseEnteredAt as WorkflowStore['phaseEnteredAt'];
    }
    // Restore the attention-check tally so a reload can't reset a participant's
    // fails to zero and hand them a fresh set of chances. attnCheckMaxFails is
    // deliberately NOT restored — it comes from /api/config, and the live value
    // must win over whatever threshold was in force when the snapshot was saved.
    if (d.screening && typeof d.screening === 'object') {
      const saved = d.screening as Partial<Screening>;
      next.screening = {
        ...state.screening,
        attnCheckFails: saved.attnCheckFails ?? state.screening.attnCheckFails,
        // A recorded screen-out is sticky: a snapshot can set it, never clear it.
        screenedOut: state.screening.screenedOut || !!saved.screenedOut,
        screenOutReason: state.screening.screenOutReason ?? saved.screenOutReason ?? null,
      };
    }

    // Determine the phase to restore. Prefer the explicit `phase` field;
    // fall back to the latest entry in phaseEnteredAt. A phase this build
    // doesn't know is ignored rather than restored.
    let savedPhase: Phase | null = null;
    if (typeof d.phase === 'string') {
      if (isPhase(d.phase)) savedPhase = d.phase;
    } else if (next.phaseEnteredAt) {
      const entries = Object.entries(next.phaseEnteredAt).filter(([p]) => isPhase(p));
      if (entries.length > 0) {
        entries.sort(([, a], [, b]) => (b as number) - (a as number));
        savedPhase = entries[0][0] as Phase;
      }
    }
    if (savedPhase) next.phase = savedPhase;

    return next;
  }),
}));
