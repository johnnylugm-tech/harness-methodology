# harness-methodology development helpers
# Requires Python 3.10+ (pyproject.toml: requires-python = ">=3.10")
#
# Usage:
#   make test          — check Python version, then run full test suite
#   make lint          — check Python version, then run ruff
#   make check-python  — version check only (useful for CI debugging)
#
# Override the Python interpreter:
#   make PYTHON=/opt/homebrew/bin/python3.11 test

PYTHON     ?= python3
PYTHON_MIN := 3.10

.PHONY: help check-python test lint

help:
	@echo "Targets:"
	@echo "  test          Run full test suite (requires Python $(PYTHON_MIN)+)"
	@echo "  lint          Run ruff linter"
	@echo "  mutation      Run mutmut mutation testing"
	@echo "  check-python  Verify Python $(PYTHON_MIN)+ is in use"
	@echo ""
	@echo "Override interpreter:  make PYTHON=/usr/local/bin/python3.11 test"

check-python:
	@$(PYTHON) -c "\
import sys; \
v = sys.version_info; \
ok = v >= (3, 10); \
status = 'OK' if ok else 'FAIL — 3.10+ required'; \
print(f'Python {v.major}.{v.minor}.{v.micro}  [{sys.executable}]  {status}'); \
sys.exit(0 if ok else 1)" || { \
	echo ""; \
	echo "Fix: ensure 'python3' in PATH resolves to 3.10+."; \
	echo "  macOS Homebrew:  export PATH=\"/opt/homebrew/opt/python@3.11/bin:\$$PATH\""; \
	echo "  Or override:     make PYTHON=/opt/homebrew/bin/python3.11 test"; \
	exit 1; \
}

test: check-python
	$(PYTHON) -m pytest

lint: check-python
	$(PYTHON) -m ruff check .

mutation: check-python
	@echo "Running mutation testing (baseline 10s)..."
	mutmut run -b 10
