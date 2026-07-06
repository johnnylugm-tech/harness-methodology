"""Phase 9 Change Request lifecycle commands (cr-open/update/status/close).

Extracted verbatim from harness_cli.py (方案六 family 1/7). harness_cli
re-exports these names, so tests and callers are unaffected.
"""

import argparse
import json
import sys
from pathlib import Path


def _cr_next_steps(cr: dict) -> str:
    """Type-specific guidance printed after cr-open / cr-update."""
    if cr.get("type") == "bug":
        return (
            "  Next steps (CR-BUG — SUP.9 problem resolution):\n"
            "    1. Write a FAILING repro test; record it:\n"
            f"       harness_cli.py cr-update --cr {cr['id']} --set repro_test=tests/test_crNN_repro.py\n"
            f"    2. Document root cause: cr-update --cr {cr['id']} --set root_cause='...'\n"
            f"    3. Advance: cr-update --cr {cr['id']} --status ANALYZED → APPROVED → IN_PROGRESS\n"
            "    4. Fix code (keep [FR-XX] annotations); repro turns green; full suite green\n"
            "    5. Re-run Gate 1 on touched FRs: run-gate --gate 1 --fr-id FR-XX --phase 9\n"
            f"    6. Record evidence: cr-update --cr {cr['id']} --set affected_frs=FR-XX --set resolution.fix_commit=<sha>\n"
            f"    7. cr-update --cr {cr['id']} --status VERIFIED, then: cr-close --cr {cr['id']}"
        )
    return (
        "  Next steps (CR-FEAT — SUP.10 change request):\n"
        f"    1. Impact analysis: cr-update --cr {cr['id']} --set affected_frs=FR-XX,FR-YY "
        "--set impact_analysis.srs=true ...\n"
        f"    2. Approval: cr-update --cr {cr['id']} --set approval.approved_by=<name> "
        "--set approval.justification='...'\n"
        f"    3. Advance: cr-update --cr {cr['id']} --status ANALYZED → APPROVED → IN_PROGRESS\n"
        "    4. Update SRS.md / SAD.md / TEST_SPEC.md; new modules → amend-sab\n"
        "    5. TDD implement ([FR-XX] annotations + tests/test_frNN.py); Gate 1 per touched FR\n"
        "    6. Rebuild attestation: build-trace-attestation --write\n"
        f"    7. Record evidence, --status VERIFIED, then: cr-close --cr {cr['id']}"
    )


def cmd_cr_open(args: argparse.Namespace) -> int:
    """Open a Phase 9 Change Request ticket (CR-BUG / CR-FEAT)."""
    from core.maintenance import CRManager, CRValidationError
    project = Path(args.project).resolve()
    try:
        mgr = CRManager(project)
        cr = mgr.create(args.type, args.title, args.description or "",
                        severity=args.severity)
    except CRValidationError as exc:
        print(f"[cr-open] {exc}", file=sys.stderr)
        return 1
    print(f"[cr-open] {cr['id']} opened (type={cr['type']}, status=OPEN)")
    print(f"  Ticket: .methodology/change_requests/{cr['id']}.json")
    print(_cr_next_steps(cr))
    return 0


