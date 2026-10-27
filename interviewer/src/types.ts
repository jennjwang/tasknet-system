export type TaskStatus = 'unreviewed' | 'confirmed' | 'edited' | 'removed';

export interface TaskEdit {
  from: string;            // value before this edit
  to: string;              // value after this edit
  charsChanged: number;    // Levenshtein distance from -> to
  timestamp: number;       // ms since epoch
}

export interface TaskItem {
  name: string;
  originalName: string;
  source?: 'interview' | 'gap';  // provenance: generated from interview extraction vs gap-fill
  status: TaskStatus;
  edits?: TaskEdit[];          // full chronological history of edits made to this task
  isAttentionCheck?: boolean;  // tasks from clearly unrelated occupations; expected answer is "no"
  addedByParticipant?: boolean; // tasks the participant typed in on the all-done screen
  shownAt?: number;            // epoch ms the card first became the active question
  answeredAt?: number;         // epoch ms the participant answered (yes/no) this card
  timeSpentMs?: number;        // answeredAt - shownAt: dwell time on this card
}

export type Phase = 'setup' | 'screen' | 'background' | 'occupation-select' | 'task-selection' | 'study-complete' | 'screen-out';

// One O*NET-SOC occupation shown on the occupation self-ID screen.
export interface OccupationCandidate {
  code: string;        // O*NET-SOC code, e.g. "15-1252.00"
  title: string;
  definition?: string;
  why?: string;        // model's one-line reason (shown candidates only)
  source?: 'match' | 'related';  // 'match' = LLM top-N; 'related' = O*NET related occ of the top match
}

// The participant's occupation self-identification, captured on the occupation
// self-ID screen (shown after the interview, before task selection).
export interface OccupationSelection {
  selectedCode: string;                  // the SOC the participant identified with
  selectedTitle: string;
  fromSearch: boolean;                   // true if picked via free search rather than a shown candidate
  shownSets: OccupationCandidate[][];    // every model-suggested set shown (incl. after "show different")
  rejectedCodes: string[];               // codes cycled past via "show different"
  hints: string[];                       // steerable hints the participant gave
  selectedAt: number;                    // epoch ms
}

// Outcome of the pre-interview occupation screener. Written in two steps: the two
// answers land when the questions are done, then the pick and verdict when the
// participant chooses on the selector. The target occupation is deliberately
// absent — the server never tells the client what was being screened for, so
// nothing here can leak it.
export interface ScreenerOutcome {
  title: string;              // answer to "your job and what field is it in"
  responsibilities: string;   // answer to "your primary responsibilities"
  selectedCode?: string;      // the O*NET-SOC occupation the participant picked
  selectedTitle?: string;
  pass?: boolean;             // whether that pick cleared the gate
  at: number;                 // epoch ms
}

export interface BackgroundTurn {
  field: string;
  question: string;
  answer: string;
  isFollowUp: boolean;
  timestamp: number;
}
