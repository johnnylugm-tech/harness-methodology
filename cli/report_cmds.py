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
    from core.spawn_log_schema import rows_that_can_carry
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

    # Round 50 站5: the denominator is the rows that COULD carry a cost, not
    # every row. The workflow substrate has no envelope and no clock, so its
    # rows are not missing a measurement — they are from a writer that cannot
    # take one. Measured on taskq-api: 152 of 292 rows carried a cost, which
    # read as 48% of the run's spending lost; against the 176 cost-capable
    # rows it is 86%, and the 116 excluded are named rather than counted.
    cost_capable = rows_that_can_carry(entries, "total_cost_usd")
    cost_values = [e["total_cost_usd"] for e in cost_capable if isinstance(e.get("total_cost_usd"), (int, float))]
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
        "cost_entries_total": len(cost_capable),
        "cost_entries_excluded_substrate": total - len(cost_capable),
        "tokens_input_total": sum(u.get("input_tokens", 0) for u in usage_dicts) if usage_dicts else None,
        "tokens_output_total": sum(u.get("output_tokens", 0) for u in usage_dicts) if usage_dicts else None,
        "duration_seconds_avg": round(sum(durations) / len(durations), 3) if durations else None,
        "duration_api_ms_avg": round(sum(api_durations) / len(api_durations), 1) if api_durations else None,
    }


def _failure_modes_report(project: Path) -> dict:
    """Round 16 站3: reclassify the same spawn-log entries _spawn_log_report
    reads against core.failure_modes' MAST-aligned rules. Independent read
    (each run-report section is self-contained, same as every other section
    here) — not a derivative of _spawn_log_report's output."""
    from core.failure_modes import UNCLASSIFIED, classify_entry, is_failure_entry, summarize
    from core.sessions_spawn_logger import SessionsSpawnLogger
    entries = _read_jsonl(project / SessionsSpawnLogger.LOG_FILENAME)
    if not entries:
        return {"available": False}
    summary = summarize(entries)
    timestamps = [e["timestamp"] for e in entries if isinstance(e.get("timestamp"), str)]
    # Round 19 站1: name the shapes behind unclassified_failure_count, so the
    # operator sees WHICH phrasing needs a rule instead of only that some do.
    # De-duplicated and capped — this is a pointer to the gap, not a log dump.
    samples: list[str] = []
    for entry in entries:
        if not is_failure_entry(entry):
            continue
        if classify_entry(entry)["mode_id"] != UNCLASSIFIED:
            continue
        text = f"{entry.get('status')}: {str(entry.get('error_output') or '')[:120]}"
        if text not in samples:
            samples.append(text)
        if len(samples) >= 5:
            break
    return {
        "available": True,
        **summary,
        "unclassified_failure_samples": samples,
        "first_timestamp": timestamps[0] if timestamps else None,
        "last_timestamp": timestamps[-1] if timestamps else None,
    }


def _gate_provenance_report(project: Path) -> dict:
    """Round 19 站3: which harness commit produced each gate verdict on record.

    Deliberately minimal — verdict, composite, enforcer. The question this
    answers is "did the enforcer change between these two runs?", which is what
    taskq's Gate 2 BLOCK(96.7) -> PASS(96.7) could not be asked of. Results
    written before that station have no enforcer_sha and report None rather
    than being hidden, so a mixed-vintage project reads honestly.
    """
    from cli._shared import gate_verdict_paths
    gates: list[dict] = []
    for gate in (1, 2, 3, 4):
        # Round 26: finalized-only. gate_result_paths puts the agent's
        # pre-finalize .sessi-work draft first — correct for the read that drives
        # scoring, wrong here, and the reason this section reported verdict=None
        # for every Gate 1 while the persisted per-FR file said PASS.
        for path in gate_verdict_paths(project, gate):
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            gates.append({
                "gate": gate,
                # Gate 1 is per-FR, so several finalized verdicts coexist and this
                # row describes ONE of them (the first by sorted FR id). Carrying
                # the fr_id says which — an unlabelled verdict invites the reader
                # to take it for the gate's, which is the ambiguity the None it
                # replaced at least did not have.
                "fr_id": data.get("fr_id"),
                "verdict": data.get("verdict") or data.get("passed"),
                "composite_score": data.get("composite_score") or data.get("overall_score"),
                "enforcer_sha": data.get("enforcer_sha"),
                # Round 27 站3: how many of this verdict's dimensions still carry
                # proof of what they read. The evidence itself is normally under
                # the gitignored .sessi-work/ and does not survive the run, so a
                # low number here means the verdict is no longer checkable.
                "evidence_digest_count": len(data.get("evidence_digest") or {}),
                "scored_dimension_count": len(data.get("breakdown") or {}),
            })
            break
    if not gates:
        return {"available": False}
    return {"available": True, "gates": gates}


