"""harness/crg_independent.py — framework-owned CRG metrics.

Computes the *architecture* dimension (community_cohesion) independently of the
agent by driving the `code-review-graph` CLI + Python API as a subprocess, then
writing `.sessi-work/crg_metrics.json`. The agent never produces these scores.

CRG is a **required component** (like ruff/mypy/pytest), verified at preflight.
Any failure raises `CrgIndependentError` — there is NO graceful degradation to
agent-reported scores (that would reopen the fabrication path this closes).

The CRG package lives under its own interpreter (the `code-review-graph` console
script's shebang), which is generally NOT the harness interpreter, so the graph
dump runs via subprocess under that interpreter (`crg_dump_communities.py`).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from core.degradation_ledger import record_degradation

_DUMP_SCRIPT = Path(__file__).parent / "ssi" / "scripts" / "crg_dump_communities.py"
_BUILD_TIMEOUT = 600
_POST_TIMEOUT = 300
_DUMP_TIMEOUT = 120


class CrgIndependentError(RuntimeError):
    """Raised when the independent CRG run cannot produce metrics (hard failure)."""


def crg_binary() -> str:
    """Return the path to the `code-review-graph` CLI, or raise if absent."""
    binary = shutil.which("code-review-graph")
    if not binary:
        raise CrgIndependentError(
            "code-review-graph not found on PATH. CRG is a required component "
            "(install it during project setup, like ruff/mypy/pytest). The framework "
            "cannot compute the architecture dimension independently without it."
        )
    return binary


def _crg_interpreter(binary: str) -> str:
    """Return the Python interpreter that has code_review_graph installed.

    `code-review-graph` is a console script; its shebang points at the right
    interpreter. Falls back to the harness interpreter only if the shebang is
    unreadable (the subprocess will then surface an ImportError if wrong).
    """
    try:
        first = Path(binary).read_text(encoding="utf-8", errors="replace").splitlines()[0]
        if first.startswith("#!"):
            interp = first[2:].strip().split()[0]
            if interp and Path(interp).exists():
                return interp
    except (OSError, IndexError):
        pass
    return sys.executable


def _run(cmd: list[str], *, cwd: str | None, timeout: int, label: str) -> str:
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise CrgIndependentError(f"{label} timed out after {timeout}s") from exc
    except OSError as exc:
        raise CrgIndependentError(f"{label} could not be executed: {exc}") from exc
    if proc.returncode != 0:
        raise CrgIndependentError(
            f"{label} failed (rc={proc.returncode}): {(proc.stderr or proc.stdout)[-500:]}"
        )
    return proc.stdout


def _ensure_gitignored(project_root: str) -> None:
    """Ensure `.code-review-graph/` is git-ignored.

    `build` writes a tens-of-MB graph DB there; without this entry
    `commit_and_push_gate` could commit it. CRG's own `init` adds the entry, but a
    manually-installed binary may not have run it. Non-fatal hygiene only.
    """
    gi = Path(project_root) / ".gitignore"
    try:
        existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
        if ".code-review-graph" in existing:
            return
        with gi.open("a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write("# Added by harness crg_independent\n.code-review-graph/\n")
    except OSError:
        pass


def graph_file_set(graph_db: Path) -> set[str]:
    """The distinct source files the graph actually covers, resolved.

    Read-only. An unreadable or schema-less DB yields an empty set, which
    `needs_full_rebuild` then reports as covering nothing — the safe
    direction, since the response is to rebuild rather than to trust it.
    """
    try:
        con = sqlite3.connect(f"file:{graph_db}?mode=ro", uri=True)
    except sqlite3.Error:
        return set()
    try:
        rows = con.execute("SELECT DISTINCT file_path FROM nodes").fetchall()
    except sqlite3.Error:
        return set()
    finally:
        con.close()
    return {str(Path(r[0]).resolve()) for r in rows if r[0]}


def needs_full_rebuild(
    graph_files: set[str], source_files: set[str],
) -> tuple[bool, set[str]]:
    """(stale, files_the_graph_is_missing) for a graph vs the delivered tree.

    Measured on a correct build, taskq-renew clean clone: the two sets were
    equal — 47 files each, zero difference in either direction. So equality
    is the right predicate, and any difference is real staleness:

      - files the project delivers that the graph never saw (the taskq-renew
        defect: 11 of 47), which shrinks the community partition
      - files the graph remembers that the project no longer delivers, whose
        nodes keep contributing to that partition
    """
    return graph_files != source_files, source_files - graph_files


def graph_coverage_gap(metrics: "dict") -> list[str]:
    """The delivered source files the graph never parsed, sorted.

    Round 44 站3. `needs_full_rebuild` above already forces one full rebuild
    when the graph does not cover the delivered tree; what survives that
    rebuild was recorded in the degradation ledger and, since Round 42 站4c,
    counted in the gate result's `calibration` block as
    `graph_files`/`source_files`. Nothing compared the two: a repository-wide
    grep finds one producer and no consumer.

    Measured on taskq-advance's Phase 3, four such residuals — 41 files
    graphed against 47 delivered — while `architecture` scored 91.7 and
    passed. Its final round reached 50/50, so no wrong verdict was observed;
    the gap is that nothing would have stopped one. Station 0 premise 3
    re-measured the predicate on every live project (taskq 20/20,
    taskq-renew 47/47, taskq-api 40/40, taskq-advance 50/50,
    run-all-by-workflow 22/22): full coverage is reachable, so a score
    measured over less than the delivered tree is a score of something else.

    Empty for metrics written before `_unparsed_files` existed — Round 39/40:
    a record predating a field is not a violation, and Round 32/35: an absent
    measurement is not a finding.
    """
    unparsed = metrics.get("_unparsed_files")
    if not isinstance(unparsed, list):
        return []
    return sorted(str(p) for p in unparsed)


def _delivered_sources(root: str) -> set[str]:
    """The project's delivered source files, in its own language."""
    from core.utils.delivery_scope import iter_delivered_files
    from core.utils.lang_patterns import project_language, source_extensions

    exts = source_extensions(project_language(root))
    return {
        str(p.resolve())
        for p in iter_delivered_files(Path(root))
        if p.suffix.lower() in exts
    }


