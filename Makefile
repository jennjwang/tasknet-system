# TaskNet System — the pipeline. The tests need neither the data package nor any keys.
#
#   make setup       submodule + venv with aggregate (aggregate/) and the tasknet package + npm workspace install
#   make test        aggregate's tests, tasknet's tests, then the Node test suite of interviewer/
#   make onet        download the O*NET reference files into data/onet/ (CC BY 4.0, not stored here)
#   make process OCC=<slug>   process one occupation's sessions (ingest -> normalize -> dedup)
#   make list OCC=<slug>      build the task list for one occupation (LLM calls; aggregate/aggregate/aggregation/chain.py)
#   make audit OCC=<slug>     check no session task was lost in processing (no API calls)

VENV ?= .venv
PY   ?= $(CURDIR)/$(VENV)/bin/python
OCC  ?= accountants-and-auditors
# PYTHONPATH as a fallback for interpreters that skip editable-install .pth files
export PYTHONPATH := $(CURDIR):$(CURDIR)/aggregate

.PHONY: setup test onet process list audit

setup:
	git submodule update --init
	test -d $(VENV) || python3 -m venv $(VENV)
	$(PY) -m pip install -q -e './aggregate[test]' -e '.[test]'
	npm install --no-audit --no-fund

test:
	cd aggregate && $(PY) -m pytest -q
	$(PY) -m pytest -q
	npm test

onet:
	$(PY) -m tasknet.cli onet

# Reads $DATA_DIR/interviews/$(OCC)/. Deterministic apart from its LLM rewrites, which are cached under cache/.
process:
	$(PY) -m tasknet.cli process $(OCC)

list:
	$(PY) -m tasknet.cli build $(OCC)

audit:
	$(PY) -m tasknet.cli audit $(OCC)