def _workflow_block_report(project: Path) -> dict:
    """Where runs stopped, and who owns each stop (Round 48 站2).

    The one run artifact that had no reader because it had no writer. Counts
    are per SIGNATURE, not per row: a block recorded three times is one place
    the pipeline keeps stopping, and reporting it as three would flatten the
    fact worth seeing back into a total.
    """
    from core.workflow_blocks import open_blocks, read_blocks

    rows = read_blocks(project)
    if not rows:
        return {"available": False}
    still_open = open_blocks(project)
    repeats = Counter(row.get("signature") for row in rows if not row.get("resolved"))
    return {
        "available": True,
        "total_records": len(rows),
        "open": len(still_open),
        "by_owner": dict(Counter(row.get("owner", "?") for row in still_open)),
        "open_blocks": [
            {
                "signature": row.get("signature"),
                "phase": row.get("phase"),
                "step": row.get("step"),
                "owner": row.get("owner"),
                "seen": repeats.get(row.get("signature"), 1),
                "recurred_after_resolution": bool(row.get("recurred_after_resolution")),
                "message": (row.get("message") or "")[:160],
            }
            for row in still_open
        ],
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
        # Round 32 站6: the ledger's highest-frequency entry, counted.
        # Measured on a live P4: all four rows were the same row — TDD-GREEN
        # escalating 40 -> 80 turns, for four of the eight FRs. The escalation
        # absorbed the cost silently and nothing read the record, so the
        # default stayed 40. This does not change the default; it surfaces the
        # number that would justify changing it.
        "turn_ceiling_escapes": sum(
            1 for e in entries if "max_turns escalated" in str(e.get("what", ""))
        ),
        "by_component_what": [
            {"component": c, "what": w, "count": n}
            for (c, w), n in sorted(counts.items(), key=lambda kv: -kv[1])
        ],
    }


