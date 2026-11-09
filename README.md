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
| 2 aggregate  | `aggregate/`, `tasknet/` | `aggregate/` is the aggregation library, [S-ONET/aggregate](https://github.com/S-ONET/aggregate), as a git submodule (the same library terkel uses). It normalises the reports (split, filter, rewrite, debrand, dedup) and builds the list: the paper's steps, cluster → synthesize → split → overlap → refile, repeated until no new shared task emerges. The method, every setting (`aggregate/aggregate/configs.yml`) and every prompt are documented there. `tasknet/` is this system's thin layer over it: `ingest.py` reads the Interviewer's sessions, `pipeline.py` runs process/build into the workspace, `auditing.py` checks no task was lost, `onet.py` fetches and reads O\*NET, `env.py` maps the keys in `.env`, and `cli.py` is the `tasknet` command |
| 3 validation | `validation/`  | the validation survey: `server/` (routes mounted by the Interviewer), `ui/` (React components and API client), `tools/` (the response analysis and the export to CSV). The task lists it shows (`tools/inputs/inventories.json`) are a synthetic example; build real ones with `tools/adapters/build_inputs.py`                                                                                                                                                                                                                                                                                           |

**The task definition** (what separates one task from another, §2.2 of the paper) is one file,
`aggregate/aggregate/prompts/shared/task_dimensions.txt`. Every step that decides whether two things are one
task splices it in: cluster, one_duty (split), overlap_labels, resolve_group, sharpen and dispose. To change it
without editing the submodule, copy it to `prompts/task_dimensions.txt` in the workspace. A prompt placed flat
in `prompts/` replaces the packaged one of the same name (`tasknet/pipeline.py::prompts`). An edit has three
costs:

- every cached verdict of those prompts misses, so the next build pays for them again;
- the ledgers under `results/aggregation/<occ>/ledgers/` were judged under the old definition;
- an amend build applies the new definition only to what changes.

So build `--fresh` with its own cache (`LLM_CACHE_DIR=cache/aggregation-v2`) to compare definitions.

## Data

This repository contains no interview data. The raw sessions, the built lists and the survey responses are a separate,
access-restricted package; point `DATA_DIR` at it and the pipeline reads
`$DATA_DIR/interviews/<occupation>/<uuid>.json` (unset: `data/interviews/`), or pass `--data-dir` to point
at any folder of session JSONs. The tests need no data and no keys; `process` and `build` call models.

The pipeline's O*NET reference files are not redistributed: `tasknet onet` (or `make onet`) downloads the task
statements into `data/onet/`. The interview app ships two small O*NET-derived lookup
files (occupation codes, titles and definitions; related occupations), which the survey also uses, under
CC BY 4.0; see `NOTICE`.

## Install

```bash
git clone --recurse-submodules <this repo>   # or, in an existing checkout: git submodule update --init
make setup                         # .venv with aggregate (pip install -e ./aggregate) and tasknet (pip install -e .), npm install
cp .env.example .env               # your keys: OPENAI_API_KEY, plus CLAUDE_BASE_URL / CLAUDE_API_KEY for the
                                   # Claude writer (or one LLM_API_KEY + LLM_BASE_URL for everything)
make onet                          # the O*NET task statements (occupation titles, the umbrella-verb check)
make test                          # aggregate's tests, tasknet's tests, the Node tests; no keys needed
```

`aggregate/` is pinned to a commit of S-ONET/aggregate. To move it: `cd aggregate && git fetch && git checkout <ref>`,
then commit the new pointer here.

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

`process` writes `results/processing/<occupation>/{1_ingested,2_normalized,3_deduped}.csv` (legacy column names:
`session_id`, `task_name`, `onet_code`, `onet_title`, `original_task_name`); `build` writes
`results/aggregation/<occupation>/build.json` (adopt it by copying it to `final.json`). Both live in the
**workspace**: `tasknet --home DIR <command>` / `home=` / `$WORKSPACE`, else the checkout you run from, else the current
directory. The workspace also holds `.env` and the LLM cache (`cache/`), so re-runs only pay for new work.
Models, caps, batching and the chain's settings are aggregate's `configs.yml`; overlay any of them with a
`configs.yml` in the workspace (git-ignored), e.g. `models: {cluster: {model: gpt-5.5}}` or `batch: {api: true}`.
The environment holds only keys and paths (`.env.example`). A list's families carry `sources` (the session
ids that reported them).

## Run the apps

```bash
npm install && npm run build && npm test          # the interview + validation apps (one npm workspace)
cd interviewer && cp .env.example .env && npm run dev
```

## License

CC0 1.0 Universal (`LICENSE`), except the O*NET-derived files listed in `NOTICE`, which are CC BY 4.0. The O*NET data the pipeline downloads is also CC BY 4.0.
