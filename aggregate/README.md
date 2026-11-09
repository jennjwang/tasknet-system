# tasknet: interviews to a task list

The Python pipeline, installed as the `tasknet` package (`aggregate/` is mapped to `tasknet` in `pyproject.toml`;
its prompts are in `prompts/`). Two commands take an occupation from interview sessions to a task list:

```bash
tasknet process <occupation>       # sessions -> results/processing/<occ>/3_deduped.csv   (processing)
tasknet build <occupation>         # reports  -> results/aggregation/<occ>/build.json     (aggregation)
```

The package has two layers. The **aggregator** (`Aggregator`, everything but the files marked TaskNet below)
takes task rows from any source and knows nothing about interviews, files or O\*NET: every input is passed in.
The **TaskNet layer** reads interview sessions, keeps the workspace's files, and supplies O\*NET's statements to
the writer's umbrella-verb check.

| path | what it holds |
|---|---|
| `cli.py`, `pipeline.py`, `workspace.py` | TaskNet: the `tasknet` command, process/build/verify over the workspace, its paths and settings |
| `processing/ingest.py`, `processing/audit.py` | TaskNet: session JSONs to rows; the no-task-lost audit |
| `onet_db.py`, `fetch_onet.py` | TaskNet: O\*NET titles and task statements (`data/onet/task_statements.csv`) |
| `processing/` | `normalize` (split, filter, normalize, brand), `dedup` |
| `aggregation/` | the chain (`chain.py`) and its steps: `cluster`, `synthesize`, `fusion` (the split), `overlap`, `refile` (coverage), `verify`; `blocks.py` holds scale knobs that are off for interviews |
| `judges.py` | the LLM judges (`covers`, `duties`, `overlaps`, `merge_or_separate`, `sharpen`, `dispose`, `writing_defects`) |
| `evaluate/` | the evaluation of a list on held-out data (coverage, silhouette, incidence, crosswalk, exclusivity, exposure) |
| `engine.py`, `cache.py`, `batch.py` | the model client and key routing, the verdict cache, the optional OpenAI Batch API route |
| `config.py`, `configs.yml` | every model, threshold and batching knob, with its default |
| `atoms.py`, `families.py`, `context.py`, `prompts.py` | the row table, family identity, the per-run context, prompt loading |
| `tests/` | offline tests (`pytest`), including the cache-key guard |

## Set up

From the repository root:

```bash
pip install -e '.[test]'
cp .env.example .env
python aggregate/fetch_onet.py       # O*NET task statements, for occupation titles and the umbrella-verb check
pytest                               # offline; no keys needed
```

