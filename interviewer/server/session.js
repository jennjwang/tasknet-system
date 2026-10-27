import express from 'express';
import fs from 'fs/promises';
import path from 'path';
import { toFile } from 'openai';
import { getTranscribeClient, SESSIONS_DIR } from '../lib/config.js';

const router = express.Router();

// Ids that arrive in requests and become file names (also used by server/occupation.js).

// A participant / session id, or null: the ?id= external id is alphanumeric, so anything else is rejected to
// prevent path traversal. Used by the screener, screen-out and session routes.
export function safeId(id) {
  return typeof id === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(id) ? id : null;
}

// Lenient variant for the optional external id on session files: strips disallowed characters and truncates to
// 64 instead of rejecting. '' when nothing usable is left.
export function sanitizeId(id) {
  return typeof id === 'string' ? id.replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 64) : '';
}


const sessionWriteQueue = new Map();

// True iff the payload carries no participant work — used to refuse overwrites
// of a session file with substantive state when a blank-store reload (e.g. a
// back-button revisit that autosaves before hydration finishes) tries to save
// itself over the top. A real participant's save always populates at least one of
// these slots.
//
// Judged on CONTENT ONLY. This deliberately does not consult phaseEnteredAt: a
// version of this guard short-circuited to "not empty" as soon as the payload
// had any phase past 'setup', which let a content-free screen-out bounce
// (phaseEnteredAt {setup, screen-out}, every array empty) sail through and
// replace a full record. A participant who is returned straight to a cached
// screen-out has progressed in phase and produced nothing — exactly the write
// this guard exists to stop.
function isMateriallyEmpty(d) {
  if (!d || typeof d !== 'object') return true;
  if (Array.isArray(d.backgroundTranscript) && d.backgroundTranscript.length > 0) return false;
  if (Array.isArray(d.selectedTasks) && d.selectedTasks.length > 0) return false;
  if (Array.isArray(d.taskItems) && d.taskItems.length > 0) return false;
  if (Array.isArray(d.interviewExtractedTasks) && d.interviewExtractedTasks.length > 0) return false;
  // A screened-out participant's entire contribution can be the screener answers
  // and the occupation they picked; both must count as work worth protecting.
  if (d.occupationSelection) return false;
  if (d.screener) return false;
  return true;
}

// Interview work that only ever GROWS within a session: the transcript and the
// task lists it produced. Used to detect a regression, below.
function interviewContent(d) {
  if (!d || typeof d !== 'object') return 0;
  return ((d.backgroundTranscript || []).length + (d.selectedTasks || []).length
    + (d.taskItems || []).length + (d.interviewExtractedTasks || []).length);
}

// SCREEN-OUT-THEN-RETRY GUARD. A participant who already completed (or got well
// into) an interview can reopen the same link, be re-run through the screener,
// pick a non-target occupation, and screen out — and that screen-out save, which
// carries screener answers (so isMateriallyEmpty says "not empty"), would
// overwrite the finished interview with a contentless, un-completed record.
// Completion is terminal and interview content only grows, so any
// save that un-completes a session or empties a transcript is a regression, never
// legitimate forward progress — refuse it. Server-authoritative, like the
// screen-out write itself: the client cannot be trusted to not send it.
function regresses(incoming, existing) {
  if (existing.completed && !incoming.completed) return true;
  if (interviewContent(existing) > 0 && interviewContent(incoming) === 0) return true;
  return false;
}

router.post('/api/transcribe', async (req, res) => {
  const { audio, mimeType = 'audio/webm' } = req.body;
  try {
    const transcribeClient = getTranscribeClient();
    const buffer = Buffer.from(audio, 'base64');
    // Safari's MediaRecorder reports audio/mp4 but emits AAC in an M4A container, so
    // it must be named .m4a: whisper-1 rejects the same audio named .mp4.
    const ext = mimeType.includes('mp4') ? 'm4a' : mimeType.includes('ogg') ? 'ogg' : 'webm';
    // Sent as an in-memory file. TRANSCRIBE_MODEL overrides the whisper-1 default.
    const file = await toFile(buffer, `audio.${ext}`, { type: mimeType });
    const transcription = await transcribeClient.audio.transcriptions.create({
      file,
      model: process.env.TRANSCRIBE_MODEL || 'whisper-1',
    });
    res.json({ text: transcription.text });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: err.message });
  }
});

