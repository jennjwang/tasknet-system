# tasknet: interviews to a task list

The Python pipeline, installed as the `tasknet` package (`aggregate/` is mapped to `tasknet` in `pyproject.toml`;
its prompts are in `prompts/`). Two commands take an occupation from interview sessions to a task list:

```bash
tasknet process <occupation>       # sessions -> results/processing/<occ>/3_deduped.csv   (processing)
tasknet build <occupation>         # reports  -> results/aggregation/<occ>/build.json     (aggregation)
```

| path | what it holds |
|---|---|
| `processing/` | `ingest`, `normalize`, `dedup` (run in order by `run.py`), and `audit` |
| `aggregation/` | the chain (`chain.py`) and one CLI per step: `cluster`, `synthesize`, `split`, `overlap`, `coverage`, plus `verify`; cluster identity in `identity.py` |
| `judges.py` | the LLM judges (`covers`, `one_duty`, `overlap_labels`, `writing`) |
| `engine.py` | the model client, key routing and the call cache |
| `batch.py` | the optional OpenAI Batch API route for the judges (`--batch`) |
| `lib.py` | workspace paths, prompt loading, occupation metadata |
| `onet_db.py` | O*NET titles and task statements (`data/onet/task_statements.csv`) |
| `cli.py` | the `tasknet` command |

## Set up

From the repository root:

```bash
pip install -e .
cp .env.example .env
python aggregate/fetch_onet.py       # O*NET task statements, for occupation titles and the umbrella-verb check
```

