# TaskNet System Interviewer

The Interviewer is the web app a worker uses: an LLM interviewer talks with them about their job,
turns the conversation into a task list, and has them review that list. It also hosts the validation survey
(`../validation/`, see its README).

## Layout

| Path | What it holds |
|---|---|
| `src/` | the React front end (Vite). `settings.ts` holds everything a study changes; `components/` has one folder per step (`interview/`, `tasks/`) plus the single screens; `store.ts` is the participant's state |
| `server/` | the Express server, routes only: `index.js` (entry), `interview.js`, `occupation.js`, `session.js` |
| `lib/` | what the routes call: `planner.js` (the interviewer), `tasks.js` (extract, write, gap-fill), `occupation.js` (shortlist, screener check), `retrieval.js`, `prompts.js` (loads `prompts/`), `config.js` (model client, model names, storage paths), `paths.js` (the app's folders) |
| `prompts/` | every prompt the app sends, as text files |
| `data/` | the O*NET lookup files the occupation matcher uses (`data/README.md`) |

## Participant flow

The front end is a phase machine (`src/store.ts`, rendered by `src/App.tsx`). Phase names in brackets;
components are under `src/components/`.

1. **Consent** [`setup`] — `Welcome.tsx`. Links to the consent form when one is set (see Configuration).
2. **Occupation screener** [`screen` → `occupation-select`] — `interview/Screener.tsx`, only when
   `SCREENER_TARGET_SOC` is set. Two short questions (job title, main responsibilities), which also become
   the interview's first turns, then the participant picks their O*NET occupation from a shortlist. The pick
   is the gate: a pick outside the target codes goes to [`screen-out`].
3. **Interview** [`background`] — `interview/BackgroundInterview.tsx`, on the question card it shares with
   the screener (`interview/QuestionCard.tsx`: intro screens, mic, type/speak toggle). The interviewer asks
   one question per turn (`/api/planner-next`); answers are typed or dictated. It covers a fixed topic guide,
   then probes for tasks not yet mentioned, and stops on its own or at the turn cap.
4. **Occupation selection** [`occupation-select`] — `OccupationSelect.tsx`. Without the screener, the
   participant picks their occupation here, after the interview.
5. **Task review** [`task-selection`] — `tasks/TaskSelection.tsx`. Tasks are extracted from the transcript
   and written as O*NET-style task statements, plus a few inferred tasks the worker likely does but did not
   say (`GAPFILL_COUNT`). The worker confirms, edits or removes each one and adds missing ones. One planted
   attention-check task is mixed in; answering "yes" to it more than `ATTN_CHECK_MAX_FAILS` times ends the
   session [`screen-out`].
6. **Done** [`study-complete`] — `StudyComplete.tsx`.

These are the only phases (`Phase` in `src/types.ts`).

## Install and run

The repository root is an npm workspace (`interviewer`, `validation`), so install from the root:

```bash
npm install                      # at the repository root
cd interviewer
cp .env.example .env             # then fill in the keys (see Configuration)
npm run dev                      # Vite on http://localhost:5173 + Express on :3001
```

`npm run dev` runs Vite and `node --env-file=.env server/index.js` together; Vite proxies `/api` to the server
(`API_PORT`, default 3001). For a production build:

```bash
npm run build                    # tsc + vite build -> interviewer/dist/
npm start                        # NODE_ENV=production: Express serves dist/ and the API on PORT (3001)
```

From the root, `npm run dev`, `npm run build`, `npm start` and `npm test` forward to this package.
The server needs the whole repository, not `interviewer/` alone: it mounts `../validation/server/`, and the front end imports `../validation/ui/`.

## Configuration

Two places:

- **`src/settings.ts`** — what participants see and how the task review behaves: the welcome blurb, the
  intro screens, the opening and screener questions, the occupation shortlist size, the task-list ceiling,
  the finish-early threshold, the attention checks and the edit nudge. Rebuild the front end after changing
  it. The interview's topics and their questions are in `prompts/interview/guide.json`.
- **`.env`** — the server and the models. `.env.example` lists every variable with its default and is the
  reference. The essentials:
  - **`OPENAI_API_KEY` (required).** Every model call with the default models, and audio transcription
    (`/api/transcribe`, `TRANSCRIBE_MODEL`, default `whisper-1`). To send the model calls through an
    OpenAI-compatible gateway instead (for example one serving Claude models), set `LLM_BASE_URL` +
    `LLM_API_KEY` or `CLAUDE_BASE_URL` + `CLAUDE_API_KEY` (`lib/config.js`); transcription still uses
    `OPENAI_API_KEY`.
  - **Models.** The defaults are the ones the paper reports: `PLANNER_MODEL` (the interviewer) and
    `OCCUPATION_MODEL` `gpt-5.4`, `MODEL` (task extraction, writing and gap-fill) `gpt-5.4`,
    `PLANNER_COUNT_MODEL` (the per-turn task extraction) and `SMALL_MODEL` `gpt-4o-mini`.
  - **Study setup.** `SCREENER_TARGET_SOC` (comma-separated O*NET-SOC codes; unset = no screener),
    `ATTN_CHECK_MAX_FAILS` (default 0), `GAPFILL_COUNT` (default 10), `SESSIONS_DIR`.
  - **Consent form.** None ships with the app: set `VITE_CONSENT_URL` to your form's URL (read at build time).
    A file you put in `interviewer/public/` is served at `/<name>`. Unset, the welcome screen shows no consent
    link.
  - **Interviewer tuning.** `PLANNER_MAX_TURNS` (default and hard cap 17), `PLANNER_MIN_TURNS` (5),
    `PLANNER_REWRITE`, `PLANNER_VARY`.
  - **Retrieval grounding** (off by default). `RETRIEVAL_ENABLED=true` adds task statements from a matched
    occupation to the task writer's prompt (`lib/retrieval.js`). It also needs `CORPUS_PATH`, a JSON task
    corpus (`[{occupation, code?, statements: [...]}]`); none ships with the app.

  Relative paths in `SESSIONS_DIR` and `CORPUS_PATH` are taken from `interviewer/`.

## URL parameters

- `?id=<participant>` — the participant id; sessions and screen-outs are keyed on it. Use a fresh id for a
  clean run.
- `?soc=<##-####.##>` — names the occupation the participant was invited for on the intro screens.
- `?paste=1` — allows pasting into text fields (blocked by default).

The `dev-`/`test-`/`anon-` id prefixes matter in the validation survey, not here: there they are excluded
from analysis (see `../validation/README.md`).

## Sessions

Every participant's state is autosaved as JSON to `interviewer/sessions/` (or `SESSIONS_DIR`), named
`<id>_<sessionId>.json` when the link carries `?id=`, otherwise `<sessionId>.json`. The server refuses
saves that would overwrite a session with an empty or less-progressed one. Screen-outs are also written to
`sessions/screen-outs/<id>.json`, and every screener decision to `sessions/screener/`. The session id is kept
in `localStorage` per `?id=`, so a reload resumes where the participant left off. `sessions/` is git-ignored
and created when the server starts.

## API routes

| Route | File | Purpose |
|-------|------|---------|
| `POST /api/planner-next` | `server/interview.js` | next interviewer question from the turns so far |
| `POST /api/extract-interview-tasks` | `server/interview.js` | tasks the participant mentioned in the transcript |
| `POST /api/generate-tasks-from-interview` | `server/interview.js` | task statements plus inferred tasks, streamed as server-sent events |
| `POST /api/occupation-candidates` | `server/occupation.js` | ranked O*NET-SOC shortlist for the transcript |
| `GET /api/occupations` | `server/occupation.js` | the full O*NET-SOC list (for search) |
| `GET /api/screener/config` | `server/occupation.js` | whether the screener is on |
| `POST /api/screener/probe` | `server/occupation.js` | is the screener answer specific enough; one follow-up if not |
| `POST /api/screener/select` | `server/occupation.js` | pass/fail on the participant's occupation pick |
| `POST /api/screen-out`, `GET /api/screen-status` | `server/occupation.js` | record / check a screen-out by `?id=` |
| `GET /api/session`, `POST /api/session` | `server/session.js` | load / save a session |
| `POST /api/transcribe` | `server/session.js` | audio to text (OpenAI) |
| `GET /api/config` | `server/index.js` | runtime config for the client (attention-check threshold) |
| `GET /health` | `server/index.js` | health check |

The validation survey's routes (`/api/validation/...`) are mounted from `../validation/server/validation.js`.

## Prompts

Every prompt the app sends is a text file under `prompts/`; `{{name}}` marks a value filled in at call time
(`lib/prompts.js` loads them and builds the task-list prompts).

- `prompts/interview/` — one interview turn, run by `lib/planner.js` (topic-guide coverage, gap-driven
  questions, self-stop): `guide.json` (each topic's question and coverage criteria), `next_question.txt`
  (judge coverage and write the next question), `emergent_gap.txt`, `new_tasks.txt`, `explain_question.txt`,
  the two rewrites, and `question_phrasing.txt` (shared by the question writers).
- `prompts/tasks/` — transcript to task list: `extract.txt` (the tasks they mentioned), `write.txt` (the
  task list in O*NET statement style), `gap_fill.txt` (inferred tasks).
- `prompts/occupation/` — `shortlist.txt` and `screener_check.txt` (`lib/occupation.js`), `corpus_match.txt`
  (retrieval grounding, `lib/retrieval.js`).

## Data

`data/onet-soc.json` (occupation codes, titles and definitions) and `data/onet-related.json` (related
occupations) are derived from O*NET under CC BY 4.0 (`data/README.md`, `../NOTICE`). `onet-related.json` is
rebuilt by `scripts/build_onet_related.mjs`.

## Tests

```bash
npm test                         # the task-review component tests (vitest, jsdom)
```

They stub the API and make no model calls.