Keys go in `.env` (`.env.example` lists every option). Either one key for everything, `LLM_API_KEY`, with
`LLM_BASE_URL` pointing at an endpoint that serves every model (e.g. a LiteLLM proxy); or a key per provider:
`OPENAI_API_KEY` for the gpt models and embeddings, and `CLAUDE_API_KEY` / `CLAUDE_BASE_URL` for the Claude
writer (`synthesize`, and `overlap`'s sharpen). Processing uses only gpt models, so `OPENAI_API_KEY` is enough
for it.

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
- `build` writes `build.json` and, under `results/aggregation/<occ>/`, a snapshot per step (`stages/`), every
  decision (`verdicts/`), and the lists handed between steps (`handoffs/`). It never overwrites `final.json`.
- **The published lists are frozen.** `build` refuses to rebuild an occupation listed in
  `results/evaluation/heldout_frozen.json`, because a rebuild would absorb the held-out workers.
  `SPEND_HOLDOUT=1` overrides that.
- Every model call is cached under `cache/processing/` and `cache/aggregation/`, so re-running unchanged work
  costs little. A failed call is never cached. `tests/test_cache_keys.py` pins every judge's key, so a code
  change that would orphan the cache fails the tests.

The aggregator can also be driven directly, with rows from any source:

```python
from tasknet import Aggregator, Atoms, Config
agg = Aggregator(Config.load("my.yml"), workdir="results/aggregation/lawyers", cache_dir="cache/aggregation")
build = agg.build(Atoms.read("results/processing/lawyers/3_deduped.csv"))
```

---

## Inputs

- **Interviews**: one JSON file per session, read from `$DATA_DIR/interviews/<occ>/`, else
  `data/interviews/<occ>/`, or the directory given with `--data-dir`. A session is one worker; every
  prevalence number in the pipeline is a count of sessions.
- **O\*NET**: `data/onet/task_statements.csv` (`aggregate/fetch_onet.py`, or `REFERENCE_DATA_DIR`). Used for
  occupation titles and the umbrella-verb check (synthesis, step 2).
- **Prompts**: `prompts/processing/` (one per normalize pass); `prompts/aggregation/` (`cluster/` for the
  grouping, `synthesize/` for the writer and its repair, `consolidate/` for the split and overlap judges and the
  sharpener, `refile/` for the disposer, `batch/` for the multi-item judge calls); `prompts/evaluation/` (the
  coverage and writing judges, and the evaluation's judges); `prompts/shared/` (the writing principles, task
  dimensions, task definitions and O\*NET statement forms). Prompts load by bare name (`prompts.py`).
  `prompts/evaluation/pair_relation/` is the relation judge the paper uses to compare TaskNet's statements with
  O\*NET's (Appendix G; `evaluate/crosswalk.py`).

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
| `aggregation/cluster/cluster.txt` | 1 clustering | which reports form one cluster |
| `aggregation/consolidate/one_duty.txt` | 2 synthesis (split) | whether a statement holds two tasks |
| `aggregation/consolidate/overlap_labels.txt` | 3 overlap resolution | whether two statements share an act |
| `aggregation/consolidate/resolve_group.txt` | 3 overlap resolution | merge, or keep apart |
| `aggregation/consolidate/sharpen.txt` | 3 overlap resolution | how to reword two statements apart |
| `aggregation/refile/dispose.txt` | 4 coverage checking | which statement a report belongs under |

**To change the definition, edit `task_dimensions.txt`** and every step above uses the new text on the next run;
nothing else needs changing. Three things follow from an edit:

- **Every cached verdict of those six prompts misses**, because a prompt's text is part of its cache key, so the
  next build pays for them again (and `tests/test_cache_keys.py` fails until its golden keys are re-recorded).
  To compare two definitions side by side, give each run its own cache:
  `LLM_CACHE_DIR=cache/aggregation-v2 tasknet build <occ> --fresh`.
- **`ledgers/` was judged under the old definition.** Delete `results/aggregation/<occ>/ledgers/` for a clean
  comparison, or past split verdicts will be reused.
- **Build `--fresh`.** An amend build keeps the adopted list's clusters and statements, so it only applies the
  new definition to what changes.

Not to be confused with `prompts/shared/task_definitions.txt`, which says what counts as a task at all (a
concrete, observable activity, not a goal) and is used by processing's filter.

## Processing: sessions → reports

Outputs go to `results/processing/<occ>/`.

| stage | input | output |
|---|---|---|
| `ingest` | session JSONs (each session's `selectedTasks`) | `1_ingested.csv`, one row per (session, task) |
| `normalize` | `1_ingested.csv` | `2_normalized.csv` (`original_task_name` is the text as typed, when it changed) |
| `dedup` | `2_normalized.csv` | `3_deduped.csv`: a task a participant listed twice (or two that normalized to the same statement) kept once; different participants stay separate rows |

`normalize` runs four model passes, each with its own prompt: **split** a long typed entry into one row per task
it names; **filter** out non-tasks (the only quality screen); **normalize** each kept task into a verb-led
O\*NET-style statement; remove **brand** names. `tasknet audit` checks that every session task is either in
`3_deduped.csv` or was removed by the filter, and exits non-zero if any is unaccounted for.

A **report** is one distinct task text plus the sessions that reported it (the code calls it an *atom*). Most
reports come from a single session, so the 2-worker floor below decides what can be published.

## Aggregation: reports → a task list

Every step reads and rewrites one artifact, `build.json`. It holds `core`, the list of clusters (`head`,
`members` = reports, `sources` = the sessions behind them, `prevalence`, and once written, `statement`), and the
**pool**: reports in no cluster (`released`, `declined`, `dropped_atoms`, `thin_members`).

The terms here are the paper's (report, cluster, pool, round); the code's are *atom*, *family*, *pile* and
*cycle*. `meta.n_atoms` is the number of reports.

### The seed

| condition | mode | seed |
|---|---|---|
| `final.json` exists and not `--fresh` | **AMEND** | `build.json` = a copy of the adopted list. Its clusters start unchanged, and the adopted list is the continuity registry. |
| no adopted list, or `--fresh` | **FRESH** | an empty `core` with every report in the pool, so round 1's clustering groups the whole corpus; an adopted list is still the continuity registry. |

### The round

The chain (`aggregation/chain.py`) runs the paper's four steps (§2.2) as a round, repeated until clustering
proposes no new cluster that clears the worker floor (round 1 always runs in full), at most `chain.max_cycles`
(3) times. Paths are under `results/aggregation/<occ>/`; `N` is the round.

| step | module | writes |
|---|---|---|
| 1 clustering | `cluster` | `stages/cluster.json`: new clusters (head and members, no statement yet) |
| 2 synthesis | `synthesize`, `fusion`, `synthesize` | statements for new or grown clusters (`stages/synthesize.json`); `verdicts/cycleN_fused.json` (split/keep per cluster), `handoffs/cycleN_defused.json` (splits applied); statements for the split parts |
| 3 overlap resolution | `overlap`, then `fusion` + `synthesize` again | `verdicts/cycleN_overlap.json` (MERGE/SEPARATE per colliding group), `stages/consolidate.json`; the split check on the statements merges wrote, `verdicts/cycleN_fused_merged.json`, `stages/recheck.json` |
| 4 coverage checking | `refile` | `verdicts/cycleN_coverage.json` (where each report belongs), `stages/refile.json` |
| verify | `verify` | pass/fail on the invariants; runs after the seed, every round, and at the end |

#### 1 Clustering: `cluster`

`cluster.txt` (gpt-5.4) groups the reports in the pool into *clusters*: a heading plus member reports. The prompt
carries the grouping rules (`task_dimensions.txt`, the paper's task definition), guards against over-merging,
and keeps clusters separate on genuine doubt. Different draws can group the same input quite differently,
which is why an adopted list is amended rather than redrawn.

**Worker floor.** A group is kept only if its reports come from at least 2 sessions (`min_sources`); everything
else stays in the pool. The number of new clusters is the round's stopping signal.

#### 2 Synthesis: `synthesize`, then `fusion`

`synthesize` writes the statements. For each cluster, in order:

1. **Skip** if its members and the writer prompt are unchanged since it was written (stamps `written_for`,
   `written_with`).
2. **Continuity**: if the member set matches an adopted cluster (exactly, or sharing at least
   `families.SAME_FAMILY` = 60% of the larger side), the adopted statement is used as the draft instead of a
   fresh one.
3. **Draft** (Claude Sonnet, `synthesize.txt` + `writing_principles.txt`) from the reports; the heading is not
   shown. Sibling statements are supplied so the draft stays distinct from them.
4. **Coverage**: `judges.covers` over the cluster's own reports. A draft covering less than
   `chain.coverage_min` (60%) of them is redrafted once, naming the missed reports; the redraft may not fuse
   two duties.
5. **Fixed checks** (no model call): no **umbrella lead verb** (manage, handle, conduct, oversee, pursue, deal,
   ensure, use, utilize, execute, do); at most 25 words; at most one participial "-ing" clause. `perform` may
   lead only where O\*NET statements in the occupation's SOC major group pair it with one of the words that
   follow it.
6. **Writing judge** (`judges.writing_defects`, `writing_only.txt`): at most one defect per statement, counted
   at confidence ≥ `chain.gate_conf` (0.75).
7. **Repair** (`synthesize_repair.txt`): one defect per round, up to 4 rounds. A repair is refused (keeping the
   draft) if it changes nothing, the same defect fires again, or coverage falls.
8. A fusion the repair cannot fix ships with a ⚑ `form_flag` on the row, which makes the split hear the cluster
   again.

**Adopted statements are not machine-rewritten.** A statement from the adopted list still goes through these
checks, but any defect found is only reported in the run log; the statement stays as adopted.

`fusion` splits a statement that combines two duties. For each cluster, `judges.duties` (`one_duty.txt`) sees
the statement and its reports and says whether it is one duty or two, naming the parts and their reports.

- **Past verdicts**: a cluster whose member set already has a verdict in `ledgers/defuse_seen.json` reuses it
  (see below). A cluster carrying a ⚑ is heard again once. A verdict whose call failed is kept for this round
  but never saved, so it is asked again.
- **decide()** matches the parts to reports and applies the floors per part. Outcomes: `keep` (the reports don't
  partition as claimed, or no usable parts), or `split` with a basis: `exclusive-sources` / `shared-sources`
  (two published parts), `one-supported-part` (the cluster is narrowed), or `no-supported-part` (the cluster
  dissolves into the pool).
- **apply_splits**: publishable parts become clusters; reports in no published part go to the pool.

`synthesize` then runs again and writes statements for the new parts (every other cluster is skipped).

#### 3 Overlap resolution: `overlap`

(Appendix A calls this step *partitioning*.) Merges and rewordings:

- **Prefilter**: statement pairs with embedding cosine ≥ `chain.overlap_tau` (0.44).
- **`judges.overlaps`** asks only whether any act appears in both statements. A shared object, population,
  setting or tool is not a shared act. On doubt it answers yes: a wrong yes costs one closer look, a wrong no
  publishes an overlap. Colliding pairs form groups of at most `chain.overlap_group_cap` (10) statements,
  joined closest pair first.
- **merge_or_separate** (`resolve_group.txt`) sees every member list in a group and rules **MERGE** (with a
  basis: which reports are the same work), **SEPARATE**, or **KEEP**. Different people doing the work points to
  a merge into the abstraction; the same people, to separate statements. A statement the reply leaves out is
  kept; one it lists twice keeps its first decision.
- **Strain**: a pair that collides again after being reworded apart is shown to the resolver with the number of
  times it was (`ledgers/sharpen_seen.json`, below), as evidence the two are one task.
- **Merge guards**: a merge applies only if a second resolver draw merges the same member set
  (`chain.concurrence.merge`, on), and a merge of more than `chain.merge_cap` (4) statements is withheld.
  `synthesize` writes the merged statement.
- **Sharpen** (`sharpen.txt`, Claude Sonnet) rewords the SEPARATE statements of a group; KEEP neighbours are
  shown as frozen context and never rewritten. A fresh statement takes the rewrite; an adopted one is not
  rewritten, and the proposal is only reported.
- **The split check again** (`chain.recheck_merged`, on): the statements merges wrote go through `fusion`, and
  `synthesize` writes any parts. Every other cluster replays its verdict from the ledger.

#### 4 Coverage checking: `refile`

1. For each filed report, does its own statement still cover it (`covers`)? If not, it is *off-home*.
2. The **disposer** (`dispose.txt`, shown every published statement) rules on off-home reports and the pool:
   **FILE** under the statement that claims the act (the report moves there or stays), or **UNFILED** / **LEAVE**
   (an off-home report is released to the pool; a pool report stays there). A move needs two agreeing draws.

A report leaves its cluster only when coverage and the disposer agree. Clusters that fall below the worker floor
dissolve into the pool. This step never creates a cluster or writes a statement; the pool it leaves is what the
next round's clustering groups.

### Invariants (`verify`)

- **`filed + pool == n_atoms`.** Reports that leave a cluster are kept in the pool keys and forwarded by every
  step.
- **Prevalence follows the members.** `families.recount` recomputes `sources` and `prevalence` wherever a step
  rewrites members.
- No report in two clusters, no member that is not a report, no duplicate statement or statement id, every
  cluster at or above the worker floor.

### Artifacts and what carries between builds

Under `results/aggregation/<occ>/`:

| path | holds | keep? |
|---|---|---|
| `final.json` / `.csv` | the adopted list, the deliverable; nothing downstream reads a build artifact | yes |
| `build.json` | the latest build (`build.done` marks a completed one) | until adopted |
| `ledgers/defuse_seen.json` | every split verdict, with its statement and members | yes |
| `ledgers/sharpen_seen.json` | per pair of clusters, how many times they were reworded apart | yes |
| `stages/`, `verdicts/`, `handoffs/` | per-step snapshots, decisions, inter-step lists | per run (`rm -rf verdicts handoffs stages`) |

**What later rounds and builds remember (`ledgers/`).** Two files change later decisions, and both match
clusters by their member set rather than by wording:

- `defuse_seen.json`: whether each cluster is one duty or two. A cluster whose reports already have a verdict
  reuses it, even after its statement was reworded, so a statement is not split one build and kept the next
  just because the judge was asked again. The model cache cannot do this: it is keyed on the exact statement
  text. A ⚑ fusion flag from synthesize re-opens a cluster once (`reopened_on` records that).
- `sharpen_seen.json`: how many times each pair of clusters was reworded apart. The resolver sees the count
  (its prompt calls it STRAIN) when the pair collides again: a pair that keeps colliding however it is worded is
  probably one task.

Delete `ledgers/` to start an occupation's memory over. (An earlier release kept these as
`history/split_verdicts.json` and `history/reword_counts.json`; `build` copies them over once.) Each cluster also
carries stamps: `written_for` (member key), `written_with` (prompt hash) and `form_flag` (an open fusion flag ⚑).
Nothing in the chain reads human rulings: to disagree with a statement, edit or drop it in the adopted list.

**Adopting a build**: compare `build.json` with `final.json` (new, gone, grown and reworded clusters, matched by
member set), then `cp build.json final.json`.

## Judges and writers

Every model is a key under `models:` in `configs.yml`:

| role | model (key) | step |
|---|---|---|
| filter | gpt-5.4 (`filter`) | processing |
| split, normalize, brand | gpt-5.4-mini, effort low (`normalize`) | processing |
| grouping | gpt-5.4, effort medium (`cluster`) | cluster |
| writer | claude-sonnet-4-6 (`write`) | synthesize |
| writing judge, `judges.writing_defects` | gpt-5.4-mini (`writing`) | synthesize |
| coverage, `judges.covers` | gpt-5.4-mini (`covers`) | synthesize, refile |
| `judges.duties` | gpt-5.4-mini (`duties`) | fusion |
| `judges.overlaps` | gpt-5.4 (`overlaps`) | overlap |
| merge_or_separate | gpt-5.4 (`merge`) | overlap |
| sharpen | claude-sonnet-4-6 (`sharpen`) | overlap |
| disposer | gpt-5.4-mini (`dispose`) | refile |

`tasknet build --batch` sends the gpt judges through the OpenAI Batch API at half price, blocking until each
batch finishes.

## Settings

Every model, threshold and batching knob is a key in `aggregate/configs.yml`, with its default and a comment. To
change one, put the keys you change in a YAML file and point `TASKNET_CONFIG` at it; it overlays the defaults
key by key:

```yaml
models:
  write: {model: claude-opus-4-7}
chain:
  max_cycles: 2
```

Changing a model or prompt changes the cache key, so the next run pays for that judge again. The environment
holds the rest:

| variable | what |
|---|---|
| `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `CLAUDE_API_KEY`, `CLAUDE_BASE_URL`, `LLM_API_KEY`, `LLM_BASE_URL` | the keys and endpoints (`.env.example`) |
| `WORKSPACE`, `DATA_DIR`, `REFERENCE_DATA_DIR`, `LLM_CACHE_DIR`, `HELDOUT_FROZEN` | paths (relative ones are taken from the workspace) |
| `TASKNET_CONFIG` | a YAML overlaid on `configs.yml` |
| `PREWARM=0` | no prompt-cache warm-up calls |
| `SPEND_HOLDOUT=1` | rebuild a frozen list |
