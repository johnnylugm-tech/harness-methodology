"""Round 104 站2 — "where is this project's Python" had eight answers.

`core/utils/venv_env.py` exists because two call sites had each solved this
separately. Its module docstring says so, and says what it is for:

    this module is the shared `.venv`/`venv` bin-dir probe both were
    duplicating, so a third call site does not duplicate it again.

There were eight. The module supplies the BIN DIRECTORY and stops there, so
every caller that actually wanted an interpreter appended the last segment
itself, and they disagreed on three things:

    core/utils/venv_env.py:19        .venv venv Windows   — bin dir only
    verify_system_reach.py:349       .venv venv Windows   python
    env_verify.py:233                .venv venv Windows   python3
    env_verify.py:283                .venv venv Windows   python
    bootstrap_env.py:198             .venv venv Windows   python
    ensure_project_init.py:87        .venv       Windows  python
    ensure_project_init.py:182       .venv       Windows  python
    ssot_manifest.py:581 (Round 101) .venv                python

The last one is this round's own doing, and it is the weakest of the eight:
`project / ".venv" / "bin" / "python"`, which abstains on Windows and on any
project whose virtualenv is `venv/`. `env_verify`'s two copies sit fifty lines
apart in one file and spell the executable differently.

The fix is `find_venv_python`, the missing half, deliberately the UNION of all
eight behaviours so adopting it cannot turn any site's "found" into "not
found" — measured across 25 projects: zero regressions, and zero widenings
either, because every corpus venv is `.venv` and carries both `python` and
`python3`. Today this changes nothing; what it changes is where the ninth call
site gets its answer.

Two AST rules, because the eight sites are written two ways. Measured against
the tree before the sweep: 9 hits covering all seven non-SSOT sites, and zero
false positives on `env_verify.py:162` or `agent_spawner.py:466`, which build
a bin directory (for PATH) and name no interpreter.

Known limit, stated rather than papered over: both rules read string LITERALS.
`vd = ".venv"` followed by `project / vd / "bin" / "python"` is the same defect
and is invisible here. Constant folding across statements is not something an
AST walk does, and pretending otherwise would make this guard's silence mean
less than it does.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]

_PYTHON_EXE = {"python", "python3", "python.exe"}
_VENV_DIR = {".venv", "venv"}

#: The SSOT itself, and the only file allowed to name an interpreter filename
#: next to a virtualenv directory.
_ALLOWED = {"core/utils/venv_env.py"}

_SCANNED = ("cli", "core", "harness", "scripts", "detection")


def _strings(node: ast.AST) -> "set[str]":
    return {n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def hand_built_interpreter_paths(root: Path) -> "list[str]":
    """Every place that spells out a venv interpreter instead of asking."""
    hits: list[str] = []
    for directory in _SCANNED:
        for path in sorted((root / directory).rglob("*.py")):
            rel = str(path.relative_to(root))
            if rel in _ALLOWED:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, OSError):
                continue
            for node in ast.walk(tree):
                # Rule A — the `"python.exe" if windows else "python"` idiom.
                # Five of the eight sites were written this way.
                if isinstance(node, ast.IfExp) and "python.exe" in _strings(node):
                    hits.append(f"{rel}:{node.lineno}  "
                                f"chooses an interpreter filename by platform")
                # Rule B — a `/` chain naming both a venv dir and an executable.
                elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                    found = _strings(node)
                    if (found & _VENV_DIR) and (found & _PYTHON_EXE):
                        hits.append(f"{rel}:{node.lineno}  "
                                    f"builds a venv interpreter path by hand")
    return sorted(hits)


def test_nothing_builds_a_venv_interpreter_path_by_hand() -> None:
    """One place answers it; everywhere else asks.

    Round 101 added the eighth answer while fixing a different instance of
    "the framework filled in something it did not know", which is why this is
    a guard and not a note in a docstring.
    """
    offenders = hand_built_interpreter_paths(REPO)
    assert not offenders, (
        "these resolve a project's virtualenv interpreter themselves instead "
        "of calling core.utils.venv_env.find_venv_python(), which is how the "
        "eight copies came to disagree about `venv/` and about Windows:\n  "
        + "\n  ".join(offenders)
        + "\n    → call find_venv_python(project); it returns None when there "
          "is no virtualenv.")


def test_the_ssot_is_what_the_callers_actually_use(tmp_path) -> None:
    """Counter-proof CP-4's shape, as a test rather than a manual step.

    A guard that only checks "nobody spells it out" passes just as well when
    every caller has its own correct-but-separate helper. Replacing the single
    definition has to change what a consumer says — the failure Round 97 CP-5b,
    Round 98 CP-11 and Round 99 CP-13b each found in turn.
    """
    import core.utils.venv_env as venv_env
    from harness.ssot_manifest import manifest_missing_declared_tools

    project = tmp_path / "proj"
    (project / ".methodology").mkdir(parents=True)
    (project / ".methodology" / "env_contract.json").write_text(
        '{"cli_tools": ["ruff"]}', encoding="utf-8")
    (project / ".venv" / "bin").mkdir(parents=True)
    (project / ".venv" / "bin" / "python").write_text("", encoding="utf-8")

    calls: list = []
    real = venv_env.find_venv_python

    def spy(root):
        calls.append(Path(root))
        return real(root)

    venv_env.find_venv_python = spy  # type: ignore[assignment]
    try:
        manifest_missing_declared_tools(project)
    finally:
        venv_env.find_venv_python = real  # type: ignore[assignment]

    assert calls, (
        "manifest_missing_declared_tools did not go through "
        "core.utils.venv_env.find_venv_python — it is answering the question "
        "locally again, which is the state this round removed")


# ── the SSOT's own contract ────────────────────────────────────────────────

def test_it_finds_the_dot_venv_layout(tmp_path) -> None:
    from core.utils.venv_env import find_venv_python

    exe = tmp_path / ".venv" / "bin" / "python"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    assert find_venv_python(tmp_path) == exe


def test_it_finds_the_plain_venv_layout(tmp_path) -> None:
    """The case Round 101's copy could not see. No corpus project uses it, so
    this is the only place it is exercised — which is why it is pinned."""
    from core.utils.venv_env import find_venv_python

    exe = tmp_path / "venv" / "bin" / "python"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    assert find_venv_python(tmp_path) == exe


def test_it_accepts_python3_when_python_is_absent(tmp_path) -> None:
    """`env_verify.py:233` looked for `python3` and its neighbour fifty lines
    down looked for `python`. The union keeps both, so folding them together
    cannot make either site stop finding a venv it used to find."""
    from core.utils.venv_env import find_venv_python

    exe = tmp_path / ".venv" / "bin" / "python3"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    assert find_venv_python(tmp_path) == exe


def test_no_virtualenv_is_none_not_a_guess(tmp_path) -> None:
    """Reverse control. A project without a virtualenv has no interpreter to
    name, and every caller branches on that — abstaining is the answer, not a
    path that happens not to exist."""
    from core.utils.venv_env import find_venv_python

    assert find_venv_python(tmp_path) is None


def test_a_bin_dir_without_an_interpreter_is_none(tmp_path) -> None:
    """`find_venv_bin_dir` says a directory exists; that is not the same claim.
    A half-built venv must not be reported as an interpreter."""
    from core.utils.venv_env import find_venv_python

    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    assert find_venv_python(tmp_path) is None
