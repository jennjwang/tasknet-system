// Transcript → task list: extract the tasks the participant mentioned, write them as one MECE list in O*NET
// statement style, then add a few inferred (gap-fill) tasks.
import { client, MODEL } from './config.js';
import { EXTRACT_TASKS_PROMPT, mentionedTasksBlock, buildWriteTasksPrompt, buildGapFillPrompt } from './prompts.js';
import { retrieveExemplarBlock } from './retrieval.js';

// A clean Q→A transcript from interview turns, skipping empty answers.
export function transcriptFromTurns(backgroundTranscript = []) {
  return backgroundTranscript
    .filter(t => t && typeof t.answer === 'string' && t.answer.trim().length > 0)
    .map(t => `Q: ${t.question}\nA: ${t.answer}`)
    .join('\n\n');
}

// The participant's role/field: the answer to the interview's opening question.
export function roleFromTranscript(backgroundTranscript = []) {
  return backgroundTranscript.find(t => t && typeof t.answer === 'string' && t.answer.trim())?.answer.trim() || '';
}

// The distinct recurring paid-work tasks the participant explicitly MENTIONED. They ground the task writer, so the
// list reflects what the participant said rather than what is typical for the role.
export async function extractMentionedTasks(backgroundTranscript) {
  const turns = transcriptFromTurns(backgroundTranscript);
  if (!turns) return [];
  const jobTitle = roleFromTranscript(backgroundTranscript);
  const profileBlock = jobTitle
    ? `Participant role context (for resolving pronouns and references — do NOT invent activities from it):\n` +
      `- Job title: ${jobTitle}\n\n`
    : '';
  const response = await client.chat.completions.create({
    model: MODEL,
    // Temp 0: extraction grounds every later step, so it should be consistent and high-recall.
    temperature: 0,
    response_format: { type: 'json_object' },
    messages: [
      { role: 'system', content: EXTRACT_TASKS_PROMPT },
      { role: 'user', content: `${profileBlock}Background:\n\n${turns}\n\nExtract the distinct recurring paid-work tasks per the rules.` },
    ],
  });
  const parsed = JSON.parse(response.choices[0].message.content);
  return Array.isArray(parsed.tasks)
    ? parsed.tasks.filter(t => typeof t === 'string' && t.trim()).map(t => t.trim())
    : [];
}

// Each {"name": ...} in a JSONL reply, skipping lines that don't parse.
function* jsonlNames(lines) {
  for (const line of lines) {
    const t = line.trim();
    if (!t) continue;
    try { const obj = JSON.parse(t); if (obj.name) yield obj.name; } catch { /* skip non-JSON lines */ }
  }
}

// Write the participant's task list, calling onTask(name, source) for each task in order: first their own tasks
// (source 'interview'), then the inferred ones ('gap'). Returns counts for the log.
//   count               ceiling on their own tasks (the client always sends it; 30 if omitted)
//   backgroundTranscript the full Q/A, read only by gap-fill for their background and seniority
export async function generateTasks({ jobTitle, occupationCode, interviewTasks = [], count, backgroundTranscript = [] }, onTask) {
  const fullTranscript = Array.isArray(backgroundTranscript) ? transcriptFromTurns(backgroundTranscript) : '';
  const targetCount = Number.isFinite(+count) && +count > 0 ? Math.round(+count) : 30;
  const interviewBlock = mentionedTasksBlock(interviewTasks);
  const { block: exemplarBlock } = await retrieveExemplarBlock({ jobTitle, occupationCode });

  // 1. Their own tasks, streamed as JSONL and de-duplicated.
  const normalized = [];
  const seenNorm = new Set();
  const pushNorm = (name) => {
    const n = String(name).trim();
    if (!n) return;
    const key = n.toLowerCase();
    if (seenNorm.has(key)) return;
    seenNorm.add(key);
    normalized.push(n);
  };
  const stream = await client.chat.completions.create({
    model: MODEL,
    temperature: 0.7,
    stream: true,
    messages: [
      { role: 'system', content: buildWriteTasksPrompt(targetCount) },
      { role: 'user', content: `Job: ${jobTitle}${exemplarBlock}${interviewBlock}\nGenerate the MECE task list from their mentioned tasks.` },
    ],
  });
  let buffer = '';
  for await (const chunk of stream) {
    buffer += chunk.choices[0]?.delta?.content ?? '';
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const name of jsonlNames(lines)) pushNorm(name);
  }
  for (const name of jsonlNames([buffer])) pushNorm(name);

  // The model lists the most central first and treats targetCount as a ceiling; the cap is a safety net.
  const outNorm = normalized.slice(0, Math.max(1, targetCount));
  for (const name of outNorm) onTask(name, 'interview');

  // 2. Gap-fill: a few tasks they likely do but didn't say, inferred from their own answers. Fails open.
  const gapCount = Math.max(0, Number(process.env.GAPFILL_COUNT) || 10);
  const gapNames = [];
  if (gapCount > 0 && outNorm.length > 0) {
    try {
      const gapResp = await client.chat.completions.create({
        model: MODEL,
        temperature: 0.7,
        messages: [
          { role: 'system', content: buildGapFillPrompt(gapCount, outNorm) },
          { role: 'user', content: `Job: ${jobTitle}${interviewBlock}${fullTranscript ? `\n\nFULL INTERVIEW (their own words — read it for background, seniority, domains, and stakeholders like boards/investors that the task list above drops):\n${fullTranscript}` : ''}\n\nAdd only the inferred tasks they likely do but did NOT mention that you can tie to something specific above — at most ${gapCount}, but fewer (or none) is expected. Do NOT pad to reach ${gapCount}.` },
        ],
      });
      const seen = new Set(outNorm.map((n) => n.toLowerCase().trim()));
      for (const raw of jsonlNames((gapResp.choices[0].message.content || '').split('\n'))) {
        const name = String(raw || '').trim();
        if (name && !seen.has(name.toLowerCase())) { seen.add(name.toLowerCase()); gapNames.push(name); }
      }
      for (const name of gapNames.slice(0, gapCount)) onTask(name, 'gap');
    } catch (gapErr) {
      console.error('[generate-tasks-from-interview] gap-fill skipped:', gapErr.message);
    }
  }
  return { normalized: normalized.length, shown: outNorm.length, gapNames };
}
