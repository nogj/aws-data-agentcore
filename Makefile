.PHONY: test lint

test:
	../venv/bin/python -m pytest -q

lint:
	../venv/bin/python -m ruff check app main.py tests scripts
