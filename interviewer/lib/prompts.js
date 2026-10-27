// Every prompt the app sends is a text file under prompts/ ({{name}} marks a value filled in at call time):
//   interview/   one interview turn (lib/planner.js) and the topic guide
//   tasks/       transcript → task list (lib/tasks.js)
//   occupation/  which occupation the participant is in (lib/occupation.js, lib/retrieval.js)
// This module loads them and builds the task-list prompts.
import { readFileSync } from 'node:fs';

// The text of prompts/<rel>, without the file's trailing newline.
export function loadPrompt(rel) {
  return readFileSync(new URL(`../prompts/${rel}`, import.meta.url), 'utf8').replace(/\n$/, '');
}

// Replace each {{name}} in `template` with vars[name]; a placeholder with no value is an error.
export function fill(template, vars) {
  return template.replace(/\{\{(\w+)\}\}/g, (_, name) => {
    if (!(name in vars)) throw new Error(`prompt placeholder {{${name}}} has no value`);
    return String(vars[name]);
  });
}

// /api/extract-interview-tasks: the recurring paid-work tasks the participant explicitly mentioned, at the
// granularity they said them.
export const EXTRACT_TASKS_PROMPT = loadPrompt('tasks/extract.txt');

const WRITE_TASKS = loadPrompt('tasks/write.txt');
const GAP_FILL = loadPrompt('tasks/gap_fill.txt');

// The mentioned-tasks block for the task writer's user message.
export function mentionedTasksBlock(interviewTasks = []) {
  return interviewTasks.length > 0
    ? `\nTASKS THE PARTICIPANT EXPLICITLY MENTIONED (every one must be COVERED by exactly one task in your output — absorbed/merged as needed, never copied in verbatim or dropped):\n${interviewTasks.map((t) => `- ${t}`).join('\n')}\n`
    : '';
}

// /api/generate-tasks-from-interview: roll the participant's mentioned tasks into one MECE list of their own
// tasks in O*NET statement style, with no invented role coverage. `count` is the ceiling on the list's length.
export function buildWriteTasksPrompt(count) {
  const ceil = Number.isFinite(+count) && +count > 0 ? Math.round(+count) : null;
  return fill(WRITE_TASKS, {
    count,
    ceiling_clause: ceil ? ` In all cases the total must not exceed ${ceil}.` : '',
  });
}

// Gap-fill, run after the task list: at most `count` inferred tasks the participant likely does but did not
// say, each tied to their described work or background and distinct from `alreadyTasks`. Tagged source:'gap'.
export function buildGapFillPrompt(count, alreadyTasks = []) {
  const haveBlock = alreadyTasks.length
    ? `\n\nTASKS ALREADY IN THEIR LIST — do NOT repeat, restate, or add a KIND/CASE of any of these:\n${alreadyTasks.map((t) => `- ${t}`).join('\n')}`
    : '';
  return fill(GAP_FILL, { count, have_block: haveBlock });
}
