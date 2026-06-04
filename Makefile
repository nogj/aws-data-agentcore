.PHONY: test lint

test:
	pytest -q

lint:
	ruff check app main.py tests scripts/configure_gateway_target.py

