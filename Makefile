.PHONY: run test lint
run:
	hypercorn app.main:app -b 0.0.0.0:${APP_PORT:-8800}

test:
	pytest backend/tests -q
