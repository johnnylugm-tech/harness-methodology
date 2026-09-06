# harness-methodology development helpers
# Requires Python 3.11+ (pyproject.toml: requires-python = ">=3.11")
#
# Usage:
#   make test          — check Python version, then run full test suite
#   make lint          — check Python version, then run ruff
#   make check-python  — version check only (useful for CI debugging)
#   make attest        — regenerate .methodology/trace/attestation.json (PR 8)
#   make setup-hooks   — install pre-push hook (PR 10)
#   make setup         — full repo bootstrap (setup-hooks)
#
# Override the Python interpreter:
#   make PYTHON=/opt/homebrew/bin/python3.11 test

PYTHON     ?= python3
PYTHON_MIN := 3.11

.PHONY: help check-python test lint mutation attest setup-hooks setup

help:
	@echo "Targets:"
	@echo "  test          Run full test suite (requires Python $(PYTHON_MIN)+)"
	@echo "  lint          Run ruff linter"
	@echo "  mutation      Run mutmut mutation testing"
	@echo "  attest        Regenerate + stage trace attestation.json (PR 8)"
	@echo "  setup-hooks   Install git pre-push hook (PR 10)"
	@echo "  setup         Full repo bootstrap (setup-hooks)"
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

# PR 8: regenerate trace attestation.json (git-anchored) and stage it.
# The attestation is bound to git_sha; every commit invalidates it.
# After running this, the developer commits and pushes — CI's verify-trace
# step exits 0 only when the committed attestation matches re-derivation.
attest: check-python
	@if [ ! -d .git ]; then echo "ERROR: not a git repo"; exit 1; fi
	$(PYTHON) harness_cli.py build-trace-attestation --project .
	git add .methodology/trace/attestation.json
	@echo "attestation.json staged. Run: git commit"

# PR 10: install git hooks (pre-push runs full preflight).
# Idempotent — re-running just overwrites the existing hook files.
setup-hooks: check-python
	@if [ ! -d .git ]; then echo "ERROR: not a git repo"; exit 1; fi
	bash scripts/setup-git-hooks.sh
	@echo "Git hooks installed. pre-push now runs full preflight."

# PR 10: full repo bootstrap. Currently equivalent to setup-hooks; reserved
# for future bootstrap steps (e.g., initial attestation build).
setup: setup-hooks
	@echo "Repo bootstrap complete. Pre-push hook enforces preflight on every push."
