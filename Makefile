# TaskNet System — the pipeline. The tests need neither the data package nor any keys.
#
#   make setup       venv with the tasknet package (source in aggregate/) + npm workspace install
#   make test        the Node test suite of interviewer/
#   make onet        download the O*NET reference files into data/onet/ (CC BY 4.0, not stored here)
#   make process OCC=<slug>   process one occupation's sessions (ingest -> normalize -> dedup)
#   make list OCC=<slug>   build the task list for one occupation (LLM calls; see aggregate/aggregation/chain.py)

VENV ?= .venv
PY   ?= $(CURDIR)/$(VENV)/bin/python
OCC  ?= accountants-and-auditors
# No prompt-cache warm-up calls (aggregate/engine.py); set PREWARM=1 to turn them back on.
export PREWARM ?= 0

.PHONY: setup test onet process list

setup:
	test -d $(VENV) || python3 -m venv $(VENV)
	$(PY) -m pip install -q -e .
	npm install --no-audit --no-fund

test:
	npm test

onet:
	$(PY) aggregate/fetch_onet.py

# Reads $DATA_DIR/interviews/$(OCC)/. Deterministic apart from its LLM rewrites, which are cached under cache/.
process:
	$(PY) -m tasknet.cli process $(OCC)

list:
	$(PY) -m tasknet.cli build $(OCC)
