// Adaptive interview planner. The client drives turns over HTTP and calls plannerStep() once per turn.
//
//   - SPINE: criteria-gated coverage of the guide topics (prompts/interview/guide.json). One call per turn judges which
//     topics are now covered AND writes the next question, so terse answers still get drilled into tasks.
//   - EMERGENT: once the spine is covered, each question targets the most important missing area of the role's
//     task inventory.
//   - SELF-STOP: ends when the judge says the inventory is covered or two emergent turns in a row add no tasks,
//     then asks the shadow catch-all last.
// The system prompts are prompts/interview/*.txt.
import { loadPrompt, fill } from './prompts.js';

const QUESTION_PHRASING = loadPrompt('interview/question_phrasing.txt');
const system = (name) => fill(loadPrompt(`interview/${name}.txt`), { question_phrasing: QUESTION_PHRASING });
const NEW_TASKS_SYSTEM = system('new_tasks');
const NEXT_QUESTION_SYSTEM = system('next_question');
const EMERGENT_GAP_SYSTEM = system('emergent_gap');
const EXPLAIN_QUESTION_SYSTEM = system('explain_question');
const REWRITE_VARIED_SYSTEM = system('rewrite_varied');
const REWRITE_OPEN_SYSTEM = system('rewrite_open');

const GUIDE = JSON.parse(loadPrompt('interview/guide.json'));
const QUESTIONS = GUIDE.questions;
const BY_FIELD = Object.fromEntries(QUESTIONS.map((q) => [q.field, q]));
const SPINE_FIELDS = QUESTIONS.map((q) => q.field);
// Asked last, to sweep up the invisible and relational tasks.
const SHADOW_Q =
  BY_FIELD.stakeholders?.closingQuestion ||
  'One last thing — if someone shadowed you for a couple weeks, what would they catch you doing that hasn\'t come up yet?';

// PLANNER_MODEL writes and judges the questions; COUNT_MODEL extracts the new tasks in each answer at temp 0.
// Models that reject `temperature` run at 1.0 (the gateway client strips it), hence the lenient JSON parse.
const PLANNER_MODEL = process.env.PLANNER_MODEL || 'gpt-5.4';
const COUNT_MODEL = process.env.PLANNER_COUNT_MODEL || 'gpt-4o-mini';
// PLANNER_REWRITE=0 turns off the rewrite of closed / presuming / example-laden questions (rewriteOpen).
const REWRITE_BAD = process.env.PLANNER_REWRITE !== '0';
// PLANNER_VARY=0 turns off the reshape of a question that opens like one already asked (rewriteVaried).
const VARY_GUARD = process.env.PLANNER_VARY !== '0';
// Question budget including the shadow question, never above 17. Most interviews end on their own by ~13.
const MAX_TURNS = Math.min(17, Number(process.env.PLANNER_MAX_TURNS) || 17);
const MIN_TURNS = Number(process.env.PLANNER_MIN_TURNS) || 5;

// ── LLM helpers ─────────────────────────────────────────────────────────────────

// Parse a JSON object out of a reply that may be fenced or followed by prose: strip a fence, try a clean parse,
// then take the first brace-balanced object (string literals respected).
function parseJsonLoose(s) {
  if (typeof s !== 'string') return null;
  let t = s.trim();
  const fence = t.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  if (fence) t = fence[1].trim();
  try { return JSON.parse(t); } catch { /* fall through to balanced-brace scan */ }
  const start = t.indexOf('{');
  if (start < 0) return null;
  let depth = 0, inStr = false, esc = false;
  for (let k = start; k < t.length; k++) {
    const ch = t[k];
    if (inStr) {
      if (esc) esc = false;
      else if (ch === '\\') esc = true;
      else if (ch === '"') inStr = false;
    } else if (ch === '"') inStr = true;
    else if (ch === '{') depth++;
    else if (ch === '}' && --depth === 0) {
      try { return JSON.parse(t.slice(start, k + 1)); } catch { return null; }
    }
  }
  return null;
}

// One JSON call, retried once on an empty or unparseable reply (every planner reply has keys, so "{}" — which
// the gateway still returns now and then — is a failure). null if both attempts fail.
async function jsonChat(client, args) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const r = await client.chat.completions.create({ response_format: { type: 'json_object' }, ...args });
    const parsed = parseJsonLoose(r.choices?.[0]?.message?.content);
    if (parsed && Object.keys(parsed).length > 0) return parsed;
  }
  return null;
}

const bullets = (items) => items.map((t) => `- ${t}`).join('\n');

