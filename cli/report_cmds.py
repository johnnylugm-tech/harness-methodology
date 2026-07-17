"""Round 14 站1: harness_cli.py run-report — read-only aggregation of run
artifacts into the operational metrics the R12 convergence audit needed but
had to compute by hand (dispatches/FR, failure rate, degradations, untriaged
crashes, per-Gate wall-time). Writes nothing; every source is optional —
a missing/empty artifact reports available=False for that section rather
than crashing or padding the report with null-noise.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# Mirrors core/observability.py's inline construction
# (project_root / ".harness" / "traces" / "agent_trajectory.jsonl") — no
# importable constant exists there to reuse, since that module writes the
# path directly. If observability.py ever exports one, switch to it.
_TRAJECTORY_RELPATH = ".harness/traces/agent_trajectory.jsonl"
_TRAJECTORY_LINE_CAP = 50_000


def _read_jsonl(path: Path, *, line_cap: "int | None" = None) -> list[dict]:
    """Best-effort JSONL reader: missing file -> [], unreadable lines skipped,
    never raises. A malformed artifact degrades this report to less data,
    not a crash — consistent with this command's all-optional design."""
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    if line_cap is not None and len(lines) > line_cap:
        lines = lines[-line_cap:]
    out: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def _spawn_log_report(project: Path) -> dict:
    from core.sessions_spawn_logger import SessionsSpawnLogger
    entries = _read_jsonl(project / SessionsSpawnLogger.LOG_FILENAME)
    if not entries:
        return {"available": False}

    total = len(entries)
    status_counts = Counter(e.get("status", "?") for e in entries)
    error_class_counts = Counter(e["error_class"] for e in entries if e.get("error_class"))
    error_like = sum(status_counts.get(s, 0) for s in ("ERROR", "TIMEOUT", "REGRESSION_GUARD"))

    # Round 14 站0's envelope fields (total_cost_usd/usage) are absent on
    # log lines written before that station — every sum below counts only
    # entries that actually carry the field, and reports the denominator
    # alongside the total so an old/new mix never looks like zero cost.
    per_fr: dict[str, dict] = {}
    for e in entries:
        fr_id = e.get("fr_id")
        if not fr_id:
            continue
        rec = per_fr.setdefault(
            fr_id, {"dispatches": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0}
        )
        rec["dispatches"] += 1
        cost = e.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            rec["cost_usd"] += cost
        usage = e.get("usage")
        if isinstance(usage, dict):
            if isinstance(usage.get("input_tokens"), (int, float)):
                rec["input_tokens"] += usage["input_tokens"]
            if isinstance(usage.get("output_tokens"), (int, float)):
                rec["output_tokens"] += usage["output_tokens"]
    top_fr = dict(sorted(per_fr.items(), key=lambda kv: -kv[1]["dispatches"])[:10])
    for rec in top_fr.values():
        rec["cost_usd"] = round(rec["cost_usd"], 4)

    cost_values = [e["total_cost_usd"] for e in entries if isinstance(e.get("total_cost_usd"), (int, float))]
    usage_dicts = [e["usage"] for e in entries if isinstance(e.get("usage"), dict)]
    durations = [e["duration_seconds"] for e in entries if isinstance(e.get("duration_seconds"), (int, float))]
    api_durations = [e["duration_api_ms"] for e in entries if isinstance(e.get("duration_api_ms"), (int, float))]

    return {
        "available": True,
        "total_dispatches": total,
        "status_counts": dict(status_counts),
        "error_class_counts": dict(error_class_counts),
        "failure_rate": round(error_like / total, 4) if total else 0.0,
        "dispatches_per_fr_top10": top_fr,
        "cost_usd_total": round(sum(cost_values), 4) if cost_values else None,
        "cost_entries_with_data": len(cost_values),
        "cost_entries_total": total,
        "tokens_input_total": sum(u.get("input_tokens", 0) for u in usage_dicts) if usage_dicts else None,
        "tokens_output_total": sum(u.get("output_tokens", 0) for u in usage_dicts) if usage_dicts else None,
        "duration_seconds_avg": round(sum(durations) / len(durations), 3) if durations else None,
        "duration_api_ms_avg": round(sum(api_durations) / len(api_durations), 1) if api_durations else None,
    }


def _degradation_report(project: Path) -> dict:
    from core.degradation_ledger import LEDGER_RELPATH
    entries = _read_jsonl(project / LEDGER_RELPATH)
    if not entries:
        return {"available": False}
    counts = Counter((e.get("component", "?"), e.get("what", "?")) for e in entries)
    return {
        "available": True,
        "total": len(entries),
        "by_component_what": [
            {"component": c, "what": w, "count": n}
            for (c, w), n in sorted(counts.items(), key=lambda kv: -kv[1])
        ],
    }


def _crash_report(project: Path) -> dict:
    from cli.cr_cmds import triaged_marker
    from core.errors import CRASH_DIR_RELPATH
    crash_dir = project / CRASH_DIR_RELPATH
    if not crash_dir.is_dir():
        return {"available": False}
    bundles = sorted(crash_dir.glob("crash_*.json"))
    if not bundles:
        return {"available": False}
    untriaged = sum(1 for b in bundles if not triaged_marker(b).is_file())
    return {"available": True, "total": len(bundles), "untriaged": untriaged}


