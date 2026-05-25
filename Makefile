.PHONY: test eval check eval-case

test:
	pytest -q

eval:
	python -m core.eval.runner --suite golden

check:
	pytest -q
	python -m core.eval.runner --suite golden

eval-case:
ifndef CASE
	$(error CASE is required. Example: make eval-case CASE=analyst_artifact_probe)
endif
	python -m core.eval.runner --case $(CASE)
