"""Gate evaluation commands (run-gate, finalize-gate, env-check pair, gate4-tag, mutation-test-score).

Extracted verbatim from harness_cli.py (方案六); helpers moved home in
絞殺者續章 S4 — this module no longer imports harness_cli (all
dependencies are direct stdlib/core/harness imports). harness_cli still
re-exports the cmd_* names, so `from harness_cli import cmd_x` works.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from harness.harness_bridge import GateBlockedError

from cli import _shared
from cli._shared import gate_result_paths
from core import claude_md
from core.atomic_io import atomic_write_json, file_lock, state_lock_path
from core.canonical_form import canonical_form
from core.harness_config import get_timeout, get_value
from core.phase_topology import EXIT_GATE_MAP
from core.quality_gate import gate1_evidence
from core.quality_gate import spec_coverage
from core.quality_gate.cov_utils import resolve_fr_scoped_src_files
from core.quality_gate.cov_utils import _fr_source_files_from_imports  # noqa: F401  (re-export: tests/cli/test_gate_cmds_cli.py imports it from here)
from core.quality_gate.spec_coverage import _get_test_directories, _git_test_patterns
from core.state_io import StateCorruptError, load_quality_manifest, load_state
from core.utils.script_loader import load_harness_script
from harness import tool_checks
from core.utils.project_layout import ProjectLayout
from core.quality_gate.mutation_enforcer import compute_mutation_score


def cmd_run_gate(args: argparse.Namespace) -> int:
    """OTEL span wrapper for run-gate. Business logic in _cmd_run_gate_impl."""
    try:
        from core.observability import init_tracer
        _tracer = init_tracer(Path(getattr(args, "project", ".")).resolve())
    except Exception as exc:
        print(f"[WARN] run-gate: OTEL tracer init failed, proceeding without tracing: {exc}", file=sys.stderr)
        _tracer = None
    if _tracer is None:
        return _cmd_run_gate_impl(args)
    with _tracer.start_as_current_span("run_gate") as _span:
        _span.set_attribute("harness.gate", getattr(args, "gate", 1))
        _span.set_attribute("harness.phase", getattr(args, "phase", 0))
        _fr = getattr(args, "fr_id", None)
        if _fr:
            _span.set_attribute("harness.fr_id", str(_fr))
        _span.set_attribute("harness.delta", bool(getattr(args, "delta", False)))
        _exit = _cmd_run_gate_impl(args)
        _span.set_attribute("harness.exit_code", _exit)
        _span.set_attribute("harness.blocked", _exit != 0)
        return _exit


def cmd_finalize_gate(args: argparse.Namespace) -> int:
    """OTEL span wrapper for finalize-gate. Business logic in _cmd_finalize_gate_impl."""
    try:
        from core.observability import init_tracer
        _tracer = init_tracer(Path(getattr(args, "project", ".")).resolve())
    except Exception as exc:
        print(f"[WARN] finalize-gate: OTEL tracer init failed, proceeding without tracing: {exc}", file=sys.stderr)
        _tracer = None
    if _tracer is None:
        return _cmd_finalize_gate_impl(args)
    with _tracer.start_as_current_span("finalize_gate") as _span:
        _span.set_attribute("harness.gate", args.gate)
        _span.set_attribute("harness.phase", args.phase)
        _fr = getattr(args, "fr_id", None)
        if _fr:
            _span.set_attribute("harness.fr_id", str(_fr))
        try:
            _exit = _cmd_finalize_gate_impl(args)
        except Exception as _exc:
            from opentelemetry.trace import StatusCode  # type: ignore[import-not-found]
            _span.record_exception(_exc)
            _span.set_status(StatusCode.ERROR, str(_exc))
            raise
        _span.set_attribute("harness.exit_code", _exit)
        _span.set_attribute("harness.blocked", _exit != 0)
        # score/quality_complete are set by _impl on the success path via args._span_*
        _score = getattr(args, "_span_score", None)
        if _score is not None:
            _span.set_attribute("harness.score", float(_score))
            _span.set_attribute("harness.quality_complete",
                                bool(getattr(args, "_span_quality_complete", False)))
        return _exit


def cmd_run_env_check(args: argparse.Namespace) -> int:
    """Print project-aware environment evaluation prompt for Claude.

    Reads SAD.md + SRS.md from the target project, constructs an evaluation
    prompt that asks Claude to identify required env vars, CLI tools, and
    infrastructure services by reading the project's own documentation,
    then verify each against the current environment.

    Claude must evaluate inline and write .sessi-work/env_check_result.json.
    """
    from harness.harness_bridge import HarnessBridge

    project = str(Path(args.project).resolve())
    fr_id = getattr(args, "fr_id", None) or None

    bridge = HarnessBridge()
    ctx = bridge.prepare_env_check(
        project_root=project,
        phase=args.phase,
        fr_id=fr_id,
    )

    if not ctx.sad_excerpt and not ctx.srs_excerpt:
        print(
            "[WARN] Neither SAD.md nor SRS.md found in project. "
            "Env check will have no project context to evaluate.",
            file=sys.stderr,
        )

    # Ensure .sessi-work/ exists before writing the sentinel and result.
    Path(ctx.work_dir).mkdir(parents=True, exist_ok=True)

    # Write sentinel so finalize-env-check can verify run-env-check was called.
    sf = _sentinel_env_path(Path(project))
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text(f"{datetime.now(timezone.utc).isoformat()}\n", encoding="utf-8")
    print(f"[SENTINEL] {sf.relative_to(Path(project))} written.")

    # Spawn sub-agent to perform the env check inline.
    # Uses bypassPermissions so the agent can run psql, docker, etc.
    # --setting-sources "" blocks user-level CLAUDE.md/hooks (isolation).
    prompt = ctx.evaluation_prompt()
    cli = shutil.which("claude")
    if not cli:
        print("[ERROR] claude CLI not found.", file=sys.stderr)
        return 1

    cmd = [
        cli, "-p", prompt,
        "--output-format", "json",
        "--max-turns", "70",
        "--no-session-persistence",
        "--setting-sources", "",
        "--permission-mode", "bypassPermissions",
        "--disable-slash-commands",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
    ]
    print("[INFO] Spawning env-check sub-agent...")
    from core.agent_spawner import _child_env
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=get_timeout("subprocess", project),
            cwd=str(Path(project).resolve()),
            env=_child_env(),
        )
    except subprocess.TimeoutExpired as _te:
        # Observability (same class as the sessions_spawn ERROR fix):
        # TimeoutExpired carries the partial captured output — print its tail
        # so a timeout is never a black box.
        for _label, _stream in (("stdout", _te.output), ("stderr", _te.stderr)):
            if _stream:
                _txt = (_stream.decode("utf-8", "replace")
                        if isinstance(_stream, bytes) else str(_stream))
                print(f"[TIMEOUT-{_label}] ...{_txt[-500:]}", file=sys.stderr)
        # Bug #138 root-cause fix (2026-07-02): "process exited within the
        # timeout" is the wrong success proxy. The check has already succeeded
        # once env_check_result.json is (re)written — the sub-agent's
        # post-artifact wrap-up (finalize-env-check + final response) can
        # legitimately outlive the timeout (observed: artifact at 143s, kill
        # at 300s). Fall through to the artifact verification below when the
        # artifact was written by THIS spawn (mtime >= this run's sentinel);
        # fail only when it wasn't. A leftover artifact from a previous run
        # is older than the sentinel and is NOT accepted.
        _rp = Path(project) / ".sessi-work" / "env_check_result.json"
        try:
            _fresh = _rp.exists() and _rp.stat().st_mtime >= sf.stat().st_mtime
        except OSError:
            _fresh = False
        if not _fresh:
            print(
                f"[ERROR] env-check sub-agent timed out after "
                f"{get_timeout('subprocess', project)}s without writing "
                f"env_check_result.json.",
                file=sys.stderr,
            )
            return 1
        print(
            "[WARN] env-check sub-agent timed out during wrap-up, but "
            "env_check_result.json was written by this run — proceeding "
            "with artifact verification.",
            file=sys.stderr,
        )
        proc = None

    if proc is not None and proc.returncode != 0:
        print(f"[ERROR] env-check sub-agent failed (exit {proc.returncode}).", file=sys.stderr)
        if proc.stderr:
            print(proc.stderr[:500], file=sys.stderr)
        return 1

    result_path = Path(project) / ".sessi-work" / "env_check_result.json"
    if not result_path.exists():
        print("[ERROR] env-check sub-agent did not write env_check_result.json.", file=sys.stderr)
        return 1

    _fab = _verify_env_check_claims(Path(project))
    if _fab:
        print("[ERROR] env-check agent fabricated claims:\n  " + "\n  ".join(_fab), file=sys.stderr)
        return 1

    # Bug #127 root-cause fix (2026-06-27): reflect the agent's `ready` flag in
    # this command's exit code so callers (workflows / CI) can branch on `$?`
    # without spawning a second sub-agent or parsing free-form LLM output.
    # Previously the command always returned 0 even when the agent wrote
    # ready=false, forcing every workflow JS to do its own LLM-orchestrator
    # judgment pass on the result — fragile and prone to hallucinated failures.
    try:
        _ready_data = json.loads(result_path.read_text(encoding="utf-8"))
        _ready = bool(_ready_data.get("ready", False)) if isinstance(_ready_data, dict) else False
    except (ValueError, OSError):
        _ready = False
    if not _ready:
        print(
            f"[BLOCKED] env-check sub-agent wrote ready=false. "
            f"Re-run after fixing the missing items listed in {result_path}."
        )
        return 1

    print(f"[INFO] env-check complete. Result: {result_path}")
    return 0


def cmd_finalize_env_check(args: argparse.Namespace) -> int:
    """Verify env_check_result.json and report environment readiness.

    Reads the result written by Claude after inline evaluation, validates
    the sentinel exists (anti-fabrication), and prints a pass/fail summary.
    Exits 0 when ready, 1 when items are missing.
    """
    from harness.harness_bridge import HarnessBridge

    project = Path(args.project).resolve()
    fr_id = getattr(args, "fr_id", None) or None

    # Sentinel check — prevent fabricated results
    sf = _sentinel_env_path(project)
    if not sf.exists():
        print(
            f"\n[BLOCKED] Sentinel not found: {sf.relative_to(project)}\n"
            f"  Fix: `run-env-check` must be called before finalize-env-check.\n"
            f"  Writing env_check_result.json directly is not permitted."
        )
        return 1

    # Staleness check: env_check_result.json must not predate the sentinel.
    # This catches cases where an old result file is reused after a new
    # run-env-check invocation without re-running the evaluation.
    sentinel_time: datetime | None = None
    try:
        sentinel_time = datetime.fromisoformat(sf.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        pass  # non-fatal — sentinel exists, timestamp unreadable

    if sentinel_time is not None:
        result_path = project / ".sessi-work" / "env_check_result.json"
        if result_path.exists():
            try:
                _data = json.loads(result_path.read_text(encoding="utf-8"))
                _checked_at_str = _data.get("checked_at", "")
                if _checked_at_str:
                    _checked_at = datetime.fromisoformat(
                        _checked_at_str.replace("Z", "+00:00")
                    )
                    # Allow 10 s tolerance for the sentinel being written
                    # just before the sub-agent starts.
                    if _checked_at < sentinel_time - timedelta(seconds=10):
                        print(
                            "[WARN] env_check_result.json predates the sentinel — "
                            "result may be from a previous run. "
                            "Re-run: python harness_cli.py run-env-check "
                            f"--phase {args.phase} --project {project}"
                        )
            except (ValueError, OSError, KeyError):
                pass  # malformed JSON handled by finalize_env_check

    bridge = HarnessBridge()
    ctx = bridge.prepare_env_check(
        project_root=str(project),
        phase=args.phase,
        fr_id=fr_id,
    )

    ready, message = bridge.finalize_env_check(ctx)

    print(f"\n{'='*60}")
    print(f"finalize-env-check: Phase {args.phase} | project: {project.name}")
    print(f"{'='*60}")
    print(f"\n{message}")

    if ready:
        print(f"\n[READY] Environment is ready for Phase {args.phase} development.")
        return 0
    else:
        print("\n[BLOCKED] Fix the missing items above, then re-run run-env-check.")
        return 1


def cmd_gate4_tag(args: argparse.Namespace) -> int:
    """Create annotated git tag for Gate 4 pass using composite score from gate4_result.json.

    Reads gate4_result.json (from .sessi-work/, .methodology/, or project root),
    extracts composite_score, and creates:
      harness-v4-YYYYMMDD-score<SCORE>

    Usage:
      python harness_cli.py gate4-tag --project .
    """
    project = Path(args.project).resolve()

    # Locate gate4_result.json
    candidates = gate_result_paths(project, 4)
    g4_path = next((p for p in candidates if p.exists()), None)
    if g4_path is None:
        print("[ERROR] gate4_result.json not found. Run finalize-gate --gate 4 first.")
        return 1

    try:
        g4 = json.loads(g4_path.read_text(encoding="utf-8"))
        score = g4.get("composite_score", g4.get("total_score"))
    except Exception as exc:
        print(f"[ERROR] Failed to parse gate4_result.json: {exc}")
        return 1

    if score is None:
        score_str = "XX"
        print("[WARN] composite_score not found in gate4_result.json — tag will use 'XX'.")
    else:
        try:
            score_str = str(int(round(float(score))))
        except (TypeError, ValueError):
            score_str = "XX"

    from datetime import date as _date
    today = _date.today().strftime("%Y%m%d")
    tag_name = f"harness-v4-{today}-score{score_str}"
    tag_msg = f"Gate 4 PASS (score {score_str})"

    result = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(project), "tag", "-a", tag_name, "-m", tag_msg],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"[ERROR] git tag failed:\n{result.stderr.strip()}")
        return 1

    print(f"[OK] Created tag: {tag_name} ({tag_msg})")
    print("  To push: git push origin --tags")
    return 0


def cmd_mutation_test_score(args: argparse.Namespace) -> int:
    """Compute mutation_testing score by running mutmut in a temp workdir.

    Bug #105: this is the publish-side counterpart to the in-process
    `run_mutation_precheck`. finalize-gate's mutation_testing dimension is
    evaluated by an LLM sub-agent that previously ran `mutmut run` directly
    from the project root, where Bug #91's runner rewrite (workdir-only) did
    not apply. On macOS Homebrew Python 3.11+ this crashes with
    FileNotFoundError 'python', leaving the .mutmut-cache empty and the
    score at 0.

    This command wraps :func:`compute_mutation_score`, which runs mutmut
    in a workdir (with the Bug #41 setup.cfg rewrite + Bug #91 runner fix
    applied) and PROMOTES the workdir cache to project root on success.
    The LLM agent should call this command instead of running
    `mutmut run` itself.

    Exit codes:
      0 — mutmut ran and produced a score (printed as JSON)
      1 — mutmut missing / crashed / no parseable output
    """
    project = Path(args.project).resolve()
    if not project.exists():
        print(f"[ERROR] project root not found: {project}", file=sys.stderr)
        return 1
    success, score, msg = compute_mutation_score(project)
    # Machine-readable single-line JSON, easy for the LLM agent to parse.
    import json as _json
    print(_json.dumps({
        "success": success,
        "score": score,
        "message": msg,
        "cache_path": str(project / ".mutmut-cache"),
    }, ensure_ascii=False))
    return 0 if success else 1




# --- helpers moved verbatim from harness_cli.py (絞殺者續章 S4b) ---

def _sentinel_env_path(project: Path) -> Path:
    """Return the sentinel file path for env-check."""
    d = project / ".sessi-work" / "sentinels"
    return d / "env_check.flag"


def _verify_env_check_claims(project: Path) -> "list[str]":
    """A2: independently re-verify the cli_tools / env_vars the env-check agent
    claimed present. Returns fabrication findings (empty = all claims hold up).

    Only claims of `present: true` are checked — tools/vars the agent reported as
    absent/optional are not forced. infra_services (DB/docker) stay agent-reported
    (the framework cannot reliably probe them here).
    """
    result_path = project / ".sessi-work" / "env_check_result.json"
    if not result_path.exists():
        return []
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    findings: list[str] = []
    # Tools whose fast checks (PATH/venv-bin/semantic-name) all missed are
    # deferred here instead of probed inline — see the batched concurrent
    # probe below.
    _pending_probes: list[tuple[str, str, dict, list[str]]] = []
    for t in data.get("cli_tools", {}).get("required", []):
        if isinstance(t, dict) and t.get("present") and t.get("name"):
            raw_name = str(t["name"])
            # Strip parenthetical annotations added by sub-agents (e.g. "python3 (.venv)")
            # and take only the first token so "python3 -m pip" → "python3".
            _stripped = re.sub(r"\s*\(.*?\)\s*$", "", raw_name).strip()
            name = _stripped.split()[0] if _stripped else raw_name
            if not name:
                continue
            # v2.13 Bug #123 fix: skip framework-internal subcommands.
            # Names ending in `.py` (e.g. "harness_cli.py finalize-env-check") are
            # subcommands of framework scripts, not standalone PATH tools — they
            # never appear in `shutil.which()` results. Without this skip, every
            # env-check that reports a framework subcommand FAILs with a false
            # "fabricated claim" finding, blocking P3/P5/P7 entry.
            if name.lower().endswith(".py"):
                continue
            _found = shutil.which(name) is not None
            _bindir = "Scripts" if os.name == "nt" else "bin"
            if not _found:
                # PATH miss: also check venv-local bin/ and Python import as fallbacks.
                # Covers tools installed only inside .venv and Python packages (e.g.
                # pydantic) that are not CLI binaries but are valid "present" claims.
                #
                # Bug #129 root-cause fix (2026-07-02): probe project-local venvs
                # (.venv/venv) directly, not only $VIRTUAL_ENV. Orchestrated runs
                # invoke `.venv/bin/python harness_cli.py ...` without activating,
                # so VIRTUAL_ENV is never exported and the old probe was dead code
                # there — honest claims about venv-only tools were flagged as
                # fabricated. Also normalize python-version-semantic names
                # ("python311" → "python3.11"): sub-agents name the interpreter
                # after the SAD version string, but the binary is `python3.11`.
                # A wrong-version claim (e.g. python312 with only 3.11 installed)
                # still fails every probe and stays flagged.
                _cands = [name]
                _pv = re.fullmatch(r"python[-_.]?(\d)[-_.]?(\d+)", name.lower())
                if _pv:
                    _cands.append(f"python{_pv.group(1)}.{_pv.group(2)}")
                _venv_dirs = [os.environ.get("VIRTUAL_ENV", "")]
                _venv_dirs += [str(project / d) for d in (".venv", "venv")]
                for _cn in _cands:
                    if _cn != name and shutil.which(_cn):
                        _found = True
                    for _vd in _venv_dirs:
                        if _vd and os.path.exists(os.path.join(_vd, _bindir, _cn)):
                            _found = True
                            break
                    if _found:
                        break
                if not _found:
                    # Bug #128 root-cause fix (2026-06-27): semantic venv-Python names
                    # like "venv-python", "python-venv", "venv-python3" are LOGICAL
                    # names meaning "the Python interpreter inside the project's
                    # virtualenv", not literal PATH binaries. The agent's claim is
                    # honest when (a) the running interpreter is itself a venv
                    # interpreter (`sys.prefix != sys.base_prefix`), or (b) a
                    # project-local venv (.venv/venv) exists and contains a Python
                    # binary. Without this fallback, every project using venv-
                    # semantic naming gets a false "fabricated claim" finding and
                    # P3/P5/P7 entry is wrongly blocked. Generalization: any name
                    # whose lowercased tokens contain both "venv" and "python"
                    # is treated as a venv-Python semantic name.
                    _name_lc = name.lower()
                    if "venv" in _name_lc and "python" in _name_lc:
                        try:
                            if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
                                _found = True
                            else:
                                exe_name = "python.exe" if os.name == "nt" else "python3"
                                bindir = "Scripts" if os.name == "nt" else "bin"
                                for _venv_dir in (".venv", "venv"):
                                    _cand = project / _venv_dir / bindir / exe_name
                                    if _cand.exists():
                                        _found = True
                                        break
                        except Exception as exc:
                            print(f"[WARN] tool-check: venv-Python semantic-name probe for "
                                  f"'{name}' failed: {exc}", file=sys.stderr)
                if not _found:
                    # Python package fallback: "import <name>" via the current interpreter.
                    # src-layout projects (e.g. 03-development/src/taskq) are importable
                    # only with the project's src root on PYTHONPATH — the deliverable
                    # package is a valid "present" claim even before pip install.
                    _pkg = name.replace("-", "_")
                    _import_env = {**os.environ}
                    try:
                        _src_dir = ProjectLayout(project).active_src_dir
                        if _src_dir.is_dir():
                            _import_env["PYTHONPATH"] = os.pathsep.join(
                                p for p in (str(_src_dir), _import_env.get("PYTHONPATH", "")) if p
                            )
                    except Exception as exc:
                        print(f"[WARN] tool-check: could not resolve active_src_dir for "
                              f"PYTHONPATH (import probe for '{name}' may miss src-layout "
                              f"packages): {exc}", file=sys.stderr)
                    # Bug #129: try the project venv's python too — whether a
                    # plugin-only package (e.g. pytest-cov) verifies must not
                    # depend on which interpreter happens to run harness_cli.
                    _interps = [sys.executable]
                    _py_exe = "python.exe" if os.name == "nt" else "python"
                    for _vd in (".venv", "venv"):
                        _vp = project / _vd / _bindir / _py_exe
                        if _vp.exists():
                            _interps.append(str(_vp))
                    # Defer the actual subprocess spawn: several unresolved
                    # tools each sequentially spawning up to len(_interps)
                    # `import <pkg>` probes (5s timeout each) can serialize
                    # to tens of seconds on this blocking CLI path. Batch
                    # all deferred probes below and run them concurrently.
                    _pending_probes.append((raw_name, _pkg, _import_env, _interps))
                    continue
            if not _found:
                findings.append(
                    f"cli_tool '{raw_name}': claimed present, but not found on PATH, "
                    f"in $VIRTUAL_ENV/bin/, or via Python import"
                )

    if _pending_probes:
        def _probe_import(item: "tuple[str, str, dict, list[str]]") -> "tuple[str, bool]":
            _raw_name, _pkg, _import_env, _interps = item
            for _interp in _interps:
                try:
                    _r = subprocess.run(
                        [_interp, "-c", f"import {_pkg}"],
                        capture_output=True, timeout=5, env=_import_env,
                    )
                    if _r.returncode == 0:
                        return _raw_name, True
                except Exception as exc:
                    print(f"[WARN] tool-check: import probe for '{_pkg}' via "
                          f"{_interp} failed: {exc}", file=sys.stderr)
            return _raw_name, False

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(8, len(_pending_probes))
        ) as _ex:
            for _raw_name, _found_import in _ex.map(_probe_import, _pending_probes):
                if not _found_import:
                    findings.append(
                        f"cli_tool '{_raw_name}': claimed present, but not found on PATH, "
                        f"in $VIRTUAL_ENV/bin/, or via Python import"
                    )

    for v in data.get("env_vars", {}).get("required", []):
        if isinstance(v, dict) and v.get("present") and v.get("name"):
            name = str(v["name"])
            if name not in os.environ:
                findings.append(f"env_var '{name}': claimed present, but not set")
    return findings



# --- gate evaluation engine (moved verbatim from harness_cli.py, S4h) ---

# Tier 3 dimensions that require Devil's Advocate (A3) and high-score confirmation (A4)
_TIER3_DIMS: frozenset[str] = frozenset({
    "architecture", "readability", "error_handling", "documentation", "performance",
})
# Per-dim score file directory (relative to project root) — legacy fallback only


def _check_fr_test_file_exists(project: Path, fr_id: str) -> tuple[bool, str]:
    """Gate 1: verify a test file exists for the given FR (TDD RED phase).

    Accepts test_fr07.py or test_fr7.py naming. Skips non-standard FR-IDs.
    Called during cmd_finalize_gate Gate 1 path.
    """
    m = re.match(r"FR-(\d+)", fr_id, re.IGNORECASE)
    if not m:
        return True, ""
    num = m.group(1).zfill(2)
    test_dirs = _get_test_directories(project)
    if not test_dirs:
        test_dirs = [project / "tests"]  # default fallback
        
    patterns = [f"test_fr{num}.py", f"test_fr{str(int(num))}.py"]
    for test_dir in test_dirs:
        for pat in patterns:
            if (test_dir / pat).exists():
                return True, ""
    return False, (
        f"[BLOCKED] FR test file missing: tests/test_fr{num}.py\n"
        "  TDD requires a test file BEFORE implementation is merged.\n"
        "  Create tests/test_fr{num}.py with at minimum one failing test."
    )

def _check_red_phase_ordering(project: Path, fr_id: str) -> tuple[bool, str]:
    """D1 extension: test first commit must be an ancestor of source first commit.

    Uses git ancestry (merge-base --is-ancestor) rather than author timestamps:
    immune to clock skew, sub-second jitter, and glob mis-matches that pick up
    wrong files in nested test directories (e.g. 03-development/tests/).

    Source exclude uses :(glob,exclude) magic pathspec to recursively skip ALL
    test directories and test files, regardless of nesting depth — fixing the
    issue where :(exclude)tests/ only excluded the repo-root tests/ directory.

    Supports configurable source_patterns in project.json for non-standard layouts.
    TDD_JITTER_TOLERANCE is no longer needed and is ignored.
    """
    m = re.match(r"FR-(\d+)", fr_id, re.IGNORECASE)
    if not m:
        return True, ""
    num = m.group(1).zfill(2)
    num_raw = str(int(num))
    test_patterns = _git_test_patterns(project, num, num_raw)

    def _first_sha(patterns: list[str],
                   exclude_globs: list[str] | None = None) -> str | None:
        """Return the SHA of the earliest 'A'dd commit matching any pattern.

        Uses --format='%at %H' to get timestamp + SHA, then returns the SHA
        of the earliest match (handles files added, deleted, re-added).
        exclude_globs uses :(glob,exclude) pathspec — recursively excludes any
        path matching the pattern at any directory depth.
        """
        cmd = ["git", "-C", str(project), "log", "--diff-filter=A",
               "--format=%at %H", "--"]
        cmd.extend(patterns)
        if exclude_globs:
            for exc in exclude_globs:
                cmd.append(f":(glob,exclude){exc}")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            return None
        best: tuple[float, str] | None = None
        for line in r.stdout.splitlines():
            parts = line.strip().split(" ", 1)
            if len(parts) == 2:
                try:
                    ts, sha = float(parts[0]), parts[1].strip()
                    if best is None or ts < best[0]:
                        best = (ts, sha)
                except ValueError:
                    continue
        return best[1] if best else None

    test_sha = _first_sha(test_patterns)
    if test_sha is None:
        return False, (
            f"[BLOCKED] D1-RED: tests/test_fr{num}.py has no git history.\n"
            "  Commit the failing test BEFORE implementing the source."
        )

    # Source glob patterns — :(glob,exclude) recursively excludes ALL test
    # directories at any depth (fixes the 03-development/tests/ mis-match).
    src_patterns = [
        f":(glob)**/fr{num_raw}*",
        f":(glob)**/*fr_{num_raw}*",
        f":(glob)**/*fr{num}*",
    ]
    _src_exclude = ["**/tests/**", "**/test_*.py"]

    config_path = project / "project.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            overrides = config.get("source_patterns", {})
            fr_overrides = overrides.get(f"FR-{num_raw}", overrides.get(f"FR-{num}", []))
            if fr_overrides:
                src_patterns = fr_overrides if isinstance(fr_overrides, list) else [fr_overrides]
        except (json.JSONDecodeError, OSError):
            pass

    src_sha = _first_sha(src_patterns, exclude_globs=_src_exclude)
    if src_sha is None:
        return True, ""   # no source committed yet — TDD-RED phase is valid

    # Ancestry check: test_sha must be an ancestor of src_sha.
    # exit 0 → test came before source → OK (RED before GREEN).
    # exit 1 → source is not descended from test → source committed first → BLOCKED.
    try:
        anc = subprocess.run(
            ["git", "-C", str(project), "merge-base", "--is-ancestor",
             test_sha, src_sha],
            capture_output=True, timeout=10,
        )
        if anc.returncode != 0:
            return False, (
                f"[BLOCKED] D1-RED: Source was committed before test for {fr_id}.\n"
                f"  test commit : {test_sha[:12]}\n"
                f"  source commit: {src_sha[:12]}\n"
                "  TDD requires RED (failing test commit) → GREEN (source commit).\n"
                "  The test file's first commit must be an ancestor of the source "
                "file's first commit on the current branch."
            )
    except subprocess.TimeoutExpired:
        return True, ""   # ancestry check timed out → fail-open (non-fatal)
    return True, ""

def _print_fr_scoped_overrides_py(
    project: str,
    fr_id: str,
    test_file: str,
    src_dir: str,
    manifest_data: dict,
    *,
    non_code_frs: set[str],
    cov_threshold: int,
) -> None:
    """Print Gate-1 FR-scoped tool commands for a Python project."""
    # Resolution logic (fr_module_traceability → owned path, package-dir glob
    # fallback, AST-import detection, manual fr_scope_overrides) lives in
    # core.quality_gate.cov_utils so cli/fr_cmds.py's COVERAGE-FIX prompt can
    # reuse the SAME per-FR scope instead of measuring the whole src tree.
    src_files = resolve_fr_scoped_src_files(project, fr_id, test_file, src_dir, manifest_data)

    if not src_files and fr_id in non_code_frs:
        print(
            f"\n[FR-SCOPED TOOL OVERRIDES — {fr_id}]\n"
            f"Gate 1 scope is single_fr. Replace the project-wide defaults in\n"
            f"evaluate_dimension.md with these FR-scoped commands:\n\n"
            f"test_coverage — {fr_id} is declared as a non-code FR "
            f"(no scoreable source to measure):\n"
            f"  echo 'NON_CODE_FR: coverage not applicable'\n"
            f"  Score this dimension as {cov_threshold} (= threshold). "
            f"Infrastructure/config FRs are exempt from coverage measurement.\n"
            f"  Set tool_evidence = 'non-code FR: {fr_id} declared in fr_non_code'\n\n"
            f"linting — lint only the FR source directory:\n"
            f"  python3 -m ruff check {src_dir}/ --extend-ignore RUF001,RUF002,RUF003 2>&1 | head -200\n\n"
            f"type_safety — type-check only the FR source directory:\n"
            f"  python3 -m pyright {src_dir}/ --outputjson 2>&1 | head -200\n"
        )
        return

    if src_files:
        include_flag = ",".join(src_files)
        cov_cmd = (
            f"  python3 -m coverage run -m pytest {test_file} "
            f"&& python3 -m coverage json --include=\"{include_flag}\" -o - \\\n"
            f"    || PYTHONPATH=. python3 -m coverage run -m pytest {test_file} "
            f"&& python3 -m coverage json --include=\"{include_flag}\" -o - \\\n"
            f"    || PYTHONPATH=. python3 -m pytest {test_file} "
            f"--cov={src_dir} --cov-report=term-missing"
        )
        cov_note = f"  (FR source files detected: {', '.join(src_files)})"
    else:
        # Fallback: test file absent or no imports matched — use full src dir
        cov_cmd = (
            f"  python3 -m coverage run --source={src_dir} -m pytest {test_file} "
            f"&& python3 -m coverage json -o - \\\n"
            f"    || PYTHONPATH=. python3 -m coverage run --source={src_dir} -m pytest {test_file} "
            f"&& python3 -m coverage json -o - \\\n"
            f"    || PYTHONPATH=. python3 -m pytest {test_file} "
            f"--cov={src_dir} --cov-report=term-missing"
        )
        cov_note = f"  (fallback: {src_dir} — test file not found or no imports detected)"

    print(
        f"\n[FR-SCOPED TOOL OVERRIDES — {fr_id}]\n"
        f"Gate 1 scope is single_fr. Replace the project-wide defaults in\n"
        f"evaluate_dimension.md with these FR-scoped commands:\n\n"
        f"test_coverage — measure only {fr_id}'s source files:\n"
        f"{cov_cmd}\n"
        f"{cov_note}\n\n"
        f"linting — lint only the FR source directory:\n"
        f"  python3 -m ruff check {src_dir}/ --extend-ignore RUF001,RUF002,RUF003 2>&1 | head -200\n\n"
        f"type_safety — type-check only the FR source directory:\n"
        f"  python3 -m pyright {src_dir}/ --outputjson 2>&1 | head -200\n"
    )

def _print_fr_scoped_overrides_js(
    project: str,
    fr_id: str,
    num_str: str,
    test_dir_str: str,
    *,
    non_code: bool,
    cov_threshold: int,
) -> None:
    """Print Gate-1 FR-scoped tool commands for a JS/TS project.

    Per-FR scoping uses the test-TITLE filter (-t "test_frNN") instead of a
    source-file include list: the harness naming convention guarantees every
    FR test title starts with test_frNN, and both vitest and jest support
    title filtering natively.
    """
    from harness.toolchains import get_project_language, get_project_test_runner
    language = get_project_language(project)
    runner = get_project_test_runner(project) or "vitest"

    if non_code:
        print(
            f"\n[FR-SCOPED TOOL OVERRIDES — {fr_id}]\n"
            f"Gate 1 scope is single_fr. Replace the project-wide defaults in\n"
            f"evaluate_dimension.md with these FR-scoped commands:\n\n"
            f"test_coverage — {fr_id} is declared as a non-code FR "
            f"(no scoreable source to measure):\n"
            f"  echo 'NON_CODE_FR: coverage not applicable'\n"
            f"  Score this dimension as {cov_threshold} (= threshold). "
            f"Infrastructure/config FRs are exempt from coverage measurement.\n"
            f"  Set tool_evidence = 'non-code FR: {fr_id} declared in fr_non_code'\n\n"
            f"linting — lint the project (eslint scope comes from eslint.config.mjs):\n"
            f"  npx --no-install eslint . -f json 2>&1 | head -200\n\n"
            f"type_safety — type-check the project:\n"
            f"  npx --no-install tsc --noEmit --pretty false 2>&1; echo \"tsc exit=$?\"\n"
        )
        return

    if runner == "jest":
        cov_cmd = (
            f"  npx --no-install jest -t \"test_fr{num_str}\" --coverage --ci \\\n"
            f"    --coverageReporters=json-summary --coverageReporters=text"
        )
    else:
        cov_cmd = (
            f"  npx --no-install vitest run {test_dir_str} -t \"test_fr{num_str}\" "
            f"--coverage \\\n"
            f"    --coverage.reporter=json-summary --coverage.reporter=text"
        )
    tsc_cmd = (
        "npx --no-install tsc -p tsconfig.checkjs.json --noEmit --pretty false"
        if language == "javascript"
        else "npx --no-install tsc --noEmit --pretty false"
    )
    print(
        f"\n[FR-SCOPED TOOL OVERRIDES — {fr_id}]\n"
        f"Gate 1 scope is single_fr. Replace the project-wide defaults in\n"
        f"evaluate_dimension.md with these FR-scoped commands:\n\n"
        f"test_coverage — run only {fr_id}'s tests (title filter), then read\n"
        f"coverage/coverage-summary.json total.lines.pct:\n"
        f"{cov_cmd}\n"
        f"  (convention: every {fr_id} test title starts with test_fr{num_str})\n\n"
        f"linting — eslint scope comes from eslint.config.mjs:\n"
        f"  npx --no-install eslint . -f json 2>&1 | head -200\n\n"
        f"type_safety — type-check the project (tsconfig owns the include set):\n"
        f"  {tsc_cmd} 2>&1; echo \"tsc exit=$?\"\n"
    )

def _normalize_sab_module_to_dotted(mod: object) -> Optional[str]:
    """Normalise a SAB ``modules`` entry into a dotted module name.

    Delegates to `core.quality_gate.sab_amender.normalize_sab_module_to_dotted`
    — the single source of truth for this normalization — so this alignment
    check and `amend_sab` can never silently disagree about which modules
    are "registered".
    """
    from core.quality_gate.sab_amender import normalize_sab_module_to_dotted
    return normalize_sab_module_to_dotted(mod)

def _filter_phantoms_for_fr(project: str, fr_id: str, phantoms: set[str]) -> set[str]:
    """Narrow a global phantom-module set to what `fr_id`'s Gate 1 should block on.

    Gate 1 runs once per FR, in sequence (P3/P5/P7/P8 per harness/CLAUDE.md's
    Gate Status Reference). A phantom module is only this FR's problem when
    it's owned by `fr_id` itself, or owned by an FR that has ALREADY passed
    Gate 1 (real regression — that FR claimed done but the module is now
    missing). A module owned by an FR not yet gated simply hasn't been built
    yet by sequencing, not by drift.

    A module with NO owner in `fr_module_traceability` (shared/entry-layer
    scaffolding like config/models/__main__) is not skipped here even though
    it isn't blocked — it's simply not blockable at this per-FR gate, because
    no single FR's TDD loop is responsible for building it, so blocking here
    only punishes whichever FR happens to be gated first (2026-07-08 false-
    block: taskq.config/models/breaker/store/__main__ have no FR owner and
    would BLOCK any early FR forever). The real, unconditional enforcement
    point for a permanently-missing SAB module is `preflight_sab_check`
    (core/phase_hooks.py:341), which checks every SAB-layer module against
    disk regardless of FR ownership, gated at P4 entry (`self.phase >= 4`) —
    that already closes the original 6436ab6 orphan case; this per-FR gate
    doesn't need to duplicate it.

    Ownership lookup reuses `fr_module_traceability` — the same manifest
    field `_print_fr_scoped_overrides_py`/`_js` already use for per-FR
    scoping — and `_normalize_sab_module_to_dotted` so ownership keys and
    the phantom set being filtered agree on format. Manifest missing OR
    unreadable → stay conservative and return `phantoms` unfiltered
    (original behavior) — a manifest that legitimately parses to {} is NOT
    the same case and falls through to real (empty) ownership data below.
    """
    if not ProjectLayout(project).quality_manifest_path.exists():
        return phantoms
    try:
        manifest = load_quality_manifest(project)
    except StateCorruptError:
        return phantoms

    gate1_results = manifest.get("gate_results", {}).get("gate1", {})
    passed_frs = {
        fr for fr, result in gate1_results.items()
        if isinstance(result, dict) and result.get("quality_complete") is True
    }

    owner_of: dict[str, str] = {}
    for owner_fr, entries in manifest.get("fr_module_traceability", {}).items():
        mods = [entries] if isinstance(entries, str) else (entries if isinstance(entries, list) else [])
        for m in mods:
            dotted = _normalize_sab_module_to_dotted(m)
            if dotted is not None:
                owner_of.setdefault(dotted, owner_fr)

    return {
        mod for mod in phantoms
        if owner_of.get(mod) == fr_id or owner_of.get(mod) in passed_frs
    }

def _check_sab_module_alignment(project: str, gate: int, fr_id: Optional[str] = None) -> Optional[int]:
    """Gate 1 Architecture Amendment Protocol: block on bidirectional SAB drift.

    Returns 1 when gate==1 and either:
      (a) unregistered: at least one .py file in src/ is absent from SAB.json, OR
      (b) phantom: SAB.json declares modules the codebase has not implemented.
    Returns None when the check is skipped (gate != 1, SAB.json missing, no src dir)
    or when SAB and codebase are symmetrically aligned.

    SAB ``modules`` entries may be expressed in either dotted
    (``taskq.cli``) or path (``03-development/src/taskq/cli.py``) form;
    both are normalised to dotted names before comparison so the check
    agrees with `drift_detector.sab_module_to_path_variants`.

    Unregistered detection (the (a) branch, Round 6 station 2) delegates
    the on-disk scan to `core.quality_gate.sab_amender.discover_modules_at`
    instead of a locally re-implemented rglob loop — the two versions had
    silently diverged (this one never skipped ``__pycache__``), which is
    exactly the class of drift `phantom_modules` below was already
    centralised to prevent for the (b) branch.

    Phantom detection (the (b) branch) closes the silent gap that previously
    let P2 architecture planning register `taskq.config` / `taskq.models`
    layers survive into P4 uncaught. The implementation delegates to
    `core.quality_gate.sab_amender.phantom_modules` so this check, the
    standalone `amend-sab` CLI, and `preflight_sab_check` (P4+) all agree
    on what "phantom" means — three callers, one definition.

    Bug class: P2-SAB-drift — first surfaced 2026-07-06 during phase4-testing
    E2E, where preflight_sab_check BLOCKED with "Layer config: 1 modules
    missing from codebase" because nothing had enforced (b) at any earlier
    gate. Pushing the symmetric check down to Gate 1 forces amendment at
    the earliest point where recovery is still cheap (P2 amendment protocol
    or P3 implementation).

    Per-FR scoping (2026-07-08 fix): the (b) phantom check above is
    project-wide by construction, but Gate 1 is documented/designed as
    per-FR (see harness/CLAUDE.md Gate Status Reference, and
    `_print_fr_scoped_overrides_py`/`_js` using the same
    `fr_module_traceability` mapping). Phase 3 gates FRs sequentially, so
    gating an early FR (e.g. FR-01) was tripping on later FRs' modules that
    legitimately don't exist yet — see `_filter_phantoms_for_fr`. Passing
    `fr_id` narrows the phantom set to that FR's own scope before deciding
    whether to block; `fr_id=None` preserves the original unscoped check.
    """
    if gate != 1:
        return None
    sab_path = Path(project) / ".methodology" / "SAB.json"
    src_dir = ProjectLayout(project).active_src_dir
    if not (sab_path.exists() and src_dir.exists()):
        return None
    try:
        sab_data = json.loads(sab_path.read_text(encoding="utf-8"))
        sab_modules: set[str] = set()
        for layer in sab_data.get("layers", []):
            for mod in layer.get("modules", []):
                dotted = _normalize_sab_module_to_dotted(mod)
                if dotted is not None:
                    sab_modules.add(dotted)

        from core.quality_gate.sab_amender import discover_modules_at
        actual_modules: set[str] = set(discover_modules_at(src_dir))

        unregistered = actual_modules - sab_modules
        if unregistered:
            print(
                f"\n[BLOCKED] run-gate: Architecture Amendment Protocol violation.\n"
                f"Unregistered modules detected: {unregistered}\n"
                f"Fix: you must create an Amendment PR to update SAB.json and SAD.md "
                f"before Gate 1 evaluation can proceed."
            )
            return 1

        # Phantom check: SAB declares modules the codebase lacks. Use the
        # shared helper so the message + handling stay in sync with
        # `preflight_sab_check` (P4+) and the standalone `amend-sab` CLI.
        from core.quality_gate.sab_amender import phantom_modules as _phantom
        phantoms = set(_phantom(sab_data, actual_modules))
        if phantoms and fr_id:
            phantoms = _filter_phantoms_for_fr(project, fr_id, phantoms)
        if phantoms:
            print(
                f"\n[BLOCKED] run-gate: Architecture Amendment Protocol violation.\n"
                f"Phantom modules declared in SAB.json but not implemented in codebase: {sorted(phantoms)}\n"
                f"Fix: you must either:\n"
                f"  (a) implement them in 03-development/src/<module>.py, OR\n"
                f"  (b) amend SAB.json to remove them from the layer's modules list\n"
                f"      (and sync the SAD.md sections that reference the removed modules — "
                f"amend-sab does not edit SAD.md).\n"
                f"Phantom drift caught here (Gate 1) so recovery is still cheap — "
                f"otherwise P4 preflight will block on the same drift with no path back to P2 amendment."
            )
            return 1
    except Exception as e:
        print(f"Warning: SAB Module Alignment Check failed to parse: {e}")
    return None

def _cmd_run_gate_impl(args: argparse.Namespace) -> int:
    """
    Phase 1: prepare gate context and print evaluation instructions for Claude.

    Claude must evaluate inline and write .sessi-work/gate{N}_result.json,
    then call `finalize-gate` to complete threshold checks and git operations.

    Delta-check mode (--delta, P5/P7/P8): skips full re-evaluation when FR
    code hasn't changed since last Gate 1. Previous score is reused.
    """
    delta = getattr(args, "delta", False)
    fr_id = getattr(args, "fr_id", None) or None

    # ── Delta-check: skip re-evaluation if FR code unchanged ────────────
    if delta and fr_id:
        project_path = Path(args.project).resolve()
        manifest = load_quality_manifest(project_path, lenient=True)
        prev_score = (
            manifest.get("gate_results", {})
            .get("gate1", {})
            .get(fr_id, {})
            .get("score")
        )
        if prev_score is not None:
            print(f"\n{'='*60}")
            print(f"DELTA-CHECK: {fr_id} — reusing previous Gate 1 score ({prev_score})")
            print("  (No code changes detected or delta-mode active)")
            print(f"{'='*60}")
            return 0

    from harness.harness_bridge import HarnessBridge

    project = str(Path(args.project).resolve())

    # Block evaluation before printing the prompt — prevents fabrication via
    # "evaluate without tools, then install stub to pass finalize-gate".
    _tools_ok, _missing_tools = tool_checks.verify_gate_tools(args.gate, project)
    if not _tools_ok:
        print(
            f"\n[BLOCKED] run-gate: required tools not installed for Gate {args.gate}:\n"
            + "".join(f"  ✗ {m}\n" for m in _missing_tools)
            + "\n  Install tools before starting evaluation.\n"
            "  tool_score=null is not accepted for Tier 1/2 dimensions (R8).\n"
            "  See evaluate_dimension.md Step 1 for install commands."
        )
        return 8

    # Architecture Amendment Protocol: Module Alignment Check (Gate 1)
    _amend_result = _check_sab_module_alignment(project, args.gate, fr_id)
    if _amend_result is not None:
        return _amend_result

    bridge = HarnessBridge()

    print(f"\n{'='*60}\nrun-gate: Gate {args.gate} | Phase {args.phase}\n{'='*60}")

    ctx = bridge.prepare_gate(
        gate_num=args.gate,
        project_root=project,
        phase=args.phase,
        fr_id=fr_id,
    )

    print(ctx.evaluation_prompt())

    # Gate 1 scope is single_fr — inject FR-scoped tool command overrides so
    # the evaluator only measures coverage for this FR's source files, not the
    # entire project (which dilutes the score with other FRs at 0%).
    if fr_id and args.gate == 1:
        # I: use canonical_form() — handles all variants (FR-01, fr01, FR_01, etc.)
        try:
            _canon = canonical_form(fr_id)
        except ValueError:
            _canon = fr_id
        _num_match = re.match(r"FR-(\d+)", _canon)
        _num_str = (
            _num_match.group(1).zfill(2)
            if _num_match
            else _canon
        )
        _layout = ProjectLayout(project)
        _test_dir_str = _layout.get_relative_str(_layout.active_test_dir)
        _test_file = f"{_test_dir_str}/test_fr{_num_str}.py"
        _src_dir = "03-development/src"

        # Load quality_manifest for per-FR overrides (scope + non-code flag).
        _manifest_data = load_quality_manifest(project, lenient=True)

        # Issue 3 (generalized): non-code FRs (Docker Compose, SQL, YAML) have
        # no scoreable source. When scope is empty and the FR is declared
        # non-code, bypass coverage measurement and assign threshold directly.
        # quality_manifest.json: {"fr_non_code": ["FR-15"]} — the pre-v2.8 key
        # fr_non_python is honored as an alias.
        _non_code_frs = (
            set(_manifest_data.get("fr_non_code", []))
            | set(_manifest_data.get("fr_non_python", []))
        )
        _cov_threshold = int(
            _manifest_data.get("quality_targets", {}).get("min_coverage", 80)
        )

        from core.utils.lang_patterns import project_language as _proj_lang
        _language = _proj_lang(Path(project))
        if _language in ("javascript", "typescript"):
            _print_fr_scoped_overrides_js(
                project, fr_id, _num_str, _test_dir_str,
                non_code=fr_id in _non_code_frs, cov_threshold=_cov_threshold,
            )
        else:
            _print_fr_scoped_overrides_py(
                project, fr_id, _test_file, _src_dir, _manifest_data,
                non_code_frs=_non_code_frs, cov_threshold=_cov_threshold,
            )

    print("\n" + "─" * 60)
    print("NEXT STEP: Evaluate the dimensions above, then run:")
    fr_flag = f" --fr-id {fr_id}" if fr_id else ""
    print(
        f"  python harness_cli.py finalize-gate --gate {args.gate} "
        f"--phase {args.phase} --project {args.project}{fr_flag}"
    )
    print("─" * 60)

    # Write sentinel so finalize-gate can verify run-gate was actually called.
    # Without this file, finalize-gate will block to prevent fabricated gate scores.
    # v2.13: pass args.phase so the sentinel is scoped to this phase (Bug #121).
    sf = _shared._sentinel_path(Path(project), args.gate, fr_id, phase=args.phase)
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text(f"{datetime.now(timezone.utc).isoformat()}\n", encoding="utf-8")
    print(f"[SENTINEL] {sf.relative_to(Path(project))} written.")
    return 0

_SCORES_SUBDIR = Path(".sessi-work") / "round_1" / "scores"

def _find_latest_round_dir(project: Path) -> "tuple[Path, int] | None":
    """Return (scores_dir, round_number) for the highest-numbered round_N with score files.

    Looks for .sessi-work/round_N/scores/*.json directories and returns the one
    with the largest N that actually contains score files.  Falls back to None
    if .sessi-work doesn't exist or no round directories have score files.
    """
    sessi = project / ".sessi-work"
    if not sessi.is_dir():
        return None
    rounds: list[tuple[Path, int]] = []
    for d in sessi.iterdir():
        if d.is_dir() and d.name.startswith("round_"):
            suffix = d.name[len("round_"):]
            if suffix.isdigit():
                rounds.append((d, int(suffix)))
    rounds.sort(key=lambda x: x[1], reverse=True)
    for rd, rn in rounds:
        scores_dir = rd / "scores"
        if list(scores_dir.glob("*.json")):
            return scores_dir, rn
    return None

_DA_EVIDENCE_MIN_CHARS = 120  # minimum length for challenge / response to count as real

def _validate_da_evidence(dim: str, g4: dict) -> "str | None":
    """A3 hardening: verify a Tier 3 dim's Devil's Advocate challenge is artifact-backed.

    A bare `devil_advocate.<dim>: true` is no longer sufficient — the agent must record
    the actual challenge under `devil_advocate_evidence.<dim>` with substantive
    `challenge` and `response` text. Returns a violation message, or None if valid.
    """
    evidence = g4.get("devil_advocate_evidence", {})
    if not isinstance(evidence, dict) or dim not in evidence:
        return (f"'{dim}': devil_advocate.{dim}=true but devil_advocate_evidence.{dim} is missing. "
                f"Record the actual DA challenge (a Claude sub-agent challenger persona's "
                f"critique + the defence) — a bare boolean is not accepted.")
    entry = evidence[dim]
    if not isinstance(entry, dict):
        return f"'{dim}': devil_advocate_evidence.{dim} must be an object with challenge + response."
    for field in ("challenge", "response"):
        val = str(entry.get(field, "")).strip()
        if len(val) < _DA_EVIDENCE_MIN_CHARS:
            return (f"'{dim}': devil_advocate_evidence.{dim}.{field} is too short "
                    f"({len(val)} chars < {_DA_EVIDENCE_MIN_CHARS}) — provide the real "
                    f"{field}, not a placeholder.")
    return None

def _load_gate_result_json(project: Path, gate: int) -> dict:
    """Load gate{gate}_result.json from the standard candidate locations.

    Candidate order (first parseable hit wins): .sessi-work/ (agent-written,
    freshest), .methodology/ (persisted by a previous finalize-gate), project
    root. Returns {} when no candidate exists or parses.
    """
    result_candidates = [
        project / ".sessi-work" / f"gate{gate}_result.json",
        project / ".methodology" / f"gate{gate}_result.json",
        project / f"gate{gate}_result.json",
    ]
    for candidate in result_candidates:
        if candidate.exists():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except Exception as _e:
                print(f"[Gate {gate}] ⚠ Could not parse {candidate}: {_e} — skipping extended checks",
                      file=sys.stderr)
    return {}

def _collect_da_waivers(project: Path, gate: int, gres: "dict | None" = None) -> "tuple[bool, set[str]]":
    """Collect artifact-backed DA score-threshold waivers from gate{gate}_result.json.

    A waiver lets a CRG-ONLY dim (e.g. architecture) pass below threshold when
    the Devil's Advocate challenge concluded the design is intentional
    (Orchestrator/hub-and-spoke, small-package Leiden fragmentation). Valid at
    Gate 3 and Gate 4 — finalize_gate's threshold zeroing is gate-agnostic.

    Requires devil_advocate.<dim>=true, da_waiver.<dim>=true, AND real
    devil_advocate_evidence.<dim> (challenge + response). Returns
    (blocked, da_waivers): blocked=True only when a requested waiver's DA
    evidence is missing/insufficient — a requested-but-unbacked waiver must
    fail loudly (fabrication guard), not be silently dropped.

    Note: the .methodology/ candidate can carry a waiver persisted from a
    previous finalize-gate run (parity with the long-standing Gate 4
    behavior); .sessi-work/ is checked first so a fresh agent-written file
    always wins.
    """
    blocked = False
    da_waivers: set[str] = set()
    g = _load_gate_result_json(project, gate) if gres is None else gres
    if not g:
        return blocked, da_waivers
    devil_advocate: dict = g.get("devil_advocate", {})
    _da_waiver_raw: dict = g.get("da_waiver", {})
    for _dim, _waived in _da_waiver_raw.items():
        if not (_waived and devil_advocate.get(_dim, False)):
            continue
        _w_problem = _validate_da_evidence(_dim, g)
        if _w_problem:
            print(
                f"\n[BLOCKED] Gate {gate} (A3): da_waiver for '{_dim}' requires DA evidence.\n"
                f"  Fix: {_w_problem}",
                file=sys.stderr,
            )
            blocked = True
            continue
        # Only apply the waiver when the dimension is actually below threshold.
        # If tool_score >= threshold the dimension already passes; accepting
        # the waiver would still set da_waiver_needs_human_review = True in
        # quality_manifest.json, which is a false-positive review flag.
        _bd = g.get("breakdown", {}).get(_dim, {})
        _tool_score = float(_bd.get("tool_score", 0.0))
        _threshold = float(_bd.get("threshold", float("inf")))
        if _tool_score >= _threshold:
            print(
                f"[Gate {gate}] A3: da_waiver for '{_dim}' skipped — "
                f"tool_score={_tool_score:.1f} ≥ threshold={_threshold:.1f} "
                "(waiver not needed; dimension already passes).",
                file=sys.stderr,
            )
            continue
        da_waivers.add(_dim)
        print(
            f"[Gate {gate}] A3: DA waiver active for '{_dim}' "
            "(score threshold bypassed — artifact-backed DA challenge confirmed intentional design).",
            file=sys.stderr,
        )
    return blocked, da_waivers

def _check_gate4_prerequisites(project: Path) -> "tuple[bool, set[str]]":
    """
    Run all Gate 4 blocking prerequisites before calling bridge.finalize_gate.

    Returns (blocked, da_waivers):
        blocked    — True if any prerequisite fails
        da_waivers — set of dimension names whose score threshold is waived via DA challenge

    Checks:
        A3 — devil_advocate: each marked-done Tier 3 dim (and every da_waiver) must carry a
             real `devil_advocate_evidence` artifact (challenge + response, not a bare boolean)
        B2 — per-dim score files exist in latest round_N/scores/ and have correct round field

    Non-blocking advisory: A5 issue_registry_path (contents are agent-written).
    Removed: A2 model_used (constant "claude" after MCP backends dropped),
    A4 high_score_confirmations (self-attested boolean ceremony).
    """
    blocked = False
    da_waivers: set[str] = set()

    # ── Load gate4_result.json for A2/A3/A4/A5 ───────────────────────
    g4 = _load_gate_result_json(project, 4)

    if g4:
        # (A2 model_used removed — after the MCP backends were dropped every dim
        # is evaluated by the Claude sub-agent, so the field was a constant "claude"
        # with zero verification value.)

        # ── A3: Devil's Advocate for Tier 3 dims ─────────────────────
        devil_advocate: dict = g4.get("devil_advocate", {})
        if not devil_advocate:
            print(
                "\n[BLOCKED] Gate 4 (A3): 'devil_advocate' field missing from gate4_result.json.\n"
                "  Fix: for each Tier 3 dimension, add devil_advocate: {dim: true/false}.\n"
                f"  Required dims: {sorted(_TIER3_DIMS)}",
                file=sys.stderr,
            )
            blocked = True
        else:
            not_done = [d for d in _TIER3_DIMS if not devil_advocate.get(d, False)]
            if not_done:
                print(
                    "\n[BLOCKED] Gate 4 (A3): Devil's Advocate challenge not completed for:\n"
                    + "\n".join(f"  - {d}" for d in sorted(not_done)) + "\n"
                    "  For each Tier 3 dim, dispatch a Claude sub-agent with a challenger persona\n"
                    "  to critique the evaluation, then set devil_advocate.<dim> = true AND record\n"
                    "  the challenge under devil_advocate_evidence.<dim> in gate4_result.json.",
                    file=sys.stderr,
                )
                blocked = True
            else:
                # A3 hardening: each marked-done Tier 3 dim must be artifact-backed.
                _da_problems = [
                    msg for d in sorted(_TIER3_DIMS)
                    if devil_advocate.get(d, False) and (msg := _validate_da_evidence(d, g4))
                ]
                if _da_problems:
                    print(
                        "\n[BLOCKED] Gate 4 (A3): Devil's Advocate evidence missing or insufficient:\n"
                        + "\n".join(f"  - {m}" for m in _da_problems),
                        file=sys.stderr,
                    )
                    blocked = True
                else:
                    # DA challenge complete + artifact-backed — collect score-threshold
                    # waivers (shared with the Gate 3 path; see _collect_da_waivers).
                    _w_blocked, da_waivers = _collect_da_waivers(project, 4, gres=g4)
                    blocked = blocked or _w_blocked

        # ── A5: Issue Registry (advisory only — no longer blocks) ─────
        # The registry contents are agent-written; "exists + non-empty" never
        # verified anything an agent couldn't trivially satisfy. Downgraded to a
        # non-blocking advisory.
        issue_registry_path_str: str = g4.get("issue_registry_path", "")
        if not issue_registry_path_str:
            print("[Gate 4] (A5, advisory): 'issue_registry_path' not set in gate4_result.json.",
                  file=sys.stderr)
        else:
            issue_registry: Optional[Path] = (project / issue_registry_path_str) if not Path(issue_registry_path_str).is_absolute() else Path(issue_registry_path_str)
            # Containment check: agent-controlled path must resolve inside the
            # project root. Blocking traversal (`../../etc/passwd`) probes here
            # even though the registry contents are only advisory.
            from harness.harness_bridge import path_escapes_root
            try:
                if issue_registry and path_escapes_root(issue_registry, project):
                    print(
                        f"[Gate 4] (A5, advisory): issue_registry_path escapes project root "
                        f"({issue_registry}); refusing to read.",
                        file=sys.stderr,
                    )
                    issue_registry = None
            except (OSError, RuntimeError):
                issue_registry = None
            if issue_registry is not None and not issue_registry.exists():
                print(f"[Gate 4] (A5, advisory): issue registry not found: {issue_registry}",
                      file=sys.stderr)
            elif issue_registry is not None:
                try:
                    registry_data = json.loads(issue_registry.read_text(encoding="utf-8"))
                    if not registry_data:
                        print(f"[Gate 4] (A5, advisory): issue registry is empty: {issue_registry}",
                              file=sys.stderr)
                except json.JSONDecodeError:
                    print(f"[Gate 4] (A5, advisory): issue registry is not valid JSON: {issue_registry}",
                          file=sys.stderr)

    # ── B2: Per-dim score files (latest round, stale-round detection) ────
    _b2_latest = _find_latest_round_dir(project)
    _b2_round: int | None = None
    if _b2_latest is None:
        # Fallback to hardcoded round_1 path for backward compat
        scores_dir = project / _SCORES_SUBDIR
    else:
        scores_dir, _b2_round = _b2_latest

    if not scores_dir.is_dir():
        print(
            f"\n[BLOCKED] Gate 4 (B2): Per-dimension score directory not found.\n"
            f"  Expected: {scores_dir}\n"
            "  Fix: write individual <dim>.json files for each evaluated dimension.",
            file=sys.stderr,
        )
        blocked = True
    else:
        score_files = list(scores_dir.glob("*.json"))
        if not score_files:
            print(
                f"\n[BLOCKED] Gate 4 (B2): No per-dimension score files found in {scores_dir}.\n"
                "  Fix: write <dim>.json (e.g. architecture.json, linting.json) for each evaluated dimension.",
                file=sys.stderr,
            )
            blocked = True
        else:
            # Stale-round detection: each score file's "round" field must match the directory number.
            if _b2_round is not None:
                stale_files = []
                for sf in score_files:
                    try:
                        _sf_data = json.loads(sf.read_text(encoding="utf-8"))
                        _sf_round = _sf_data.get("round")
                        # Only flag if "round" is explicitly set to a different value.
                        # Missing "round" is caught by score.py R1 (required field) — not stale.
                        if _sf_round is not None and _sf_round != _b2_round:
                            stale_files.append(
                                f"{sf.name} (round={_sf_round!r}, expected {_b2_round})"
                            )
                    except Exception as exc:
                        print(f"[WARN] Gate 4 (B2) stale-round check: {sf.name} unparseable "
                              f"(caught separately by score.py R1): {exc}", file=sys.stderr)
                if stale_files:
                    print(
                        f"\n[BLOCKED] Gate 4 (B2): Stale score files detected in {scores_dir}:\n"
                        + "\n".join(f"  - {s}" for s in stale_files) + "\n"
                        "  Score files were copied from an earlier round without re-evaluation.\n"
                        "  Re-run the SSI evaluation for each stale dimension.",
                        file=sys.stderr,
                    )
                    blocked = True
                else:
                    print(
                        f"[Gate 4] B2: {len(score_files)} per-dim score file(s) found "
                        f"(round={_b2_round}) ✅",
                        file=sys.stderr,
                    )
            else:
                print(f"[Gate 4] B2: {len(score_files)} per-dim score file(s) found ✅", file=sys.stderr)

    # ── B3: CRG recon output existence ────────────────────────────────
    # If the gate config declares crg.reconnaissance: true, the CRG bridge
    # must have been executed before finalize-gate is called.  The canonical
    # evidence is .sessi-work/crg_reconnaissance.json (written by the CRG
    # reconnaissance protocol).
    # A missing or empty file means CRG was never run — architecture-tier
    # scores derived from CRG data are therefore groundless.
    from core.harness_config import is_dim_disabled
    if is_dim_disabled("architecture", str(project)):
        print("[Gate 4] B3: CRG recon check skipped (crg_architecture disabled)", file=sys.stderr)
    else:
        try:
            import yaml as _yaml
            import glob as _b3glob
            _crg_cfg_files = sorted(_b3glob.glob(
                str(project / "harness" / "gate_configs" / "gate4_*.yaml")
            ))
            _crg_recon_required = False
            for _crg_cfg_path in _crg_cfg_files:
                try:
                    _crg_cfg = _yaml.safe_load(Path(_crg_cfg_path).read_text(encoding="utf-8"))
                    if (_crg_cfg or {}).get("crg", {}).get("reconnaissance"):
                        _crg_recon_required = True
                        break
                except Exception as _b3_cfg_exc:
                    print(f"[Gate 4] B3: skipping {_crg_cfg_path} (parse error: {_b3_cfg_exc})",
                          file=sys.stderr)
            if _crg_recon_required:
                recon_file = project / ".sessi-work" / "crg_reconnaissance.json"
                recon_exists = recon_file.is_file() and recon_file.stat().st_size > 0
                if not recon_exists:
                    print(
                        "\n[BLOCKED] Gate 4 (B3): CRG reconnaissance output not found.\n"
                        f"  Expected: {recon_file} (non-empty)\n"
                        "  Gate 4 config declares crg.reconnaissance: true — the CRG bridge\n"
                        "  must be executed before finalize-gate to provide architecture-tier\n"
                        "  evaluation context.\n"
                        "  Run the CRG reconnaissance protocol, then re-run:\n"
                        "    python harness_cli.py finalize-gate --gate 4 --phase 6 --project .",
                        file=sys.stderr,
                    )
                    blocked = True
                else:
                    print(
                        f"[Gate 4] B3: CRG recon output found "
                        f"({recon_file.name}, {recon_file.stat().st_size} bytes) ✅",
                        file=sys.stderr,
                    )
        except Exception as _b3exc:
            print(f"[Gate 4] B3: CRG recon check error ({_b3exc}) — skipping", file=sys.stderr)

    return blocked, da_waivers

def _finalize_gate_preflight(args: argparse.Namespace, project_path: Path) -> "int | None":
    """S0: tool availability + commit interval + sentinel check."""
    project = str(project_path)
    fr_id = getattr(args, "fr_id", None) or None

    # S0a: Tool availability
    _tools_ok, _missing_tools = tool_checks.verify_gate_tools(args.gate, project)
    if not _tools_ok:
        print(
            f"\n[BLOCKED] Required tools not installed for Gate {args.gate}:\n"
            + "".join(f"  ✗ {m}\n" for m in _missing_tools)
            + "\n  Install the missing tools and re-run finalize-gate.\n"
            "  Tool scores must come from actual tool execution, not estimation."
        )
        return 8

    # S0b: Commit interval enforcement (P1 — prevent batch fabrication).
    # Per-FR isolation: pass fr_id so distinct FRs finalizing in the same
    # 2s window are not falsely flagged as batch fabrication.
    _interval_ok, _interval_msg = gate1_evidence.check_commit_intervals(
        project, args.phase, args.gate, fr_id
    )
    if not _interval_ok:
        print(f"\n[BLOCKED] Commit interval violation: {_interval_msg}")
        print("  Re-run per-FR evaluations with genuine evidence and natural spacing.")
        return 1

    # Sentinel: run-gate must have been called before finalize-gate
    # v2.13: pass args.phase so the path matches what run-gate wrote
    # in the same phase (Bug #121 — no cross-phase sentinel reuse).
    sf = _shared._sentinel_path(project_path, args.gate, fr_id, phase=args.phase)
    if not sf.exists():
        print(
            f"\n[BLOCKED] run-gate --gate {args.gate} --phase {args.phase}"
            + (f" --fr-id {fr_id}" if fr_id else "")
            + f" --project {args.project}"
            f"\n  must be called before finalize-gate."
            f"\n  Missing sentinel: {sf.relative_to(project_path)}"
            f"\n  Writing gate{{N}}_result.json directly without run-gate is not permitted."
        )
        return 1

    return None

def _finalize_gate_fr_checks(args: argparse.Namespace, project_path: Path) -> "int | None":
    """I-2/I-3/I-4: Gate 1 per-FR checks (test file existence, RED ordering, spec coverage)."""
    fr_id = getattr(args, "fr_id", None) or None
    _active_tests = ProjectLayout(project_path).active_test_dir

    # I-2: FR test file existence
    if args.gate == 1 and fr_id and _active_tests.is_dir():
        _fr_ok, _fr_msg = _check_fr_test_file_exists(project_path, fr_id)
        if not _fr_ok:
            print(_fr_msg)
            return 8

    # I-3: RED phase ordering
    if args.gate == 1 and fr_id and _active_tests.is_dir():
        _red_ok, _red_msg = _check_red_phase_ordering(project_path, fr_id)
        if not _red_ok:
            print(_red_msg)
            return 1

    # I-4: Spec Coverage (Gate 1, threshold 40%)
    if args.gate == 1 and fr_id and (ProjectLayout(project_path).test_spec_path).exists():
        _sc1_code, _sc1_pct = spec_coverage._run_spec_coverage_check(
            project_path, 40.0, fr_id=fr_id, verbose=True
        )
        if _sc1_code != 0:
            print(f"\n[BLOCKED] Gate 1 spec-coverage [{fr_id}] {_sc1_pct:.1f}% < 40% threshold")
            print("  Fix: add test cases for this FR's uncovered TEST_SPEC.md sections, then re-run.")
            return 1

    return None

def _finalize_gate_cross_checks(args: argparse.Namespace, project_path: Path) -> "int | None":
    """I-5/I-6: Gates 2-4 D4 spec-coverage + PR 4 trace dimension.

    NOTE: HR-10/HR-01 A/B audit removed — see comment in _cmd_finalize_gate_impl.
    """
    # I-5: D4 Spec Coverage (Gates 2-4, unified v2.6)
    # Thresholds: Gate2=60%, Gate3=80%, Gate4=90%.
    if args.gate >= 2 and (ProjectLayout(project_path).test_spec_path).exists():
        # F-2.4 fix: source the threshold from the canonical constant
        # in `spec_tracking_checker` to prevent silent divergence if
        # either side is updated independently.
        from core.quality_gate.spec_tracking_checker import SPEC_COV_THRESHOLDS
        _sc_threshold = SPEC_COV_THRESHOLDS.get(args.gate, 60.0)
        _sc_code, _sc_pct = spec_coverage._run_spec_coverage_check(
            project_path, _sc_threshold, verbose=True
        )
        if _sc_code != 0:
            print(f"\n[BLOCKED] Gate {args.gate} spec-coverage {_sc_pct:.1f}% < {_sc_threshold}%")
            print("  Fix: add test cases for the uncovered TEST_SPEC.md sections, then re-run.")
            return 1

    # ── I-6: PR 4 closed-loop trace dimension (Gates 2-4) ───────────
    # Fuses 4a (FR→code→test, 100% over IN_PROGRESS+VERIFIED FRs) with
    # 4b (TEST_SPEC→test, gate-specific threshold). Merged = min(4a, 4b).
    # Skipped if no SAD.md and no [FR-XX] annotations (project not at P3+).
    # The framework-computed score is patched into gate{N}_result.json
    # breakdown so it flows through bridge.finalize_gate (same pattern as
    # _crg_overrides_applied for the architecture dimension).
    if args.gate >= 2:
        try:
            from core.quality_gate.spec_tracking_checker import (
                compute_trace_dimension,
            )
            _trace = compute_trace_dimension(project_path, args.gate)
            if _trace.get("error"):
                print(f"\n[WARN] trace dimension error: {_trace['error']}",
                      file=sys.stderr)
            _t_4a = _trace["4a_fr_to_test_pct"]
            _t_4b = _trace["4b_test_spec_pct"]
            _t_4c = _trace.get("4c_nfr_to_test_pct", 100.0)
            _t_merged = _trace["merged_pct"]
            _t_passed = _trace["passed"]
            print(
                f"\n[trace] Gate {args.gate} | "
                f"4a (FR→code→test): {_t_4a:.1f}% ≥ {_trace['threshold_4a']}%  "
                f"4b (TEST_SPEC→test): {_t_4b:.1f}% ≥ {_trace['threshold_4b']:.1f}%  "
                f"4c (NFR→test): {_t_4c:.1f}%  "
                f"merged: {_t_merged:.1f}%  "
                f"{'PASS' if _t_passed else 'FAIL'}"
            )
            if _trace["active_uncoded"]:
                print(f"  active FRs without code: {_trace['active_uncoded']}")
            if _trace["active_untested"]:
                print(f"  active FRs without test: {_trace['active_untested']}")
            if _trace.get("nfr_untested"):
                print(f"  NFRs without test coverage: {_trace['nfr_untested']}")
            # ── Patch trace score into gate{N}_result.json breakdown ─────
            # Same pattern as the architecture CRG override in
            # harness_bridge.finalize_gate (line ~1418): the framework
            # overrides the agent's score for trace because the agent has
            # no tool to compute it.
            _gp = project_path / ".sessi-work" / f"gate{args.gate}_result.json"
            if _gp.exists():
                try:
                    _gr = json.loads(_gp.read_text(encoding="utf-8"))
                    _gr.setdefault("breakdown", {}).setdefault(
                        "traceability", {}
                    )["score"] = _t_merged
                    _gr["breakdown"]["traceability"]["tool_evidence"] = (
                        f"framework: compute_trace_dimension(gate={args.gate}) → "
                        f"4a={_t_4a:.1f}% 4b={_t_4b:.1f}% 4c={_t_4c:.1f}% merged={_t_merged:.1f}%"
                    )
                    _gr["breakdown"]["traceability"]["threshold"] = float(
                        _trace["threshold_4a"]
                    )
                    _gr["breakdown"]["traceability"]["framework_override"] = True
                    _gp.write_text(
                        json.dumps(_gr, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except (OSError, json.JSONDecodeError) as _gp_err:
                    print(f"[WARN] could not patch trace score into result: {_gp_err}",
                          file=sys.stderr)
            if not _t_passed:
                print(
                    f"\n[BLOCKED] Gate {args.gate} trace dimension "
                    f"merged {_t_merged:.1f}% < threshold "
                    f"(4a={_trace['threshold_4a']}%, "
                    f"4b={_trace['threshold_4b']:.1f}%)\n"
                    f"  Fix: close the FR→code→test (4a) or TEST_SPEC→test (4b) "
                    f"traceability gap, whichever is lower, then re-run."
                )
                return 1
        except Exception as e:
            # Framework-side error: fail-closed at G2+ (don't silently pass)
            print(f"\n[BLOCKED] compute_trace_dimension raised: {e}\n"
                  f"  Fix: investigate the exception above (likely a malformed "
                  f"TRACEABILITY_MATRIX.md or missing SAD.md) before re-running.",
                  file=sys.stderr)
            return 1

    return None  # all cross-checks passed

def _mark_gate_commit_failed(project_path: Path, gate: int, fr_id: str | None) -> None:
    """Roll back gate_results.quality_complete after a failed git commit.

    finalize-gate optimistically patches quality_manifest.json's gate_results
    BEFORE attempting the git commit. Every phase workflow (phase3..8-*.js)
    treats quality_complete==True as the SOLE authority that a gate passed —
    it never inspects this CLI's exit code. If `git commit` is rejected
    (e.g. prepare-commit-msg hook stale trace attestation) after that
    optimistic write, the on-disk manifest still reads True even though
    nothing landed in git. Flip it back so quality_complete==True always
    implies "durably committed".
    """
    _mfst = project_path / ".methodology" / "quality_manifest.json"
    if not _mfst.exists():
        return
    try:
        _mfst_json = load_quality_manifest(project_path)
        _gr = _mfst_json.get("gate_results", {}) or {}
        if gate == 1:
            _actual_fr = fr_id or "unknown"
            _entry = (_gr.get("gate1") or {}).get(_actual_fr)
        else:
            _entry = _gr.get(f"gate{gate}")
        if isinstance(_entry, dict):
            _entry["quality_complete"] = False
            _entry["commit_landed"] = False
            atomic_write_json(_mfst, _mfst_json)
            print(f"  [WARN] git commit did not land — rolled back quality_complete "
                  f"to False for gate{gate}" + (f"/{fr_id}" if fr_id else ""))
            # Surface hook rejection details captured by git_strategy._commit
            _diag = project_path / ".sessi-work" / "last_commit_blocked.txt"
            if _diag.exists():
                try:
                    _diag_text = _diag.read_text(encoding="utf-8")
                    print(f"  [DIAG] Hook rejection details:\n{_diag_text[:2000]}")
                except OSError:
                    pass
    except (OSError, StateCorruptError) as _mf_err:
        print(f"  [WARN] Could not roll back quality_manifest.json after commit failure: {_mf_err}")

def _cmd_finalize_gate_impl(args: argparse.Namespace) -> int:
    """
    Phase 2: read gate{N}_result.json, check thresholds, update manifest, git.

    Called after Claude has completed inline evaluation and written the result file.
    Delegates preflight/fr/cross-checks to section helpers; handles bridge + post-flight.

    NOTE: HR-10/HR-01 A/B audit (sessions_spawn.log entry-count + distinct-session
    enforcement) was REMOVED. The log is a plain agent-writable file; proof of an
    independent Agent B review cannot be derived from it. P1/P2 quality is enforced
    by the Agent B deliverable review itself; P3+ by tool-scored gates and S4.
    AgentSpawner still records dispatches to sessions_spawn.log as a non-blocking debug trail.
    """
    from harness.harness_bridge import HarnessBridge, GateBlockedError

    project_path = Path(args.project).resolve()
    project = str(project_path)
    bridge = HarnessBridge()
    fr_id = getattr(args, "fr_id", None) or None

    print(f"\n{'='*60}\nfinalize-gate: Gate {args.gate} | Phase {args.phase}\n{'='*60}")

    if (code := _finalize_gate_preflight(args, project_path)) is not None:
        return code
    if (code := _finalize_gate_fr_checks(args, project_path)) is not None:
        return code
    if (code := _finalize_gate_cross_checks(args, project_path)) is not None:
        return code

    # ── Gate 4 extra enforcement (A1/A2/A3/A4/A5/B2) ─────────────────
    _da_waivers: set[str] = set()
    if args.gate == 4:
        _gate4_block, _da_waivers = _check_gate4_prerequisites(Path(project))
        if _gate4_block:
            return 5
    elif args.gate == 3:
        # Gate 3 honors the same artifact-backed DA waivers (from
        # gate3_result.json) — waiver collection only, none of the Gate 4
        # A3-completeness/A5/B2/B3 prerequisites apply at this gate.
        _g3_block, _da_waivers = _collect_da_waivers(Path(project), 3)
        if _g3_block:
            return 5

    # Rebuild context (loads config; skips CRG recon second time since recon file already exists)
    ctx = bridge.prepare_gate(
        gate_num=args.gate,
        project_root=project,
        phase=args.phase,
        fr_id=fr_id,
    )

    try:
        result = bridge.finalize_gate(ctx, da_waivers=_da_waivers)
        # Surface score/quality_complete to OTEL span wrapper via args (Namespace allows
        # dynamic attributes; wrapper reads _span_score/_span_quality_complete after _impl).
        args._span_score = result.score  # type: ignore[attr-defined]
        args._span_quality_complete = result.quality_complete  # type: ignore[attr-defined]
        print(f"\nGATE {args.gate} PASSED")
        print(f"  score           : {result.score:.1f}")
        print(f"  quality_complete: {result.quality_complete}")
        print(f"  open_critical   : {result.open_critical}")
        print(f"  open_high       : {result.open_high}")

        # Write finalize sentinel — advance-phase checks this to prove finalize-gate
        # was actually called (not bypassed by fabricating quality_manifest.json).
        # v2.13: pass args.phase so the path matches run-gate's per-phase path
        # (Bug #121).
        _fsf = _shared._finalize_sentinel_path(project_path, args.gate, fr_id, phase=args.phase)
        _fsf.parent.mkdir(parents=True, exist_ok=True)
        _fsf.write_text(f"{datetime.now(timezone.utc).isoformat()}\n", encoding="utf-8")

        # ── Persist gate result to .methodology/ (phase-persistent evidence) ──
        # gate{N}_result.json is written by the agent to .sessi-work/, which is
        # (a) gitignored and (b) wiped by advance-phase's rmtree. The PhaseAuditor
        # C10 check needs gate4_result.json as Gate 4 PASS evidence in CI. Copy the
        # just-finalized result to .methodology/ where it is committable and survives
        # the phase-transition cleanup.
        for _gp_src in (
            project_path / ".sessi-work" / f"gate{args.gate}_result.json",
            project_path / f"gate{args.gate}_result.json",
        ):
            if _gp_src.exists():
                _gp_dst = project_path / ".methodology" / f"gate{args.gate}_result.json"
                try:
                    _gp_dst.parent.mkdir(parents=True, exist_ok=True)
                    # Patch composite_score with the harness-computed weighted value.
                    # The agent writes its own self-assessed score to gate{N}_result.json;
                    # the harness recomputes it from breakdown weights.  Without this
                    # patch the persisted file would still carry the agent's raw score.
                    try:
                        _gp_json = json.loads(_gp_src.read_text(encoding="utf-8"))
                        _gp_json["composite_score"] = round(result.score, 4)
                        # P6-BUG-13: also patch harness-computed fields so that
                        # PhaseAuditor C10 and advance-phase can read gate PASS
                        # status from the committable .methodology/ copy without
                        # requiring a manual post-finalize patch.
                        _gp_json["quality_complete"] = result.quality_complete
                        _gp_json["verdict"] = "PASS" if result.quality_complete else "FAIL"
                        _gp_json["passed"] = result.quality_complete
                        _payload = json.dumps(_gp_json, indent=2, ensure_ascii=False)
                        _gp_dst.write_text(_payload, encoding="utf-8")
                        # Fix H-E (2026-07-15): also write the per-FR canonical
                        # history at .methodology/gate_results/gate{N}/{fr_id}.json
                        # so per-FR audit/debug can inspect a specific FR's gate
                        # verdict without overwriting the previous FR's history.
                        # Best-effort: a write failure here must NOT cascade into
                        # the latest-alias write above (which has its own try/except).
                        if fr_id:
                            try:
                                _gp_per_fr = (
                                    project_path
                                    / ".methodology"
                                    / "gate_results"
                                    / f"gate{args.gate}"
                                    / f"{fr_id}.json"
                                )
                                _gp_per_fr.parent.mkdir(parents=True, exist_ok=True)
                                _gp_per_fr.write_text(_payload, encoding="utf-8")
                                print(
                                    f"  per-fr          : {_gp_per_fr.relative_to(project_path)}"
                                )
                            except OSError as _gp_pf_err:
                                print(
                                    f"  [WARN] Could not persist per-FR gate result: {_gp_pf_err}"
                                )
                    except json.JSONDecodeError:
                        # Malformed source — fall back to verbatim copy
                        _gp_dst.write_text(_gp_src.read_text(encoding="utf-8"), encoding="utf-8")
                    print(f"  persisted       : {_gp_dst.relative_to(project_path)} (committable)")
                except OSError as _gp_err:
                    print(f"  [WARN] Could not persist gate result to .methodology/: {_gp_err}")
                break

        # ── Bug #118: keep .methodology/quality_manifest.json gate_results in sync ──
        # Without this, the next phase's entry_gate sees gate_results.gate{N}=null
        # and blocks the advance. Pre-fix required a manual edit; auto-patch the
        # gate that just finalized.
        _mfst = project_path / ".methodology" / "quality_manifest.json"
        if _mfst.exists():
            try:
                _mfst_json = load_quality_manifest(project_path)
                _mfst_gr = _mfst_json.setdefault("gate_results", {})
                _gr_key = f"gate{args.gate}"
                if args.gate == 1 and fr_id:
                    # Gate 1: per-FR dict under gate1.{fr_id}
                    _g1 = _mfst_gr.setdefault("gate1", {})
                    if not isinstance(_g1, dict):
                        _g1 = {}
                        _mfst_gr["gate1"] = _g1
                    _prev = _g1.get(fr_id) or {}
                    _g1[fr_id] = {
                        "score": round(result.score, 2),
                        "quality_complete": result.quality_complete,
                        "rounds_used": (int(_prev.get("rounds_used", 0)) if isinstance(_prev, dict) else 0) + 1,
                        "open_critical": result.open_critical,
                        "open_high": result.open_high,
                    }
                else:
                    # Gate 2+: composite block at gate_results.gate{N}
                    _prev = _mfst_gr.get(_gr_key) or {}
                    if not isinstance(_prev, dict):
                        _prev = {}
                    _mfst_gr[_gr_key] = {
                        **_prev,
                        "score": round(result.score, 2),
                        "quality_complete": result.quality_complete,
                        "rounds_used": (int(_prev.get("rounds_used", 0)) if isinstance(_prev, dict) else 0) + 1,
                        "open_critical": result.open_critical,
                        "open_high": result.open_high,
                        "phase": args.phase,
                        "gate": args.gate,
                        "fr_scope": fr_id or "all",
                        "overall_score": round(result.score, 2),
                    }
                atomic_write_json(_mfst, _mfst_json)
                print(f"  manifest        : quality_manifest.json {_gr_key} patched "
                      f"(score={round(result.score, 2)}, qc={result.quality_complete})")
            except (OSError, StateCorruptError) as _mf_err:
                print(f"  [WARN] Could not patch quality_manifest.json gate_results: {_mf_err}")

        # ── Structural post-flight for phase-exit gates (gate ≥ 2) ──────────
        # Checks ASPICE artifact cross-references and drift against artifacts
        # finalize-gate called directly also needs these blocking checks so the
        # FSM cannot advance past a gate with structural violations.
        # NOTE: _update_state_checkpoint intentionally placed AFTER this block —
        # if postflight fails we return early without marking the gate as passed.
        if args.gate >= 2:
            print(f"\n[POST-FLIGHT] Structural checks (Gate {args.gate})...")
            try:
                from core.phase_hooks import PhaseHooks
                _ph = PhaseHooks(project, phase=args.phase, enable_kill_switch=False,
                                 drift_threshold=get_value(project, "drift_threshold"))
                _art = _ph.postflight_artifact_links()
                _drft = _ph.postflight_drift_check()
                _pf_ok = _art.get("passed", True) and _drft.get("passed", True)
                if not _pf_ok:
                    # Structural postflight (artifact links / drift) detects substantive
                    # gaps — a broken phase artifact chain or spec↔code drift over the
                    # threshold. These need real work, not an auto-fix: the auto_fix
                    # strategies only emit stubs/comments that never clear these checks
                    # (verified end-to-end), so block honestly.
                    print(f"\n[BLOCKED] Post-flight structural check failed after Gate {args.gate}.")
                    print("  Fix the issues listed above, then re-run:")
                    print(f"  python harness_cli.py finalize-gate --gate {args.gate} "
                          f"--phase {args.phase} --project {project}")
                    return 5
                print("[POST-FLIGHT] Structural checks PASS")
            except ImportError:
                print("[WARN] PhaseHooks unavailable — postflight structural checks skipped")
            except Exception as _pf_exc:
                # Blocking only for Gate 4 (final gate); earlier gates warn only.
                if args.gate >= 4:
                    print(f"[BLOCKED] Post-flight error: {_pf_exc}")
                    print(f"  Fix: investigate the exception above, then re-run:\n"
                          f"    python harness_cli.py finalize-gate --gate {args.gate} "
                          f"--phase {args.phase} --project {project}")
                    return 5
                print(f"[WARN] Post-flight hooks error (non-blocking): {_pf_exc}")

        # ── Advisory: rounds_used=0 suggests A/B evaluation was skipped ──
        _rounds = getattr(result, "rounds_used", None)
        if _rounds is None:
            _rounds = 0
        if _rounds == 0 and args.gate == 1:
            print(
                f"  [WARN] rounds_used=0 for {fr_id or 'this gate'}: "
                "Gate 1 with zero review rounds suggests A/B evaluation was skipped. "
                "Ensure Agent A and Agent B both ran."
            )

        # ── D2: Score uniformity CRITICAL check ──────────────────────────
        # stddev=0 (all scores identical) is impossible under genuine
        # per-FR evaluation — it means scores were batch-copied.
        # This is a harder block than the existing advisory check in
        # harness_bridge.py (which only LOGs low variance).
        #
        # Saturation exemption: when ALL dimension scores are at (or near)
        # the ceiling (mean ≥ 99.5), stddev == 0 is a legitimate outcome.
        # Example: a 25-line minimal module where ruff, mypy, and pytest-cov
        # all genuinely score 100.  Blocking this case is a false positive.
        # The suspicious pattern is mid-range uniformity (e.g. all 78.5),
        # not ceiling uniformity.
        # Exclude not-yet-applicable dims (score=None — e.g. CRG architecture
        # override or a benchmark-less perf dim at Gate 2+) before the variance
        # math.  Mirrors harness_bridge None handling (skip None dims); without
        # it, statistics.pstdev/sum raise TypeError on None, crashing
        # finalize-gate AFTER the manifest patch — a split-write that leaves
        # gate_results recorded but the gate un-finalized.
        _d_scores = [d.score for d in result.dimensions if d.score is not None]
        if len(_d_scores) >= 3:
            import statistics as _stats
            _d_stdev = _stats.pstdev(_d_scores)
            _d_mean = sum(_d_scores) / len(_d_scores)
            _saturated = _d_mean >= 99.5  # all tools at maximum — not suspicious
            if _d_stdev == 0.0 and not _saturated:
                print(
                    f"\n[BLOCKED] CRITICAL: All {len(_d_scores)} dimension scores "
                    f"are identical ({_d_scores[0]:.1f}).\n"
                    f"  Genuine per-dimension evaluation produces natural variance.\n"
                    f"  Re-run run-gate with actual tool execution per dimension."
                )
                return 1
            # Advisory: low-but-nonzero variance (skip when saturated)
            if _d_stdev < 0.5 and not _saturated:
                print(
                    f"  [WARN] Per-dimension scores cluster tightly "
                    f"(stddev={_d_stdev:.3f}) — verify evidence trail."
                )

        # ── D2: Gate repeat detection ─────────────────────────────────────
        # Check if this gate+FR has been finalized before (within this phase).
        # Repeated identical finalizations suggest batch-rerun without fixes.
        _dup_flag = project_path / ".sessi-work" / "sentinels" / f"finalized_{args.gate}_{(fr_id or 'phase').replace('-','').lower()}.flag"
        if _dup_flag.exists():
            print(
                f"\n[WARN] Gate {args.gate} was previously finalized for this phase/FR.\n"
                f"  Re-running without changes wastes CI resources.\n"
                f"  If this is intentional (e.g., after fixing issues), ignore this warning."
            )
        _dup_flag.parent.mkdir(parents=True, exist_ok=True)
        _dup_flag.write_text(f"{datetime.now(timezone.utc).isoformat()}\n", encoding="utf-8")

        # ── S1: Phase Truth for last gate of phase ────────────────────────
        # Ensures PhaseTruthVerifier runs even when finalize-gate is called
        _last_gate = EXIT_GATE_MAP.get(args.phase)
        if _last_gate is not None and args.gate == _last_gate and args.phase >= 3:
            print(f"\n[PHASE-TRUTH] Phase {args.phase} final gate — running HR-11 check...")
            try:
                from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
                verifier = PhaseTruthVerifier(project, args.phase)
                truth_result = verifier.verify()
                if not truth_result["passed"]:
                    print(
                        f"\n[BLOCKED] Phase {args.phase} truth = "
                        f"{truth_result['total_score']:.0f}% < 90% (HR-11)"
                    )
                    print("  Fix gaps then re-run finalize-gate.")
                    return 11
                print(f"  [HR-11] Phase Truth = {truth_result['total_score']:.0f}% ≥ 90% ✓")
            except ImportError:
                print("  [BLOCKED] PhaseTruthVerifier unavailable — cannot verify Phase Truth")
                print("  Fix: check the harness/ submodule is present and importable, then re-run finalize-gate.")
                return 11
            except Exception as _pte:
                print(f"  [WARN] Phase Truth check error: {_pte}")

        _update_state_checkpoint(
            Path(args.project).resolve(), args.gate, fr_id,
            gate_score=result.score, phase=args.phase,
        )
        claude_md.update_claude_md(Path(args.project).resolve())  # gate pass → refresh CLAUDE.md

        # P1: Record successful finalization timestamp HERE (after all checks pass),
        # not inside check_commit_intervals.  Failed attempts must not leave a trace
        # so that retries don't accumulate phantom entries.
        gate1_evidence.record_gate_timestamp(Path(args.project).resolve(), args.phase, args.gate, fr_id)

        # ── Auto-generate machine STAGE_PASS.md ──────────────────────
        _shared._generate_stage_pass(project_path, args.gate, args.phase)

        # ── Auto-generate quality deliverables for Gate 4 ─────────────
        if args.gate == 4:
            # Bug fix P6-2026-07-07: cwd-relative `from scripts.X` failed
            # whenever finalize-gate was run from the project root (scripts/
            # lives under the harness submodule, not the consumer project).
            # Each generator is loaded by absolute file path so the call works
            # regardless of cwd / PYTHONPATH.
            #
            # A1-2026-07-07: helper hoisted to module-scope `load_harness_script`
            # (see top of file) so `_run_phase_auditor` and `cmd_audit_phase`
            # share the same code path; this inline definition is removed.
            try:
                _qreport_mod = load_harness_script("generate_quality_report.py")
                _qreport_mod.generate_quality_report(str(Path(args.project).resolve()))
            except Exception as _qre:
                print(f"  [WARN] QUALITY_REPORT.md generation skipped: {_qre}")

            try:
                _rnotes_mod = load_harness_script("generate_release_notes.py")
                _rnotes_mod.generate_release_notes(str(Path(args.project).resolve()))
            except Exception as _rne:
                print(f"  [WARN] RELEASE_NOTES.md generation skipped: {_rne}")

        # ── CRG cross-phase baseline: snapshot metrics for the next exit gate ──
        _project_path = Path(args.project).resolve()
        _crg_metrics_path = _project_path / ".sessi-work" / "crg_metrics.json"
        if _crg_metrics_path.is_file() and args.gate in EXIT_GATE_MAP.values():
            try:
                import shutil as _shutil
                _baseline_path = (
                    _project_path / ".methodology"
                    / f"crg_baseline_p{args.phase}.json"
                )
                _shutil.copy2(_crg_metrics_path, _baseline_path)
                # Stamp with git SHA for traceability
                import subprocess as _sp
                _sha_r = _sp.run(
                    ["git", "rev-parse", "HEAD"],
                    capture_output=True, text=True, cwd=str(_project_path),
                )
                _bl_data = json.loads(_baseline_path.read_text(encoding="utf-8"))
                _bl_data["_baseline_sha"] = _sha_r.stdout.strip()
                _bl_data["_baseline_phase"] = args.phase
                _baseline_path.write_text(json.dumps(_bl_data, indent=2), encoding="utf-8")
                print(f"  [CRG] Baseline saved: .methodology/crg_baseline_p{args.phase}.json")
            except Exception as _bl_exc:
                print(f"  [WARN] CRG baseline save failed: {_bl_exc}")

        git = _shared._make_git(args, Path(args.project).resolve())
        git.ensure_gitignore()
        # Round 12 站2b: heal a stale attestation BEFORE the gate commit
        # fires the prepare-commit-msg hook — replaces the prompt-side
        # TRACE-PRECHECK ritual (37 ritual commits on integration-test).
        _shared.ensure_fresh_attestation(Path(args.project).resolve())
        # Bug fix (P8 E2E 2026-07-04): write last_milestone_command for gate 4
        # to state.json BEFORE commit_and_push_gate so the audit field lands in
        # the pushed commit. Previously this block was inside the else AFTER
        # push, leaving state.json dirty in the working tree forever. The
        # original raw `write_text` also lacked the file_lock used elsewhere
        # (file_lock + atomic_write_json pattern from _update_state_checkpoint).
        # See plan: ~/.claude/plans/abundant-stargazing-hejlsberg.md
        _prev_g4_milestone_command = None
        _wrote_g4_milestone_state = False
        if args.gate == 4:
            _state_path = project_path / ".methodology" / "state.json"
            if _state_path.exists():
                try:
                    with file_lock(state_lock_path(project_path)):
                        _sd = load_state(project_path)
                        _prev_g4_milestone_command = _sd.get("last_milestone_command")
                        _sd["last_milestone_command"] = (
                            f"finalize-gate --gate 4 --phase {args.phase}"
                        )
                        atomic_write_json(_state_path, _sd)
                        _wrote_g4_milestone_state = True
                except Exception as _sme:
                    print(f"  [WARN] Could not write last_milestone_command to state.json: {_sme}")
        if args.gate == 1:
            _commit_ok = git.commit_fr_gate1(fr_id or "unknown", result.score, args.phase)
        else:
            _commit_ok = git.commit_and_push_gate(args.gate, args.phase, result.score)
            if _commit_ok:
                # Post-push self-check: warn loudly on dirty residue. Push itself
                # succeeded — the dirt is post-commit residue. Don't fail-fast.
                _dirty = _shared._post_push_self_check(Path(args.project).resolve())
                if _dirty:
                    print(
                        f"  [WARN] post-push dirty tree ({len(_dirty)} path(s)):\n"
                        + "\n".join(f"    • {p}" for p in _dirty[:10])
                        + (f"\n    ... and {len(_dirty) - 10} more" if len(_dirty) > 10 else "")
                    )

        if not _commit_ok:
            _mark_gate_commit_failed(project_path, args.gate, fr_id)
            # B3 (弱點強化): revert the optimistic gate-4 audit write — same
            # field-level pattern as push-milestone/push-checkpoint (dd9129b).
            # ci_state_helper trusts last_milestone_command alone, so a failed
            # gate-4 push must not read as a completed finalize.
            if _wrote_g4_milestone_state:
                try:
                    with file_lock(state_lock_path(project_path)):
                        _sd = load_state(project_path)
                        if _prev_g4_milestone_command is None:
                            _sd.pop("last_milestone_command", None)
                        else:
                            _sd["last_milestone_command"] = _prev_g4_milestone_command
                        atomic_write_json(
                            project_path / ".methodology" / "state.json", _sd
                        )
                except Exception as _revert_err:  # pylint: disable=broad-exception-caught
                    print(f"  [WARN] Could not revert gate-4 milestone field: {_revert_err}")
            print(
                f"\n[BLOCKED] Gate {args.gate} evaluation passed but the git commit "
                "did not land (see '[git WARN] git commit failed' above — often a "
                "prepare-commit-msg hook rejection, e.g. stale trace attestation).\n"
                "  quality_manifest.json rolled back to quality_complete=false.\n"
                "  Fix the reported error, then re-run:\n"
                f"  python harness_cli.py finalize-gate --gate {args.gate} "
                f"--phase {args.phase} --project {project}"
            )
            return 6
        return 0

    except FileNotFoundError as e:
        print(f"\n[ERROR] {e}")
        print(
            f"  Run `python harness_cli.py run-gate --gate {args.gate} "
            f"--phase {args.phase} --project {args.project}` first,\n"
            "  then evaluate the dimensions and write the result file."
        )
        return 2

    except GateBlockedError as e:
        project_path = Path(args.project).resolve()
        print(_format_block_diagnostic(
            e, args.gate, args.phase, fr_id, 3, project_path,
        ))
        # Direction C: distil the block into cross-run failure memory so the
        # next run recalls it (best-effort — must never break the gate flow).
        try:
            from core.lessons import record_gate_block
            record_gate_block(project_path, gate_num=args.gate, phase=args.phase,
                              fr_id=fr_id, result=e.result)
        except Exception as _lessons_exc:  # noqa: BLE001
            print(f"[WARN] finalize-gate: could not record cross-run failure "
                  f"memory for this block: {_lessons_exc}", file=sys.stderr)
        return 1

def _update_state_checkpoint(
    project: Path, gate_num: int, fr_id: str | None,
    gate_score: float | None = None, phase: int | None = None,
) -> None:
    """Write last_gate / last_fr to .methodology/state.json after a gate passes.

    Cross-process locked (SG-12): two parallel finalize-gate calls cannot
    race on the read-modify-write of state.json.
    """
    from datetime import datetime, timezone
    state_path = project / ".methodology" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(state_lock_path(project)):
        # lenient=True: unreadable state.json overwrites with a fresh object
        # (any fields other than last_gate/last_fr/last_update are lost) —
        # recorded on the degradation ledger, same trade-off this site
        # already made by hand before core/state_io.py existed.
        existing = load_state(project, lenient=True)
        # Track Gate 1 score for inter-FR variance check (D2 extension)
        if gate_num == 1 and fr_id and gate_score is not None and phase is not None:
            gate1_evidence.record_gate1_score(project, phase, fr_id, gate_score)
        existing["last_gate"] = gate_num
        existing["last_fr"] = fr_id
        existing["last_update"] = datetime.now(timezone.utc).isoformat()
        # Record phase_truth_passed when the phase exit gate completes
        _current_phase = int(existing.get("current_phase", phase or 0))
        if gate_num == EXIT_GATE_MAP.get(_current_phase):
            existing["phase_truth_passed"] = True
        atomic_write_json(state_path, existing)

_DIMENSION_HINTS: dict[str, str] = {
    "linting":            "Run `ruff check . --fix` (or flake8); resolve all remaining lint errors",
    "type_safety":        "Run `mypy .`; add missing annotations and fix all type errors",
    "test_coverage":      "Run `pytest --cov` to find uncovered lines; add unit tests for each gap",
    "security":           "Fix OWASP-category issues; validate all inputs; remove eval/exec patterns",
    "secrets_scanning":   "Remove hard-coded secrets; move to env vars / vault; run `gitleaks detect`",
    "license_compliance": "Run `pip-licenses`; replace or vendor GPL/incompatible dependencies",
    "mutation_testing":   "Run `mutmut run`; add assertions that kill every surviving mutant",
    "architecture":       (
        "Two distinct failure modes — check tool_evidence to identify which applies: "
        "(1) CRG community issues: if god-module (size>50) or low cohesion (all communities <0.3) — "
        "either file an artifact-backed DA waiver in .sessi-work/gate{N}_result.json "
        "(devil_advocate + da_waiver + devil_advocate_evidence.architecture; valid at Gate 3 AND Gate 4) "
        "then re-run finalize-gate, OR reduce cross-package coupling so CRG detects sub-communities; "
        "for persistent CRG false positives (workflow tooling counted as product code, small-package "
        "Leiden over-fragmentation) calibrate crg_excludes / crg_cohesion_healthy in "
        ".methodology/harness_config.json; "
        "(2) Import boundary violations: verify imports comply with SAD.md layer boundaries and fix violations."
    ),
    "readability":        "Add [FR-XX] docstrings with Citations:; split functions >30 lines",
    "error_handling":     (
        "CRG flow_coverage score: percent of call-chain flows with at least one specific error handler. "
        "Use `get_affected_flows` CRG tool to identify which flows lack handlers. "
        "Fix: add try/except with specific exception types (not bare `except:`) to I/O, "
        "network, and external service calls. Bare `except:` does NOT improve CRG score."
    ),
    "documentation":      "All public APIs need [FR-XX] docstrings with Citations: + line numbers",
    "performance":        "Profile with cProfile; fix N+1 queries; add caching where needed",
}

def _format_block_diagnostic(
    exc: "GateBlockedError",
    gate_num: int,
    phase: int,
    fr_id: str | None,
    max_rounds: int,
    project: Path,
) -> str:
    """Format a structured diagnostic for a gate BLOCKED event; also writes last_block.md."""
    failing = [d for d in exc.result.dimensions if d.score is not None and d.score < d.threshold]
    passing = [d for d in exc.result.dimensions if d.score is not None and d.score >= d.threshold]

    lines = [
        "",
        "─" * 60,
        f"GATE {gate_num} BLOCKED"
        + (f"  fr={fr_id}" if fr_id else "")
        + f"  phase={phase}  after {max_rounds} SSI round(s)",
        f"  composite score : {exc.result.score:.1f}",
        f"  open critical   : {exc.result.open_critical}",
        f"  open high       : {exc.result.open_high}",
        "",
        f"Failing dimensions ({len(failing)}):",
    ]
    for dim in failing:
        if dim.score is not None:
            gap = dim.threshold - dim.score
            lines.append(
                f"  [FAIL] {dim.name:<22} score={dim.score:>5.1f}  "
                f"need={dim.threshold:>5.1f}  gap={gap:>4.1f}"
            )
        else:
            lines.append(
                f"  [FAIL] {dim.name:<22} score= None  "
                f"need={dim.threshold:>5.1f}  gap= N/A"
            )
        hint = _DIMENSION_HINTS.get(dim.name, "Review dimension-specific issues in SSI output")
        lines.append(f"         → {hint}")

    if passing:
        lines.append("")
        lines.append(
            f"Passing ({len(passing)}): "
            + ", ".join(f"{d.name}={d.score:.1f}" if d.score is not None else f"{d.name}=None" for d in passing)
        )

    fr_flag = f" --fr-id {fr_id}" if fr_id else ""
    lines.extend([
        "",
        "Fix the failing dimensions above, then resume:",
        f"  python harness_cli.py run-gate --gate {gate_num} --phase {phase}"
        f"{fr_flag} --project {project}",
        "─" * 60,
    ])

    # Write .methodology/last_block.md
    report_lines = [
        f"# Gate {gate_num} BLOCKED — Phase {phase}",
        "",
        f"Generated: {__import__('datetime').datetime.now().isoformat()}",
        f"fr_id: {fr_id or 'n/a'} | rounds: {exc.result.rounds_used} | "
        f"open_critical: {exc.result.open_critical} | open_high: {exc.result.open_high}",
        "",
        "## Failing Dimensions",
        "",
    ]
    for dim in failing:
        hint = _DIMENSION_HINTS.get(dim.name, "Review SSI output")
        if dim.score is not None:
            gap = dim.threshold - dim.score
            report_lines += [
                f"### {dim.name}",
                f"- score: {dim.score:.1f} / threshold: {dim.threshold:.1f} (gap: {gap:.1f})",
                f"- fix: {hint}",
                "",
            ]
        else:
            report_lines += [
                f"### {dim.name}",
                f"- score: None / threshold: {dim.threshold:.1f} (gap: N/A)",
                f"- fix: {hint}",
                "",
            ]
    report_lines += [
        "## Resume Commands",
        "",
        "```bash",
        f"python harness_cli.py run-gate --gate {gate_num} --phase {phase}"
        + (f" --fr-id {fr_id}" if fr_id else "")
        + f" --project {project}",
        "```",
    ]
    try:
        report_path = project / ".methodology" / "last_block.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(report_lines), encoding="utf-8")
        lines.append(f"  Full report → {report_path}")
    except Exception as _write_exc:  # pylint: disable=broad-exception-caught
        print(f"[WARN] finalize-gate: could not write last_block.md: {_write_exc}", file=sys.stderr)

    return "\n".join(lines)


def register(sub) -> None:
    """Wire this family's parsers onto the main subparser action."""
    # run-gate (Phase 1: prepare + print evaluation prompt)
    rg = sub.add_parser("run-gate", help="Prepare gate evaluation; print prompt for Claude")
    rg.add_argument("--gate",    type=int, required=True, choices=[1, 2, 3, 4])
    rg.add_argument("--phase",   type=int, required=True, help="Current phase number")
    rg.add_argument("--project", default=".", help="Project root (default: .)")
    rg.add_argument("--fr-id",   default=None, help="FR ID (Gate 1 only)", dest="fr_id")
    rg.add_argument("--skip-preflight", action="store_true", help="Skip preflight validation before gate (Item 9)")
    rg.add_argument("--delta", action="store_true", help="Delta-check mode (P5/P7/P8): skip re-evaluation if FR code unchanged")
    rg.set_defaults(func=cmd_run_gate)

    # finalize-gate (Phase 2: read result.json, check thresholds, git)
    fg = sub.add_parser(
        "finalize-gate",
        help="Finalize gate after Claude evaluation; checks thresholds and commits",
    )
    fg.add_argument("--gate",    type=int, required=True, choices=[1, 2, 3, 4])
    fg.add_argument("--phase",   type=int, required=True, help="Current phase number")
    fg.add_argument("--project", default=".", help="Project root (default: .)")
    fg.add_argument("--fr-id",   default=None, help="FR ID (Gate 1 only)", dest="fr_id")
    fg.add_argument("--no-git",  action="store_true", dest="no_git",
                    help="Disable git commit/push after gate pass")
    fg.set_defaults(func=cmd_finalize_gate)

    # run-env-check (project-aware environment readiness — inline LLM evaluation)
    rec = sub.add_parser(
        "run-env-check",
        help="Print project-aware environment evaluation prompt (reads SAD.md + SRS.md)",
    )
    rec.add_argument("--phase",   type=int, required=True, help="Current phase number")
    rec.add_argument("--project", default=".", help="Project root (default: .)")
    rec.add_argument("--fr-id",   default=None, help="FR ID (optional, for FR-scoped checks)")
    rec.set_defaults(func=cmd_run_env_check)

    # finalize-env-check (verify env_check_result.json)
    fec = sub.add_parser(
        "finalize-env-check",
        help="Verify env_check_result.json and report environment readiness",
    )
    fec.add_argument("--phase",   type=int, required=True, help="Current phase number")
    fec.add_argument("--project", default=".", help="Project root (default: .)")
    fec.add_argument("--fr-id",   default=None, help="FR ID (optional)")
    fec.set_defaults(func=cmd_finalize_env_check)

    # gate4-tag (create annotated git tag from gate4_result.json)
    g4t = sub.add_parser(
        "gate4-tag",
        help="Create annotated git tag for Gate 4 pass using composite score from gate4_result.json",
    )
    g4t.add_argument("--project", default=".", help="Project root (default: .)")
    g4t.set_defaults(func=cmd_gate4_tag)

    mts = sub.add_parser(
        "mutation-test-score",
        help="Run mutmut in a workdir and publish the score to .mutmut-cache "
             "(Bug #105: framework-owned path for the mutation_testing dimension).",
    )
    mts.add_argument("--project", default=".", help="Project root (default: .)")
    mts.set_defaults(func=cmd_mutation_test_score)
