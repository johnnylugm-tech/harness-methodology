"""Gate evaluation commands (run-gate, finalize-gate, env-check pair, gate4-tag, mutation-test-score).

Extracted verbatim from harness_cli.py (方案六). Free names that live
in harness_cli resolve through `_hc.` at call time, so existing
monkeypatches on harness_cli attributes keep working. harness_cli
re-exports these cmd_* names, so `from harness_cli import cmd_x`
imports are unaffected.
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

from core.harness_config import get_timeout
from core.utils.project_layout import ProjectLayout
from core.quality_gate.mutation_enforcer import compute_mutation_score
import harness_cli as _hc


def cmd_run_gate(args: argparse.Namespace) -> int:
    """OTEL span wrapper for run-gate. Business logic in _cmd_run_gate_impl."""
    try:
        from core.observability import init_tracer
        _tracer = init_tracer(Path(getattr(args, "project", ".")).resolve())
    except Exception:
        _tracer = None
    if _tracer is None:
        return _hc._cmd_run_gate_impl(args)
    with _tracer.start_as_current_span("run_gate") as _span:
        _span.set_attribute("harness.gate", getattr(args, "gate", 1))
        _span.set_attribute("harness.phase", getattr(args, "phase", 0))
        _fr = getattr(args, "fr_id", None)
        if _fr:
            _span.set_attribute("harness.fr_id", str(_fr))
        _span.set_attribute("harness.delta", bool(getattr(args, "delta", False)))
        _exit = _hc._cmd_run_gate_impl(args)
        _span.set_attribute("harness.exit_code", _exit)
        _span.set_attribute("harness.blocked", _exit != 0)
        return _exit


def cmd_finalize_gate(args: argparse.Namespace) -> int:
    """OTEL span wrapper for finalize-gate. Business logic in _cmd_finalize_gate_impl."""
    try:
        from core.observability import init_tracer
        _tracer = init_tracer(Path(getattr(args, "project", ".")).resolve())
    except Exception:
        _tracer = None
    if _tracer is None:
        return _hc._cmd_finalize_gate_impl(args)
    with _tracer.start_as_current_span("finalize_gate") as _span:
        _span.set_attribute("harness.gate", args.gate)
        _span.set_attribute("harness.phase", args.phase)
        _fr = getattr(args, "fr_id", None)
        if _fr:
            _span.set_attribute("harness.fr_id", str(_fr))
        try:
            _exit = _hc._cmd_finalize_gate_impl(args)
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
            timeout=get_timeout("subprocess"),
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
                f"{get_timeout('subprocess')}s without writing "
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
            f"  run-env-check must be called before finalize-env-check.\n"
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
    candidates = [
        project / ".sessi-work" / "gate4_result.json",
        project / ".methodology" / "gate4_result.json",
        project / "gate4_result.json",
    ]
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
                        except Exception:
                            pass
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
                    except Exception:
                        pass
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
                except Exception:
                    pass
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
