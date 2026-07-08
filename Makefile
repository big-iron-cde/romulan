.PHONY: docs docs-serve

PORT ?= 8000

docs:
	uv sync --group docs
	uv run sphinx-build -W -b html docs docs/_build/html

docs-serve: docs
	@echo "Docs at http://127.0.0.1:$(PORT)/  (Ctrl-C to stop)"
	uv run python -m http.server $(PORT) --bind 127.0.0.1 --directory docs/_build/html