def _crash_report(project: Path) -> dict:
    from cli.cr_cmds import triaged_marker
    from core.errors import crash_bundle_paths
    bundles = crash_bundle_paths(project)
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
    """Pure aggregation — no printing, no argparse. Each of the five
    sections is independently optional (see module docstring)."""
    return {
        "project": str(project),
        "spawn_log": _spawn_log_report(project),
        "failure_modes": _failure_modes_report(project),
        "gate_provenance": _gate_provenance_report(project),
        "degradations": _degradation_report(project),
        "workflow_blocks": _workflow_block_report(project),
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

    fm = report["failure_modes"]
    lines.append("")
    lines.append("## Failure modes (MAST-aligned, docs/OBSERVABILITY.md)")
    if not fm["available"]:
        lines.append("  n/a — log not found or empty")
    else:
        lines.append(f"  total: {fm['total']}  (failures: {fm['failure_total']})")
        lines.append(f"  by mode: {fm['mode_counts']}")
        lines.append(f"  by MAST category: {fm['category_counts']}")
        # The failure-scoped figure first, and labelled as the real one: the
        # all-entry percentage counts successes, which match no failure rule by
        # construction, so it rises when a run goes WELL (Round 19 站1).
        lines.append(
            f"  unexplained failures: {fm['unclassified_failure_count']}"
            f"/{fm['failure_total']} ({fm['unclassified_failure_pct']}%)"
        )
        lines.append(
            f"  unclassified over all entries: {fm['unclassified_count']} "
            f"({fm['unclassified_pct']}%) — includes successes, not a defect signal"
        )
        for sample in fm.get("unclassified_failure_samples") or []:
            lines.append(f"    needs a rule: {sample}")
        lines.append(f"  span: {fm['first_timestamp']} .. {fm['last_timestamp']}")

    gp = report["gate_provenance"]
    lines.append("")
    lines.append("## Gate provenance (which harness commit produced each verdict)")
    if not gp["available"]:
        lines.append("  n/a — no gate result files found")
    else:
        for row in gp["gates"]:
            enforcer = row["enforcer_sha"] or "not recorded (result predates Round 19 站3)"
            scope = f" [{row['fr_id']}]" if row.get("fr_id") else ""
            lines.append(
                f"  Gate {row['gate']}{scope}: verdict={row['verdict']} "
                f"score={row['composite_score']} enforcer={enforcer}"
            )
            _dg, _dims = row.get("evidence_digest_count", 0), row.get("scored_dimension_count", 0)
            if _dg:
                lines.append(f"      evidence digests: {_dg}/{_dims} dimension(s)")
            elif _dims:
                lines.append(
                    f"      evidence digests: 0/{_dims} — this verdict cannot be "
                    f"re-checked; its evidence was not fingerprinted (pre-Round-27 result)"
                )
            if str(row.get("enforcer_sha") or "").endswith("-dirty"):
                lines.append(
                    "      WARN: enforcer was a dirty working tree — this verdict "
                    "corresponds to no commit and cannot be reproduced"
                )
        lines.append(
            "  NOTE: a log spanning multiple rounds/eras mixes different "
            "code versions — counts are not evidence of the CURRENT "
            "failure distribution (see docs/PROPOSAL_ADJUDICATIONS.md's "
            "Round 15 entry)."
        )

    dg = report["degradations"]
    lines.append("")
    from core.degradation_ledger import LEDGER_RELPATH as _ledger_rel
    lines.append(f"## Degradations ({_ledger_rel})")
    if not dg["available"]:
        lines.append("  n/a — ledger not found or empty")
    else:
        lines.append(f"  total: {dg['total']}")
        if dg.get("turn_ceiling_escapes"):
            lines.append(
                f"  turn-ceiling escapes: {dg['turn_ceiling_escapes']} — steps "
                f"that did not fit their configured max_turns and were "
                f"re-dispatched with a raised one. A high count against the FR "
                f"count is the case for raising values.step_max_turns."
            )
        for item in dg["by_component_what"]:
            lines.append(f"    {item['component']}: {item['what']} ({item['count']})")

    wb = report["workflow_blocks"]
    lines.append("")
    from core.workflow_blocks import LEDGER_RELPATH as _blocks_rel
    lines.append(f"## Workflow blocks ({_blocks_rel})")
    if not wb["available"]:
        lines.append("  n/a — no run has recorded a halt in this project")
    else:
        lines.append(f"  records: {wb['total_records']}  open: {wb['open']}")
        if wb["by_owner"]:
            lines.append("  by owner: " + ", ".join(
                f"{owner}={count}" for owner, count in sorted(wb["by_owner"].items())))
        for item in wb["open_blocks"]:
            seen = f" (seen {item['seen']}x)" if item["seen"] > 1 else ""
            back = " <- RETURNED AFTER A REPAIR" if item["recurred_after_resolution"] else ""
            lines.append(
                f"    P{item['phase']} / {item['step']} [{item['owner']}]{seen}{back}: "
                f"{item['message']}"
            )
        if any(i["recurred_after_resolution"] for i in wb["open_blocks"]):
            lines.append(
                "  ^ a block marked resolved has come back at the same "
                "coordinate. The repair was recorded, not verified — do not "
                "repeat it unchanged"
            )
        if wb["by_owner"].get("harness"):
            lines.append(
                "  ^ a harness-owned block is not a project quality failure — "
                "run the harness repair workflow, not a fix agent"
            )
        if wb["by_owner"].get("unknown"):
            lines.append(
                "  ^ unknown means the halt carried no evidence of ownership. "
                "It is not a project failure by default; see "
                "core/fault_owner.py's text rules for what would name one"
            )

    cr = report["crash_bundles"]
    lines.append("")
    from core.errors import CRASH_DIR_RELPATH
    lines.append(f"## Crash bundles ({CRASH_DIR_RELPATH}/)")
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


# Round 19 站1 — failure-corpus export.
#
# The fields a corpus entry keeps are exactly the RAW SIGNALS the classifier's
# rules read (core.failure_modes), and deliberately NOT `error_class`: that is
# a verdict stamped at dispatch time, and keeping it would make the corpus
# replay old verdicts instead of exercising the current signature registry
# end-to-end (see core.failure_modes._effective_error_class).
#
# Everything identifying is dropped — session_id, the prompt text, timestamps,
# phase/fr_id, any path. A corpus records failure SHAPES, not a log copy. That
# is also why it is safe to carry shapes observed on one project into this
# repo's fixtures at all.
# Round 41 站3 added `transport_error`: it is a raw signal, not a verdict, and
# it is the field `core.failure_modes._effective_error_class` re-derives from.
# A corpus exported without it would exercise only the pre-Round-41 fallback
# path, which is the opposite of what a corpus is for.
_CORPUS_FIELDS = (
    "role", "status", "error_output", "regression_flags", "inner_status",
    "transport_error",
)


def build_failure_corpus(project: Path) -> list[dict]:
    """Read-only: distinct failure shapes from a project's sessions_spawn.log.

    Failures only (core.failure_modes.is_failure_entry) — a successful dispatch
    matches no failure rule by construction and would only pad the corpus.
    De-duplicated on the kept fields, since the corpus measures shape coverage,
    not how often a shape recurred.
    """
    from core.failure_modes import is_failure_entry
    from core.sessions_spawn_logger import SessionsSpawnLogger
    seen: set[str] = set()
    out: list[dict] = []
    for entry in _read_jsonl(project / SessionsSpawnLogger.LOG_FILENAME):
        if not is_failure_entry(entry):
            continue
        shape = {k: entry[k] for k in _CORPUS_FIELDS if k in entry}
        key = json.dumps(shape, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        out.append(shape)
    return out


def cmd_export_failure_corpus(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    shapes = build_failure_corpus(project)
    if not shapes:
        print(f"[WARN] no failed dispatches found under {project} — nothing to export",
              file=sys.stderr)
    payload = "".join(json.dumps(s, sort_keys=True, ensure_ascii=False) + "\n" for s in shapes)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload, encoding="utf-8")
        print(f"[export-failure-corpus] {len(shapes)} distinct failure shape(s) -> {out_path}")
    else:
        sys.stdout.write(payload)
    return 0


def cmd_log_dispatch(args: argparse.Namespace) -> int:
    """Append workflow-substrate dispatch records to sessions_spawn.log.

    Round 26 站5. The harness has two dispatch substrates and instrumented one:
    per-FR steps go through `run-fr-step` -> core/agent_spawner.spawn(), which logs
    cost, turns, usage and outcome; everything else — all of P1 and P2, every
    preflight, every peer review — goes through the workflow script's own `agent()`,
    which the harness never saw. taskq-plus: 42 spawn-log entries, all phase 3,
    while the trajectory file recorded phase_1_preflight and phase_2_preflight
    spans beside them, so run-report's "42 dispatches, failure rate 9.52%" was a
    P3/P4 number presented as the run's.

    The generated `dispatch()` wrapper buffers records and hands them to the next
    dispatch, which calls this. Records carry role / phase_label / status only:
    the Workflow sandbox has no clock and no envelope access, so cost, turns and
    duration remain available for Python-spawned dispatches ONLY. What this
    recovers is the denominator (see docs/OBSERVABILITY.md).

    Deliberately forgiving: a malformed batch is reported and skipped rather than
    failing the caller. This runs inside another agent's turn as bookkeeping, and
    losing an observability record must never break the work being observed.
    """
    project = Path(args.project).resolve()
    try:
        batch = json.loads(args.batch)
    except (TypeError, ValueError) as exc:
        print(f"[log-dispatch] skipped: --batch is not valid JSON: {exc}",
              file=sys.stderr)
        return 0
    if not isinstance(batch, list):
        print(f"[log-dispatch] skipped: --batch must be a JSON array, "
              f"got {type(batch).__name__}", file=sys.stderr)
        return 0

    from core.sessions_spawn_logger import log_spawn_event
    from core.spawn_log_schema import validate_row

    written = 0
    for entry in batch:
        if not isinstance(entry, dict):
            continue
        fields = dict(entry)
        role = str(fields.pop("role", "workflow-agent"))
        status = str(fields.pop("status", "complete"))
        try:
            written_entry = log_spawn_event(
                project, role=role, task="", session_id="", status=status, **fields
            )
            written += 1
            # Round 50 站5. The generated JS is the one writer whose field names
            # are not reviewed alongside the schema, so a new column arriving
            # from it is reported here — after the write, because a row nobody
            # has a name for is still a row that happened. The enforcement is
            # tests/test_spawn_log_schema.py, which reads the shipped
            # run-all.js; this is the runtime half that names the drift on a
            # project that is running an older workflow copy.
            for problem in validate_row(written_entry):
                print(f"[log-dispatch] {role}: {problem}", file=sys.stderr)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"[log-dispatch] entry skipped ({role}): "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
    print(f"[log-dispatch] recorded {written}/{len(batch)} workflow dispatch(es)")
    return 0


def cmd_record_block(args: argparse.Namespace) -> int:
    """Record where a workflow stopped, and say who owns it (Round 48 站2).

    The Workflow sandbox has no filesystem, no shell and no clock (see
    run-all.js's own Round 26 note), so a workflow cannot write this itself —
    it spends one dispatch on this command. `log-dispatch` avoids that cost by
    riding along on the NEXT dispatch's prompt, which is unavailable here by
    definition: a halt is terminal, and there is no next dispatch. One
    dispatch, once per aborted run, is the price of the run's last fact.

    Classification happens HERE rather than in the generated JS. The owner
    table is `core/fault_owner.py`; putting a copy of it in a workflow string
    literal would be Round 36's shape (one fact, N statements) on a surface
    nobody can unit-test.

    Exit code is always 0. This command reports a block that already happened;
    failing it would replace the caller's real problem with a bookkeeping one.
    """
    from core.fault_owner import classify_fault, routes_to_harness_repair
    from core.workflow_blocks import record_block

    project = Path(args.project).resolve()
    verdict = classify_fault(exit_code=args.exit_code, text=args.message)
    try:
        signature = record_block(
            project,
            phase=args.phase,
            step=args.step,
            owner=verdict.owner,
            message=args.message,
            exit_code=args.exit_code,
            evidence=verdict.evidence,
        )
    except OSError as exc:
        print(f"[record-block] could not write the ledger: {exc}", file=sys.stderr)
        return 0

    routes = routes_to_harness_repair(verdict)
    print(json.dumps({
        "signature": signature,
        "owner": verdict.owner,
        "evidence": verdict.evidence,
        "repair_workflow": ".claude/workflows/harness-repair.js" if routes else None,
    }, ensure_ascii=False))
    if routes:
        print(
            "[record-block] owner=harness — this is not a project quality "
            "failure. Launch the harness repair workflow with this signature, "
            "then relaunch run-all.",
            file=sys.stderr,
        )
    return 0


def register(sub) -> None:
    rb = sub.add_parser(
        "record-block",
        help="Record where a workflow stopped and classify whose fault it is "
             "(harness / project / infra / unknown) — the one pipeline event "
             "that landed nowhere before Round 48",
    )
    rb.add_argument("--project", default=".", help="Project root (default: .)")
    rb.add_argument("--phase", type=int, required=True, help="Phase 1-8")
    rb.add_argument("--step", required=True,
                    help="The phase box the run stopped in, e.g. 'Gate 3'")
    rb.add_argument("--message", required=True,
                    help="The halt message, verbatim — framework-authored text "
                         "only (see core/fault_owner.py on why an agent's reply "
                         "must not be classified)")
    rb.add_argument("--exit-code", type=int, default=None,
                    help="Exit code, when the halt came from a CLI command")
    rb.set_defaults(func=cmd_record_block)

    ld = sub.add_parser(
        "log-dispatch",
        help="Append workflow-substrate dispatch records (role/phase/status) to "
             "sessions_spawn.log — the half of the dispatch population the Python "
             "spawner never sees (Round 26)",
    )
    ld.add_argument("--project", default=".", help="Project root (default: .)")
    ld.add_argument("--batch", required=True,
                    help="JSON array of dispatch records, as emitted by the "
                         "generated workflow's dispatch() wrapper")
    ld.set_defaults(func=cmd_log_dispatch)

    ec = sub.add_parser(
        "export-failure-corpus",
        help="Read-only: export de-identified, de-duplicated dispatch-failure "
             "shapes from a project's sessions_spawn.log, for use as classifier "
             "test fixtures (tests/fixtures/failure_corpus/)",
    )
    ec.add_argument("--project", default=".", help="Project root (default: .)")
    ec.add_argument("--out", default=None,
                    help="Write JSONL here (default: stdout)")
    ec.set_defaults(func=cmd_export_failure_corpus)

    rr = sub.add_parser(
        "run-report",
        help="Read-only aggregation of run artifacts: spawn dispatches "
             "(cost/tokens/failure rate/dispatches-per-FR), MAST-aligned "
             "failure modes, degradations, crash bundles, agent-trajectory "
             "span wall-times",
    )
    rr.add_argument("--project", default=".", help="Project root (default: .)")
    rr.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    rr.set_defaults(func=cmd_run_report)
