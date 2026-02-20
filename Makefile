.PHONY: run test lint
run:
	hypercorn app.main:app -b 0.0.0.0:8000

test:
	pytest backend/tests -q
