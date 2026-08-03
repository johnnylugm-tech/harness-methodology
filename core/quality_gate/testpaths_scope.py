"""Round 32 站5 — which tests count, according to the project and according to us.

Measured on a live P4 project. `setup.cfg` declares nine entries:

    [tool:pytest]
    testpaths = .../test_fr05.py .../test_fr06.py .../integration .../test_fr01.py
                .../test_fr03.py .../test_fr04.py .../test_fr08.py
                .../test_coverage_p100.py .../test_nfr_patterns.py

and `03-development/tests/` holds seven more that no entry covers:

    test_fr02.py  test_fr07.py  test_security_threats.py
    test_property_invariants.py  test_perf_benchmarks.py
    test_main_module.py  test_bug_hunt_run_all_breaker.py

Two of them are the FR test files for FR-02 and FR-07. A bare `pytest` — what
the agent runs, and what evaluate_dimension.md tells it to run — collects the
nine. The framework runs `pytest <active_test_dir>`, an explicit path that
overrides `testpaths`, and collects all sixteen. Two denominators, compared as
if they were one, and `setup.cfg`'s pytest section was not registered as a
score-altering input, so neither number reached the verdict.

Narrowing the default test set is the project's decision to make. This module
reports the difference; it never rewrites anything. Same shape as Round 31
站4's `scope_drift`, and the same rule as Round 27 站3 / Round 30 站6: an input
that can move a score travels with the verdict.
"""
from __future__ import annotations

import configparser
from pathlib import Path

__all__ = ["declared_testpaths", "testpaths_drift", "declaring_file"]

# pytest's own precedence order, so this module reads what pytest reads. That
# matters more than it looks: on the project this round came from, a nine-entry
# `[tool:pytest] testpaths` in setup.cfg sits beside a `pytest.ini` naming the
# whole test directory — and pytest.ini wins, so the narrow list is dead config
# nobody reads. A checker that read setup.cfg first would report a drift that
# does not exist. (tox.ini is omitted: nothing in this framework's supported
# layouts uses it, and adding an unread source is how dead branches start.)
_SOURCES = ("pytest.ini", "pyproject.toml", "setup.cfg")


def declaring_file(project_root: "str | Path") -> "Path | None":
    """The first config file that carries a `testpaths` declaration, or None."""
    root = Path(project_root)
    for name in _SOURCES:
        path = root / name
        if path.is_file() and _read_testpaths(path) is not None:
            return path
    return None


def declared_testpaths(project_root: "str | Path") -> "list[str] | None":
    """The project's own default test set, or None when it declares none.

    None means "no file here says which tests count" — never "the answer is
    the empty set" (Round 31's parse-failure rule). The difference matters:
    an empty set would make every test file in the project look excluded.
    """
    path = declaring_file(project_root)
    return _read_testpaths(path) if path is not None else None


def testpaths_drift(project_root: "str | Path") -> "dict | None":
    """What the project declares vs what the framework collects, or None.

    Returns None when the project declares nothing — there is no drift to
    report against an absent declaration. Otherwise:

        {"declared_source": "<abs path to the config file>",
         "declared": [...],            # verbatim, as written
         "collected": [...],           # what the framework's test target holds
         "not_in_declared": [...]}     # collected minus declared, sorted
    """
    root = Path(project_root)
    declared = declared_testpaths(root)
    if declared is None:
        return None

    source = declaring_file(root)
    covered = _expand(root, declared)
    collected = _collected_test_files(root)
    return {
        "declared_source": str(source),
        "declared": list(declared),
        "collected": sorted(collected),
        "not_in_declared": sorted(collected - covered),
    }


# ── reading ─────────────────────────────────────────────────────────────

def _read_testpaths(path: Path) -> "list[str] | None":
    """`testpaths` from one config file, or None when it carries none.

    Every failure mode — unreadable, unparseable, key absent — returns None,
    because they all mean the same thing to the caller: this file does not
    say which tests count.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    if path.name == "pyproject.toml":
        return _split(_toml_testpaths(text))

    parser = configparser.ConfigParser()
    try:
        parser.read_string(text)
    except configparser.Error:
        return None
    for section in ("tool:pytest", "pytest"):
        if parser.has_option(section, "testpaths"):
            return _split(parser.get(section, "testpaths"))
    return None


def _toml_testpaths(text: str) -> "str | list | None":
    try:
        import tomllib
    except ImportError:  # pragma: no cover — Python < 3.11
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        # Narrow on purpose: the exception-swallow ratchet rejects a broad
        # `except` that returns a success-shaped value with no diagnostic, and
        # it was right to — "this file is malformed" and "this file has no
        # testpaths" mean the same thing to the caller, but only the decode
        # error is a case we have actually reasoned about.
        return None
    tool = data.get("tool")
    if not isinstance(tool, dict):
        return None
    pytest_cfg = tool.get("pytest")
    if not isinstance(pytest_cfg, dict):
        return None
    opts = pytest_cfg.get("ini_options")
    if not isinstance(opts, dict):
        return None
    return opts.get("testpaths")


def _split(value: "str | list | None") -> "list[str] | None":
    if value is None:
        return None
    if isinstance(value, list):
        entries = [str(v).strip() for v in value]
    else:
        entries = str(value).split()
    entries = [e for e in entries if e]
    return entries or None


# ── comparing ───────────────────────────────────────────────────────────

def _expand(root: Path, entries: "list[str]") -> set:
    """Every test file the declared entries cover, project-relative."""
    covered: set = set()
    for entry in entries:
        target = root / entry
        if target.is_dir():
            covered.update(_test_files_under(root, target))
        elif target.is_file():
            covered.add(target.relative_to(root).as_posix())
    return covered


def _collected_test_files(root: Path) -> set:
    """What the framework's own test target holds — the same directory
    core.quality_gate.test_suite_run.resolve_targets hands to pytest, so the
    two sides of this comparison cannot drift apart."""
    from core.quality_gate.test_suite_run import resolve_targets

    test_target, _ = resolve_targets(root)
    target = root / test_target if test_target else root
    if not target.is_dir():
        return set()
    return _test_files_under(root, target)


def _test_files_under(root: Path, directory: Path) -> set:
    return {
        p.relative_to(root).as_posix()
        for p in directory.rglob("test_*.py")
        if "__pycache__" not in p.parts
    }
