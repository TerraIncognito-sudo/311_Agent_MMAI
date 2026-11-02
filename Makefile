PY ?= python

.PHONY: install serve ingest test reproduce

install:
	$(PY) -m pip install -r requirements.txt

serve:
	$(PY) run.py serve

ingest:
	$(PY) run.py ingest

test:
	$(PY) run.py test

# W5/W6: regenerates every results table from cached model outputs (stub until harness v2)
reproduce:
	@echo "not implemented yet: eval harness v2 (src/eval/), due Nov 18"
