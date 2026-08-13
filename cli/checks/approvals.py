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

    # Pre-write citation sanity: block unresolvable citations before they land on
    # disk. advance-phase's `_verify_agent_b_approvals_core` already runs this
    # check via `unresolvable_citations`, but persisting a bad citation and
    # blocking later is a worse UX than rejecting at write time: it gives the
    # orchestrator a concrete error to retry on instead of an opaque halt at
    # the phase boundary. Off-by-one range citation observed in production.
    # `TEST_INVENTORY.yaml:791-860` for an 859-line file).
    if isinstance(payload, dict):
        _citations = payload.get("citations", [])
        # Structural guard: advance-phase requires `citations` to be a list
        # (verify_agent_b_approvals_core line 275-279). Mirror that here so
        # we reject malformed payloads with a clear message instead of
        # letting `unresolvable_citations` iterate chars of a string.
        if _citations and not isinstance(_citations, list):
            print(
                "[write-approval] BLOCKED: `citations` must be a list of "
                "`path:line` strings; got "
                + type(_citations).__name__
                + ". advance-phase would reject this exact same shape.",
                file=sys.stderr,
            )
            return 1
        if _citations:
            _bad = agent_b_approvals.unresolvable_citations(project, _citations)
            if _bad:
                print(
                    "[write-approval] BLOCKED: citation(s) do not resolve — "
                    + "; ".join(_bad),
                    file=sys.stderr,
                )
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


def register(sub) -> None:
    """Wire the approval/manifest subcommands onto the main subparser action.

    R49-B 站3: a command's flags now live beside its body, so adding one
    touches this file and nothing else. Moved verbatim out of
    cli/check_cmds.py's 295-line register().
    """
    # check-manifest-integrity (Fix IV — single source of truth for the
    # manifest-corruption check; workflow JS should call this instead of
    # reimplementing it inline)
    cmi = sub.add_parser(
        "check-manifest-integrity",
        help="Validate quality_manifest.json structure (fr_ids/fr_module_traceability/"
             "gate1 truncation patterns) — single source of truth for the check "
             "workflow JS previously reimplemented inline",
    )
    cmi.add_argument("--project", default=".", help="Project root (default: .)")
    cmi.add_argument("--phase", type=int, default=None,
                     help="Current phase number (enables the Gate-1-emptied corruption "
                          "check, which only applies at phase >= 3)")
    cmi.set_defaults(func=cmd_check_manifest_integrity)

    # manifest
    mf = sub.add_parser("manifest", help="Generate quality_manifest.json at P2 exit")
    mf.add_argument("--fr-ids", nargs="+", required=True, metavar="FR_ID")
    mf.add_argument("--sad",    default="02-architecture/SAD.md", help="Path to SAD.md")
    mf.add_argument("--no-git", action="store_true", dest="no_git",
                    help="Disable git commit/push after manifest generation")
    mf.add_argument("--force", action="store_true",
                    help="Overwrite an existing quality_manifest.json "
                         "(default: preserve existing manifest)")
    mf.set_defaults(func=cmd_manifest)

    # generate-verification-report  (P5 — fixes Finding #16)
    gvr = sub.add_parser(
        "generate-verification-report",
        help="Generate 05-verification/VERIFICATION_REPORT.md from manifest + SRS.md",
    )
    gvr.add_argument("--project", default=".", help="Project root (default: .)")
    gvr.set_defaults(func=cmd_generate_verification_report)

    # verify-agent-b-approvals
    vab = sub.add_parser(
        "verify-agent-b-approvals",
        help="Verify Agent B approval JSONs exist for all FRs (blocks if missing or non-APPROVE)",
    )
    vab.add_argument("--phase",   type=int, required=True, help="Current phase number")
    vab.add_argument("--project", default=".", help="Project root (default: .)")
    vab.add_argument("--fr-ids",  default="", dest="fr_ids",
                     help="Comma-separated FR IDs (default: read from quality_manifest.json)")
    vab.set_defaults(func=cmd_verify_agent_b_approvals)

    # write-approval (architectural fix for Bug v22 — replaces LLM-as-shell-wrapper persistApproval)
    wa = sub.add_parser(
        "write-approval",
        help="Deterministically persist an Agent B approval JSON to disk + verify in one call "
             "(replaces workflow JS persistApproval LLM-as-shell-wrapper; atomic write + size check, "
             "exit 0=ok 1=write-fail 2=verify-fail).",
    )
    wa.add_argument("--project", default=".", help="Project root (default: .)")
    wa.add_argument("--fr-id", required=True, dest="fr_id",
                    help="Deliverable ID (e.g. 'SRS.md'). File written to "
                         ".methodology/agent_b_approvals/<fr-id>.json")
    wa.add_argument("--json", default=None,
                    help="JSON payload as a string. Use single quotes around the JSON to escape "
                         "inner double quotes in shell.")
    wa.add_argument("--stdin", action="store_true",
                    help="Read JSON payload from stdin (alternative to --json for large payloads)")
    wa.set_defaults(func=cmd_write_approval)

    # verify-file (architectural fix — replaces 18 LLM-as-shell-wrapper verify sites in 6 phases)
    vf = sub.add_parser(
        "verify-file",
        help="Deterministically verify a file exists + meets size/parse criteria "
             "(replaces workflow JS ctxCheck / load-ctx-a / envReport / persistApproval verify). "
             "Exit 0=ok, 1=missing/invalid.",
    )
    vf.add_argument("--file", required=True, help="File path to verify (absolute, or relative to --project)")
    vf.add_argument("--project", default=".", help="Project root for relative --file paths (default: .)")
    vf.add_argument("--expect", choices=["any", "json", "yaml", "text"], default="any",
                    help="Content expectation: any (default) | json (parse) | yaml (parse) | text (any)")
    vf.add_argument("--min-bytes", type=int, default=1, dest="min_bytes",
                    help="Minimum file size in bytes (default: 1)")
    vf.set_defaults(func=cmd_verify_file)
