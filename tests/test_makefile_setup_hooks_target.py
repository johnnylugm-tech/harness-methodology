"""PR 10: Makefile `setup-hooks` and `setup` target test.

Regression test: the Makefile must contain `setup-hooks:` and `setup:`
targets that invoke `scripts/setup-git-hooks.sh`.
"""
from pathlib import Path


def test_makefile_has_setup_hooks_target():
    makefile = Path(__file__).resolve().parent.parent / "Makefile"
    text = makefile.read_text(encoding="utf-8")
    assert "setup-hooks:" in text, "Makefile must define `setup-hooks:` target"
    assert "setup-git-hooks.sh" in text, \
        "setup-hooks target must call setup-git-hooks.sh"


def test_makefile_has_setup_target():
    makefile = Path(__file__).resolve().parent.parent / "Makefile"
    text = makefile.read_text(encoding="utf-8")
    # `setup:` target (chained from setup-hooks)
    assert "\nsetup:" in text or " setup:" in text, \
        "Makefile must define `setup:` target"


def test_makefile_setup_targets_in_phony():
    makefile = Path(__file__).resolve().parent.parent / "Makefile"
    text = makefile.read_text(encoding="utf-8")
    phony_line = next((line for line in text.splitlines() if line.startswith(".PHONY:")), None)
    assert phony_line is not None
    assert "setup-hooks" in phony_line, f"setup-hooks must be in .PHONY; got: {phony_line}"
    assert "setup" in phony_line, f"setup must be in .PHONY; got: {phony_line}"


def test_makefile_help_lists_setup_targets():
    makefile = Path(__file__).resolve().parent.parent / "Makefile"
    text = makefile.read_text(encoding="utf-8")
    assert "setup-hooks" in text
    assert "setup" in text
