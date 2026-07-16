"""Round 12 站0a guard: dispatch-prompt commands must be allowlist-compatible.

Production incident (2026-07-16 P3 run): dispatch prompts told spawned
agents to run bare `pytest tests/ -q` / `ruff check ...` while the target
project's `.claude/settings.local.json` allowlist only covered
`Bash(python3 *)` / `Bash(git add *)` / `Bash(git commit -m ' *)` /
`Bash(git push *)`. Under a permission-restricted spawned session the
agents hit the wall, stalled awaiting an approval that cannot come in a
headless pipeline, and the per-FR loop burned 140 dispatches / ~2.5h on
FR-01 alone.

Fix: every agent-executed command in prompt text uses the `python3 -m`
module form (pytest/ruff/coverage/pyright/bandit/radon/detect_secrets all
ship a `__main__`), and `git commit -m` uses the single-quote form the
allowlist pattern matches. These tests scan the three prompt-text sources
so a future prompt edit cannot silently regress to a bare command form.

Known allowlist-INCOMPATIBLE commands deliberately left as-is (no
python3-module form exists): gitleaks, semgrep, npx-driven JS/TS tools,
`node benchmarks/run.mjs`, and the NON_CODE_FR `echo` pseudo-command —
documented in docs/CONVERGENCE_AUDIT_2026-07.md.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Backtick-quoted commands in prompt f-strings: `pytest ...` / `ruff ...`
# must be `python3 -m pytest ...` / `python3 -m ruff ...`.
_BARE_BACKTICK_CMD_RE = re.compile(r"`(pytest|ruff|coverage|bandit)\s")

# git commit double-quote form does not match the allowlist pattern
# Bash(git commit -m ' *). Prompts must use the single-quote form.
_GIT_COMMIT_DOUBLE_QUOTE_RE = re.compile(r'git commit -m \\"')


def test_fr_cmds_prompts_use_module_form():
    src = (REPO / "cli" / "fr_cmds.py").read_text(encoding="utf-8")
    offenders = [
        f"line {i}: {line.strip()[:90]}"
        for i, line in enumerate(src.splitlines(), 1)
        if _BARE_BACKTICK_CMD_RE.search(line)
    ]
    assert not offenders, (
        "bare tool command in fr_cmds prompt text (agents copy these "
        "verbatim; use `python3 -m <tool> ...`):\n" + "\n".join(offenders)
    )


def test_fr_cmds_git_commit_uses_single_quote_form():
    src = (REPO / "cli" / "fr_cmds.py").read_text(encoding="utf-8")
    offenders = [
        f"line {i}" for i, line in enumerate(src.splitlines(), 1)
        if _GIT_COMMIT_DOUBLE_QUOTE_RE.search(line)
    ]
    assert not offenders, (
        "git commit -m with double quotes in prompt text — the allowlist "
        f"pattern is `git commit -m ' *` (single quote): {offenders}"
    )


def test_gate_cmds_fr_scoped_overrides_use_module_form():
    """The FR-SCOPED TOOL OVERRIDES block is what the GATE1 evaluator agent
    actually executes (it failed 29× on 2026-07-16). Python-path override
    commands must be module-form."""
    src = (REPO / "cli" / "gate_cmds.py").read_text(encoding="utf-8")
    for banned in (
        "\n  ruff check ",
        "\n  pyright ",
        '"  coverage run',
        "PYTHONPATH=. coverage run",
    ):
        assert banned not in src, (
            f"bare tool command {banned!r} in gate_cmds FR-scoped overrides — "
            "use python3 -m module form"
        )


def test_evaluate_dimension_python_commands_use_module_form():
    """evaluate_dimension.md is the per-dimension tool-command SSOT quoted
    into gate prompts. Its python-toolchain commands must be module-form;
    JS/TS npx/node/semgrep/gitleaks lines have no module form and are
    exempt (see module docstring)."""
    text = (REPO / "harness" / "ssi" / "prompts" / "evaluate_dimension.md").read_text(
        encoding="utf-8"
    )
    offenders = [
        f"line {i}: {line[:90]}"
        for i, line in enumerate(text.splitlines(), 1)
        if re.match(r"^(pytest|ruff|coverage|bandit|radon|detect-secrets|pyright)\s", line)
    ]
    assert not offenders, (
        "bare python-toolchain command in evaluate_dimension.md:\n"
        + "\n".join(offenders)
    )