router.get('/api/session', async (req, res) => {
  const sessionId = typeof req.query.sessionId === 'string' ? req.query.sessionId : '';
  if (!safeId(sessionId)) {
    return res.status(400).json({ error: 'invalid sessionId' });
  }
  const safeExtId = sanitizeId(req.query.externalId);
  const baseName = safeExtId ? `${safeExtId}_${sessionId}` : sessionId;
  const filePath = path.join(SESSIONS_DIR, `${baseName}.json`);
  try {
    const raw = await fs.readFile(filePath, 'utf-8');
    const data = JSON.parse(raw);
    res.json({ found: true, data });
  } catch (err) {
    if (err.code === 'ENOENT') return res.json({ found: false });
    console.error('[session] read failed:', err);
    res.status(500).json({ error: 'read failed' });
  }
});

router.post('/api/session', async (req, res) => {
  const { sessionId, ...rest } = req.body;
  if (!sessionId || typeof sessionId !== 'string') {
    return res.status(400).json({ error: 'sessionId required' });
  }
  // Optional external participant ID (the ?id= a recruit arrives with). Client
  // already sanitizes, but re-sanitize defensively so a hostile body can't
  // slip path separators or dots into the filename.
  const safeExtId = sanitizeId(rest.externalId);
  const baseName = safeExtId ? `${safeExtId}_${sessionId}` : sessionId;
  const filePath = path.join(SESSIONS_DIR, `${baseName}.json`);
  const incoming = { sessionId, ...rest, savedAt: new Date().toISOString() };
  const payload = JSON.stringify(incoming, null, 2);
  // Chain this write onto whatever is already in flight for this sessionId,
  // then write atomically (temp file + rename) so partial writes can't be
  // observed by readers (or by a racing concurrent writer).
  const previous = sessionWriteQueue.get(sessionId) || Promise.resolve();
  let skipped = false;
  const next = previous
    .catch(() => {})
    .then(async () => {
      // Two guards, both needing the existing file, so read it once. (1) don't
      // let a blank-store revisit (back button → fresh load → no hydration yet →
      // debounced autosave or beacon fires with empty state) wipe real work; and
      // (2) don't let a screen-out-then-retry regress a completed/in-progress
      // interview (see regresses()). Read inside the serialized write task so
      // concurrent writers can't race us.
      const incomingEmpty = isMateriallyEmpty(incoming);
      // Only a save that could be a regression needs the check; a growing,
      // still-completing save can skip the read on the fast path.
      if (incomingEmpty || !incoming.completed || interviewContent(incoming) === 0) {
        try {
          const raw = await fs.readFile(filePath, 'utf8');
          const existing = JSON.parse(raw);
          if (incomingEmpty && !isMateriallyEmpty(existing)) {
            console.warn('[session] refusing to overwrite non-empty file with empty payload', { sessionId, incomingPhase: incoming.phase, existingSavedAt: existing.savedAt });
            skipped = true;
            return;
          }
          if (!incomingEmpty && regresses(incoming, existing)) {
            console.warn('[session] refusing screen-out/regression overwrite of more-progressed session',
              { sessionId, existingCompleted: !!existing.completed, incomingPhase: incoming.phase, existingSavedAt: existing.savedAt });
            skipped = true;
            return;
          }
        } catch (err) {
          // ENOENT or unparseable existing file → fall through and write.
          if (err.code !== 'ENOENT') {
            console.warn('[session] guard read failed, proceeding with write:', err.message);
          }
        }
      }
      const tmpPath = `${filePath}.tmp-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      await fs.writeFile(tmpPath, payload);
      await fs.rename(tmpPath, filePath);
    });
  sessionWriteQueue.set(sessionId, next);
  try {
    await next;
    res.json({ ok: true, skipped });
  } catch (err) {
    console.error('[session] write failed:', err);
    res.status(500).json({ error: 'write failed' });
  } finally {
    // Drop the queue entry once it settles AND nothing newer is queued behind it.
    if (sessionWriteQueue.get(sessionId) === next) {
      sessionWriteQueue.delete(sessionId);
    }
  }
});

export default router;
