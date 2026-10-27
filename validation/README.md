# TaskNet System validation survey

A survey in which workers rate a task list for their own occupation. A rater describes their job, picks their
O*NET occupation, and is shown the published list for that occupation (a rater whose occupation has no list
is screened out). For each task they say whether it is
part of their job and, if it is, rate it (frequency, importance, duration, evaluability, training). They then
review their answers, mark which tasks top performers do better, and give feedback on the questions. The
responses measure how well a list covers the work of people who were not interviewed for it.

Attention checks are tasks from unrelated occupations planted in the list. The server alone knows which
statements they are. A rater who marks one as part of their job is cut short without being told why, and
their response is recorded as failing the check.

## Layout

| Folder | Contents |
|--------|----------|
| `server/` | Express routes (`validation.js`), storage (`validation-store.js`), balanced task subsets (`coverage-subset.js`) |
| `ui/` | React components (`CoverageStudy.tsx` is the entry) and the client API |
| `tools/` | the input task lists (`inputs/`), the script that builds them (`adapters/`), and the analysis scripts (`coverage/`) |

There is no separate server or build. `interviewer/server/index.js` mounts `server/validation.js`, and
`interviewer/src/main.tsx` renders `CoverageStudy` instead of the interview when the URL has `?phase=validation`.
The survey also uses three Interviewer routes: `/api/occupation-candidates` (the occupation shortlist, an
LLM call), `/api/occupations` (the full catalog for search) and `/api/transcribe` (voice answers).

## Run it

Set up and start the Interviewer (see `../interviewer/README.md`), then open:

```
http://localhost:5173/?phase=validation&id=dev-1
```

`?id=` is the rater id; a link without one gets an `anon-` id stored in the browser. Ids are sticky (the
assignment and a draft are kept per id), so use a new id for a clean run.

Ids starting with `test`, or with `dev-`, `anon-` or `e2e-`, are excluded from every analysis
(`tools/lib/io.mjs::testReason`).

Responses, assignments, occupation checks and drafts are saved as JSON under `validation/tools/out/`
(git-ignored) unless `VALIDATION_DATABASE_URL` points at Postgres, where the server creates its tables
(`coverage_fit`, `coverage_assignment`, `coverage_response`, `coverage_draft`; schema in
`server/validation-store.js`) on first use. `VALIDATION_DIR` moves the whole `tools/` root (inputs and
`out/`) for the server and the analysis tools alike. The `VALIDATION_*` and `COVERAGE_*` variables in
`interviewer/.env.example` control storage, the results-view access codes, and how many raters see each task.
`GET /api/validation/results?viewer=<code>` serves the submitted responses as JSON (test ids dropped) for any
code listed in `VALIDATION_VIEWERS`; with none set it answers 503.

## Inputs

`tools/inputs/inventories.json` holds one task list per occupation, keyed by O*NET-SOC code, plus the
attention-check statements. The shipped file is a synthetic example so the app runs. For real lists, `tools/adapters/build_inputs.py` reads `results/aggregation/<occupation>/final.json` (the
adopted output of the aggregate pipeline) and writes the same file. It needs the `tasknet` package
(`pip install -e .` at the root). Without `--occ` it includes every occupation that has a `final.json`.
Attention checks are carried over from the existing file, not generated.

```bash
python validation/tools/adapters/build_inputs.py [--occ accountants-and-auditors ...] [--n 0]
```

`--n 0` (the default) shows every task; `--n k` shows k tasks spread evenly across the prevalence ranking.

## Analysis tools

Run from the repository root. They read responses, screener records and assignments from `tools/out/` or,
when `VALIDATION_DATABASE_URL` is set, from Postgres (add `--env-file=interviewer/.env`).

| Command | What it does |
|---------|--------------|
| `node validation/tools/coverage/coverage.mjs` | Scores responses per occupation: share of tasks endorsed (CIs over raters), per-task endorsement, Krippendorff's alpha, completion time. `MIN_MINUTES` (default 5) sets the time exclusion. No API calls. |
| `node validation/tools/coverage/export.mjs [--all]` | Writes `ratings.csv`, `participants.csv` and `events.csv` to `tools/out/`. No API calls. |

`--all` includes test ids. The repository ships no real responses, so both scripts report zero participants
until you collect some.
