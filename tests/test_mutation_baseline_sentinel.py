"""Round 68 站0 — the mutation baseline has to say its own name.

An external review of taskq-cc took 4 points off for "test environment
isolation": under a bare environment its `test_nfr_spec_coverage.py` behaves
differently from under `make verify-system`. It is true, and the cause is on
this side of the line.

`_mutmut_subprocess_env` re-runs the project's own test suite once per mutant
with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Four of taskq-cc's acceptance tests
have to exclude themselves from that run — one shells out to a nested pytest,
one re-runs the whole suite through `make`, two assert on artifacts the
mutation dimension itself produces. The framework gives them nothing true to
ask, so they ask this:

    if os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") == "1":
        pytest.skip(...)                      # x4, lines 677/689/844/963

That variable means "plugin autoload is off". It does not mean "you are the
mutation baseline". The project is reading a side effect as a signal because
the signal does not exist — and its own NFR-09 is a zero-skip rule, so the
framework's environment is what put four skips in a suite that forbids them.

The same absence shows up in the registry: docs/CONFIGURATION.md lists every
env var the framework READS and none of the eight it WRITES into a project's
subprocess. A variable this framework exports into the project's own test
process is exactly as much of a public interface as one it consumes, and
`PYTEST_DISABLE_PLUGIN_AUTOLOAD` being absent from the registry is why it was
available to be repurposed.

Second half, same station: `[mutmut] paths_to_mutate` is rendered from the SAB
by `_regenerate_mutmut_scope`, and `[mutmut] runner` — which decides WHICH
tests get to kill a mutant, and therefore what the score is — is left entirely
to the project. taskq-cc's is hand-written and carries
`-k "not test_ac_n and not test_perf_"` plus a machine-absolute interpreter
path. Narrowing is sometimes genuinely necessary (a test that re-runs the
suite cannot be in the mutant set), so this is recorded rather than blocked —
but it stops being invisible.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "CONFIGURATION.md"

SENTINEL = "HARNESS_MUTATION_BASELINE"


def test_the_baseline_env_carries_a_sentinel_that_means_what_it_says(tmp_path):
    from core.quality_gate.mutation_enforcer import _mutmut_subprocess_env

    env = _mutmut_subprocess_env(str(tmp_path))

    assert env.get(SENTINEL) == "1", (
        f"the mutation baseline runs a project's suite without telling it. A "
        f"test that must not recurse has to key on something, and today the "
        f"only thing available is PYTEST_DISABLE_PLUGIN_AUTOLOAD, whose "
        f"meaning is unrelated. Got: {sorted(k for k in env if 'HARNESS' in k)}"
    )
    assert env.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") == "1", (
        "the sentinel must be added beside the sandbox, not instead of it — "
        "Bug #142's default-deny is still load-bearing"
    )


def _written_env_names() -> dict[str, set[str]]:
    """Every `env["NAME"] = ...` the production packages perform.

    Derived from the code rather than from a list of blessed names, for the
    reason test_configuration_doc.py's `_env_wrappers` gives about reads: the
    next one someone writes is covered the day it is written.
    """
    found: dict[str, set[str]] = {}
    for base in ("cli", "core", "harness", "scripts", "detection"):
        for f in (REPO / base).rglob("*.py"):
            try:
                tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            except (SyntaxError, ValueError, OSError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if not (isinstance(target, ast.Subscript)
                            and isinstance(target.slice, ast.Constant)
                            and isinstance(target.slice.value, str)):
                        continue
                    holder = getattr(target.value, "id", None) or getattr(
                        target.value, "attr", None)
                    if holder in ("env", "environ", "_env", "new_env"):
                        found.setdefault(target.slice.value, set()).add(
                            str(f.relative_to(REPO)))
    return found


def test_every_env_var_the_framework_exports_is_registered():
    """docs/CONFIGURATION.md's env registry covered reads only.

    Measured at the time this was written: eight variables are written into a
    subprocess environment by the production packages and zero of them appear
    in the registry — including the one taskq-cc reverse-engineered into a
    semantic signal.
    """
    doc = DOC.read_text(encoding="utf-8")
    missing = sorted(k for k in _written_env_names() if f"`{k}`" not in doc)
    assert not missing, (
        f"env vars the framework exports into a subprocess and never "
        f"registered: {missing}. A project cannot be told not to depend on an "
        f"interface nobody wrote down"
    )


def _setup_cfg(tmp_path: Path, runner: str) -> Path:
    (tmp_path / "setup.cfg").write_text(
        "[mutmut]\n"
        "paths_to_mutate=src\n"
        f"runner={runner}\n",
        encoding="utf-8",
    )
    (tmp_path / ".methodology").mkdir(exist_ok=True)
    return tmp_path


def _ledger(project: Path) -> str:
    path = project / ".methodology" / "degradations.jsonl"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def test_a_runner_that_narrows_the_mutant_killers_is_recorded(tmp_path):
    """taskq-cc's, verbatim minus the interpreter."""
    from core.quality_gate.mutmut_scope import record_runner_scope

    project = _setup_cfg(
        tmp_path,
        'python -m pytest --tb=no -q -k "not test_ac_n and not test_perf_"')
    findings = record_runner_scope(project)

    assert any("-k" in f for f in findings), (
        f"the runner filters which tests may kill a mutant — that decides the "
        f"score — and nothing said so: {findings}"
    )
    assert "mutation:runner" in _ledger(project), (
        "the finding did not reach the ledger, so it is answerable only by "
        "someone who was watching the console"
    )


