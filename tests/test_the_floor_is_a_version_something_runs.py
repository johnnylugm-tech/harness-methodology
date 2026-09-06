"""Round 104 站1 — the declared Python floor was a version nothing ran.

`harness/ssot_manifest.py` imported `tomllib` at the top of a branch with no
guard. `tomllib` is 3.11+. `pyproject.toml` said `requires-python = ">=3.10"`,
`harness_cli.py:168` let 3.10 through, and `scripts/bootstrap_env.py` would
build a venv from a 3.10 interpreter — which every generated workflow then
uses as `PY`. Reproduced by making `tomllib` unimportable: the exception
escapes `manifest_missing_declared_tools` and is caught only at
`harness_cli.py`'s crash boundary, so P3 exit was exit 70 for any project with
a `pyproject.toml`.

The eight places that stated the floor did NOT disagree with each other — all
eight said 3.10, and `bootstrap_env.py`'s "Same floor harness_cli.py enforces"
comment was true. The defect is that the agreed answer had no witness: CI runs
3.11 in all five jobs, this framework's own venv is 3.11, all nineteen corpus
venvs are 3.11, and `templates/harness_quality_gate.yml` — the CI this
framework SHIPS to projects — already required 3.11 in four places. Nothing
had ever executed this framework on the version it claimed to support, and two
code paths were broken there: this crash, and `testpaths_scope._toml_testpaths`
returning None on ImportError, which its own caller's docstring forbids
("None means 'no file here says which tests count' — never 'the answer is the
empty set'").

Three rules, and they are not equally load-bearing:

  1. The eight statements agree. GREEN before this round — it locks the fix in
     so nobody changes one literal and not the other seven. It is not what
     would have found this.
  2. The floor is a version CI actually executes the suite on. RED before this
     round: floor 3.10, CI 3.11. This is the mechanism that was missing, and
     it is an execution proof rather than a static model of which stdlib
     module arrived when (Round 50) — `test_pyproject_dependencies_count_as_
     declared` walks the crashing line, so a CI job on the floor runs it.
  3. A `sys.version_info < X` branch with `X <= floor` can never fire. Raising
     a floor strands the code that only ran below it (Round 39); this names
     it instead of leaving it to be noticed. It found
     `cli/advance_prechecks.py:893`'s 3.10-only coverage hint.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]

#: The two floor gates themselves. Both run on an interpreter that has NOT yet
#: been vetted — that is their whole purpose — so their comparison against the
#: floor is live code, not a stranded branch.
_FLOOR_GATES = {"harness_cli.py", "scripts/bootstrap_env.py"}

_SCANNED = (".", "cli", "core", "harness", "scripts", "detection")


def _version_tuple(text: str) -> "tuple[int, int] | None":
    match = re.search(r"(\d+)\.(\d+)", text)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _ruff_target(value: str) -> "tuple[int, int] | None":
    """`py310` is 3.10, not 3.310 — the minor is the whole remaining run.

    Written the naive way first (strip "py", insert a dot), which read
    `target-version = "py310"` as Python 3.310 and made every other rule in
    this file fail for the wrong reason.
    """
    match = re.fullmatch(r"py(\d)(\d+)", value)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _const_pair(elts) -> "tuple[int, int] | None":
    if len(elts) < 2 or not all(isinstance(e, ast.Constant) for e in elts[:2]):
        return None
    return (elts[0].value, elts[1].value)


def _tuple_from(path: str, pick) -> "tuple[int, int] | None":
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        got = pick(node)
        if got is not None:
            return got
    return None


def _version_info_gate(node) -> "tuple[int, int] | None":
    if (isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Attribute)
            and node.left.attr == "version_info"
            and isinstance(node.comparators[0], ast.Tuple)):
        return _const_pair(node.comparators[0].elts)
    return None


def _min_python_assign(node) -> "tuple[int, int] | None":
    if (isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_MIN_PYTHON"
                    for t in node.targets)
            and isinstance(node.value, ast.Tuple)):
        return _const_pair(node.value.elts)
    return None


def _first_match(rel: str, pattern: str) -> "tuple[int, int] | None":
    match = re.search(pattern, (REPO / rel).read_text(encoding="utf-8"))
    return _version_tuple(match.group(1)) if match else None


def floor_statements() -> "dict[str, tuple[int, int] | None]":
    """Every place this repository states which Python it requires."""
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    classifiers = sorted(
        v for c in pyproject["project"].get("classifiers", [])
        if (m := re.fullmatch(r"Programming Language :: Python :: (\d+\.\d+)", c))
        and (v := _version_tuple(m.group(1))))

    return {
        "pyproject.toml::requires-python":
            _version_tuple(pyproject["project"]["requires-python"]),
        "pyproject.toml::classifiers (lowest)":
            classifiers[0] if classifiers else None,
        "pyproject.toml::tool.ruff.target-version":
            _ruff_target(pyproject["tool"]["ruff"]["target-version"]),
        "harness_cli.py::sys.version_info gate":
            _tuple_from("harness_cli.py", _version_info_gate),
        "scripts/bootstrap_env.py::_MIN_PYTHON":
            _tuple_from("scripts/bootstrap_env.py", _min_python_assign),
        "tests/test_spec_contract.py::mypy --python-version":
            _first_match("tests/test_spec_contract.py",
                         r'"--python-version",\s*"(\d+\.\d+)"'),
        "harness/ssi/scripts/verify_tools.py::CORE_BY_LANG":
            _first_match("harness/ssi/scripts/verify_tools.py",
                         r'"Python (\d+\.\d+)\+"'),
        "docs/USER_MANUAL.md":
            _first_match("docs/USER_MANUAL.md", r"#\s*Python (\d+\.\d+)\+"),
    }


def declared_floor() -> "tuple[int, int]":
    """The one version the eight statements name. Skips if they disagree —
    that is rule 1's finding to report, not every other rule's."""
    distinct = set(floor_statements().values())
    if len(distinct) != 1 or None in distinct:
        pytest.skip("the floor statements disagree; see rule 1")
    return next(iter(distinct))  # type: ignore[return-value]


def ci_python_versions() -> "set[tuple[int, int]]":
    """The interpreters this repository's own CI actually runs on."""
    found: set = set()
    for workflow in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        for raw in re.findall(r"python-version:\s*['\"]?(\d+\.\d+)",
                              workflow.read_text(encoding="utf-8")):
            found.add(_version_tuple(raw))
    return found