def cmd_cr_update(args: argparse.Namespace) -> int:
    """Update CR fields and/or advance its status (fail-closed validation)."""
    from core.maintenance import CRManager, CRValidationError
    project = Path(args.project).resolve()
    mgr = CRManager(project)
    try:
        if args.set:
            fields: dict = {}
            for kv in args.set:
                if "=" not in kv:
                    print(f"[cr-update] --set expects field=value, got {kv!r}", file=sys.stderr)
                    return 1
                key, _, raw = kv.partition("=")
                key = key.strip()
                # affected_frs: comma-separated FR list
                if key == "affected_frs":
                    value: object = [v.strip() for v in raw.split(",") if v.strip()]
                else:
                    try:
                        value = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        value = raw
                # dotted keys merge into nested dicts (resolution.fix_commit=...)
                if "." in key:
                    top, _, leaf = key.partition(".")
                    cur = mgr.load(args.cr).get(top)
                    nested = dict(cur) if isinstance(cur, dict) else {}
                    nested[leaf] = value
                    fields[top] = nested
                else:
                    fields[key] = value
            cr = mgr.update_fields(args.cr, fields)
            print(f"[cr-update] {cr['id']} fields updated: {', '.join(fields)}")
        if args.status:
            cr = mgr.transition(args.cr, args.status)
            print(f"[cr-update] {cr['id']} → {cr['status']}")
        if not args.set and not args.status:
            print("[cr-update] nothing to do — pass --status and/or --set", file=sys.stderr)
            return 1
    except CRValidationError as exc:
        print(f"[cr-update] BLOCKED: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_cr_status(args: argparse.Namespace) -> int:
    """List all CRs, or show one ticket in full."""
    from core.maintenance import CRManager, CRValidationError
    project = Path(args.project).resolve()
    mgr = CRManager(project)
    if args.cr:
        try:
            cr = mgr.load(args.cr)
        except CRValidationError as exc:
            print(f"[cr-status] {exc}", file=sys.stderr)
            return 1
        print(json.dumps(cr, indent=2, ensure_ascii=False))
        return 0
    crs = mgr.list_all()
    if args.json:
        print(json.dumps(crs, indent=2, ensure_ascii=False))
        return 0
    if not crs:
        print("[cr-status] no change requests found "
              "(open one with: harness_cli.py cr-open --type bug|feat --title ...)")
        return 0
    print(f"{'CR':<8} {'TYPE':<8} {'STATUS':<12} {'FRs':<20} TITLE")
    for cr in crs:
        cr_type = f"CR-{'BUG' if cr.get('type') == 'bug' else 'FEAT'}" if cr.get("type") else "?"
        print(f"{cr.get('id', '?'):<8} {cr_type:<8} {cr.get('status', '?'):<12} "
              f"{','.join(cr.get('affected_frs', [])):<20} {cr.get('title', '')}")
    return 0


def cmd_cr_close(args: argparse.Namespace) -> int:
    """Close a CR after the full re-entry checklist passes (fail-closed).

    Checklist layers:
      1. Ticket-intrinsic evidence (CRManager.closure_problems):
         fix_commit, repro_test existence (bug), affected_frs
      2. Gate 1: every affected FR must have quality_complete=true in
         quality_manifest.json gate_results.gate1
      3. Trace attestation: verify_attestation exit 0 (rebuild with
         build-trace-attestation --write after artifact changes)
      4. Drift: spec/SAD drift clean (HIGH severity blocks)
    All pass → status CLOSED + MAINTENANCE_LOG.md append + decision log.
    Any failure → print the missing items, exit 1, ticket unchanged.
    """
    from core.maintenance import CRManager, CRValidationError
    project = Path(args.project).resolve()
    mgr = CRManager(project)
    try:
        cr = mgr.load(args.cr)
    except CRValidationError as exc:
        print(f"[cr-close] {exc}", file=sys.stderr)
        return 1

    problems: list[str] = []
    if cr.get("status") != "VERIFIED":
        problems.append(
            f"status is {cr.get('status')!r} — cr-close requires VERIFIED "
            f"(advance with cr-update --status)")

    # 1. ticket-intrinsic evidence
    problems.extend(mgr.closure_problems(cr))

    # 2. Gate 1 per affected FR (quality_manifest is the authority)
    manifest_path = project / ".methodology" / "quality_manifest.json"
    gate1: dict = {}
    if manifest_path.exists():
        try:
            _mf = json.loads(manifest_path.read_text(encoding="utf-8"))
            gate1 = (_mf.get("gate_results") or {}).get("gate1") or {}
        except (json.JSONDecodeError, OSError) as exc:
            problems.append(f"quality_manifest.json unreadable: {exc}")
    else:
        problems.append("quality_manifest.json not found — cannot verify Gate 1 results")
    for fr in cr.get("affected_frs", []):
        rec = gate1.get(fr)
        if not isinstance(rec, dict) or not rec.get("quality_complete"):
            problems.append(
                f"{fr}: no passing Gate 1 record in quality_manifest "
                f"(run: run-gate --gate 1 --fr-id {fr} --phase 9 && finalize-gate)")

    # 3. trace attestation (skip only when explicitly requested — e.g. a
    #    docs-only CR that provably touched no traceability inputs)
    if not args.skip_attestation:
        try:
            from scripts.verify_trace_attestation import verify_attestation
            att_code, att_msg = verify_attestation(project)
            if att_code != 0:
                problems.append(
                    f"trace attestation not clean (exit {att_code}): {att_msg}")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            problems.append(f"trace attestation verify failed to run: {exc}")

    # 4. drift (HIGH+ blocks)
    try:
        from detection.drift_detector import DriftDetector, DriftSeverity
        detector = DriftDetector(str(project))
        for check_name, result in (("spec", detector.detect_spec_drift()),
                                   ("sad", detector.detect_sad_drift())):
            high = [i for i in result.drift_items
                    if i.severity in (DriftSeverity.HIGH, DriftSeverity.CRITICAL)]
            if high:
                problems.append(
                    f"{check_name} drift: {len(high)} HIGH+ item(s), e.g. "
                    f"{high[0].location}: {high[0].description}")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        problems.append(f"drift detection failed to run: {exc}")

    if problems:
        print(f"[cr-close] BLOCKED — {cr['id']} cannot close:", file=sys.stderr)
        for p in problems:
            print(f"  ✗ {p}", file=sys.stderr)
        return 1

    try:
        # One StateTransaction: CR json + MAINTENANCE_LOG land together
        # (or not at all) — see CRManager.close.
        cr, log_path = mgr.close(cr["id"])
    except CRValidationError as exc:
        print(f"[cr-close] BLOCKED: {exc}", file=sys.stderr)
        return 1

    # decision log (audit trail — same channel as gate decisions)
    try:
        from harness.decision_log import (
            DecisionContext, DecisionLogEntry, DecisionLogWriter,
        )
        writer = DecisionLogWriter(str(project / ".methodology" / "decision_logs"))
        writer.write(DecisionLogEntry(
            ctx=DecisionContext(agent_id="cr-close", phase=9,
                                fr_id=",".join(cr.get("affected_frs", [])) or None),
            decision="GATE_PASS",
            reasoning=f"{cr['id']} ({cr['type']}) closed: {cr.get('title', '')}",
            metadata={"cr_id": cr["id"],
                      "fix_commit": (cr.get("resolution") or {}).get("fix_commit", "")},
        ))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"  [WARN] decision log write failed (CR still closed): {exc}")

    print(f"[cr-close] {cr['id']} CLOSED ✓")
    print(f"  MAINTENANCE_LOG: {log_path}")
    print(f"  Next: push-milestone --type cr-close --project {args.project}")
    return 0