// The concrete recurring tasks an answer adds beyond `covered`.
async function newTasksFrom(client, answer, covered) {
  const j = await jsonChat(client, {
    model: COUNT_MODEL, temperature: 0,
    messages: [
      { role: 'system', content: NEW_TASKS_SYSTEM },
      { role: 'user', content: `COVERED:\n${bullets(covered) || '(none)'}\n\nANSWER:\n${answer}\n\nList only the genuinely new tasks.` },
    ],
  });
  const tasks = (j?.tasks || []).filter((t) => typeof t === 'string' && t.trim());
  return { count: tasks.length, tasks };
}

// Spine turn: judge which guide topics are now covered and write the next question, in one call.
async function strikeAndGenerate(client, history, covered, asked, remainingTopics) {
  const j = await jsonChat(client, {
    model: PLANNER_MODEL, temperature: 0.4,
    messages: [
      { role: 'system', content: NEXT_QUESTION_SYSTEM },
      { role: 'user', content: `TOPICS — field: text, with criteria:\n${remainingTopics.map((t) => `- ${t.field}: ${t.text}\n   criteria: ${(t.criteria || []).join(' | ')}`).join('\n')}\n\nConversation so far:\n${history.join('\n')}\n\nAlready-asked questions (do NOT repeat or rephrase):\n${bullets(asked) || '(none)'}\n\nTasks already covered:\n${bullets(covered) || '(none)'}\n\nJudge coverage, then advance the most valuable uncovered topic with the single best next question.` },
    ],
  });
  return { covered: Array.isArray(j?.covered) ? j.covered : [], gap: j?.gap ?? null, topic: j?.topic ?? null, question: j?.question ?? null, done: !!j?.done };
}

// Emergent turn: picture the role's full task inventory, diff it against what's gathered, and ask about the most
// important missing area; done when only marginal areas remain.
async function emergentGap(client, history, covered, asked) {
  const j = await jsonChat(client, {
    model: PLANNER_MODEL, temperature: 0.4,
    messages: [
      { role: 'system', content: EMERGENT_GAP_SYSTEM },
      { role: 'user', content: `Conversation so far:\n${history.join('\n')}\n\nTasks GATHERED so far:\n${bullets(covered) || '(none)'}\n\nAlready-asked questions (do NOT repeat or rephrase):\n${bullets(asked)}\n\nLAST TWO questions asked — your question MUST use a clearly DIFFERENT sentence structure than these:\n${bullets(asked.slice(-2)) || '(none)'}\n\nName the biggest gap in their task inventory and ask about it — or set done if it's well covered.` },
    ],
  });
  return { gap: j?.gap ?? null, question: j?.question ?? null, done: j == null ? true : !!j?.done };
}

// ── question post-checks ────────────────────────────────────────────────────────

const YES_NO_STEM = /^(do|does|are|is|was|were|have|has|had|did|can|could|would|will|should)\b/i;

const openerOf = (q) =>
  (q || '').toLowerCase().replace(/[^a-z0-9 ]/g, ' ').split(/\s+/).filter(Boolean).slice(0, 3).join(' ');

// The earlier question this one shares its first three words with, or null. Checked in code because the prompt's
// "vary the structure" rule alone doesn't hold; scoped to the whole session, since that is the reuse people notice.
function echoOf(q, asked) {
  const op = openerOf(q);
  return op ? asked.find((r) => openerOf(r) === op) ?? null : null;
}

// Closed, presuming or example-laden questions, which rewriteOpen rephrases.
function looksBad(q) {
  const s = (q || '').trim();
  return YES_NO_STEM.test(s)
    || /^how do you (handle|manage|deal with)\b/i.test(s)
    || /\b(like|such as|e\.g\.|for example|including)\b/i.test(s);
}

// An explanation that is itself confusing: two questions, too long, a yes/no stem, or example instances. Unlike
// looksBad it allows "including", because an explanation must name broad kinds of work ("including care given");
// naming instances ("like a chart note") would lead the participant.
const EXPLANATION_LISTS_INSTANCES = /\b(like|such as|e\.g\.|for example)\b/i;
function badExplanation(q) {
  const s = (q || '').trim();
  return (s.match(/\?/g) || []).length > 1
    || s.split(/\s+/).length > 55
    || EXPLANATION_LISTS_INSTANCES.test(s)
    || YES_NO_STEM.test(s);
}