# ── rule 1: the statements agree ────────────────────────────────────────────

def test_every_statement_of_the_floor_names_the_same_version() -> None:
    """A lock, not a discovery: all eight already agreed before this round.

    It exists because the fix is eight literals in eight files, and the next
    person to move one has no way to find the other seven by reading any of
    them — `bootstrap_env.py`'s "Same floor harness_cli.py enforces" was a
    hand-maintained cross-reference that happened to still be true.
    """
    statements = floor_statements()
    unreadable = [k for k, v in statements.items() if v is None]
    assert not unreadable, (
        "a statement of the Python floor could not be parsed, so this guard "
        f"is not watching it: {unreadable}")

    distinct = set(statements.values())
    assert len(distinct) == 1, (
        "the Python floor is stated in eight places and they no longer agree:\n  "
        + "\n  ".join(f"{k}: {v[0]}.{v[1]}" for k, v in sorted(statements.items())))


# ── rule 2: the floor is a version something runs ───────────────────────────

def test_the_floor_is_an_interpreter_ci_runs_the_suite_on() -> None:
    """The rule that was missing.

    A supported version nothing executes is a claim with no witness (Round 46),
    and this repository had carried one long enough for two code paths to break
    on it unnoticed. Pinning "the floor must be in CI's matrix" makes the claim
    self-proving: the suite exercises the floor on every push, so a stdlib
    module newer than the floor fails there instead of at a project's P3 exit.
    """
    floor = declared_floor()
    runs_on = ci_python_versions()
    assert floor in runs_on, (
        f"this framework declares Python {floor[0]}.{floor[1]}+ and CI never "
        f"runs the suite on it (CI runs "
        f"{sorted(f'{a}.{b}' for a, b in runs_on)}). Nothing proves the floor "
        f"works — `tomllib` in harness/ssot_manifest.py did not, and P3 exit "
        f"crashed there for every project with a pyproject.toml.\n"
        f"    → either add the floor to .github/workflows/*.yml, or raise the "
        f"floor to a version CI already runs.")


# ── rule 3: nothing is stranded below the floor ─────────────────────────────

def _stranded_branches(floor: "tuple[int, int]") -> "list[str]":
    out: list[str] = []
    for directory in _SCANNED:
        base = REPO / directory
        paths = (sorted(base.glob("*.py")) if directory == "."
                 else sorted(base.rglob("*.py")))
        for path in paths:
            rel = str(path.relative_to(REPO))
            if rel in _FLOOR_GATES:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, OSError):
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.Compare)
                        and isinstance(node.left, ast.Attribute)
                        and node.left.attr == "version_info"
                        and isinstance(node.ops[0], (ast.Lt, ast.LtE))
                        and isinstance(node.comparators[0], ast.Tuple)):
                    version = _const_pair(node.comparators[0].elts)
                    if version is not None and version <= floor:
                        out.append(
                            f"{rel}:{node.lineno}  sys.version_info < {version}")
    return out


def test_no_branch_is_stranded_below_the_floor() -> None:
    """Raising a floor strands whatever only ran beneath it (Round 39).

    `cli/advance_prechecks.py:893` printed an async-coverage caveat for Python
    < 3.11. Once `harness_cli.py` refuses to start below 3.11 that line cannot
    execute, and a caveat nobody can reach is worse than none: it reads as a
    live consideration to the next person editing the block.
    """
    floor = declared_floor()
    stranded = _stranded_branches(floor)
    assert not stranded, (
        f"these branches test for a Python older than the declared floor "
        f"({floor[0]}.{floor[1]}), which harness_cli.py already refuses to "
        f"start on, so they can never run:\n  " + "\n  ".join(stranded)
        + "\n    → delete them, or lower the floor if they are still needed.")


def test_the_floor_gates_themselves_are_not_reported() -> None:
    """Reverse control for the exemption.

    `harness_cli.py` and `bootstrap_env.py` compare against the floor and must
    keep doing so — they run on the interpreter that has not been checked yet.
    A rule that called those two stranded would delete the only enforcement
    there is.
    """
    floor = declared_floor()
    reported = " ".join(_stranded_branches(floor))
    for gate in _FLOOR_GATES:
        assert gate not in reported, (
            f"{gate} is the floor gate itself and must not be reported as "
            f"stranded: {reported}")
