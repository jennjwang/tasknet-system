// Which occupation the participant is in: the shortlist and the occupation list, the screener, and the
// screen-out records that keep a screened-out ?id= from coming back.
//
// The screener is a gate before the interview (consent → intro → two questions → occupation selector →
// interview), so a participant in the wrong occupation costs a couple of cheap calls instead of a whole
// interview plus task generation.
//
// Division of labour: the CLASSIFIER PRESENTS, the PARTICIPANT DECIDES. Candidate
// occupations come from /api/occupation-candidates (one LLM pass over the full
// O*NET-SOC list, ranked, plus O*NET-related occupations of the top match) driven
// by the two screener answers. The participant picks the one they identify with
// on the selector screen, and THAT PICK is what this gate rules on — the model
// never gets a vote on who is admitted.
//
// The target is only ever compared server-side and is never sent to the client,
// so nothing in the response reveals what the study is recruiting for.
import express from 'express';
import fs from 'fs/promises';
import path from 'path';
import { SCREENER_DIR, SCREEN_OUTS_DIR, SCREENER_TARGET_CODES, screenerEnabled } from '../lib/config.js';
import { occupationCandidates, listOccupations, checkScreenerAnswers } from '../lib/occupation.js';
import { safeId } from './session.js';

const router = express.Router();

router.post('/api/occupation-candidates', async (req, res) => {
  // topN is per-caller: the interview app's occupation screener asks for 5 (a
  // wider shortlist means fewer genuine targets picking a near-miss and screening
  // themselves out); the validation study's screen omits it and keeps the default.
  const { backgroundTranscript = [], excludeCodes = [], hint = '', topN, preferAgreement, unionCap } = req.body;
  if (!Array.isArray(backgroundTranscript)) {
    return res.status(400).json({ error: 'backgroundTranscript must be an array' });
  }
  try {
    const candidates = await occupationCandidates(backgroundTranscript, {
      excludeCodes: Array.isArray(excludeCodes) ? excludeCodes : [],
      hint: typeof hint === 'string' ? hint : '',
      ...(Number.isFinite(topN) ? { topN } : {}),
      preferAgreement: !!preferAgreement,
      // 0 = uncapped (the screener); omitted = the default cap.
      ...(Number.isFinite(unionCap) ? { unionCap } : {}),
    });
    console.log(`[occupation-candidates] excluded=${(excludeCodes || []).length} hint=${hint ? 'y' : 'n'} topN=${topN ?? 'default'} -> ${candidates.length}`);
    res.json({ candidates });
  } catch (err) {
    console.error('[occupation-candidates]', err);
    res.status(500).json({ error: err.message });
  }
});

router.get('/api/occupations', async (_req, res) => {
  try {
    res.json({ occupations: await listOccupations() });
  } catch (err) {
    console.error('[occupations]', err);
    res.status(500).json({ error: err.message });
  }
});

// Whether a recruiting target is configured. Off → no screener phase at all and
// the flow is consent → interview → occupation self-ID.
router.get('/api/screener/config', (_req, res) => {
  res.json({ enabled: screenerEnabled() });
});

// Is there enough here to identify an occupation? The candidate list is built
// from these two answers, and the classifier never reports that it had nothing to
// work with — "idk" / "stuff" still yields a confident five-item menu of real
// occupations, which the participant then picks from and the gate rules on. This
// catches that before it becomes a decision.
//
// The rule is the interview guide's own, for the same `responsibilities` topic:
// follow up only when the answer is genuinely EMPTY or evasive ("a bit of
// everything", "hard to say", "depends"), and nudge for one concrete thing rather
// than restating the question. The caller asks at most once, so nobody is trapped
// behind it. Fails OPEN — an unreachable judge admits the answer as-is.
router.post('/api/screener/probe', async (req, res) => {
  const title = typeof (req.body && req.body.title) === 'string' ? req.body.title.trim() : '';
  const responsibilities =
    typeof (req.body && req.body.responsibilities) === 'string' ? req.body.responsibilities.trim() : '';

  try {
    const { sufficient, followUp } = await checkScreenerAnswers(title, responsibilities);
    console.log(`[screener] probe "${responsibilities.slice(0, 40)}" -> sufficient=${sufficient}`);
    res.json({ sufficient, followUp });
  } catch (err) {
    // Never let a judge outage block someone: treat it as good enough.
    console.error('[screener] probe failed — admitting as-is:', err);
    res.json({ sufficient: true, followUp: null });
  }
});

