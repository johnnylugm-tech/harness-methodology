"""PR 8: Makefile `attest` target test.

Regression test: the Makefile must contain an `attest:` target that
calls `harness_cli.py build-trace-attestation` and stages the result.
This guards against accidental Makefile edits that silently remove
the developer workflow.
"""
from pathlib import Path


def test_makefile_has_attest_target():
    makefile = Path(__file__).resolve().parent.parent / "Makefile"
    text = makefile.read_text(encoding="utf-8")
    assert "attest:" in text, "Makefile must define `attest:` target"
    assert "build-trace-attestation" in text, \
        "attest target must call build-trace-attestation"
    assert "attestation.json" in text, \
        "attest target must reference attestation.json"
    assert "git add" in text, \
        "attest target must git add the new attestation.json"


def test_makefile_attest_target_in_phony():
    makefile = Path(__file__).resolve().parent.parent / "Makefile"
    text = makefile.read_text(encoding="utf-8")
    # `.PHONY: ... attest ...` — locate the .PHONY line and confirm attest is there
    phony_line = next((l for l in text.splitlines() if l.startswith(".PHONY:")), None)
    assert phony_line is not None, "Makefile must have a .PHONY line"
    assert "attest" in phony_line, f"attest must be in .PHONY; got: {phony_line}"


def test_makefile_help_lists_attest():
    makefile = Path(__file__).resolve().parent.parent / "Makefile"
    text = makefile.read_text(encoding="utf-8")
    # The help target prints target names; attest should appear there
    assert "attest" in text, "Makefile must mention attest (in help)"


def test_makefile_attest_guards_git_repo():
    """attest must error out if not in a git repo (git add would fail)."""
    makefile = Path(__file__).resolve().parent.parent / "Makefile"
    text = makefile.read_text(encoding="utf-8")
    # The attest target should have a guard like `@if [ ! -d .git ]; then ...`
    # before the `git add` line
    attest_block = text.split("attest:", 1)[1].split("\n\n", 1)[0]
    assert ".git" in attest_block, \
        "attest target must check for .git before running git add"