def register(sub) -> None:
    """Wire the Phase 9 CR parsers onto the main subparser action."""
    cro = sub.add_parser(
        "cr-open",
        help="P9: open a Change Request ticket (CR-BUG=SUP.9 bug-fix, CR-FEAT=SUP.10 feature change)",
    )
    cro.add_argument("--type", required=True, choices=["bug", "feat"],
                     help="bug (SUP.9 problem resolution) | feat (SUP.10 change request)")
    cro.add_argument("--title", required=True, help="Short CR title")
    cro.add_argument("--description", default="", help="Longer description")
    cro.add_argument("--severity", default=None,
                     choices=["critical", "high", "medium", "low"],
                     help="bug: severity; feat: priority")
    cro.add_argument("--project", default=".", help="Project root (default: .)")
    cro.set_defaults(func=cmd_cr_open)

    cru = sub.add_parser(
        "cr-update",
        help="P9: update CR fields (--set field=value) and/or advance status (fail-closed)",
    )
    cru.add_argument("--cr", required=True, help="CR id, e.g. CR-01")
    cru.add_argument("--status", default=None,
                     choices=["ANALYZED", "APPROVED", "IN_PROGRESS", "VERIFIED", "REJECTED"],
                     help="Target status (CLOSED goes through cr-close)")
    cru.add_argument("--set", action="append", default=[], metavar="FIELD=VALUE",
                     help="Field update, repeatable. Dotted keys merge nested "
                          "(resolution.fix_commit=abc123); affected_frs is comma-separated")
    cru.add_argument("--project", default=".", help="Project root (default: .)")
    cru.set_defaults(func=cmd_cr_update)

    crs_p = sub.add_parser(
        "cr-status",
        help="P9: list Change Requests, or show one ticket (--cr CR-NN)",
    )
    crs_p.add_argument("--cr", default=None, help="CR id for full detail")
    crs_p.add_argument("--json", action="store_true", help="JSON output (list mode)")
    crs_p.add_argument("--project", default=".", help="Project root (default: .)")
    crs_p.set_defaults(func=cmd_cr_status)

    crc = sub.add_parser(
        "cr-close",
        help="P9: close a CR after the full re-entry checklist passes "
             "(evidence + Gate 1 + attestation + drift; fail-closed)",
    )
    crc.add_argument("--cr", required=True, help="CR id, e.g. CR-01")
    crc.add_argument("--skip-attestation", action="store_true", dest="skip_attestation",
                     help="Skip trace-attestation check (docs-only CR that touched no traceability inputs)")
    crc.add_argument("--project", default=".", help="Project root (default: .)")
    crc.set_defaults(func=cmd_cr_close)