// Did the last answer say they didn't understand the question? Only phrasings that ASK what was meant count; short
// or vague answers are a coverage matter for the judge.
const ASKS_WHAT_YOU_MEAN = [
  /\b(can|could) you (elaborate|clarify|explain|rephrase)/i,
  /\b(not sure|unsure|unclear) (what|how) (you |this |that )?(mean|meant|refer)/i,
  /\bwhat do you mean\b/i,
  /\b(need|needed|would need) (some )?clarification/i,
  // About THE QUESTION only: a bare "doesn't make sense" also turns up in descriptions of work.
  /\b(that|this|the question|your question) (doesn'?t|does not) (really )?(apply|fit|make sense)/i,
];
// A real "I don't follow" is short; long answers that happen to contain a phrase are describing their work, and
// explaining an answered question reads as not listening.
const CLARIFY_MAX_WORDS = 30;
const asksWhatYouMean = (a) =>
  !!a && a.trim().split(/\s+/).length <= CLARIFY_MAX_WORDS &&
  ASKS_WHAT_YOU_MEAN.some((re) => re.test(a));

// ── rewrites ────────────────────────────────────────────────────────────────────

async function rewriteOpen(client, question) {
  const j = await jsonChat(client, {
    model: PLANNER_MODEL, temperature: 0.3,
    messages: [
      { role: 'system', content: REWRITE_OPEN_SYSTEM },
      { role: 'user', content: `Rewrite: ${question}` },
    ],
  });
  return j?.question || question;
}

// Explain a question the participant didn't understand instead of re-asking it. Up to three attempts, each retry
// naming the defect badExplanation found; example answers are the main risk, since a struggling participant would
// agree with them and we'd record our words as theirs.
async function explainQuestion(client, question, history) {
  let out = question;
  let fix = '';
  for (let attempt = 0; attempt < 3; attempt++) {
    const j = await jsonChat(client, {
      model: PLANNER_MODEL, temperature: 0.4,
      messages: [
        { role: 'system', content: EXPLAIN_QUESTION_SYSTEM },
        { role: 'user', content: `Conversation so far:\n${history.join('\n')}\n\nThe question they didn't understand: ${question}\n\nExplain what you meant and ask it in a grounded form.${fix}` },
      ],
    });
    out = j?.question || out;
    if (!badExplanation(out)) return out;
    fix = (out.match(/\?/g) || []).length > 1
      ? ' Your last attempt asked TWO questions. Exactly one question mark, no second ask chained on.'
      : out.split(/\s+/).length > 55
        ? ' Your last attempt was too long. Two sentences, under 45 words.'
        : EXPLANATION_LISTS_INSTANCES.test(out)
          ? ' Your last attempt listed example answers ("like…", "such as…"). Name broad kinds of work instead — never a document, artifact or task.'
          : ' Your last attempt opened with a yes/no stem. Start with what you meant, and keep the question answerable only by describing.';
  }
  return out;
}

// Reshape a question that echoes an earlier one, keeping its target: walking one activity slice by slice is
// where the unvolunteered tasks come from, so only the sameness of shape is the defect. Re-checked and retried
// once, because the reshape can land on a shape that's also taken (often "Walk me through…").
async function rewriteVaried(client, question, asked) {
  let out = question;
  const shown = [];
  for (let attempt = 0; attempt < 2; attempt++) {
    const echo = echoOf(out, asked);
    if (!echo) break;
    for (const q of [echo, ...asked.slice(-2)]) if (!shown.includes(q)) shown.push(q);
    const j = await jsonChat(client, {
      model: PLANNER_MODEL, temperature: 0.6,
      messages: [
        { role: 'system', content: REWRITE_VARIED_SYSTEM },
        { role: 'user', content: `For contrast, the questions this one currently echoes:\n${bullets(shown)}\n\nRewrite, same target, different shape: ${out}` },
      ],
    });
    out = j?.question || out;
  }
  return out;
}

// ── one planner turn ────────────────────────────────────────────────────────────
// Process the latest answer and decide the next question. The server is stateless: the caller passes back the
// `state` from the previous response each turn.
//   turns: [{ question, answer }], every question asked so far (the latest answer is unprocessed)
//   state: the previous response's state (null or omitted on the first call)
// Returns { question, phase, gap, done, stopReason, state, spineTotal }. The first call (no turns) returns the
// opening question; done=true means the interview is over and question is null.
export async function plannerStep(client, opts = {}) {
  // spineTotal lets the client show real progress (topics struck out of the spine).
  const r = await plannerTurn(client, opts);
  return { ...r, spineTotal: SPINE_FIELDS.length };
}

async function plannerTurn(client, { turns = [], state = null } = {}) {
  if (!turns.length) {
    return {
      question: QUESTIONS[0].text, phase: 'open', gap: 'opening — role & field', done: false, stopReason: null,
      state: { covered: [], remaining: [...SPINE_FIELDS], emYields: [], lastPhase: 'open', stopReason: null },
    };
  }

  const st = state || { covered: [], remaining: [...SPINE_FIELDS], emYields: [], lastPhase: 'open' };
  let { covered, remaining, emYields, lastPhase } = st;
  const history = turns.flatMap((t) => [`Interviewer: ${t.question}`, `Participant: ${t.answer}`]);
  const asked = turns.map((t) => t.question);
  // A cold start with several turns already in hand (a reload mid-interview, or the screener's two answers)
  // extracts tasks from every answer at once, so none of the earlier ones are lost.
  const coldStart = !state && turns.length > 1;
  const lastAnswer = coldStart
    ? turns.map((t) => t.answer).join('\n\n')
    : turns[turns.length - 1].answer;
  const prevAnswer = turns.length > 1 ? turns[turns.length - 2].answer : '';

  // The shadow question was the last one asked: the interview is complete.
  if (lastPhase === 'shadow') {
    const post = await newTasksFrom(client, lastAnswer, covered);
    covered = [...covered, ...post.tasks];
    return { question: null, phase: 'done', gap: null, done: true, stopReason: st.stopReason || 'shadow-complete', state: { ...st, covered } };
  }

  const n = turns.length;
  const onSpine = remaining.length > 0 && lastPhase !== 'emergent' && lastPhase !== 'shadow';

  // 1) Process the latest answer: always extract new tasks; on the spine, also judge coverage and get the next
  //    question from the merged call.
  let post, coveredFields = [], merged = null;
  if (onSpine) {
    [post, merged] = await Promise.all([
      newTasksFrom(client, lastAnswer, covered),
      strikeAndGenerate(client, history, covered, asked, remaining.map((f) => BY_FIELD[f])),
    ]);
    coveredFields = merged.covered;
  } else {
    post = await newTasksFrom(client, lastAnswer, covered);
  }
  covered = [...covered, ...post.tasks];
  // A weekly description makes periodicTasks look covered by inference; it only counts once actually asked.
  if (!st.periodicAsked) coveredFields = coveredFields.filter((f) => f !== 'periodicTasks');
  remaining = remaining.filter((f) => !coveredFields.includes(f));
  if (lastPhase === 'emergent') emYields = [...emYields, post.count];

  const newState = (extra) => ({ covered, remaining, emYields, lastPhase, stopReason: st.stopReason || null, periodicAsked: st.periodicAsked || false, ...extra });
  const shadow = (stopReason) => ({
    question: SHADOW_Q, phase: 'shadow', gap: 'final catch-all', done: false, stopReason,
    state: newState({ lastPhase: 'shadow', stopReason }),
  });
  const askShadow = () => shadow(st.stopReason || 'capped');
  if (n >= MAX_TURNS - 1) return askShadow();

  // 2) SPINE. If the planner gives no question it ends (shadow), except that periodicTasks is always asked once.
  if (remaining.length > 0) {
    if (!merged || !merged.question) {
      if (!st.periodicAsked && remaining.includes('periodicTasks') && BY_FIELD.periodicTasks) {
        return { question: BY_FIELD.periodicTasks.text, phase: 'spine', gap: 'periodic/longer-cycle tasks', done: false, stopReason: null, state: newState({ lastPhase: 'spine', periodicAsked: true }) };
      }
      return shadow('no-question');
    }
    const spineTurn = (question) => ({
      question, phase: 'spine', gap: merged.gap || '', done: false, stopReason: null,
      state: newState({ lastPhase: 'spine', periodicAsked: st.periodicAsked || merged.topic === 'periodicTasks' }),
    });
    // A static topic's guide text is asked verbatim the first time; a follow-up uses the adaptive question.
    const stat = BY_FIELD[merged.topic]?.static ? BY_FIELD[merged.topic].text : null;
    const useStatic = stat && !asked.some((q) => q.trim() === stat.trim());
    let question = useStatic ? stat : merged.question;
    // They said they didn't understand: explain the last question with a dedicated call (asked to advance the
    // topic, the model just re-asks in synonyms). Once only — two requests in a row means move on.
    if (asksWhatYouMean(lastAnswer) && !asksWhatYouMean(prevAnswer)) {
      return spineTurn(await explainQuestion(client, asked[asked.length - 1] || question, history));
    }
    // Static text is exempt from both rewrites: its wording is deliberate.
    if (!useStatic && REWRITE_BAD && looksBad(question)) question = await rewriteOpen(client, question);
    if (!useStatic && VARY_GUARD && echoOf(question, asked)) question = await rewriteVaried(client, question, asked);
    return spineTurn(question);
  }

  // 3) EMERGENT. Stop (after MIN_TURNS) when the judge says done or two emergent turns in a row added no tasks.
  const eg = await emergentGap(client, history, covered, asked);
  const dry = emYields.length >= 2 && emYields.slice(-2).every((y) => y === 0);
  if (n >= MIN_TURNS && (eg.done || dry)) return shadow(eg.done ? 'gap-complete' : 'gap-saturated');
  if (!eg.question) return askShadow();
  let question = eg.question;
  if (REWRITE_BAD && looksBad(question)) question = await rewriteOpen(client, question);
  if (VARY_GUARD && echoOf(question, asked)) question = await rewriteVaried(client, question, asked);
  return { question, phase: 'emergent', gap: eg.gap || '', done: false, stopReason: null, state: newState({ lastPhase: 'emergent' }) };
}
