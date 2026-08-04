"""Mutation testing FSM enforcer.

Implements the full mutmut protocol from
``harness/ssi/prompts/evaluate_dimension.md`` §mutation_testing:

* ``-b 10`` baseline budget (Bug F)
* editable-install detection → hard block (Bug F)
* cwd isolation via ``mktemp -d`` (Bug F)
* absolute testpaths in temp ``setup.cfg`` (Bug F)
* data-only file auto-exclusion (Bug F)
* ``paths_to_exclude`` from ``setup.cfg`` passed via CLI (Bug G)
* stash + restore of project-root ``.mutmut-cache`` (Bug #42)
* rewrite of ``[mutmut]`` section in temp setup.cfg (Bug #41)

**Important**: ``mutmut apply`` MUST be invoked inside the workdir created
by ``run_mutation_precheck``, never against ``project`` directly. ``mutmut
apply`` writes mutated source to its current directory; if invoked against
the project root, it will leave mutated source in place and break the
next precheck. See ``_apply_mutmut_to_workdir`` for the safe pattern.
"""
import configparser
import os  # Bug #43: used by _copy_setup_cfg_to_workdir to detect abs testpaths
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Union

from core.harness_config import get_timeout
from core.quality_gate.mutmut_report import format_score_message
from core.quality_gate.mutmut_scope import mutate_dirs
from core.utils.project_layout import ProjectLayout

