// Study settings: what a study running the TaskNet System would change, in one place. Everything here is shown to
// participants or shapes what they see; the components import it and hold no study-specific values of their own.
//
// Set elsewhere:
//   interviewer/.env (server, read at start)   SCREENER_TARGET_SOC (turns the screener on), ATTN_CHECK_MAX_FAILS,
//                                              GAPFILL_COUNT, the model names, PLANNER_MAX_TURNS / _MIN_TURNS
//   interviewer/.env (front end, build time)   VITE_CONSENT_URL
//   prompts/interview/guide.json               the interview's topics, their questions and coverage criteria
//   prompts/                                   every prompt the app sends
//   the participant's link                     ?id= (participant id), ?soc= (named on the intro screens)

// ── Welcome ─────────────────────────────────────────────────────────────────────

// The study blurb on the welcome screen, one paragraph per entry.
export const STUDY_DESCRIPTION = [
  "Thank you for participating. We're developing better ways to understand how people's tasks at work are changing as they use AI tools.",
  "You'll have a short conversation with an AI interviewer about your role and a typical week. From your answers, we'll put together a task list for you to review — mark what applies, reword anything that doesn't quite fit, and add tasks we might have missed.",
];

// ── Interview ───────────────────────────────────────────────────────────────────

// Intro screens shown once, before the first question — explain the process and ask
// for detailed answers up front, since knowing what's coming tends to make people give
// fuller responses from the start. Shown one at a time with a Continue button.
//
// The middle screen names the recruited-for occupation when the link carried a
// ?soc= code (resolved to a title on App mount). "The specific job you were
// recruited for" only works if the participant remembers which job that was —
// naming it removes the guess for anyone who holds more than one. No article
// before the title: O*NET-SOC titles are plural ("Registered Nurses").
export function introScreens(invitedTitle: string | null): string[] {
  return [
    "In a moment, an AI interviewer will ask you a few questions about your job and the tasks you do. From your answers, we'll put together a list of your tasks to review afterwards.",
    invitedTitle
      ? `Please answer about the specific job you were recruited for — **${invitedTitle}** — and not other work you do.`
      : "Please answer about the specific job you were recruited for, not other work you do.",
    "Finally, please be as detailed and thorough as you can — including the smaller, less obvious tasks, not just the main ones. The more you share, the more complete your task list will be.",
  ];
}

// Deliberately not "your CURRENT role": participants qualify on a specific job
// experience that isn't necessarily the job they hold today, and the word
// "current" pushes them to answer about the wrong one. Kept neutral — no cue
// about recruitment, which would tell them the study screens on occupation.
// Also the screener's first question. Must match the `jobTitle` topic's text in prompts/interview/guide.json:
// change both together.
export const OPENING_QUESTION = "To start, what's your job and what field is it in?";
export const OPENING_PLACEHOLDER = "Your job and the field/industry it's in";

// Closing thank-you shown after the last question, before task selection
export const OUTRO_TEXT =
  "Great! From this quick interview, we'll generate a list of tasks for you to review and refine next.";

// ── Screener (only when SCREENER_TARGET_SOC is set) ─────────────────────────────

// The question text is VERBATIM the guide's `jobTitle` and `responsibilities`
// wording (prompts/interview/guide.json). That matters twice over: the
// participant meets the same voice they're about to be interviewed by, and both
// answers are recorded as planner turns, so when the interview starts, its
// resume path re-derives coverage from them and the planner strikes those two
// topics instead of asking again. Change them together with guide.json.
export const SCREENER_QUESTIONS = [
  {
    question: OPENING_QUESTION,
    placeholder: OPENING_PLACEHOLDER,
  },
  {
    question: "What are your primary responsibilities at work?",
    placeholder: "The main things you're responsible for day-to-day",
  },
];

// Below this many characters the responsibilities answer is worth checking for
// substance before it becomes the basis of a gate decision — "idk" / "a bit of
// everything" still produce a confident-looking candidate list, so without this
// the gate rules on a pick made from nothing. Above it, the answer is substantive
// by construction and the check is skipped, so the common case costs nothing.
export const THIN_ANSWER_CHARS = 140;

// ── Occupation selection ────────────────────────────────────────────────────────

// How many LLM matches to ask the classifier for. The screen then shows the FULL
// union it returns (these matches plus O*NET's related occupations) in a scroll,
// so this only sets how much of the list is model-ranked rather than how much the
// participant gets to see. Wider than the validation study's 3 because here the
// pick can be the GATE.
export const MATCH_COUNT = 5;

// ── Task review ─────────────────────────────────────────────────────────────────

// Ceiling on the generated list. We show ALL the participant's tasks — no
// artificial max or min — so nothing they described gets dropped; this only
// bounds pathological runs. The server caps the model at this and emits
// everything it produces under it. Real interviews land at ~3–18 tasks, so this
// rarely binds; it mainly guards against a runaway/incoherent transcript.
// KEEP IN SYNC with the server's fallback default in lib/tasks.js
// (/api/generate-tasks-from-interview `targetCount`).
export const GENERATION_CEILING = 30;

// Number of cards the participant must rate before the "Finish early" escape
// unlocks — a burnout valve, independent of how long the full list is.
export const DONE_THRESHOLD = 15;

// Attention checks: O*NET-style tasks from clearly unrelated occupations,
// spliced into the generated list. Any honest participant marks them "I don't do
// this"; answering "yes" counts as a fail (see advance()). 0 turns them off.
//
// ONE, not two. Participants often read a check as a mistake in their list. One check still catches
// someone clicking "yes" through the whole list, which is what it is for; a second mostly doubles the
// chance of looking broken to an honest participant.
export const ATTENTION_CHECK_COUNT = 1;

// The pool attention checks are drawn from. Each is a real task drawn only from trades, food, agriculture
// and personal care. If you recruit in those fields, replace them with tasks your participants can't do.
export const ATTENTION_CHECKS: string[] = [
  "Cut, color, and style clients' hair according to their requests at a salon.",
  "Prepare and plate dishes on the line during a restaurant's dinner service.",
  "Weld steel components on a fabrication line using arc-welding equipment.",
  "Harvest ripe produce by hand and sort it into crates for shipment to distributors.",
  "Mow lawns, trim hedges, and plant seasonal flowers for residential properties.",
  "Install and solder copper piping to supply water throughout a new building.",
];

// The "Make it yours" edit nudge stays hidden until the participant has passed
// this many task cards without editing any task name. Once they edit any task,
// the nudge stops appearing for the rest of the flow.
export const NUDGE_AFTER_UNEDITED = 3;
