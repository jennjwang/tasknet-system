import type { BackgroundTurn, OccupationCandidate } from '../types';

function postJson(url: string, body: unknown): Promise<Response> {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// POST and parse the JSON reply; a non-2xx reply throws with the response text.
async function postJsonOk<T>(url: string, body: unknown): Promise<T> {
  const res = await postJson(url, body);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// The JSON reply of `request`, or `fallback` on a network error, a non-2xx reply or bad JSON.
async function jsonOr<T>(request: Promise<Response>, fallback: T): Promise<T> {
  try {
    const res = await request;
    return res.ok ? await res.json() : fallback;
  } catch {
    return fallback;
  }
}

// Integrated MECE generator (the only task generator): normalizes the
// interview-extracted tasks to O*NET standard, merges overlaps, and adds
// importance gap-fill — streaming one flat list over SSE.
export async function generateTasksFromInterview(
  jobTitle: string,
  interviewTasks: string[],
  onTask: (name: string, meta?: { source?: 'interview' | 'gap' }) => void,
  count?: number,
  backgroundTranscript?: { question: string; answer: string }[], // full Q/A — grounds gap-fill in their actual words/background
  occupationCode?: string,                           // self-identified O*NET-SOC code → exact retrieval lookup
): Promise<void> {
  const res = await postJson('/api/generate-tasks-from-interview',
    { jobTitle, occupationCode, interviewTasks, count, backgroundTranscript });
  if (!res.ok || !res.body) throw new Error(await res.text());
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop() ?? '';
    for (const part of parts) {
      const lines = part.split('\n');
      const eventLine = lines.find(l => l.startsWith('event: '));
      const dataLine = lines.find(l => l.startsWith('data: '));
      if (!eventLine || !dataLine) continue;
      const event = eventLine.slice(7);
      const data = JSON.parse(dataLine.slice(6));
      if (event === 'task' && typeof data?.name === 'string')
        onTask(data.name, { source: data.source });
      else if (event === 'error') throw new Error(data.message ?? data.error ?? 'stream error');
      else if (event === 'done') return;
    }
  }
}

// The work activities the participant EXPLICITLY mentioned in the interview;
// they ground the task generator.
export async function extractInterviewTasks(backgroundTranscript: BackgroundTurn[]): Promise<string[]> {
  const data = await postJsonOk<{ tasks?: unknown }>('/api/extract-interview-tasks', { backgroundTranscript });
  return Array.isArray(data.tasks) ? data.tasks : [];
}

// Adaptive interview planner, one decision step per call: pass every Q/A asked so
// far plus the opaque `state` from the previous response; get back the next
// question and updated state. First call: turns=[] → opening question.
// done=true means the interview is over (the shadow catch-all was the last question).
interface PlannerStep {
  question: string | null;
  phase: 'open' | 'spine' | 'emergent' | 'shadow' | 'done';
  gap: string | null;
  done: boolean;
  stopReason: string | null;
  state: unknown;
}

export function plannerNext(
  turns: { question: string; answer: string }[],
  state: unknown,
): Promise<PlannerStep> {
  return postJsonOk('/api/planner-next', { turns, state });
}

export async function transcribeAudio(blob: Blob): Promise<string> {
  const base64 = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve((reader.result as string).split(',')[1]);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
  const data = await postJsonOk<{ text: string }>('/api/transcribe', { audio: base64, mimeType: blob.type });
  return data.text;
}

// Is there enough in the two screener answers to identify an occupation? Returns
// a follow-up question to ask when there isn't. Fails open (server-side too), so
// a judge outage never blocks a participant behind an extra question.
export function screenerProbe(body: {
  title: string;
  responsibilities: string;
}): Promise<{ sufficient: boolean; followUp: string | null }> {
  return jsonOr(postJson('/api/screener/probe', body), { sufficient: true, followUp: null });
}

// Pre-interview occupation gate: rules on the occupation the PARTICIPANT picked
// on the selector screen (the model only proposed the candidates). The target is
// compared server-side and never sent here, so the response says nothing about
// what the study is recruiting for.
export function screenerSelect(body: {
  code: string;
  selectedTitle: string;
  fromSearch: boolean;
  title: string;
  responsibilities: string;
  externalId: string | null;
  sessionId: string;
}): Promise<{ pass: boolean }> {
  return postJsonOk('/api/screener/select', body);
}

// Whether a recruiting target is configured (SCREENER_TARGET_SOC). Treats an
// unreachable config as off so a blip can't strand participants on a screen that
// will never load; this only decides whether the screen is SHOWN, and
// /api/screener/select checks the target itself, so it can't be used to sneak
// past the gate.
export async function fetchScreenerEnabled(): Promise<boolean> {
  const json = await jsonOr<{ enabled?: unknown }>(fetch('/api/screener/config'), { enabled: false });
  return !!json?.enabled;
}

// Ranked O*NET-SOC candidates for the occupation selector. Throws on a network
// error or a reply that isn't JSON.
export async function fetchOccupationCandidates(body: {
  backgroundTranscript: BackgroundTurn[];
  excludeCodes: string[];
  hint: string;
  topN: number;
  preferAgreement: boolean;
  unionCap: number;
}): Promise<OccupationCandidate[]> {
  const data = await (await postJson('/api/occupation-candidates', body)).json();
  return Array.isArray(data.candidates) ? data.candidates : [];
}

// The full O*NET-SOC list (code + title), or [] if it can't be fetched.
export async function fetchOccupations(): Promise<OccupationCandidate[]> {
  const json = await jsonOr<{ occupations?: unknown }>(fetch('/api/occupations'), { occupations: [] });
  return Array.isArray(json?.occupations) ? json.occupations : [];
}

// Resolve an O*NET-SOC code to its title, for the recruited-for nudge (?soc=)
// on the interview's intro screens. Null if the code isn't in the SOC list or
// the list can't be fetched — the intro then falls back to its occupation-less
// wording rather than naming a code nobody can read.
export async function fetchOccupationTitle(code: string): Promise<string | null> {
  return (await fetchOccupations()).find((o) => o.code === code)?.title ?? null;
}

interface AppConfig {
  attnCheckMaxFails: number;
}

const DEFAULT_CONFIG: AppConfig = {
  attnCheckMaxFails: 0,
};

export async function fetchAppConfig(): Promise<AppConfig> {
  return { ...DEFAULT_CONFIG, ...(await jsonOr(fetch('/api/config'), DEFAULT_CONFIG)) };
}

export function fetchSession(
  sessionId: string,
  externalId?: string | null,
): Promise<{ found: boolean; data?: Record<string, unknown> }> {
  const params = new URLSearchParams({ sessionId });
  if (externalId) params.set('externalId', externalId);
  return jsonOr(fetch(`/api/session?${params.toString()}`), { found: false });
}

export async function saveSession(
  sessionId: string,
  data: Record<string, unknown>,
): Promise<void> {
  await postJson('/api/session', { sessionId, ...data });
}

// Fire-and-forget save that survives page unload — `fetch` is cancelled when
// the tab closes, but `sendBeacon` is the platform's purpose-built escape
// hatch for unload-time POSTs. Returns whether the beacon was queued.
export function saveSessionBeacon(
  sessionId: string,
  extra: Record<string, unknown>,
): boolean {
  if (typeof navigator === 'undefined' || !navigator.sendBeacon) return false;
  const body = JSON.stringify({ sessionId, ...extra });
  const blob = new Blob([body], { type: 'application/json' });
  return navigator.sendBeacon('/api/session', blob);
}

// Persist a screen-out keyed on the ?id= external id so a refresh can't reset it.
export async function recordScreenOut(
  id: string,
  sessionId: string,
  reason: string,
): Promise<void> {
  await postJson('/api/screen-out', { id, sessionId, reason });
}

// Check on app mount whether this participant was already screened out, keyed
// on the same ?id= the screen-out record was written under.
export function checkScreenStatus(
  id: string,
): Promise<{ screenedOut: boolean; reason?: string; at?: string }> {
  return jsonOr(fetch(`/api/screen-status?id=${encodeURIComponent(id)}`), { screenedOut: false });
}