# Basenames that are almost certainly data-only (no logic to mutate).
_DATA_ONLY_NAMES: frozenset[str] = frozenset({
    "config.py", "constants.py", "settings.py", "__init__.py",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_mutmut_workdir(project: Path) -> tuple[Path, str]:
    """Return ``(cwd, paths_to_mutate)`` resolved from project ``setup.cfg``.

    Reads ``[mutmut] paths_to_mutate`` from the project-root ``setup.cfg``,
    falling back to ``03-development/src`` when the key is absent.

    Round 30 站2 — ONE source. Round 29 added a second read path here (parse
    SAB.json at mutation time when setup.cfg had no key), which meant the scope
    was decided in two places: a generated setup.cfg AND a live SAB parse, with
    no rule for which wins when they disagree. Two sources for one decision is
    the shape this repo keeps paying for. setup.cfg is now the only value read
    at mutation time; the SAB remains the upstream SSOT and reaches it through
    ``core.quality_gate.mutmut_scope`` at the P2→P3 handoff, where the result
    lands in a commit a human can review.

    The "no declared scope" degradation moved to that generator too: recording
    it here fired once per mutation run and said nothing new each time.
    """
    root_cfg = configparser.ConfigParser()
    root_cfg.read(str(project / "setup.cfg"))

    layout = ProjectLayout(project)
    default_paths = layout.get_relative_str(layout.phase3_development_dir / "src")
    paths: str = default_paths

    if root_cfg.has_section("mutmut"):
        paths = root_cfg.get("mutmut", "paths_to_mutate", fallback=default_paths)

    cwd = project
    parts = Path(paths).parts
    # If the configured path is nested (e.g. 03-development/src), check
    # whether the top-level subdirectory carries its own [mutmut] config.
    if len(parts) > 1:
        sub_project = project / parts[0]
        sub_cfg = configparser.ConfigParser()
        sub_cfg.read(str(sub_project / "setup.cfg"))
        if sub_cfg.has_section("mutmut"):
            cwd = sub_project
            paths = str(Path(*parts[1:]))

    return cwd, paths


def _is_editable_install(project: Path) -> bool:
    """Check whether *project* is installed in editable (``pip install -e``) mode.

    Editable installs place a ``.pth`` file in site-packages pointing back to
    the original source.  When mutmut copies mutated code to a temp dir,
    Python resolves imports via the ``.pth`` file → mutations are never tested.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--editable", "--format", "json"],
            capture_output=True, text=True, timeout=30,
            cwd=str(project),
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        packages = json.loads(result.stdout)
        project_path = project.resolve()
        for pkg in packages:
            loc = (
                pkg.get("editable_project_location")
                or pkg.get("location")
                or ""
            )
            if loc and Path(loc).resolve() == project_path:
                return True
        return False
    except Exception:
        return False


def _read_paths_to_exclude(cwd: Path) -> list[str]:
    """Read ``paths_to_exclude`` from ``[mutmut]`` in ``setup.cfg``.

    ConfigParser returns the value as a single string; mutmut 2.x iterates
    its characters instead of splitting on whitespace → effectively broken.
    We read it ourselves, split by whitespace, and pass individual
    ``--paths-to-exclude`` CLI options (Bug G).
    """
    cfg = configparser.ConfigParser()
    cfg.read(str(cwd / "setup.cfg"))
    if not cfg.has_section("mutmut"):
        return []
    raw = cfg.get("mutmut", "paths_to_exclude", fallback="")
    return raw.split() if raw.strip() else []


def _detect_data_only_files(src_dirs: "list[Path]") -> list[str]:
    """Auto-detect data-only ``.py`` files that have no mutate-able logic.

    Returns **basenames** (mutmut matches ``paths_to_exclude`` on basename).
    Files matching ``_DATA_ONLY_NAMES`` are excluded immediately; for the
    rest a heuristic counts logic-keyword lines at any indentation level.

    Round 30 站2: takes the LIST of mutate roots. `paths_to_mutate` has always
    been comma-separated, and both callers used to hand this a single
    `cwd / <the whole comma string>` — a directory that cannot exist once a
    project declares more than one, so the scan silently returned nothing.
    """
    excludes: list[str] = []
    # ``\s+`` covers 2-space, 4-space, and tab-indented code.
    _logic_re = re.compile(
        r"^\s+(if |for |while |with |return |raise |try:|except |assert )",
        re.MULTILINE,
    )
    for src_dir in src_dirs:
        for py_file in src_dir.rglob("*.py"):
            basename = py_file.name
            if basename in _DATA_ONLY_NAMES:
                excludes.append(basename)
                continue
            try:
                text = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if not _logic_re.search(text):
                excludes.append(basename)
    return sorted(set(excludes))


#: SQLite's file header. mutmut 2.x's cache is a plain sqlite database.
_SQLITE_MAGIC = b"SQLite format 3\x00"


def _is_resumable_cache(cache_path: Path) -> bool:
    """Whether *cache_path* holds prior mutmut results worth inheriting.

    Round 31 站3. Resuming means copying the project-root cache into the temp
    workdir, so anything unusable there becomes this run's problem: a truncated
    or non-sqlite file makes `_count_mutmut_results` raise "file is not a
    database" and turns a working run into an error. Both shapes occur — a
    crashed run leaves a zero-byte file, and a partially-written one leaves a
    fragment.

    Checking the header rather than opening the database keeps the decision
    cheap and keeps the failure mode explicit: an unreadable cache means
    "start clean", never "fail the run".
    """
    try:
        if not cache_path.is_file() or cache_path.stat().st_size <= len(_SQLITE_MAGIC):
            return False
        with open(cache_path, "rb") as fh:
            return fh.read(len(_SQLITE_MAGIC)) == _SQLITE_MAGIC
    except OSError:
        return False


def _abs_paths_to_mutate(cwd: Path, paths_to_mutate: str) -> str:
    """Convert comma-separated relative paths to absolute, comma-separated."""
    parts = [p.strip() for p in paths_to_mutate.split(",") if p.strip()]
    return ",".join(str((cwd / p).resolve()) for p in parts)


def _resolve_test_dir(cwd: Path, project: Path) -> Optional[str]:
    """Return the absolute path to the test directory, relative to *cwd*.

    *cwd* is the resolved mutmut working directory — either the project
    root (no override) or a subdirectory (when ``setup.cfg`` subdir override
    is active). Tests always live as siblings of the source, so we search
    relative to *cwd*. Candidate lists come from ``ProjectLayout`` so the
    path layout is centralised.
    """
    layout = ProjectLayout(project)
    cwd_resolved = cwd.resolve()
    project_resolved = project.resolve()
    if cwd_resolved == project_resolved:
        candidates: list[Path] = layout.root_test_dir_candidates
    else:
        candidates = ProjectLayout.subdir_test_dirs(cwd)
    for candidate in candidates:
        if candidate.is_dir():
            return str(candidate.resolve())
    return None


_WELL_KNOWN_RUNNERS = frozenset({
    "pytest", "python -m pytest", "python3 -m pytest", "python -m unittest",
    "python3 -m unittest", "py.test", "python -m doctest",
})


def _find_source_setup_cfg(project: Path) -> Optional[Path]:
    """Locate the project's ``setup.cfg``, considering nested layouts.

    Bug #106 fix: for the recommended nested layout (e.g.
    ``03-development/setup.cfg`` carrying the project's actual pytest
    config), the source setup.cfg is NOT at project root. Searching
    project root alone would skip the file entirely, falling into the
    "no setup.cfg" branch that generates a minimal config with only
    ``testpaths`` — silently dropping ``pythonpath``, ``addopts``, and
    any other pytest config the project author wrote.

    Search order:
    1. ``<project>/setup.cfg`` (root-level — legacy / non-nested layout)
    2. ``<project>/<phase3_development_dir>/setup.cfg`` (nested layout)
       where ``phase3_development_dir`` comes from ``ProjectLayout``.

    Returns the first match, or ``None`` if neither exists.
    """
    root_cfg = project / "setup.cfg"
    if root_cfg.exists():
        return root_cfg
    layout = ProjectLayout(project)
    nested_cfg = layout.phase3_development_dir / "setup.cfg"
    if nested_cfg.exists():
        return nested_cfg
    return None


def _has_resolvable_testpaths(cp: configparser.ConfigParser) -> bool:
    """True when ``[tool:pytest] testpaths`` names something that will still
    exist once pytest runs from the temp workdir.

    Only an absolute path survives the move: a relative entry resolves
    against cwd, and cwd is a directory holding one generated setup.cfg.
    """
    if not (cp.has_section("tool:pytest") and cp.has_option("tool:pytest", "testpaths")):
        return False
    for entry in shlex.split(cp.get("tool:pytest", "testpaths").strip()):
        if os.path.isabs(entry) and Path(entry).exists():
            return True
    return False


def _copy_setup_cfg_to_workdir(
    project: Path,
    workdir: str,
    abs_test_dir: str = "",
    cwd: Optional[Path] = None,
) -> None:
    """Copy ``setup.cfg`` into *workdir* with [mutmut] section rewritten for
    temp-workdir context (Bug #41 fix).

    mutmut 2.x reads several settings (runner, tests_dir, backup, disable)
    from setup.cfg relative to cwd. A project's setup.cfg is written for its
    own cwd and may carry runner/tests_dir values that don't work in the
    temp workdir mutmut creates. Force-set the values we know are correct.

    Safety: only rewrite `runner` if the existing value is one of the
    well-known defaults (pytest/python-m-pytest/...). If the project uses
    a custom runner (e.g. `make test`), log a warning and leave it alone.

    Bug 7 fix: *cwd* is the resolved mutmut working directory returned by
    ``_resolve_mutmut_workdir``. When it differs from *project* (nested layout
    with a subdir-level setup.cfg), read the source config from *cwd* instead
    of always scanning from project root.
    """
    # Bug 7: when a nested cwd is provided, use it as the config source
    # instead of always scanning from project root.
    if cwd is not None:
        setup_cfg = cwd / "setup.cfg"
        if not setup_cfg.exists():
            setup_cfg = _find_source_setup_cfg(project) or (project / "setup.cfg")
    else:
        setup_cfg = _find_source_setup_cfg(project) or (project / "setup.cfg")
    cp = configparser.ConfigParser()
    if setup_cfg.exists():
        cp.read(str(setup_cfg), encoding="utf-8")
    if "mutmut" not in cp:
        cp["mutmut"] = {}
    mut = cp["mutmut"]

    # Always set tests_dir to the absolute path the framework will use.
    # NOTE what this does and does not do: mutmut 2.5.1 uses tests_dir to
    # hash the suite and to keep test files out of the mutant pool
    # (__main__.py:340-352). It never reaches the runner command, so it
    # cannot tell the workdir's pytest what to collect — the finalisation at
    # the end of this function does that.
    if abs_test_dir:
        mut["tests_dir"] = abs_test_dir

    # Rewrite runner only if it's a well-known default. Custom runner
    # scripts (make test, bash scripts) are out of scope — the project
    # author is responsible for making them workdir-aware.
    # Bug #91: use sys.executable (the interpreter actually running the
    # framework) instead of the hardcoded "python -m pytest". On modern
    # macOS / Homebrew Python 3.11+ there is no `python` symlink (only
    # `python3` / `python3.11`), so mutmut's Popen of `python -m pytest`
    # throws FileNotFoundError [Errno 2] No such file or directory: 'python'.
    # sys.executable always resolves to a real interpreter, including
    # inside a virtualenv.
    existing_runner = mut.get("runner", "").strip()
    # Bug #116: use prefix matching so 'python3 -m pytest -x --assert=plain …'
    # is treated as a well-known runner (exact match missed variants with extra
    # flags, leaving the system `python3` in place even when sys.executable
    # resolves to a different interpreter — manifests as score=0 on machines
    # where `python3` is an older version that can't run the test suite).
    # When a well-known prefix is detected, normalise to the canonical form;
    # the extra flags in the original runner were for test-discovery workarounds
    # that --tests-dir already handles.
    _is_well_known = (
        not existing_runner
        or existing_runner in _WELL_KNOWN_RUNNERS
        or any(existing_runner.startswith(p + " ") for p in _WELL_KNOWN_RUNNERS)
    )
    if _is_well_known:
        mut["runner"] = f"{sys.executable} -m pytest"
    else:
        print(f"[WARN] setup.cfg [mutmut] runner is custom ({existing_runner!r}); "
              f"not overriding. Ensure your runner picks up tests_dir={abs_test_dir}.",
              file=sys.stderr)

    # Strip stale "backup" (Bug #42: mutmut apply leaves <file>.bak; we
    # manage state via stash instead). Strip "disable" (project disable
    # lines can hide mutants the framework expects to be tested).
    mut.pop("backup", None)
    mut.pop("disable", None)

    # Promote [tool:pytest] testpaths to absolute so the workdir's pytest
    # discovery is unambiguous.
    # (No-ops when the project has no setup.cfg — `cp` then carries no
    # [tool:pytest] section and the finalisation below supplies one.)
    # Resolve relative paths against the setup.cfg's directory (Bug #106b),
    # not project root — for nested layouts (03-development/) the source of
    # truth is the nested setup.cfg, and its relative paths are relative
    # to its own directory, not the project root.
    cfg_dir = setup_cfg.parent
    if cp.has_section("tool:pytest") and cp.has_option("tool:pytest", "testpaths"):
        rel = cp["tool:pytest"]["testpaths"].strip()
        if rel:
            # Bug 5 fix: multi-value testpaths (e.g. "tests other_tests") must
            # be split and resolved individually. pytest accepts space-separated
            # paths in INI format. Joining them as one bogus path makes pytest
            # search a literal "tests other_tests" directory that does not exist.
            parts = shlex.split(rel)
            resolved = []
            for p in parts:
                if os.path.isabs(p):
                    resolved.append(p)
                else:
                    abs_p = (cfg_dir / p).resolve()
                    if abs_p.exists():
                        resolved.append(str(abs_p))
            if resolved:
                cp["tool:pytest"]["testpaths"] = " ".join(resolved)

    # Bug #106 fix: promote [tool:pytest] pythonpath to absolute. pytest reads
    # `pythonpath` as a cwd-relative path during early startup and inserts it
    # into sys.path BEFORE site module's PYTHONPATH env handling. A relative
    # `pythonpath = src` in the workdir resolves to `<workdir>/src` (which
    # doesn't exist) and silently breaks imports of the project's own
    # package — observed as `ModuleNotFoundError: No module named 'taskq'`
    # on integration-test. Rewrite to absolute so workdir pytest discovery
    # works the same as project-root pytest discovery.
    if cp.has_section("tool:pytest") and cp.has_option("tool:pytest", "pythonpath"):
        rel = cp["tool:pytest"]["pythonpath"].strip()
        if rel:
            # Bug 6 fix: multi-value pythonpath (e.g. "src lib") must be
            # split and resolved individually. pytest reads each entry as a
            # separate sys.path insertion. Leaving "src lib" as one literal
            # path resolves to <cfg_dir>/src lib (does not exist), breaking
            # imports for both packages.
            parts = shlex.split(rel)
            resolved = []
            for p in parts:
                if os.path.isabs(p):
                    resolved.append(p)
                else:
                    abs_pp = (cfg_dir / p).resolve()
                    if abs_pp.exists():
                        resolved.append(str(abs_pp))
                    else:
                        # Non-existent entry: warn but keep the original
                        # string for misconfigured projects (preserves
                        # existing behavior — they'll get the same
                        # ModuleNotFoundError they had before, not a silent
                        # change to a different broken state).
                        print(f"[WARN] setup.cfg [tool:pytest] pythonpath entry={p!r} "
                              f"resolves to {abs_pp} which does not exist; "
                              f"not adding to pythonpath.",
                              file=sys.stderr)
            if resolved:
                cp["tool:pytest"]["pythonpath"] = " ".join(resolved)
            # If none of the parts resolved, leave the original string unchanged.

    # Round 35 站1: the workdir's pytest must be told what to collect, and
    # this is the one place that can tell it.
    #
    # mutmut runs the resolved [mutmut] runner as its baseline
    # (time_test_suite) with cwd set to this workdir, and raises on any
    # non-zero exit. `tests_dir` above does NOT reach that command —
    # mutmut 2.5.1 uses it to hash the suite and to exclude test files from
    # mutation (__main__.py:340-352), never as a pytest argument. So an empty
    # workdir with no testpaths collects nothing, exits 5, and the whole
    # dimension reports zero.
    #
    # Bug #43 wrote testpaths for exactly this reason, but only when the
    # project had NO setup.cfg. A project whose setup.cfg exists and says
    # nothing about pytest — the common shape when tests reach their source
    # through a conftest.py sys.path insertion — took the other branch and
    # got no target at all. The condition belongs on the proposition that
    # decides the outcome: does the config we are about to write name a test
    # location that survives the move to the workdir?
    if abs_test_dir and not _has_resolvable_testpaths(cp):
        if "tool:pytest" not in cp:
            cp["tool:pytest"] = {}
        cp["tool:pytest"]["testpaths"] = abs_test_dir

    with open(Path(workdir) / "setup.cfg", "w", encoding="utf-8") as f:
        cp.write(f)


_XDIST_TOKEN_RE = re.compile(r"^(-n(\d+|auto)?|--numprocesses(=.*)?|--dist(=.*)?)$")
_COV_TOKEN_RE = re.compile(r"^--cov(=.*)?$")


def _resolved_workdir_runner(workdir: str) -> str:
    """Read back the [mutmut] runner _copy_setup_cfg_to_workdir already wrote
    into *workdir*/setup.cfg. "" on any read failure — callers must still
    apply PYTEST_DISABLE_PLUGIN_AUTOLOAD in that case, never skip it."""
    cp = configparser.ConfigParser()
    try:
        cp.read(str(Path(workdir) / "setup.cfg"), encoding="utf-8")
    except (OSError, configparser.Error):
        return ""
    return cp.get("mutmut", "runner", fallback="")


def _mutmut_subprocess_env(workdir: str) -> dict:
    """Environment for the ``mutmut run`` subprocess (Bug #142).

    Default-deny: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 stops pytest from
    auto-loading every pytest11-entry-point plugin installed in this venv.
    Pytest's own core plugins (assertion rewriting, capsys, monkeypatch,
    etc.) and anything explicitly passed via -p are unaffected. Without
    this, an unrelated autoloaded plugin can crash mutmut's ephemeral
    workdir or silently mis-report a mutant as killed/survived based on
    stale fingerprints — a project's ``[mutmut] runner`` carried
    ``--testmon``; pytest-testmon's incremental-fingerprint hook tried to
    read a file inside mutmut's temp workdir and crashed with
    IsADirectoryError, burning three Gate 2 rounds at score=0 with zero
    mutants evaluated.

    Explicit allow-list: flags already present in the FINAL resolved
    [mutmut] runner are the only signal of demand for a specific plugin's
    functionality; re-enable exactly that via PYTEST_ADDOPTS (pytest
    prepends it to every invocation, including mutmut's internal ones). A
    demand this map doesn't recognise fails loudly with pytest's own
    "unrecognized arguments" error, surfaced in the existing
    stdout/stderr-capturing failure message — strictly better than silent
    corruption or an INTERNALERROR wall.
    """
    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

    runner = _resolved_workdir_runner(workdir)
    try:
        tokens = shlex.split(runner)
    except ValueError:
        tokens = runner.split()

    needed = []
    if any(_XDIST_TOKEN_RE.match(t) for t in tokens):
        needed.append("xdist")
    if any(_COV_TOKEN_RE.match(t) for t in tokens):
        needed.append("pytest_cov")

    if needed:
        addopts = " ".join(f"-p {name}" for name in needed)
        prior = env.get("PYTEST_ADDOPTS", "").strip()
        env["PYTEST_ADDOPTS"] = f"{prior} {addopts}".strip() if prior else addopts

    return env


def _apply_mutmut_to_workdir(mutant_id: Union[str, int], workdir: str) -> None:
    """Safely apply a mutmut mutant INSIDE the workdir (Bug #42 safety).

    `mutmut apply` writes the mutated source for the given mutant id to
    its current directory. Invoking it against the project root leaves
    mutated source in place, breaking the next precheck. Callers must
    pass the workdir returned by ``run_mutation_precheck`` and execute
    the apply in a subprocess with ``cwd=workdir``.
    """
    subprocess.run(
        ["mutmut", "apply", str(mutant_id)],
        cwd=workdir, capture_output=True, text=True, timeout=30,
    )


def _paths_to_exclude_flag(excludes: list[str]) -> str:
    """Build a single ``--paths-to-exclude=...`` flag for mutmut 2.x.

    mutmut's CLI defines this option as ``type=click.STRING`` (no
    ``multiple=True``), so multiple flags on the command line would
    collapse to the last value. mutmut itself splits the string on ``,``
    and ``\\n`` at parse time, so we comma-join all excludes here.
    """
    return f"--paths-to-exclude={','.join(excludes)}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _write_survivors_artifact(
    project: Path, tool: str, survivors: list, raw: "str | None" = None
) -> None:
    """Persist surviving mutants to .methodology/mutation_survivors.json.

    v2.9 C5: each survivor is a 'behavior no test asserts' lead — the Gate-3
    bug-hunt targeting manifest (bug-hunt-targets) consumes this file, so
    survivor triage becomes hunt input instead of dying inside a fail message.
    Written on every run, including a PASS (an empty list is evidence too).
    Best-effort: artifact write failure never affects the precheck verdict.
    """
    import json as _json
    from datetime import datetime, timezone

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "survivor_count": len(survivors),
        "survivors": survivors,
    }
    if raw is not None:
        payload["raw"] = raw[-5000:]
        # Round 31 站1: what the tool itself said, beside what we parsed. The
        # artifact that recorded `survivor_count: 0` next to a raw banner
        # reading `Survived (308)` was self-refuting, and nobody could see it
        # because only one of the two numbers was a field.
        from core.quality_gate.mutmut_report import parse_reported_total
        payload["reported_total"] = parse_reported_total(raw)
    try:
        out = project / ".methodology" / "mutation_survivors.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(payload, indent=2, ensure_ascii=False),
                       encoding="utf-8")
    except OSError:
        pass


#: Where the framework records the mutation score it computed itself.
#: Project-root-relative; also spelled in harness_bridge's S4 check and in
#: evaluate_dimension.md, all three via this constant or a citation of it.
MUTATION_SCORE_ARTIFACT = ".methodology/mutation_score.json"


def _write_score_artifact(
    project: Path,
    *,
    killed: int,
    survived: int,
    score: float,
    paths_to_mutate: str,
    paths_to_exclude: "list[str]",
    mutated_files: int,
    cache_path: Path,
) -> None:
    """Record the score THIS function computed, for the gate to read.

    Round 31 站2. `mutation_testing` is tier-1 and `objective_primary: true`,
    and it was the only tier-1 dimension whose number the framework never
    produced: this function had zero production callers, its CLI wrapper was
    merely *suggested* in a prompt, and the score that reached a live Gate 2
    came from a prose file the agent wrote by hand. The gate could not tell a
    transcription from a measurement because there was nothing to compare
    against.

    The denominator travels with the numerator on purpose (Round 27 站7):
    `paths_to_exclude` removes files from the mutant pool and is written by the
    party being scored, so the verdict has to carry which files were actually
    mutated, not just the ratio that survived them.

    Best-effort: a failed artifact write must not change the score this run
    computed — but it does leave a ledger line, because a missing artifact is
    what the gate blocks on.
    """
    import json as _json
    from datetime import datetime, timezone

    from core.harness_provenance import enforcer_sha

    cache_sha = None
    if cache_path.exists():
        import hashlib
        try:
            cache_sha = hashlib.sha256(cache_path.read_bytes()).hexdigest()
        except OSError:
            cache_sha = None

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool": "mutmut",
        "score": score,
        "killed": killed,
        "survived": survived,
        "paths_to_mutate": paths_to_mutate,
        "paths_to_exclude": sorted(paths_to_exclude),
        "mutated_files": mutated_files,
        "cache_sha256": cache_sha,
        "enforcer_sha": enforcer_sha(),
    }
    out = project / MUTATION_SCORE_ARTIFACT
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(payload, indent=2, ensure_ascii=False),
                       encoding="utf-8")
    except OSError as exc:
        from core.degradation_ledger import record_degradation
        record_degradation(
            str(project), "mutation:score-artifact",
            f"could not write {MUTATION_SCORE_ARTIFACT} ({exc})",
            why="the gate blocks a passing mutation claim without this file",
        )


def _write_unmeasured_artifact(project: Path, reason: str) -> None:
    """Record that the framework RAN and could not produce a score.

    Round 35 站2. The artifact used to be written only on success, so its
    absence carried two incompatible facts: "nobody ran the command" and "the
    framework ran it and the run could not yield a number". The gate could
    only see the absence, so it said the first while the second was true —
    measured on a live Gate 2, where the block told a project to kill mutants
    that had never been produced.

    `score: null` is the distinguishing field, and it is the same rule Round
    32 站4 gave the tool scorers: a number the harness could not measure is
    not the number zero. The reason travels with it because the operator's
    next action depends on it (repair the scope, install the tool, fix the
    workdir) and none of those actions is "write better tests".

    Best-effort, like its success twin: failing to record this must not turn
    into a second failure, but it does leave a ledger line.
    """
    import json as _json
    from datetime import datetime, timezone

    from core.harness_provenance import enforcer_sha

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool": "mutmut",
        "score": None,
        "could_not_measure": reason,
        "enforcer_sha": enforcer_sha(),
    }
    out = project / MUTATION_SCORE_ARTIFACT
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(payload, indent=2, ensure_ascii=False),
                       encoding="utf-8")
    except OSError as exc:
        from core.degradation_ledger import record_degradation
        record_degradation(
            str(project), "mutation:score-artifact",
            f"could not write {MUTATION_SCORE_ARTIFACT} ({exc}) for a run that "
            f"could not measure ({reason[:120]})",
            why="without this file the gate reads the absence as 'nobody ran it'",
        )


def _parse_mutmut_survivors(results_output: str) -> list:
    """Parse `mutmut results` output into survivor entries.

    Round 31 站1: the parsing itself lives in
    :mod:`core.quality_gate.mutmut_report`, the one place that knows mutmut's
    formats. The version that lived here accepted only bare comma-separated
    ids and so read every real run — mutmut collapses consecutive ids into
    ranges — as having no survivors.
    """
    from core.quality_gate.mutmut_report import parse_survivors
    return parse_survivors(results_output)


def _count_mutmut_results(cache_path: Path) -> tuple[int, int]:
    """Count killed vs survived mutants from the mutmut 2.x sqlite cache.

    Bug #108: the previous implementation parsed `mutmut results` stdout
    for emoji counts (🎉/🙁/⏰/🤔), but mutmut 2.x only prints 🙁 for
    survivors in that output — killed mutants never appear. The
    authoritative data is in the cache's ``Mutant`` table, where each
    row carries a ``status`` value.

    Status mapping (mutmut 2.x sqlite schema):
      - ok_killed     → counts as killed
      - bad_survived  → counts as survived
      - timeout       → counts as survived (per evaluate_dimension.md:
                       tests took too long, mutant may have escaped)
      - suspicious    → counts as survived
      - pending, checking, no_tests, skipped, check_failed → ignored
                       (test infrastructure issue, not a mutant verdict)

    Returns (killed, survived). Returns (0, 0) only if the cache file is
    missing. Raises sqlite3.Error/OSError/IOError if it exists but is
    unreadable (locked/corrupt) — the caller must not treat a read failure
    as a clean zero-mutant result (see test_sqlite_error_not_swallowed_as_zero_mutants).
    """
    import sqlite3
    if not cache_path.exists():
        return 0, 0
    try:
        db = sqlite3.connect(str(cache_path))
        cur = db.cursor()
        # ok_killed is the only "killed" verdict in mutmut 2.x.
        cur.execute("SELECT count(*) FROM Mutant WHERE status = 'ok_killed'")
        killed = cur.fetchone()[0]
        # Anything not "killed" and not a pending/infra-failure row
        # counts as a survivor from the framework's perspective.
        cur.execute(
            "SELECT count(*) FROM Mutant "
            "WHERE status IN ('bad_survived', 'timeout', 'suspicious')"
        )
        survived = cur.fetchone()[0]
        db.close()
        return killed, survived
    except (sqlite3.Error, OSError, IOError):
        raise


def run_stryker_precheck(project: Path) -> tuple[bool, str]:
    """StrykerJS TDD-PRECHECK for js/ts projects.

    Runs ``npx --no-install stryker run`` (project config: stryker.conf.json,
    copied by init-project) and enforces zero surviving mutants from the
    jsonReporter output (reports/mutation/mutation.json). Same contract as the
    mutmut path: (True, "") on pass, (False, reason) on any failure — a
    missing tool, a crashed run, or survivors all block.
    """
    import json as _json
    import shutil as _shutil

    # Precheck: `npx` may be missing entirely on a clean machine — without this,
    # subprocess.run raises FileNotFoundError which bypasses the (False, reason)
    # contract and surfaces as a hard traceback to the caller.
    if not _shutil.which("npx"):
        return False, (
            "npx not found on PATH. Required for StrykerJS TDD-PRECHECK.\n"
            "Install Node.js + npm (Node 18+ recommended)."
        )

    probe = subprocess.run(
        ["npx", "--no-install", "stryker", "--version"],
        cwd=project, capture_output=True, text=True, timeout=60,
    )
    if probe.returncode != 0:
        return False, (
            "StrykerJS not installed. Required for TDD-PRECHECK.\n"
            "Install the pinned devDependencies (templates/js_toolchain/"
            "package.json) and run: npm ci"
        )

    report_path = project / "reports" / "mutation" / "mutation.json"
    try:
        r = subprocess.run(
            ["npx", "--no-install", "stryker", "run"],
            cwd=project, capture_output=True, text=True,
            timeout=get_timeout("mutation", project),  # 60 min hard cap — same budget as the mutmut path
        )
    except subprocess.TimeoutExpired:
        return False, (
            "stryker timed out after 60 minutes. The test suite may be too "
            "slow per mutant; narrow the `mutate` globs in stryker.conf.json."
        )

    if r.returncode != 0 and not report_path.exists():
        return False, (
            f"stryker run crashed (return code {r.returncode}).\n\n"
            f"STDOUT:\n{r.stdout.strip()[-2000:]}\n\n"
            f"STDERR:\n{r.stderr.strip()[-2000:]}"
        )

    if not report_path.exists():
        return False, (
            "stryker produced no reports/mutation/mutation.json — ensure "
            "stryker.conf.json keeps the json reporter (jsonReporter.fileName)."
        )

    try:
        report = _json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError) as e:
        return False, f"cannot parse stryker mutation.json: {e}"

    survived: list[str] = []
    survivor_entries: list[dict] = []
    for file_path, file_data in (report.get("files") or {}).items():
        for mutant in file_data.get("mutants", []):
            if mutant.get("status") == "Survived":
                loc = (mutant.get("location") or {}).get("start") or {}
                survived.append(
                    f"{file_path}:{loc.get('line', '?')} "
                    f"{mutant.get('mutatorName', '?')}"
                )
                survivor_entries.append({
                    "file": file_path,
                    "line": loc.get("line"),
                    "mutant_id": mutant.get("id"),
                    "mutator": mutant.get("mutatorName"),
                })
    _write_survivors_artifact(project, "stryker", survivor_entries)
    if survived:
        listing = "\n".join(f"  - {s}" for s in survived[:20])
        more = f"\n  ... and {len(survived) - 20} more" if len(survived) > 20 else ""
        return False, (
            f"Mutation testing failed: {len(survived)} surviving mutant(s) found.\n"
            f"{listing}{more}"
        )
    return True, ""


def run_mutation_precheck(project: Path) -> tuple[bool, str]:
    """Run the language's mutation tool and enforce no surviving mutants.

    python — full mutmut protocol from ``evaluate_dimension.md``:
    editable-install detection, ``-b 10`` baseline budget, cwd isolation
    via temp dir, absolute testpaths, data-only file auto-exclusion, and
    ``paths_to_exclude`` CLI passthrough (Bug G).
    js/ts — StrykerJS via :func:`run_stryker_precheck` (mutation.json report).
    """
    from core.utils.lang_patterns import project_language
    if project_language(project) in ("javascript", "typescript"):
        return run_stryker_precheck(project)

    if not shutil.which("mutmut"):
        return False, (
            "mutmut not installed. Required for TDD-PRECHECK. "
            "Install: pip install mutmut"
        )

    cwd, paths_to_mutate = _resolve_mutmut_workdir(project)

    src_dirs = mutate_dirs(cwd, paths_to_mutate)
    missing = [str(p.relative_to(cwd)) for p in src_dirs if not p.exists()]
    if missing:
        return False, f"paths_to_mutate contains missing entries: {missing}"

    # --- Bug F: editable install detection ---
    if _is_editable_install(project):
        return False, (
            "Project is installed in editable mode (pip install -e). "
            "This prevents mutmut from testing mutations — Python resolves "
            "imports to the original (unmutated) source via .pth files.\n"
            "Fix:  pip install .  (non-editable, regular install)\n"
            "Then re-run TDD-PRECHECK."
        )

    # --- Bug G: read paths_to_exclude from setup.cfg (split properly) ---
    declared_excludes = _read_paths_to_exclude(cwd)

    # --- Bug F: auto-detect data-only files ---
    auto_excludes = _detect_data_only_files(src_dirs)
    # Declared excludes take precedence — don't duplicate.
    auto_excludes = [e for e in auto_excludes if e not in frozenset(declared_excludes)]

    # --- Bug F: absolute paths (temp-dir cwd isolation) ---
    abs_mutate = _abs_paths_to_mutate(cwd, paths_to_mutate)

    workdir = tempfile.mkdtemp(prefix="_mutmut_run.", dir="/tmp")
    cache_file = project / ".mutmut-cache"
    workdir_cache = Path(workdir) / ".mutmut-cache"

    # Bug #42 fix: stash the project-root .mutmut-cache BEFORE running
    # mutmut. The precheck's `finally` block unconditionally writes
    # workdir_cache back to cache_file (line ~506). If the precheck fails
    # partway (TimeoutExpired, subprocess crash, or interrupt), the
    # project root may be left with a partial cache that blocks the next
    # run. Stash + restore guarantees the project-root cache is either
    # the original (on success or failure) or absent (if it never existed).
    stash_dir: Optional[str] = None
    if cache_file.exists():
        stash_dir = tempfile.mkdtemp(prefix="_mutmut_cache_stash.", dir="/tmp")
        shutil.copy2(cache_file, Path(stash_dir) / ".mutmut-cache")

    # Set by the success path so the finally clause can decide whether to
    # promote workdir output (only on success) vs discard it.
    _precheck_ok: bool = False

    try:
        # Bug fix: mutmut 2.x does NOT read [tool:pytest] testpaths — it
        # needs --tests-dir with an absolute path. Without this the
        # test-discovery code crashes with FileNotFoundError in the temp
        # workdir that has no tests/ subdirectory.
        test_dir = _resolve_test_dir(cwd, project)
        if test_dir is None:
            return False, (
                "No test directory found. Searched for tests/, test/, "
                "03-development/tests/ relative to the mutmut workdir. "
                "Mutation testing is meaningless without tests — cannot proceed."
            )
        # Bug #41 fix: rewrite [mutmut] section for temp-workdir context.
        # Must run BEFORE mutmut reads setup.cfg (which it does on startup).
        _copy_setup_cfg_to_workdir(project, workdir, test_dir, cwd=cwd)
        # Do NOT copy existing .mutmut-cache into workdir: precheck must always
        # perform a fresh run.  Inheriting an old cache causes mutmut to report
        # previously-survived mutants as still-survived without re-testing them,
        # inflating the survivor count and blocking advance-phase incorrectly.

        cmd = [
            "mutmut", "run",
            f"--paths-to-mutate={abs_mutate}",
            "-b", "10",                     # Bug F: baseline budget
        ]
        cmd.append(f"--tests-dir={test_dir}")
        all_excludes = declared_excludes + auto_excludes
        if all_excludes:
            cmd.append(_paths_to_exclude_flag(all_excludes))

        r = subprocess.run(
            cmd, cwd=workdir, capture_output=True, text=True,
            env=_mutmut_subprocess_env(workdir),  # Bug #142: sandbox pytest plugin autoload
            timeout=get_timeout("mutation", project),  # 60 min hard cap — mutation testing is meaningless if it hangs
        )

        if r.returncode not in (0, 2):
            return False, (
                f"mutmut run crashed (return code {r.returncode}).\n\n"
                f"STDOUT:\n{r.stdout.strip()}\n\n"
                f"STDERR:\n{r.stderr.strip()}"
            )

        # mutmut results reads .mutmut-cache only — no pytest invocation, no env= needed (Bug #142)
        res = subprocess.run(
            ["mutmut", "results"], cwd=workdir, capture_output=True, text=True,
            timeout=30,
        )
        if res.returncode != 0:
            return False, (
                f"mutmut results command failed (return code {res.returncode}).\n"
                f"STDERR:\n{res.stderr.strip()}"
            )
        out = res.stdout.strip()
        _write_survivors_artifact(
            project, "mutmut", _parse_mutmut_survivors(out), raw=out
        )
        if out:
            m = re.search(r"Survived[^(]*\((\d+)\)", out)
            if m and int(m.group(1)) > 0:
                return False, (
                    f"Mutation testing failed: {m.group(1)} surviving mutant(s) found.\n\n"
                    f"{out}"
                )

        _precheck_ok = True
        return True, ""
    except subprocess.TimeoutExpired:
        return False, (
            "mutmut timed out after 60 minutes. "
            "The test suite may be too slow per mutant, or a mutant caused "
            "an infinite loop. Consider excluding data-only files via "
            "paths_to_exclude in setup.cfg to reduce the mutant count."
        )
    except Exception as e:
        return False, f"Error running mutmut: {e}"
    finally:
        # Bug #42 fix: reconcile the project-root cache with the workdir
        # output, based on whether the precheck succeeded.
        #
        # _precheck_ok is set inside the try block on the success path.
        # If we reach the finally clause without it being set, the
        # precheck failed (or raised); in that case the workdir cache
        # may be partial, so we discard it.
        if _precheck_ok and workdir_cache.exists():
            # 2. No prior cache, precheck succeeded — promote the
            #    workdir output to project root.
            shutil.copy2(workdir_cache, cache_file)
            if stash_dir is not None and Path(stash_dir).exists():
                shutil.rmtree(stash_dir, ignore_errors=True)
        elif not _precheck_ok and stash_dir is not None and Path(stash_dir).exists():
            # 1. Prior cache existed — restore it (project root must be
            #    exactly as it was before this precheck).
            stashed_cache = Path(stash_dir) / ".mutmut-cache"
            if stashed_cache.exists():
                shutil.copy2(stashed_cache, cache_file)
            shutil.rmtree(stash_dir, ignore_errors=True)
        else:
            # 3. No prior cache, precheck failed/raised — discard any
            #    partial workdir output. Leave the project root
            #    cache-free so the next precheck starts clean.
            try:
                cache_file.unlink(missing_ok=True)
            except OSError:
                pass

        shutil.rmtree(workdir, ignore_errors=True)


def compute_mutation_score(project: Path) -> "tuple[bool, float | None, str]":
    """Compute the mutation score, and record the outcome either way.

    Round 35 站2. `_compute_mutation_score` below produces the verdict; this
    is the one place that writes down a run which could not produce one, so
    the ten abort paths do not each have to remember to.

    The artifact's absence now means one thing again: nobody ran the command.
    "The framework ran it and could not measure" is a different fact and gets
    its own record — see :func:`_write_unmeasured_artifact`.
    """
    ok, score, message = _compute_mutation_score(project)
    if not ok:
        _write_unmeasured_artifact(project, reason=message)
    return ok, score, message


def _compute_mutation_score(project: Path) -> "tuple[bool, float | None, str]":
    """Run mutmut in a temp workdir and PUBLISH the result cache to project root.

    Bug #105: the previous design only validated pass/fail (run_mutation_precheck)
    and stashed the project-root .mutmut-cache away. But finalize-gate's
    mutation_testing dimension is evaluated by an LLM sub-agent that parses
    `mutmut results` from the project-root cache. Without a published cache,
    the agent runs `mutmut run` directly from project root, where Bug #91's
    runner rewrite (workdir-only) does not apply — the project setup.cfg has
    no [mutmut] section, mutmut 2.x falls back to `runner = python`, and on
    macOS Homebrew Python 3.11+ (no `python` symlink) it crashes with
    FileNotFoundError, leaving an empty cache and a score of 0.

    This function is the publish side of the protocol: it runs mutmut in a
    workdir with the same isolation/setup.cfg-rewrite machinery as
    run_mutation_precheck, but on success PROMOTES the workdir cache to
    project root so downstream consumers (LLM agent) can read it. The
    score is also returned directly so callers that want to skip the
    parse step can use the float.

    Returns:
        (success, score, message)
        - success: True iff mutmut ran AND produced parseable output
        - score:   0.0–100.0 (killed / (killed + survived) × 100)
                   ⏰ (timeout) and 🤔 (suspicious) count as survived.
                   **None when success is False** — Round 35 站2. It used to
                   be 0.0, and every consumer reads the number rather than
                   the flag, so "the framework could not measure" and "mutmut
                   ran and every mutant survived" arrived as the same value.
                   A live Gate 2 blocked on `mutation_testing scored 0.0,
                   needs 75.0` and told the project to kill mutants that were
                   never produced. Same rule Round 32 站4 applied to
                   _score_pytest, _score_exit_code_binary and
                   _score_pytest_benchmark.
        - message: human-readable status (also written to gate prompt)
    """
    from core.utils.lang_patterns import project_language
    if project_language(project) in ("javascript", "typescript"):
        return _compute_stryker_score(project)

    if not shutil.which("mutmut"):
        return False, None, (
            "mutmut not installed. Required for mutation_testing dimension. "
            "Install: pip install 'mutmut<3'"
        )

    cwd, paths_to_mutate = _resolve_mutmut_workdir(project)
    src_dirs = mutate_dirs(cwd, paths_to_mutate)
    missing = [str(p.relative_to(cwd)) for p in src_dirs if not p.exists()]
    if not src_dirs or missing:
        return False, None, (
            f"paths_to_mutate names {missing or 'nothing'} which does not exist "
            f"under {cwd}; mutmut did not run — check [mutmut] paths_to_mutate "
            f"in setup.cfg (regenerated from the SAB at the P2→P3 handoff)."
        )

    if _is_editable_install(project):
        return False, None, (
            "Project is installed in editable mode (pip install -e). "
            "This prevents mutmut from testing mutations."
        )

    declared_excludes = _read_paths_to_exclude(cwd)
    auto_excludes = _detect_data_only_files(src_dirs)
    auto_excludes = [e for e in auto_excludes if e not in frozenset(declared_excludes)]
    abs_mutate = _abs_paths_to_mutate(cwd, paths_to_mutate)

    workdir = tempfile.mkdtemp(prefix="_mutmut_score.", dir="/tmp")
    cache_file = project / ".mutmut-cache"
    workdir_cache = Path(workdir) / ".mutmut-cache"

    try:
        test_dir = _resolve_test_dir(cwd, project)
        if test_dir is None:
            return False, None, (
                "No test directory found. Mutation testing is meaningless "
                "without tests — cannot proceed."
            )

        # Bug #41 + #91: rewrite setup.cfg for workdir context (mutmut 2.x
        # hardcodes `python`; modern macOS lacks that symlink).
        _copy_setup_cfg_to_workdir(project, workdir, test_dir, cwd=cwd)

        # Round 31 站3: RESUME. The TimeoutExpired branch below has claimed
        # since Bug v26 that the partial cache it publishes lets "the next run
        # resume" — and no run ever did, because every call opens a fresh
        # mktemp workdir and mutmut reads .mutmut-cache from its cwd. Nothing
        # copied it in; the five copy2 calls in this file all copy outward.
        # The observable cost: on the project that motivated Bug v26 the agent
        # ended up running mutmut file by file and accumulating the project-root
        # cache by hand, because the framework's own retry always started from
        # zero and never finished inside the 60-minute cap.
        #
        # Safe because mutmut 2.x keys the cache on source-line CONTENT and
        # `tested_against_hash` (mutmut/cache.py:get_cached_mutation_statuses):
        # edit the source or the tests and the affected mutants go back to
        # UNTESTED. Only work that is still valid is inherited.
        # run_mutation_precheck deliberately does NOT do this — its contract is
        # a fresh verdict every time.
        if _is_resumable_cache(cache_file):
            shutil.copy2(cache_file, workdir_cache)
            print(f"  [mutation] resuming from {cache_file.stat().st_size} "
                  f"bytes of prior results")

        cmd = [
            "mutmut", "run",
            f"--paths-to-mutate={abs_mutate}",
            "-b", "10",
        ]
        cmd.append(f"--tests-dir={test_dir}")
        all_excludes = declared_excludes + auto_excludes
        if all_excludes:
            cmd.append(_paths_to_exclude_flag(all_excludes))

        r = subprocess.run(
            cmd, cwd=workdir, capture_output=True, text=True,
            env=_mutmut_subprocess_env(workdir),  # Bug #142: sandbox pytest plugin autoload
            timeout=get_timeout("mutation", project),
        )
        # mutmut 2.x exit codes:
        #   0 = all mutants killed (rare; full pass)
        #   1 = baseline test failed (no mutants tested) — real failure
        #   2 = some mutants had test exceptions / timeouts — partial
        #       success, cache is still valid, score is meaningful.
        # We treat 0 and 2 as "ran successfully" so the actual score is
        # reported; only 1 (and other unexpected codes) abort.
        if r.returncode not in (0, 2):
            return False, None, (
                f"mutmut run failed (return code {r.returncode}).\n"
                f"STDOUT:\n{r.stdout.strip()[-2000:]}\n"
                f"STDERR:\n{r.stderr.strip()[-2000:]}"
            )

        # mutmut results reads .mutmut-cache only — no pytest invocation, no env= needed (Bug #142)
        res = subprocess.run(
            ["mutmut", "results"], cwd=workdir, capture_output=True, text=True,
            timeout=30,
        )
        out = res.stdout.strip()
        _write_survivors_artifact(
            project, "mutmut", _parse_mutmut_survivors(out), raw=out
        )

        # Score from the sqlite cache (mutmut 2.x). Bug #108: parsing
        # `mutmut results` stdout for emoji counts is broken — that command
        # only prints 🙁 for survived mutants, never 🎉 for killed ones.
        # The authoritative source is the `Mutant` table in the cache db.
        killed, survived = _count_mutmut_results(workdir_cache)
        total = killed + survived

        # Cross-check: if sqlite is 0 but text output says non-zero, sqlite may be corrupt.
        text_total = 0
        if total == 0 and out:
            m = re.search(r"TotalMutants\s*=\s*(\d+)", out)
            if m:
                text_total = int(m.group(1))
        if total == 0 and text_total > 0:
            return False, None, (
                f"mutmut produced 0 mutants in cache but text output shows {text_total} total mutants. "
                f"Cache may be corrupt or unreadable."
            )

        # Round 30 站2: the SCOPE travels with the score. taskq-advance's Gate 2
        # recorded mutation_testing=0 three times with no artifact anywhere
        # stating that 3384 lines were mutated against a SPEC that limited the
        # dimension to 1846 — the number that explains the verdict was the one
        # number the verdict did not carry. This string is the tool_evidence the
        # gate reads, so the scope is now inside the judgement itself.
        # Round 31 站1: spelled by mutmut_report.format_score_message, whose
        # parser sits beside it — the gate's own cross-check could not read the
        # f-string that used to live here.
        if total == 0:
            score = 0.0
            msg = (f"mutmut produced 0 mutants. Score = 0. "
                   f"[scope: {paths_to_mutate}]")
        else:
            score = round(100.0 * killed / total, 1)
            msg = format_score_message(
                killed=killed, survived=survived, score=score,
                scope=paths_to_mutate,
            )

        # Bug #105: PUBLISH the workdir cache to project root. The LLM agent
        # evaluating mutation_testing reads `mutmut results` from this path.
        # On failure we leave the project-root cache untouched so callers
        # can distinguish "we ran mutmut and it crashed" from "we have
        # valid prior results".
        if workdir_cache.exists():
            shutil.copy2(workdir_cache, cache_file)
        else:
            # workdir cache never created (all source excluded). Remove any
            # stale project-root cache so downstream sees a clean zero, not
            # a stale score from a prior run.
            if cache_file.exists():
                cache_file.unlink()

        # Round 31 站2: the framework records its own number. Written after the
        # cache is published so cache_sha256 fingerprints the artifact the gate
        # and any downstream reader will actually see.
        _write_score_artifact(
            project,
            killed=killed, survived=survived, score=score,
            paths_to_mutate=paths_to_mutate,
            paths_to_exclude=all_excludes,
            mutated_files=sum(
                len(list(d.rglob("*.py"))) for d in src_dirs if d.is_dir()
            ),
            cache_path=cache_file,
        )
        return True, score, msg

    except subprocess.TimeoutExpired:
        # Bug v26: publish partial workdir cache so the next
        # compute_mutation_score call resumes from it. mutmut 2.x's
        # `get_cached_mutation_statuses` (`mutmut/__init__.py:709-720`) skips
        # any mutant whose status is not UNTESTED, so a partial cache from a
        # timed-out run is a free head-start — without this, every retry
        # starts from zero and can never finish a large scope on slow
        # runtimes (Python 3.11 + mutmut 2.x + service+storage scope ~700
        # mutants routinely blows the 60-minute STALL_TIMEOUTS["mutation"]
        # cap). An empty / missing workdir_cache means the run never even
        # started evaluating — don't publish a zero-byte file (mutmut will
        # treat it as a fresh empty cache and skip the resume path).
        partial_msg = ""
        if workdir_cache.exists() and workdir_cache.stat().st_size > 0:
            try:
                shutil.copy2(workdir_cache, cache_file)
                partial_msg = (
                    f" (partial cache ({workdir_cache.stat().st_size} bytes) "
                    f"published to {cache_file.name}; the next run copies it "
                    f"into its workdir and resumes from there)"
                )
            except OSError as exc:
                partial_msg = f" (partial-cache publish failed: {exc})"
        return False, None, (
            "mutmut timed out after 60 minutes."
            + partial_msg
            + " Consider excluding data-only files via paths_to_exclude in "
            "setup.cfg."
        )
    except Exception as e:
        return False, None, f"Error running mutmut: {e}"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _compute_stryker_score(project: Path) -> "tuple[bool, float | None, str]":
    """JS/TS variant of compute_mutation_score (StrykerJS)."""
    import json
    report_path = project / "reports" / "mutation" / "mutation.json"
    if not report_path.exists():
        return False, None, (
            f"Stryker report not found at {report_path}. "
            "Run `npx stryker run` first."
        )
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return False, None, f"Failed to read Stryker report: {e}"
    mutants: list = []
    for file_data in (report.get("files") or {}).values():
        mutants.extend(file_data.get("mutants") or [])
    # Timeout counts as survived, matching the mutmut path's documented
    # policy (_count_mutmut_results above) — a timed-out mutant means tests
    # took too long, not that they proved the mutation wrong.
    killed = sum(1 for m in mutants if m.get("status") == "Killed")
    total = len(mutants)
    if total == 0:
        return True, 0.0, "Stryker produced 0 mutants. Score = 0."
    score = round(100.0 * killed / total, 1)
    return True, score, f"killed={killed} total={total} score={score}"
