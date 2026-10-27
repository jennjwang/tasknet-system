# TaskNet System

The TaskNet System interviews workers about their jobs, turns what they say into an occupational task list, and
validates that list with a second sample of workers. It is a standalone system: it runs on any occupation with
its own interviewer, aggregation pipeline and validation survey. TaskNet itself is the task database the
system builds. This repository is the system.

The TaskNet System was built for, and first used in, the paper

> Wang, J., Haupt, A., Robinson, I., Lewis, P., Brynjolfsson, E., and Yang, D. (2026). _TaskNet: Building
> Occupational Task Inventories from Worker Interviews at Scale._ SSRN working paper.
> https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7670419

If you use the TaskNet System, please cite that paper:

```bibtex
@misc{wang2026tasknet,
  title     = {TaskNet: Building Occupational Task Inventories from Worker Interviews at Scale},
  author    = {Wang, Jennifer and Haupt, Andreas and Robinson, Isaac and Lewis, Phil and Brynjolfsson, Erik and Yang, Diyi},
  year      = {2026},
  publisher = {SSRN},
  doi       = {10.2139/ssrn.7670419},
  url       = {https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7670419},
}
```

Three phases, three directories:

| phase        | directory      | what it holds                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ------------ | -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 interview  | `interviewer/` | the Interviewer, the web app a worker talks to: an adaptive LLM interviewer (Node/Express + React), the occupation screener, the task-review cards, the prompts that drive them (`interviewer/prompts/`); `scripts/build_onet_related.mjs` rebuilds the shipped `data/onet-related.json`                                                                                                                                                                                                                                                                                                                  |
| 2 aggregate  | `aggregate/`   | the Python pipeline, installed as the `tasknet` package: `processing/` reads the interview sessions into reports (split, filter, normalise, dedup), `aggregation/` builds the list (the paper's four steps, cluster → synthesize → overlap → coverage, repeated until no new shared task emerges — `aggregation/chain.py`; the method and every setting are in `aggregate/README.md`), `judges.py`/`engine.py` are the LLM judges and the model client, `prompts/` every prompt they load (by bare name — `lib.py::load_prompt`), `fetch_onet.py` downloads the O\*NET task statements the pipeline reads |
| 3 validation | `validation/`  | the validation survey: `server/` (routes mounted by the Interviewer), `ui/` (React components and API client), `tools/` (the response analysis and the export to CSV). The task lists it shows (`tools/inputs/inventories.json`) are a synthetic example; build real ones with `tools/adapters/build_inputs.py`                                                                                                                                                                                                                                                                                           |

**The task definition** (what separates one task from another, §2.2 of the paper) is one file,
`aggregate/prompts/shared/task_dimensions.txt`, shared by every step that decides whether two things are one task. How to
edit it, and what an edit costs, is in `aggregate/README.md` ("The task definition").

## Data

This repository contains no interview data. The raw sessions, the built lists and the survey responses are a separate,
access-restricted package; point `DATA_DIR` at it and the pipeline reads
`$DATA_DIR/interviews/<occupation>/<uuid>.json` (unset: `data/interviews/`), or pass `--data-dir` to point
at any folder of session JSONs. The tests need no data and no keys; `process` and `build` call models.

The pipeline's O*NET reference files are not redistributed: `python aggregate/fetch_onet.py` downloads the task
statements into `data/onet/`. The interview app ships two small O*NET-derived lookup
files (occupation codes, titles and definitions; related occupations), which the survey also uses, under
CC BY 4.0; see `NOTICE`.

## Install

```bash
pip install -e .                   # from a checkout (or `pip install .` for a copy)
cp .env.example .env               # your keys: OPENAI_API_KEY, plus CLAUDE_BASE_URL / CLAUDE_API_KEY for the
                                   # Claude writer (or one LLM_API_KEY + LLM_BASE_URL for everything)
python aggregate/fetch_onet.py       # the O*NET task statements (occupation titles, the umbrella-verb check)
```

## Run the pipeline

From the command line:

```bash
tasknet process accountants-and-auditors      # reads $DATA_DIR/interviews/accountants-and-auditors/
tasknet build accountants-and-auditors      # the task list (LLM calls)
tasknet audit accountants-and-auditors      # no task lost in processing (no API calls)
```

Or from Python:

```python
import tasknet
tasknet.process("accountants-and-auditors", data_dir="path/to/sessions")
tasknet.build("accountants-and-auditors")
tasks = tasknet.load_tasks("accountants-and-auditors", which="build")
```

`process` writes `results/processing/<occupation>/3_deduped.csv`; `build` writes
`results/aggregation/<occupation>/build.json` (adopt it by copying it to `final.json`). Both live in the
**workspace**: `tasknet --home DIR <command>` / `home=` / `$WORKSPACE`, else the checkout you run from, else the current
directory. The workspace also holds `.env` and the LLM cache (`cache/`), so re-runs only pay for new work.
The environment variables that pin models, batching and caching are listed in `aggregate/README.md`.

## Run the apps

```bash
npm install && npm run build && npm test          # the interview + validation apps (one npm workspace)
cd interviewer && cp .env.example .env && npm run dev
```

## License

CC0 1.0 Universal (`LICENSE`), except the O*NET-derived files listed in `NOTICE`, which are CC BY 4.0. The O*NET data the pipeline downloads is also CC BY 4.0.