Keys go in `.env` (`.env.example` lists every option). Either one key for everything, `LLM_API_KEY`, with
`LLM_BASE_URL` pointing at an endpoint that serves every model (e.g. a LiteLLM proxy); or a key per provider:
`OPENAI_API_KEY` for the gpt models and embeddings, and `CLAUDE_API_KEY` / `CLAUDE_BASE_URL` for the Claude
writer (`synthesize`, and `overlap`'s sharpen). Processing uses only gpt models, so `OPENAI_API_KEY` is enough
for it. To bill a stage to its own key, set `<STAGE>_API_KEY`: `NORMALIZE_API_KEY`, `CLUSTER_API_KEY`,
`SYNTHESIZE_API_KEY`, `SPLIT_API_KEY`, `OVERLAP_API_KEY`, `COVERAGE_API_KEY`. For each call the stage's own key
wins, then `LLM_API_KEY`, then the provider's; `engine.client()` prints the route and key it picked.

## Run it

```bash
tasknet process lawyers --data-dir path/to/sessions    # default: $DATA_DIR/interviews/lawyers/
tasknet audit lawyers                                  # every session task accounted for (no API calls)
tasknet build lawyers                                  # amend the adopted final.json, or build fresh if none
tasknet build lawyers --fresh                          # ignore final.json, build from scratch
tasknet verify results/aggregation/lawyers/build.json  # check the invariants (no API calls)
cp results/aggregation/lawyers/build.json results/aggregation/lawyers/final.json   # adopt it
```

```python
import tasknet
tasknet.process("lawyers", data_dir="path/to/sessions")
tasknet.build("lawyers")
tasks = tasknet.load_tasks("lawyers", which="build")
```

- `process`: ingest always runs and appends only new rows; later stages run only when their output is missing
  or older than their input. `--force` re-runs everything from a fresh ingest; `--from normalize --to dedup`
  runs a subset.
- `build` writes `build.json` and per-run logs under `results/aggregation/<occ>/logs/<timestamp>/`. It never
  overwrites `final.json`.
- **The published lists are frozen.** `build` refuses to rebuild an occupation listed in
  `results/evaluation/heldout_frozen.json`, because a rebuild would absorb the held-out workers.
  `SPEND_HOLDOUT=1` overrides that.
- Every model call is cached under `cache/processing/` and `cache/aggregation/`, so re-running unchanged work
  costs little. A failed call is never cached.

To run one aggregation step by hand, set the occupation and pass artifact names relative to
`results/aggregation/<occ>/`. Set `BATCH=0` as the chain does: run by hand, the stages otherwise send the gpt
judges through the Batch API and wait for it.

```bash
export OCCUPATION=lawyers BATCH=0
python -m tasknet.aggregation.cluster --over-pool build.json
python -m tasknet.aggregation.synthesize --in build.json --out build.json --adopted final.json
python -m tasknet.aggregation.split --in build.json --out verdicts/split.json --apply handoffs/split.json
python -m tasknet.aggregation.synthesize --in handoffs/split.json --out build.json --adopted final.json
python -m tasknet.aggregation.overlap --in build.json --out verdicts/overlap.json --apply handoffs/merged.json \
    --sharpen build.json --adopted final.json
python -m tasknet.aggregation.coverage --in build.json --apply build.json
```

---

## Inputs

- **Interviews**: one JSON file per session, read from `$DATA_DIR/interviews/<occ>/`, else
  `data/interviews/<occ>/`, or the directory given with `--data-dir`. A session is one worker; every
  prevalence number in the pipeline is a count of sessions.
- **O*NET**: `data/onet/task_statements.csv` (`aggregate/fetch_onet.py`, or `REFERENCE_DATA_DIR`). Used for
  occupation titles and the umbrella-verb check (synthesis, step 4).
- **Prompts**: `prompts/processing/` (one per normalize pass); `prompts/aggregation/` (`cluster.txt`,
  `dispose.txt`, `synthesize/` for the writer and its repair, `consolidate/` for the split and overlap judges and
  the sharpener); `prompts/evaluation/` (the coverage and writing judges); `prompts/shared/` (the writing
  principles, task dimensions, task definitions and O*NET statement forms). Prompts load by bare name
  (`lib.load_prompt`). One prompt is not loaded by the pipeline: `prompts/evaluation/pair_relation/` is the
  relation judge the paper uses to compare TaskNet's statements with O*NET's (Appendix G); the comparison
  script itself is not part of this release.

## The task definition

The paper defines a task as **the highest node in a goal hierarchy whose subtasks differ only in context**
(§2.2): recording vitals in different systems is one task, preparing meals and feeding clients are two. That
separation decides what the published list is, and it lives in **one file:
`prompts/shared/task_dimensions.txt`**. It lists the dimensions that divide two pieces of work into separate
tasks (the act, the side of an exchange, what the receiver must be told, the judgement it takes, the trigger)
and the ones that are context and stay inside a task as examples (who it is for, the subject or field, the kind
of artifact, the stage in a process, the tool or setting, the chores of one ongoing responsibility), and how to
decide when they disagree.

Every step that decides whether two things are one task splices that file in with
`{{include:task_dimensions}}`:

| prompt | step | the decision it informs |
|---|---|---|
| `aggregation/cluster.txt` | 1 clustering | which reports form one cluster |
| `aggregation/consolidate/one_duty.txt` | 2 synthesis (split) | whether a statement holds two tasks |
| `aggregation/consolidate/overlap_labels.txt` | 3 overlap resolution | whether two statements share an act |
| `aggregation/consolidate/resolve_group.txt` | 3 overlap resolution | merge, or keep apart |
| `aggregation/consolidate/sharpen.txt` | 3 overlap resolution | how to reword two statements apart |
| `aggregation/dispose.txt` | 4 coverage checking | which statement a report belongs under |

**To change the definition, edit `task_dimensions.txt`** and every step above uses the new text on the next run;
nothing else needs changing. Three things follow from an edit:

- **Every cached verdict of those six prompts misses**, because a prompt's text is part of its cache key, so the
  next build pays for them again. To compare two definitions side by side, give each run its own cache:
  `LLM_CACHE_DIR=cache/aggregation-v2 tasknet build <occ> --fresh`.
- **`history/` was judged under the old definition.** Delete `results/aggregation/<occ>/history/` for a clean
  comparison, or past split verdicts will be reused.
- **Build `--fresh`.** An amend build keeps the adopted list's clusters and statements, so it only applies the
  new definition to what changes.

Not to be confused with `prompts/shared/task_definitions.txt`, which says what counts as a task at all (a
concrete, observable activity, not a goal) and is used by processing's filter and the label-quality judge.

## Processing: sessions → reports

Outputs go to `results/processing/<occ>/`.

| stage | input | output |
|---|---|---|
| `ingest` | session JSONs (each session's `selectedTasks`) | `1_ingested.csv`, one row per (session, task) |
| `normalize` | `1_ingested.csv` | `2_normalized.csv` |
| `dedup` | `2_normalized.csv` | `3_deduped.csv`: a task a participant listed twice (or two that normalized to the same statement) kept once; different participants stay separate rows |

`normalize` runs four model passes, each with its own prompt: **split** a long typed entry into one row per task
it names; **filter** out non-tasks (the only quality screen); **normalize** each kept task into a verb-led
O*NET-style statement; remove **brand** names. `tasknet audit` checks that every session task is either in
`3_deduped.csv` or was removed by the filter, and exits non-zero if any is unaccounted for.

An **report** is one distinct task text plus the sessions that reported it (`cluster.load_reports()`). Most reports
come from a single session, so the 2-worker floor below decides what can be published.

## Aggregation: reports → a task list

Every step reads and rewrites one artifact, `build.json`. It holds `core`, the list of clusters (`head`,
`members` = reports, `workers`, `prevalence`, and once written, `statement`), and the **pool**: reports in no
cluster, kept under the `cluster.POOL_KEYS` keys.

The terms are the paper's (report, cluster, pool, round). The JSON keys inside the artifacts are older and stay
as they are, so existing lists keep loading: `core` is the list of clusters, `members` a cluster's reports,
`meta.n_atoms` the number of reports, and `released`, `declined`, `dropped_atoms`, `thin_members` the pool.

### The seed

| condition | mode | seed |
|---|---|---|
| `final.json` exists and `FRESH` ≠ 1 | **AMEND** | `build.json` = a copy of the adopted list. Its clusters start unchanged, and the adopted list is the continuity registry (`--adopted final.json`). |
| no adopted list, or `FRESH=1` | **FRESH** | an empty `core` with every report in the pool, so round 1's clustering groups the whole corpus. `ADOPTED` may still name a snapshot as the registry. |

### The round

The chain runs the paper's four steps (§2.2) as a round, repeated until clustering proposes no new cluster that
clears the worker floor (round 1 always runs in full), at most `MAX_ROUNDS` (3) times. Paths are under
`results/aggregation/<occ>/`; `N` is the round.

| step | runs | writes |
|---|---|---|
| 1 clustering | `cluster` | `build.json` + new clusters (head and members, no statement yet) |
| 2 synthesis | `synthesize`, `split`, `synthesize` | statements for new or grown clusters; `verdicts/roundN_split.json` (SPLIT/KEEP per cluster), `handoffs/roundN_split.json` (splits applied); statements for the split parts |
| 3 overlap resolution | `overlap` | `verdicts/roundN_overlap.json` (MERGE/SEPARATE per colliding group), `handoffs/roundN_merged.json` (merges applied), `build.json` with SEPARATE statements reworded |
| 4 coverage checking | `coverage` | `verdicts/roundN_coverage.json` (where each report belongs), `build.json` with reports moved, filed from the pool, or released to it |
| verify | `verify` | pass/fail on the invariants; runs after the seed, every round, and at the end |

After steps 1–4 the chain copies `build.json` to `stages/<step>.json`.

#### 1 Clustering: `cluster`

`cluster.txt` (gpt-5.4) groups the reports in the pool into *clusters*: a heading plus member reports. The prompt
carries the grouping rules (`task_dimensions.txt`, the paper's task definition), guards against over-merging,
and keeps clusters separate on genuine doubt. Different draws can group the same input quite differently,
which is why an adopted list is amended rather than redrawn.

**Worker floor.** A group is kept only if its reports come from at least 2 sessions (`MIN_WORKERS`); everything
else stays in the pool. The number of new clusters is the round's stopping signal.

#### 2 Synthesis: `synthesize`, then `split`

`synthesize` writes the statements. For each cluster, in order:

1. **Skip** if its members and the writer prompt are unchanged since it was written (stamps `written_for`,
   `written_with`).
2. **Continuity**: if the member set matches an adopted cluster (exactly, or sharing at least `SAME_CLUSTER` = 60%
   of the larger side), the adopted statement is used as the draft instead of a fresh one.
3. **Draft** (Claude Sonnet, `synthesize.txt` + `writing_principles.txt`) from the reports; the heading is not
   shown. Sibling statements are supplied so the draft stays distinct from them.
4. **Coverage**: `judges.covers` over the cluster's own reports. A draft covering less than `SYNTHESIZE_COV_MIN`
   (60%) of them is redrafted once, naming the missed reports; the redraft may not fuse two duties.
5. **Fixed checks** (no model call): no **umbrella lead verb** (`UMBRELLA_VERBS`: manage, handle, conduct,
   oversee, pursue, deal, ensure, use, utilize, execute, do); at most `SYNTHESIZE_MAX_WORDS` (25) words; at
   most one participial "-ing" clause. `perform` (`ATTESTED_VERBS`) may lead only where O*NET statements in
   the occupation's SOC major group pair it with one of the words that follow it.
6. **Writing judge** (`judges.writing`, `writing_only.txt`): at most one defect per statement, counted at
   confidence ≥ 0.75 (`WRITING_GATE_CONF`).
7. **Repair** (`synthesize_repair.txt`): one defect per round, up to `SYNTHESIZE_REPAIR_ROUNDS` (4) rounds. A
   repair is refused (keeping the draft) if it changes nothing, the same defect fires again, or coverage falls.
8. A fusion the repair cannot fix ships with a ⚑ `form_flag` on the row, which makes `split` hear the cluster
   again.

**Adopted statements are not machine-rewritten.** A statement from the adopted list still goes through these
checks, but any defect found is only reported in the run log; the statement stays as adopted.

`split` splits a statement that combines two duties. For each cluster, `judges.one_duty` (`one_duty.txt`) sees
the statement and its reports and says whether it is one duty or two, naming the parts and their reports.

- **Past verdicts**: a cluster whose member set already has a verdict in `history/split_verdicts.json` reuses
  it (see below). A cluster carrying a ⚑ is heard again once.
- **decide_split()** matches the parts to reports and applies the floors per part. Outcomes: `keep` (the reports don't
  partition as claimed, or no usable parts), or `split` with a basis: `exclusive-workers` / `shared-workers`
  (two published parts), `one-supported-part` (the cluster is narrowed), or `no-supported-part` (the cluster
  dissolves into the pool).
- **apply_splits**: publishable parts become clusters; reports in no published part go to the pool.

`synthesize` then runs again and writes statements for the new parts (every other cluster is skipped).

#### 3 Overlap resolution: `overlap`

(Appendix A calls this step *partitioning*.) Merges and rewordings:

- **Prefilter**: statement pairs with embedding cosine ≥ `OVERLAP_TAU` (0.44).
- **`judges.overlap_labels`** asks only whether any act appears in both statements. A shared object,
  population, setting or tool is not a shared act. On doubt it answers yes: a wrong yes costs one closer look,
  a wrong no publishes an overlap. Colliding pairs form groups.
- **resolve_group** (`resolve_group.txt`) sees every member list in a group and rules **MERGE** (with a basis:
  which reports are the same work), **SEPARATE**, or **KEEP**. Different people doing the work points to a merge
  into the abstraction; the same people, to separate statements.
- **Reword counts**: a pair that collides again after being reworded apart is shown to the resolver with the
  number of times it was (`history/reword_counts.json`, below), as evidence the two are one task.
- **Merge guards**: a merge applies only if a second resolver draw merges the same member set
  (`CONCURRENCE_MERGE`, on by default), and a merge of more than `OVERLAP_MERGE_CAP` (4) statements is withheld.
  `synthesize` writes the merged statement.
- **Sharpen** (`sharpen.txt`, Claude Sonnet) rewords the SEPARATE statements of a group; KEEP neighbours are
  shown as frozen context and never rewritten. A fresh statement takes the rewrite; a adopted one is not
  rewritten, and the proposal is only reported.

#### 4 Coverage checking: `coverage`

1. `uncovered()`: for each filed report, does its own statement still cover it (`covers`)? If not, it is
   *off-home*. Reports nothing covers are reported as *missing*.
2. The **disposer** (`dispose.txt`, shown every published statement) rules on off-home reports and the pool:
   **FILE** under the statement that claims the act (the report moves there or stays), or **UNFILED** / **LEAVE**
   (an off-home report is released to the pool; a pool report stays there). A move needs two agreeing draws.

A report leaves its cluster only when coverage and the disposer agree. Clusters that fall below the worker floor
dissolve into the pool. This step never creates a cluster or writes a statement; the pool it leaves is what the
next round's clustering groups.

### Invariants (`verify`)

- **`filed + pool == n_atoms`.** Reports that leave a cluster are kept in `cluster.POOL_KEYS` and forwarded by
  every step with `cluster.carry(build)`.
- **Prevalence follows the members.** `cluster.recount_workers(fams)` recomputes `workers` and `prevalence` wherever a
  step rewrites members.
- No report in two clusters, no member that is not a report, no duplicate statement or statement id, every
  cluster at or above the worker floor.

### Artifacts and what carries between builds

Under `results/aggregation/<occ>/`:

| path | holds | keep? |
|---|---|---|
| `final.json` / `.csv` | the adopted list, the deliverable; nothing downstream reads a build artifact | yes |
| `build.json` | the latest build | until adopted |
| `history/split_verdicts.json` | every `one_duty` verdict, with its statement and members | yes |
| `history/reword_counts.json` | per pair of clusters, how many times they were reworded apart | yes |
| `stages/`, `verdicts/`, `handoffs/`, `logs/` | per-step snapshots, decisions, inter-step lists, chain logs | per run (`rm -rf verdicts handoffs stages`) |

**What later rounds and builds remember (`history/`).** Two files change later decisions, and both match
clusters by their member set rather than by wording:

- `split_verdicts.json`: whether each cluster is one duty or two. A cluster whose reports already have a verdict
  reuses it, even after its statement was reworded, so a statement is not split one build and kept the next
  just because the judge was asked again. The model cache cannot do this: it is keyed on the exact statement
  text. A ⚑ fusion flag from synthesize re-opens a cluster once (`reopened_on` records that).
- `reword_counts.json`: how many times each pair of clusters was reworded apart. The resolver sees the count
  (its prompt calls it STRAIN) when the pair collides again: a pair that keeps colliding however it is worded is
  probably one task.

Delete `history/` to start an occupation's memory over. Each cluster also carries stamps: `written_for` (member
key), `written_with` (prompt hash) and `form_flag` (an open fusion flag ⚑). Nothing in the chain reads human rulings: to disagree with a statement, edit or drop it in
the adopted list.

**Adopting a build**: compare `build.json` with `final.json` (new, gone, grown and reworded clusters, matched by
member set), then `cp build.json final.json`. Keep a snapshot as `final_<n>.json` to name it later with
`ADOPTED`.

## Judges and writers

| role | model (variable) | step |
|---|---|---|
| filter | gpt-5.4 (`FILTER_MODEL`, `FILTER_EFFORT`) | processing |
| split, normalize, brand | gpt-5.4-mini (`NORMALIZE_MODEL`, `NORMALIZE_EFFORT` low) | processing |
| grouping | gpt-5.4 (`CLUSTER_MODEL`, `CLUSTER_EFFORT` medium) | cluster |
| writer | claude-sonnet-4-6 (`SYNTHESIZE_MODEL`) | synthesize |
| writing judge, `judges.writing` | gpt-5.4-mini (`WRITING_MODEL`) | synthesize |
| coverage, `judges.covers` | gpt-5.4-mini (`COVERS_MODEL`) | synthesize, coverage |
| `judges.one_duty` | gpt-5.4-mini (`ONEDUTY_MODEL`) | split |
| `judges.overlap_labels` | gpt-5.4 (`OVERLAP_MODEL`) | overlap |
| resolve_group | gpt-5.4 (`RESOLVE_MODEL`) | overlap |
| sharpen | claude-sonnet-4-6 (`SHARPEN_MODEL`) | overlap |
| disposer | gpt-5.4-mini (`DISPOSE_MODEL`) | coverage |

`BATCH=1` sends `covers`, `one_duty` and `overlap_labels` through the OpenAI Batch API at half price, blocking
until each batch finishes. The chain sets `BATCH=0` unless you pass `--batch`; a step run by hand batches unless
`BATCH=0`.

## Settings

Every model and threshold is an environment variable with a pinned default, with no prefix. Changing a model or
prompt changes the cache key, so the next run pays for that judge again. Beyond the models above:

| scope | variable (default) |
|---|---|
| chain | `FRESH` · `ADOPTED` · `MAX_ROUNDS` (3) · `BATCH` (0) · `CHAIN_LOGS` · `SPEND_HOLDOUT` · `PARALLEL_CALLS` (model calls at once) · `MIN_WORKERS` (2, the publishing floor) · `LLM_CACHE_DIR` |
| paths | `WORKSPACE` · `DATA_DIR` · `REFERENCE_DATA_DIR` · `HELDOUT_FROZEN` (relative paths are taken from the workspace) |
| synthesize | `SYNTHESIZE_COV_MIN` (0.6: a draft covering less of its reports is redrafted) · `SYNTHESIZE_MAX_WORDS` (25) · `SYNTHESIZE_REPAIR_ROUNDS` (4) · `WRITING_GATE_CONF` (0.75: the writing judge's defect counts at this confidence) · `SAME_CLUSTER` (0.6: share of members a cluster must have in common with an adopted one to reuse its statement). The verb lists (`UMBRELLA_VERBS`, `ATTESTED_VERBS`) are at the top of `synthesize.py` |
| overlap | `OVERLAP_TAU` (0.44: pairs below this cosine are never judged) · `CONCURRENCE_MERGE` (1) · `OVERLAP_MERGE_CAP` (4) |
| coverage | `COVER_TOPK` (0 = a sixth of the list, at least 3: statements each report is tried against) |