def _trajectory_report(project: Path, *, line_cap: int = _TRAJECTORY_LINE_CAP) -> dict:
    """line_cap is injectable (tests pass a small value directly) rather
    than requiring a monkeypatch of the module constant — see
    tests/test_patch_discipline.py's private-patch ratchet."""
    entries = _read_jsonl(project / _TRAJECTORY_RELPATH, line_cap=line_cap)
    if not entries:
        return {"available": False}
    wall_seconds: dict[str, float] = {}
    span_counts: Counter = Counter()
    for e in entries:
        name = e.get("name")
        if not name:
            continue
        start, end = e.get("start_time"), e.get("end_time")
        span_counts[name] += 1
        if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
            # OTEL SDK ReadableSpan.start_time/end_time are nanosecond epoch
            # ints (confirmed live against the installed opentelemetry-sdk).
            wall_seconds[name] = wall_seconds.get(name, 0.0) + (end - start) / 1e9
    return {
        "available": True,
        "total_spans": len(entries),
        "span_counts": dict(span_counts),
        "wall_seconds_by_span": {
            name: round(total, 3)
            for name, total in sorted(wall_seconds.items(), key=lambda kv: -kv[1])
        },
    }


def build_report(project: Path) -> dict:
    """Pure aggregation — no printing, no argparse. Each of the four
    sections is independently optional (see module docstring)."""
    return {
        "project": str(project),
        "spawn_log": _spawn_log_report(project),
        "degradations": _degradation_report(project),
        "crash_bundles": _crash_report(project),
        "trajectory": _trajectory_report(project),
    }


def _render_human(report: dict) -> str:
    lines: list[str] = [f"=== run-report: {report['project']} ==="]

    sl = report["spawn_log"]
    lines.append("")
    lines.append("## Spawn dispatches (.methodology/sessions_spawn.log)")
    if not sl["available"]:
        lines.append("  n/a — log not found or empty")
    else:
        lines.append(f"  total: {sl['total_dispatches']}")
        lines.append(f"  status: {sl['status_counts']}")
        if sl["error_class_counts"]:
            lines.append(f"  error_class: {sl['error_class_counts']}")
        lines.append(f"  failure rate: {sl['failure_rate'] * 100:.2f}%")
        if sl["dispatches_per_fr_top10"]:
            lines.append("  dispatches per FR (top 10):")
            for fr_id, rec in sl["dispatches_per_fr_top10"].items():
                lines.append(
                    f"    {fr_id}: {rec['dispatches']} dispatches, "
                    f"${rec['cost_usd']} cost, "
                    f"{rec['input_tokens']}/{rec['output_tokens']} tokens in/out"
                )
        cost = sl["cost_usd_total"]
        cost_str = f"${cost}" if cost is not None else "n/a"
        lines.append(
            f"  cost: {cost_str} total "
            f"({sl['cost_entries_with_data']}/{sl['cost_entries_total']} entries have cost data)"
        )
        tin, tout = sl["tokens_input_total"], sl["tokens_output_total"]
        lines.append(f"  tokens: {tin if tin is not None else 'n/a'} in / {tout if tout is not None else 'n/a'} out")
        dur = sl["duration_seconds_avg"]
        api_dur = sl["duration_api_ms_avg"]
        lines.append(
            f"  duration: avg {dur if dur is not None else 'n/a'}s wallclock, "
            f"avg {api_dur if api_dur is not None else 'n/a'}ms API"
        )

    dg = report["degradations"]
    lines.append("")
    lines.append("## Degradations (.sessi-work/degradations.jsonl)")
    if not dg["available"]:
        lines.append("  n/a — ledger not found or empty")
    else:
        lines.append(f"  total: {dg['total']}")
        for item in dg["by_component_what"]:
            lines.append(f"    {item['component']}: {item['what']} ({item['count']})")

    cr = report["crash_bundles"]
    lines.append("")
    lines.append("## Crash bundles (.sessi-work/crash/)")
    if not cr["available"]:
        lines.append("  n/a — no crash directory or no bundles")
    else:
        lines.append(f"  total: {cr['total']}")
        lines.append(f"  untriaged: {cr['untriaged']}"
                     + ("  <- run crash-triage --open-cr" if cr["untriaged"] else ""))

    tr = report["trajectory"]
    lines.append("")
    lines.append("## Agent trajectory spans (.harness/traces/agent_trajectory.jsonl)")
    if not tr["available"]:
        lines.append("  n/a — trace file not found or empty")
    else:
        lines.append(f"  total spans: {tr['total_spans']}")
        lines.append("  wall time by span:")
        for name, secs in tr["wall_seconds_by_span"].items():
            lines.append(f"    {name}: {secs}s ({tr['span_counts'][name]} spans)")

    return "\n".join(lines)


def cmd_run_report(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    if not project.is_dir():
        # Round 14 站1: not a BLOCKED exit — this is a read-only report and
        # every section already degrades to "n/a" on a missing artifact;
        # a nonexistent --project is just the limiting case of that same
        # rule, not a distinct failure class. WARN is visible and changes
        # nothing about how the command proceeds.
        print(f"[WARN] --project path does not exist: {project} — showing an empty report",
              file=sys.stderr)
    report = build_report(project)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(_render_human(report))
    return 0


def register(sub) -> None:
    rr = sub.add_parser(
        "run-report",
        help="Read-only aggregation of run artifacts: spawn dispatches "
             "(cost/tokens/failure rate/dispatches-per-FR), degradations, "
             "crash bundles, agent-trajectory span wall-times",
    )
    rr.add_argument("--project", default=".", help="Project root (default: .)")
    rr.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    rr.set_defaults(func=cmd_run_report)