def run_independent_crg(project_root: str, work_dir: str) -> dict:
    """Build the graph, dump communities, compute crg_metrics, write crg_metrics.json.

    Returns the metrics dict. Raises CrgIndependentError on any failure.
    """
    binary = crg_binary()
    root = str(Path(project_root).resolve())

    # 0. Keep the graph DB out of version control.
    _ensure_gitignored(root)

    # 1. Build (first time) or incremental update (only re-parses changed files), then
    #    post-process to (re)compute communities. Using `update` when a graph already
    #    exists keeps re-finalize cheap — important under the Gate 4 auto-fix loop, which
    #    re-runs finalize_gate (and thus this) every round.
    #
    #    Round 37: cheap is only worth having if it is also true. taskq-renew's
    #    graph.db read last_build_type=incremental with 11 files / 165 nodes /
    #    12 communities while the project delivered 47 files; a full build on a
    #    clean clone gave 47 files / 802 nodes / 32 communities and
    #    architecture_score 57.1 — exactly CI's number, against the 77.8 the
    #    incremental graph produced and Gate 4 folded into a passing composite.
    #    So the update result is reconciled against the delivered tree and a
    #    full build is forced when it does not cover it. Once — if the sets
    #    still differ afterwards (a file CRG cannot parse), that residue is
    #    recorded rather than looped on.
    _graph_db = Path(root) / ".code-review-graph" / "graph.db"
    _sub = "update" if _graph_db.exists() else "build"
    _run([binary, _sub], cwd=root, timeout=_BUILD_TIMEOUT, label=f"code-review-graph {_sub}")

    _sources = _delivered_sources(root)
    _stale, _missing = needs_full_rebuild(graph_file_set(_graph_db), _sources)
    if _stale and _sub == "update":
        record_degradation(
            root, "crg:graph-scope",
            f"incremental graph covered {len(graph_file_set(_graph_db))} of "
            f"{len(_sources)} delivered source file(s) — rebuilding in full",
            why=f"{len(_missing)} file(s) missing from the graph",
        )
        _run([binary, "build"], cwd=root, timeout=_BUILD_TIMEOUT,
             label="code-review-graph build (forced: stale graph)")
        _sub = "build"

    _run([binary, "postprocess"], cwd=root, timeout=_POST_TIMEOUT, label="code-review-graph postprocess")

    _graph_files = graph_file_set(_graph_db)
    _residual_stale, _residual_missing = needs_full_rebuild(_graph_files, _sources)
    if _residual_stale:
        record_degradation(
            root, "crg:graph-scope",
            f"after a full build the graph covers {len(_graph_files)} file(s) "
            f"and the project delivers {len(_sources)} — the architecture "
            f"score is measured over the graph's set",
            why=f"{len(_residual_missing)} delivered file(s) still unparsed",
        )

    # 2. Dump communities via CRG's own interpreter.
    interp = _crg_interpreter(binary)
    stdout = _run(
        [interp, str(_DUMP_SCRIPT), root],
        cwd=None, timeout=_DUMP_TIMEOUT, label="crg_dump_communities",
    )
    try:
        recon = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise CrgIndependentError(f"crg_dump_communities produced invalid JSON: {exc}") from exc

    # 3. Reuse the existing deterministic cohesion formula.
    _scripts_dir = str(_DUMP_SCRIPT.parent)
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
    from crg_analysis import compute_community_cohesion_score  # reused formula

    # Per-project calibration (crg_cohesion_healthy / crg_excludes in
    # .methodology/harness_config.json). Defensive import: this module
    # normally runs under the harness interpreter where `core` is on the
    # path, but odd sys.path setups must not break the gate.
    try:
        from core.harness_config import get_crg_settings
        _crg_cfg = get_crg_settings(root)
    except ImportError:
        _crg_cfg = {"cohesion_healthy": None, "excludes": []}

    cohesion = compute_community_cohesion_score(
        recon.get("communities", []),
        cohesion_healthy=_crg_cfg["cohesion_healthy"],
        extra_excludes=_crg_cfg["excludes"],
        project_root=root,
    )

    # 4. Large-function penalty (Phase 1 gatekeeper).
    #    crg_dump_communities.py includes large_functions_critical when
    #    find_large_functions_func is available. Each function ≥ 500 lines
    #    penalises the architecture score by 5 pts, capped at 20.
    critical_fns = recon.get("large_functions_critical", [])
    lf_penalty = min(len(critical_fns) * 5, 20)
    architecture_score = round(max(0.0, (cohesion.get("score") or 0.0) - lf_penalty), 1)

    if lf_penalty > 0:
        print(
            f"[crg] large_functions_penalty: -{lf_penalty} pts "
            f"({len(critical_fns)} function(s) ≥ 500 lines) "
            f"cohesion {cohesion.get('score'):.1f} → architecture_score {architecture_score:.1f}",
            file=sys.stderr,
        )

    metrics = {
        "community_cohesion": cohesion,
        "large_functions_critical": critical_fns,
        "large_functions_penalty": lf_penalty,
        "architecture_score": architecture_score,
        "_source": "framework-independent",
        # Round 37: the denominator travels with the number. A reader of a
        # past crg_metrics.json can now tell whether the score was measured
        # over the whole delivered tree or over a fraction of it — the
        # question nobody could answer about taskq-renew's 77.8.
        "_graph_files": len(_graph_files),
        "_source_files": len(_sources),
        # Round 44 站3: the names, not just the shortfall. A count cannot be
        # acted on; `harness_bridge` refuses the architecture dimension on
        # this list and prints it, so the operator learns which file CRG
        # could not parse (R24 站1 — a block carries the remediation).
        # Repo-relative and sorted: the absolute paths above are resolved for
        # set arithmetic and are noise in an operator message.
        "_unparsed_files": sorted(
            str(Path(p).relative_to(Path(root).resolve()))
            if str(p).startswith(str(Path(root).resolve()))
            else str(p)
            for p in _residual_missing
        ),
        "_build_type": _sub,
    }
    out = Path(work_dir) / "crg_metrics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics
