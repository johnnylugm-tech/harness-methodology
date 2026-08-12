"""check commands: the constitution, and the artifacts it makes legal.

Split out of cli/check_cmds.py in R49-B. Round 9 站0 took the constitution out
of the automatic pipeline and left it on-demand; these three commands are what
"on-demand" means in practice, plus the renderer they share.

`cmd_print_legal_artifacts` reads PHASE_DELIVERABLES, the same source
core/quality_gate/artifact_consistency.py calls LEGAL_ARTIFACTS — one table,
two readers, which is why it is imported rather than restated.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.phase_topology import VALID_PHASES
from core.utils.project_layout import ProjectLayout

def cmd_check_logic(args: argparse.Namespace) -> int:
    """Check code for logic correctness issues (output/branch/lazy-init/semantic)."""
    from scripts.spec_logic_checker import SpecLogicChecker, SemanticValidator

    project = str(Path(args.project).resolve())
    print(f"\n{'='*60}\ncheck-logic  project={project}\n{'='*60}")

    checker = SpecLogicChecker(project)
    result = checker.scan_python_files()
    checker.print_report(result)

    if args.srs and Path(args.srs).exists():
        print(f"\n{'─'*60}")
        print("Semantic Validation (SRS)")
        print(f"{'─'*60}")
        validator = SemanticValidator(args.srs)
        print(f"  Requirements: {len(validator.requirements)}")
        for fr_id, req in list(validator.requirements.items())[:5]:
            print(f"  {fr_id}: {req.get('description', '?')[:60]}...")

    return 0 if result.passed else 1


def cmd_check_constitution(args: argparse.Namespace) -> int:
    """Check constitution document quality for the current phase.

    Runs constitution postflight on the phase-specific directory so agents
    can self-check document quality during phase execution. Does NOT modify
    state.json or advance FSM — purely a diagnostic tool for iterative
    development (write → check → fix → repeat until pass).

    Usage:
      python3 harness_cli.py check-constitution --phase 1 --project .
      python3 harness_cli.py check-constitution --phase 2 --project . \\
          --file 02-architecture/adr/ADR.md
    """
    from core.quality_gate.constitution import run_constitution_check
    from core.quality_gate.constitution.runner import check_single_file
    from core.quality_gate.constitution.profile import get_profile

    project = Path(args.project).resolve()
    phase = int(args.phase)
    profile = get_profile()
    composite_threshold = profile.composite_threshold(phase)

    # ── Single-file branch (--file) ────────────────────────────────────
    file_arg = getattr(args, "file", None)
    if file_arg:
        file_path = Path(file_arg)
        if not file_path.is_absolute():
            file_path = (project / file_path).resolve()

        # Vacuous pass when the target file does not exist (or is a dir).
        if not file_path.exists():
            print(f"[SKIP] File not found: {file_path}")
            return 0
        if not file_path.is_file():
            print(f"[SKIP] Not a regular file: {file_path}")
            return 0

        print(f"\n{'='*60}")
        print(f"Constitution Self-Check — Phase {phase} (single file)")
        print(f"File: {file_path}")
        print(f"Threshold: {composite_threshold:.0f}%")
        print(f"{'='*60}")

        result = check_single_file(file_path, phase)
        return _print_constitution_result(result, composite_threshold, profile, phase, file_path)

    # ── Existing directory branch (unchanged) ──────────────────────────
    _phase_dir = ProjectLayout(project).get_phase_dir(phase)
    if not _phase_dir.exists():
        print(f"[SKIP] Phase {phase} directory not found: {_phase_dir}")
        return 0

    print(f"\n{'='*60}")
    print(f"Constitution Self-Check — Phase {phase}")
    print(f"Directory: {_phase_dir}")
    print(f"Threshold: {composite_threshold:.0f}%")
    print(f"{'='*60}")

    result = run_constitution_check(
        check_type="all", docs_path=str(_phase_dir),
        current_phase=phase, check_mode="postflight",
    )

    return _print_constitution_result(result, composite_threshold, profile, phase, _phase_dir)


def cmd_print_legal_artifacts(args: argparse.Namespace) -> int:
    """Print legal-deliverable filenames as JSON (SSOT for workflow JS prompts).

    Outputs a JSON object with two keys:
      - ``legal_artifacts``: dict[str, set[str]] — stage-dir → legal filenames
        (forward-ref whitelist, same shape as LEGAL_ARTIFACTS in legal_artifacts.py).
      - ``phase_deliverables``: dict[int, list[str]] — phase-number → deliverables
        (Agent B approval keys, same shape as PHASE_DELIVERABLES in legal_artifacts.py).

    Workflow JS callers parse this on startup instead of hardcoding a copy of the
    whitelist in their prompts, eliminating the DRY violation described in
    legal_artifacts.py's module docstring.

    Usage:
        python harness_cli.py print-legal-artifacts
    """
    from core.quality_gate.legal_artifacts import LEGAL_ARTIFACTS, PHASE_DELIVERABLES

    # Convert set values to sorted lists for deterministic JSON output.
    serializable = {
        k: sorted(v) if isinstance(v, set) else v
        for k, v in LEGAL_ARTIFACTS.items()
    }
    payload = {
        "legal_artifacts": serializable,
        "phase_deliverables": {
            str(k): v for k, v in PHASE_DELIVERABLES.items()
        },
    }
    json.dump(payload, sys.stdout, indent=2)
    print()  # trailing newline
    return 0




def _print_constitution_result(result, composite_threshold, profile, phase: int, docs_path) -> int:
    """Print per-dimension breakdown + pass/fail verdict. Shared between
    directory-mode and single-file-mode branches of cmd_check_constitution.
    *docs_path* is the graded directory or single file; it is used to enumerate
    the exact keywords behind each sub-threshold *active* dimension so a fixing
    agent adds content instead of reverse-engineering the gap (same idiom as the
    advance-phase postflight). Returns 0 on pass, 1 on fail.
    """
    from core.quality_gate.constitution.runner import missing_keywords

    # Only the active (composite-scored) dimensions gate the phase; display-only
    # dims (e.g. security on a P2 architecture doc) are shown but must NOT drive
    # keyword advice, or agents chase irrelevant terms into the wrong document.
    _active = set(profile.active_dimensions(phase))
    print(f"\n  Score: {result.score:.0f}%  (threshold={composite_threshold:.0f}%)")
    for dim, score in sorted(result.dimensions.items()):
        dim_threshold = profile.dimension_threshold(dim, phase)
        status = "✓" if score >= dim_threshold else "✗"
        suffix = ""
        if score < dim_threshold and dim in _active and docs_path is not None:
            _miss = missing_keywords(str(docs_path), dim, phase)
            if _miss:
                suffix = f"  ·  missing: {', '.join(_miss)}"
        print(f"    {status} {dim}: {score:.0f}%  (threshold={dim_threshold:.0f}%){suffix}")

    if result.violations:
        # result.violations flags any per-dimension score below its own
        # threshold (100% for P1-P4), which is independent from the
        # composite gate above (bottleneck min-of-dimensions vs
        # composite_threshold, e.g. 80%). A dimension can appear here while
        # the overall gate still PASSES — label accordingly so "Violations"
        # doesn't misread as a blocking failure when it isn't one.
        _label = "Violations" if not result.passed else "Sub-threshold notes (informational — composite already PASSED)"
        print(f"\n  {_label} ({len(result.violations)}):")
        for v in result.violations[:10]:
            print(f"    - [{v.get('dimension', '?')}] {v.get('message', str(v))[:120]}")
        if len(result.violations) > 10:
            print(f"    ... and {len(result.violations) - 10} more")

    if result.passed:
        print(f"\n  [PASS] Constitution quality ≥ {composite_threshold:.0f}% ✓")
        return 0
    print(f"\n  [FAIL] Constitution quality {result.score:.0f}% < {composite_threshold:.0f}%")
    print("  Add substantive coverage of the missing keywords listed above, then re-run check-constitution until PASS.")
    return 1


def register(sub) -> None:
    """Wire the constitution subcommands onto the main subparser action.

    R49-B 站3: a command's flags now live beside its body, so adding one
    touches this file and nothing else. Moved verbatim out of
    cli/check_cmds.py's 295-line register().
    """
    # check-logic
    cl = sub.add_parser(
        "check-logic",
        help="Check code for logic correctness (output/branch/lazy-init/semantic)",
    )
    cl.add_argument("--project", default=".", help="Project root (default: .)")
    cl.add_argument("--srs",     default=None, help="SRS.md path for semantic validation")
    cl.set_defaults(func=cmd_check_logic)

    cc = sub.add_parser(
        "check-constitution",
        help="Check document quality against constitution standards for a phase",
    )
    cc.add_argument("--phase",   required=True, type=int, choices=VALID_PHASES,
                    help="Phase to check (1–8)")
    cc.add_argument("--project", default=".", help="Project root (default: .)")
    cc.add_argument(
        "--file",
        default=None,
        help=(
            "Scope the check to a single file (relative to --project, or absolute). "
            "Missing file = vacuous pass (exit 0). "
            "Default (omitted): scan the whole phase directory."
        ),
    )
    cc.set_defaults(func=cmd_check_constitution)

    # print-legal-artifacts (SSOT exposure for workflow JS prompts — DRY fix)
    pla = sub.add_parser(
        "print-legal-artifacts",
        help="Print legal-deliverable filenames as JSON (SSOT for workflow JS)",
    )
    pla.set_defaults(func=cmd_print_legal_artifacts)