def test_a_runner_pinned_to_an_interpreter_outside_the_project_is_recorded(tmp_path):
    """taskq-cc committed `/Users/johnny/projects/taskq-cc/.venv/bin/python`.

    Correct on exactly one machine. mutmut runs the runner from an ephemeral
    workdir, which is why a project reaches for an absolute path; that is the
    framework's workdir, so the consequence is the framework's to report.
    """
    from core.quality_gate.mutmut_scope import record_runner_scope

    project = _setup_cfg(tmp_path, "/opt/elsewhere/.venv/bin/python -m pytest -q")
    findings = record_runner_scope(project)

    assert any("/opt/elsewhere" in f for f in findings), (
        f"a committed interpreter path outside the project went unreported: "
        f"{findings}"
    )


def test_a_plain_runner_is_not_reported(tmp_path):
    from core.quality_gate.mutmut_scope import record_runner_scope

    project = _setup_cfg(tmp_path, "python -m pytest --tb=no -q -p no:cacheprovider")

    assert record_runner_scope(project) == [], (
        "a runner that narrows nothing was reported anyway — a checker that "
        "fires on everything is one nobody reads"
    )
    assert "mutation:runner" not in _ledger(project)


def test_a_project_with_no_setup_cfg_is_not_reported(tmp_path):
    """No config is not a narrow config. Round 46's rule."""
    from core.quality_gate.mutmut_scope import record_runner_scope

    (tmp_path / ".methodology").mkdir()
    assert record_runner_scope(tmp_path) == []


def test_the_regenerate_hook_asks_before_its_own_early_returns():
    """`_regenerate_mutmut_scope` returns early four times — no SAB.json, an
    unreadable SAB, no `scope_layers`, an unresolvable path. The runner is a
    fact about the project in every one of those states, so the question has
    to be asked before them, not after.
    """
    import inspect

    from cli.phase_cmds import _regenerate_mutmut_scope

    src = inspect.getsource(_regenerate_mutmut_scope)
    call = src.find("record_runner_scope")
    first_return = min(
        (m.start() for m in re.finditer(r"^\s+return\b", src, re.MULTILINE)),
        default=-1,
    )
    assert call != -1, "_regenerate_mutmut_scope does not ask about the runner"
    assert call < first_return, (
        "the runner check sits after an early return, so a project without a "
        "SAB — which is the project most likely to have hand-written its "
        "runner — is never asked"
    )