// Rule on the participant's occupation pick.
router.post('/api/screener/select', async (req, res) => {
  const { code, title, responsibilities, selectedTitle, fromSearch, externalId, sessionId } = req.body || {};
  if (typeof code !== 'string' || !code.trim()) {
    return res.status(400).json({ error: 'code required' });
  }
  // Nothing configured to screen against — admit rather than reject on a
  // misconfiguration.
  if (!screenerEnabled()) return res.json({ pass: true, disabled: true });

  const pass = SCREENER_TARGET_CODES.includes(code.trim());
  const at = new Date().toISOString();
  // Participants are identified by the ?id= external id — the only identity the
  // gate has, and what the screen-out record below is keyed on.
  const who = safeId(externalId);
  const record = {
    externalId: who,
    sessionId: typeof sessionId === 'string' ? sessionId : null,
    targetCodes: SCREENER_TARGET_CODES,
    // The two screener answers the candidate list was generated from — kept so a
    // rejection can be audited against what the participant actually said.
    title: typeof title === 'string' ? title.slice(0, 2000) : null,
    responsibilities: typeof responsibilities === 'string' ? responsibilities.slice(0, 8000) : null,
    selectedCode: code.trim(),
    selectedTitle: typeof selectedTitle === 'string' ? selectedTitle.slice(0, 300) : null,
    // True when they went past the suggested candidates into free search — worth
    // knowing, since a search pick means the shortlist missed them.
    fromSearch: !!fromSearch,
    pass,
    at,
  };
  // Never drop the record for want of an id: a rejection with no audit trail can't
  // be reconstructed later. Falls back to the session id, then a timestamped name.
  const recordId = who || safeId(sessionId) || `anon-${at.replace(/[:.]/g, '-')}`;
  await fs
    .writeFile(path.join(SCREENER_DIR, `${recordId}.json`), JSON.stringify(record, null, 2))
    .catch((err) => console.error('[screener] record write failed:', err));

  // Write the screen-out here rather than trusting the client to report it: the
  // same record /api/screen-status reads, so a reload can't re-run the screener
  // and the gate can't be skipped by dropping the follow-up call.
  // Keyed on the ?id=: /api/screen-status looks the file up by that same id, so a
  // participant can't reload their way back into the gate.
  if (!pass && who) {
    await fs
      .writeFile(
        path.join(SCREEN_OUTS_DIR, `${who}.json`),
        JSON.stringify(
          { externalId: who, sessionId: record.sessionId, reason: 'occupation-mismatch', at },
          null,
          2,
        ),
      )
      .catch((err) => console.error('[screener] screen-out write failed:', err));
  }

  console.log(
    `[screener] ${recordId} picked ${record.selectedCode} ` +
      `${record.selectedTitle ? `(${record.selectedTitle}) ` : ''}` +
      `${record.fromSearch ? 'via search ' : ''}pass=${pass}`,
  );
  res.json({ pass });
});

// Screen-outs for other reasons, reported by the client (a failed attention check). Same record as the
// occupation gate's own screen-out above, and read by /api/screen-status.
router.post('/api/screen-out', async (req, res) => {
  const { id, sessionId, reason } = req.body || {};
  const safe = safeId(id);
  if (!safe) return res.status(400).json({ error: 'invalid id' });
  const filePath = path.join(SCREEN_OUTS_DIR, `${safe}.json`);
  await fs.writeFile(
    filePath,
    JSON.stringify({ externalId: safe, sessionId, reason, at: new Date().toISOString() }, null, 2),
  );
  res.json({ ok: true });
});

// Was this ?id= screened out before? Checked on load, so a reload can't get back past the gate.
router.get('/api/screen-status', async (req, res) => {
  const safe = safeId(req.query.id);
  if (!safe) return res.json({ screenedOut: false });
  const filePath = path.join(SCREEN_OUTS_DIR, `${safe}.json`);
  try {
    const raw = await fs.readFile(filePath, 'utf-8');
    const parsed = JSON.parse(raw);
    res.json({ screenedOut: true, reason: parsed.reason ?? null, at: parsed.at ?? null });
  } catch (err) {
    if (err.code === 'ENOENT') return res.json({ screenedOut: false });
    console.error(err);
    res.status(500).json({ error: err.message });
  }
});

export default router;
