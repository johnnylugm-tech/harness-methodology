"""check commands: what was approved, what was declared, what is on disk.

Split out of cli/check_cmds.py in R49-B. Six commands and two helpers around
the record of agreement — the quality manifest, Agent B's approvals, and the
verification report that cites them.

`cmd_verify_file` sits here rather than with the spec checks because what it
verifies is a claim ABOUT a file (that it exists, at the size and shape the
record says), which is the same question the approvals ask.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from core.quality_gate import agent_b_approvals
from core.quality_gate.legal_artifacts import PHASE_DELIVERABLES
from core.state_io import load_quality_manifest

def cmd_check_manifest_integrity(args: argparse.Namespace) -> int:
    """Standalone manifest-integrity check (Fix IV) — thin CLI wrapper around
    PhaseHooks.preflight_manifest_integrity(). Exists so per-phase workflow JS
    can call one narrow, correct check without running the full run-phase
    preflight pipeline. Several workflow JS files previously hand-rolled an
    inline Python one-liner reimplementing this logic with the truncation
    comparison direction inverted (`fr_trace >= fr_ids` instead of the correct
    `fr_ids >= fr_trace`); this command is the single source of truth.
    """
    project = Path(args.project).resolve()
    from core.harness_config import get_value
    from core.phase_hooks import PhaseHooks
    hooks = PhaseHooks(str(project), phase=args.phase, enable_kill_switch=False,
                       drift_threshold=get_value(project, "drift_threshold"))
    result = hooks.preflight_manifest_integrity()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("passed") else 1


def cmd_manifest(args: argparse.Namespace) -> int:
    """Generate quality_manifest.json at P2 exit.

    Refuses to overwrite an existing manifest unless ``--force`` is passed,
    because the manifest holds accumulated Gate scores that ``plan-all`` and
    other commands depend on; shrinking it silently resets pipeline progress.
    """
    from harness.harness_bridge import HarnessBridge

    sad_resolved = Path(args.sad).resolve()
    # SAB.json is written under .methodology/ at the project root, so the project
    # root is the parent directory that *contains* .methodology/. Walking up from
    # the SAD path until we find it (or fall back to the SAD's parent) keeps the
    # contract correct regardless of where SAD.md lives (02-architecture/,
    # docs/, etc.).
    project = sad_resolved.parent
    for ancestor in [sad_resolved.parent, *sad_resolved.parents]:
        if (ancestor / ".methodology").is_dir():
            project = ancestor
            break
    # nargs="+" collects space-separated FR IDs, but users may also pass
    # comma-separated values. Split on commas to support both formats.
    fr_ids: list[str] = []
    for item in args.fr_ids:
        fr_ids.extend(fid.strip() for fid in item.split(",") if fid.strip())
    bridge = HarnessBridge()
    out = bridge.generate_quality_manifest(
        fr_ids=fr_ids,
        sad_path=args.sad,
        project_root=str(project),
        force=getattr(args, "force", False),
    )
    if out is None:
        manifest_path = project / ".methodology" / "quality_manifest.json"
        print(
            f"[PRESERVE] {manifest_path.name} already exists; "
            "use --force to regenerate."
        )
        return 0
    print(f"quality_manifest.json written → {out}")
    manifest = json.loads(out.read_text(encoding="utf-8"))
    print(f"  fr_ids        : {manifest['fr_ids']}")
    print(f"  generated_at  : phase {manifest['generated_at_phase']}")
    _generate_sab_json(project)
    return 0


def cmd_generate_verification_report(args: argparse.Namespace) -> int:
    """Generate 05-verification/VERIFICATION_REPORT.md from manifest + SRS.

    Created to fix Finding #16: P5 plan's VERIFY-REPORT task said "Generate
    VERIFICATION_REPORT.md" but no harness tool produced it. The P4→P5
    handoff validator blocks on this file with no remediation path; this
    command is the canonical remediation.

    Usage:
        python3 harness_cli.py generate-verification-report --project .
    """
    from scripts.generate_verification_report import generate_verification_report

    project = Path(args.project).resolve()
    try:
        out = generate_verification_report(project)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[FAIL] generate-verification-report: {exc}", file=sys.stderr)
        return 1
    print(f"VERIFICATION_REPORT.md written → {out}")
    # Echo summary lines so the operator can see pass/fail count at a glance
    try:
        text = out.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "FRs Gate 1 PASS" in line or "Pass rate" in line:
                print(f"  {line.strip()}")
    except Exception as exc:  # non-fatal
        print(f"[WARN] generate-verification-report: could not echo summary lines: {exc}", file=sys.stderr)
    return 0


def cmd_verify_agent_b_approvals(args: argparse.Namespace) -> int:
    """Verify that Agent B approval JSON files exist for all required FRs.

    Each FR must have a corresponding .methodology/agent_b_approvals/FR-XX.json
    with review_status == "APPROVE" and the required docs_embedded list.

    NOTE: .methodology/agent_b_approvals/ is committed (not gitignored).
    Do NOT use .sessi-work/ — that directory is in .gitignore and invisible to CI.

    Usage:
      python harness_cli.py verify-agent-b-approvals --phase 8 --fr-ids FR-01,FR-02 --project .
      python harness_cli.py verify-agent-b-approvals --phase 8 --project .  # reads from manifest
    """
    project = Path(args.project).resolve()
    phase = args.phase

    fr_ids_arg = getattr(args, "fr_ids", "") or ""
    fr_ids = [f.strip() for f in fr_ids_arg.split(",") if f.strip()]
    deliverable_ids = _resolve_deliverable_ids(project, phase, fr_ids)

    if not deliverable_ids:
        print("[verify-agent-b] No FR IDs found — pass --fr-ids or ensure quality_manifest.json exists.")
        return 1

    passed, report = agent_b_approvals.verify_agent_b_approvals_core(project, phase, deliverable_ids)
    print(report)
    return 0 if passed else 1


def cmd_write_approval(args: argparse.Namespace) -> int:
    """Deterministically persist an Agent B approval JSON to disk + verify in-process.

    Replaces the LLM-as-shell-wrapper pattern in workflow JS (persistApproval helper
    that wrapped `python3 -c "open().write()"` + a second `agent()` call for disk
    verification — same anti-pattern, double agent round-trip, no real verification).

    Architecture (Bug v22 fix, 2026-06-29): write + verify happen in a single Python
    call so the harness can guarantee the file exists with the expected content.
    The Bash invocation by the workflow tool sees a single deterministic exit code
    (0 = written + verified; 1 = write failed; 2 = verify failed).

    Usage:
      python harness_cli.py write-approval --fr-id SRS.md --json '<json>'
      echo '<json>' | python harness_cli.py write-approval --fr-id SRS.md --stdin
    """
    import json as _json

    project = Path(args.project).resolve()
    fr_id = args.fr_id
    if not fr_id:
        print("[write-approval] ERROR: --fr-id is required", file=sys.stderr)
        return 1

    # Resolve JSON payload from --json arg or stdin
    if args.stdin:
        raw = sys.stdin.read()
    else:
        raw = args.json or ""
    if not raw:
        print("[write-approval] ERROR: no JSON payload (--json or --stdin required)", file=sys.stderr)
        return 1

    # Validate JSON is parseable before any disk I/O
    try:
        payload = _json.loads(raw)
    except _json.JSONDecodeError as e:
        print(f"[write-approval] ERROR: invalid JSON payload: {e}", file=sys.stderr)
        return 1

    approvals_dir = project / ".methodology" / "agent_b_approvals"
    approval_path = approvals_dir / f"{fr_id}.json"
    try:
        approvals_dir.mkdir(parents=True, exist_ok=True)
        # Atomic write (tmp + os.replace) — same pattern as taskq NFR-03 atomic contract
        tmp_path = approval_path.with_suffix(approval_path.suffix + ".tmp")
        tmp_path.write_text(_json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, approval_path)
    except OSError as e:
        print(f"[write-approval] ERROR: write failed for {approval_path}: {e}", file=sys.stderr)
        return 1

    # Deterministic in-process verify (replaces LLM-as-shell-wrapper disk check)
    if not approval_path.is_file():
        print(f"[write-approval] ERROR: verify failed — {approval_path} not on disk after write", file=sys.stderr)
        return 2
    size = approval_path.stat().st_size
    if size < 10:
        print(f"[write-approval] ERROR: verify failed — {approval_path} only {size} bytes", file=sys.stderr)
        return 2

    print(f"[write-approval] OK: {approval_path} ({size} bytes, written + verified)")
    return 0


def cmd_verify_file(args: argparse.Namespace) -> int:
    """Deterministically verify a file exists and (optionally) has parseable content.

    Replaces 18 LLM-as-shell-wrapper sites across 6 phase workflow JS files
    (ctxCheck / load-ctx-a / envReport / persistApproval verify). Single Python call
    reads the file, validates min-bytes, optionally parses JSON/YAML, and emits
    one deterministic exit code that the workflow JS regex-matches on stdout.

    Usage:
      python harness_cli.py verify-file --file path/to/ctx.json --expect json --min-bytes 50
      python harness_cli.py verify-file --file path/to/file --min-bytes 1   # any non-empty file
    """
    import json as _json

    file_path = Path(args.file)
    if not file_path.is_absolute():
        file_path = (Path(args.project).resolve() / file_path) if args.project else file_path

    expect = (args.expect or "any").lower()  # any | json | yaml | text
    min_bytes = args.min_bytes if args.min_bytes is not None else 1

    if not file_path.exists():
        print(f"[verify-file] MISSING: {file_path}", file=sys.stderr)
        return 1
    if not file_path.is_file():
        print(f"[verify-file] NOT_A_FILE: {file_path}", file=sys.stderr)
        return 1

    size = file_path.stat().st_size
    if size < min_bytes:
        print(f"[verify-file] TOO_SMALL: {file_path} ({size} bytes < {min_bytes})", file=sys.stderr)
        return 1

    if expect == "json":
        try:
            _json.loads(file_path.read_text(encoding="utf-8"))
        except (_json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            print(f"[verify-file] INVALID_JSON: {file_path}: {e}", file=sys.stderr)
            return 1
    elif expect == "yaml":
        try:
            import yaml as _yaml  # type: ignore
            _yaml.safe_load(file_path.read_text(encoding="utf-8"))
        except ImportError:
            print("[verify-file] WARN: PyYAML not installed — skipping YAML parse, treating as text", file=sys.stderr)
        except Exception as e:  # yaml.YAMLError + others
            print(f"[verify-file] INVALID_YAML: {file_path}: {e}", file=sys.stderr)
            return 1
    # expect == "any" | "text" — just existence + size check

    print(f"[verify-file] OK: {file_path} ({size} bytes, expect={expect})")
    return 0


def _generate_sab_json(project: Path) -> bool:
    """Run scripts/generate_sab.py to produce .methodology/SAB.json. Returns True on success.

    Round 5: resolves scripts/ via the shared `harness_scripts_dir()` SSOT
    instead of `Path(__file__).parent / "scripts"` — `cli/` has no `scripts/`
    subdirectory (it's a sibling at the repo root), so that arithmetic always
    resolved to a non-existent path and this call unconditionally failed.
    """
    import subprocess  # nosec B404
    from core.utils.script_loader import harness_scripts_dir
    sab_script = harness_scripts_dir() / "generate_sab.py"
    if not sab_script.exists():
        print("  [SAB] ERROR: generate_sab.py not found — pipeline blocked")
        return False
    try:
        result = subprocess.run(  # nosec B603 B607
            ["python3", str(sab_script), "--project", str(project)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            sab_path = project / ".methodology" / "SAB.json"
            print(f"  [SAB] SAB.json written → {sab_path}")
            return True
        else:
            print(f"  [SAB] ERROR: generate_sab.py failed — pipeline blocked: {result.stderr[:200]}")
            return False
    except Exception as exc:
        print(f"  [SAB] ERROR: SAB generation error — pipeline blocked: {exc}")
        return False

def _resolve_deliverable_ids(
    project: Path, phase: int, fr_ids: "list[str]"
) -> "list[str]":
    """Return the deliverable IDs to check for Agent B approvals.

    P1/P2: always returns the phase-level deliverables from PHASE_DELIVERABLES
           (per-FR approval is only meaningful from P3 onwards).
    P3+:   fr_ids from caller → quality_manifest.json → empty list.
    """
    if phase in PHASE_DELIVERABLES:
        return PHASE_DELIVERABLES[phase]
    if fr_ids:
        return fr_ids
    return load_quality_manifest(project, lenient=True).get("fr_ids", [])
