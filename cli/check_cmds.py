"""The CLI wiring for the check commands — 24 subcommands, one register().

R49-B split the command bodies into cli/checks/ by the question each asks
(specs, gates, trace, approvals, constitution, hunt). What stayed here is the
argparse surface: this file is what harness_cli.py calls, and it is where a
reader looks to find out which flags a check command takes.

The imports below are re-exports as much as dependencies. harness_cli.py does
`from cli.check_cmds import (...)` and tests do the same; a split that moved
the names out from under those callers would break code that never asked
where the implementation lived.
"""

from __future__ import annotations

from core.phase_topology import VALID_PHASES

from cli.checks.approvals import (  # noqa: F401  (re-exported for harness_cli + tests)
    _generate_sab_json,
    _resolve_deliverable_ids,
    cmd_check_manifest_integrity,
    cmd_generate_verification_report,
    cmd_manifest,
    cmd_verify_agent_b_approvals,
    cmd_verify_file,
    cmd_write_approval,
)
from cli.checks.constitution import (  # noqa: F401
    _print_constitution_result,
    cmd_check_constitution,
    cmd_check_logic,
    cmd_print_legal_artifacts,
)
from cli.checks.gates import (  # noqa: F401
    cmd_crg_arch_check,
    cmd_spec_coverage_check,
    cmd_verify_ci,
    cmd_verify_gate,
)
from cli.checks.hunt import (  # noqa: F401
    _run_gap_analysis,
    cmd_bug_hunt_targets,
    cmd_run_gap_analysis,
)
from cli.checks.specs import (  # noqa: F401
    cmd_check_artifact_consistency,
    cmd_check_property_spec,
    cmd_check_spec_alignment,
    cmd_check_test_mirrors_spec,
    cmd_check_test_spec_consistency,
    cmd_verify_spec,
)
from cli.checks.trace import (  # noqa: F401
    cmd_build_trace_attestation,
    cmd_migrate_trace_overlay,
    cmd_verify_trace,
)


def register(sub) -> None:
    """Wire this family's parsers onto the main subparser action."""
    # bug-hunt-targets (v2.9 C4 — Gate-3 adversarial-review targeting manifest)
    bht = sub.add_parser(
        "bug-hunt-targets",
        help="Aggregate hunt-targeting signals (declared/CRG/survivors/coverage) "
             "into .methodology/bug_hunt_targets.json",
    )
    bht.add_argument("--project", default=".", help="Project root (default: .)")
    bht.set_defaults(func=cmd_bug_hunt_targets)

    # spec-coverage-check (D4 unified — TEST_SPEC.md → tests/, single source of truth)
    scc = sub.add_parser(
        "spec-coverage-check",
        help="D4 unified: compare TEST_SPEC.md items against actual test implementations",
    )
    scc.add_argument("--project", default=".", help="Project root (default: .)")
    scc.add_argument("--threshold", type=float, default=80.0,
                     help="Minimum spec coverage percentage (default: 80.0)")
    scc.add_argument("--fr-id", default=None, dest="fr_id",
                     help="Check only a specific FR (e.g. FR-03)")
    scc.set_defaults(func=cmd_spec_coverage_check)

    # crg-arch-check (CI: non-interactive deterministic CRG architecture gate)
    cac = sub.add_parser(
        "crg-arch-check",
        help="Non-interactive CRG architecture gate (CI): independent score + drift regression",
    )
    cac.add_argument("--project", default=".", help="Project root (default: .)")
    cac.add_argument("--threshold", type=float, default=None,
                     help="Override the architecture floor. Omit it — the "
                          "default is resolved from the project's phase via "
                          "harness/gate_configs/, the only place the number "
                          "lives (Round 38).")
    cac.add_argument("--baseline", default=None,
                     help="Prior crg_baseline_pN.json for drift regression check")
    cac.add_argument("--drift-threshold", type=float, default=0.4,
                     help="Maximum structural drift vs baseline (default: 0.4)")
    cac.set_defaults(func=cmd_crg_arch_check)

    # verify-ci (Round 37: read back what the push produced)
    vci = sub.add_parser(
        "verify-ci",
        help="Read GitHub Actions' verdict for a pushed commit; red blocks, unobtainable is INFRA",
    )
    vci.add_argument("--project", default=".", help="Project root (default: .)")
    vci.add_argument("--sha", default=None,
                     help="Commit to ask about (default: HEAD)")
    vci.add_argument("--wait", type=int, default=0,
                     help="Seconds to wait for CI to report (default: 0 — ask once)")
    vci.set_defaults(func=cmd_verify_ci)

    # verify-gate (Round 38: run the gate's three checks and write the verdict down)
    vg = sub.add_parser(
        "verify-gate",
        help="Run a gate's verification checks and append the verdict (with the "
             "tree digest it was measured on) to .methodology/gate_verify.jsonl",
    )
    vg.add_argument("--project", default=".", help="Project root (default: .)")
    vg.add_argument("--gate", type=int, required=True, help="Gate number (2/3/4)")
    vg.add_argument("--phase", type=int, required=True, help="Phase being exited")
    vg.add_argument("--spec-threshold", type=float, required=True,
                    dest="spec_threshold",
                    help="Minimum spec-coverage percentage for this gate")
    vg.add_argument("--drift-threshold", type=float, default=0.4,
                    dest="drift_threshold",
                    help="Maximum CRG structural drift vs the P4 baseline")
    vg.set_defaults(func=cmd_verify_gate)

    # check-test-spec-consistency (P2: TEST_SPEC.md self-consistency gate)
    ctsc = sub.add_parser(
        "check-test-spec-consistency",
        help="P2: prove TEST_SPEC.md sub-assertions are self-consistent (no unsatisfiable case)",
    )
    ctsc.add_argument("--project", default=".", help="Project root (default: .)")
    ctsc.add_argument("--fr-id", dest="fr_id", default=None, help="Check only this FR (e.g. FR-03)")
    ctsc.set_defaults(func=cmd_check_test_spec_consistency)

    # check-test-mirrors-spec (P3: test faithfully implements TEST_SPEC.md)
    ctms = sub.add_parser(
        "check-test-mirrors-spec",
        help="P3: verify a RED test mirrors TEST_SPEC.md verbatim (run after the test is written)",
    )
    ctms.add_argument("--project", default=".", help="Project root (default: .)")
    ctms.add_argument("--fr-id", dest="fr_id", required=True, help="FR id (e.g. FR-01)")
    ctms.add_argument("--test-file", dest="test_files", nargs="+", required=True, help="Path(s) to the RED test file(s); accepts one or more paths to support per-FR splits like test_fr01_inputs.py + test_fr01_edge.py")
    ctms.set_defaults(func=cmd_check_test_mirrors_spec)

    # check-spec-alignment (P1: canonical_spec ↔ SRS front-edge coverage gate)
    csa = sub.add_parser(
        "check-spec-alignment",
        help="P1: prove SRS.md covers the canonical_spec (no dropped/invented FR); "
             "ingestion-mode only, N/A under elicitation",
    )
    csa.add_argument("--project", default=".", help="Project root (default: .)")
    csa.set_defaults(func=cmd_check_spec_alignment)

    # check-property-spec (Direction B: opt-in property-declaration gate)
    cps = sub.add_parser(
        "check-property-spec",
        help="Verify TEST_SPEC `**Properties**` invariants are self-consistent and "
             "executed by a property-based test (hypothesis/fast-check); opt-in per FR",
    )
    cps.add_argument("--project", default=".", help="Project root (default: .)")
    cps.add_argument("--no-require-execution", action="store_true",
                     dest="no_require_execution",
                     help="Check invariant self-consistency only (pre-P4 usage); do not "
                          "require an executing property test yet")
    cps.set_defaults(func=cmd_check_property_spec)

    # check-artifact-consistency (P2/P3: forward-ref legality + NFR→ADR coverage)
    aci = sub.add_parser(
        "check-artifact-consistency",
        help="P2/P3: catch invented forward-reference filenames (ARCHITECTURE.md vs "
             "SAD.md), module/FR-NFR ownership drift between TRACEABILITY_MATRIX.md "
             "and SPEC_TRACKING.md, and NFRs dropped from ADR.md's traceability table",
    )
    aci.add_argument("--project", default=".", help="Project root (default: .)")
    aci.add_argument("--forward-refs-only", action="store_true",
                     dest="forward_refs_only",
                     help="Check forward references only (skip NFR→ADR coverage; "
                          "useful at P1/P2 when ADR.md does not exist yet)")
    aci.set_defaults(func=cmd_check_artifact_consistency)

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

    # (check-test-inventory removed — deprecated since v2.6, it only
    #  delegated to spec-coverage-check. Use spec-coverage-check directly.)

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

    # run-gap-analysis (M3)
    ga = sub.add_parser(
        "run-gap-analysis",
        help="M3: Detect gaps between SPEC.md and codebase implementation",
    )
    ga.add_argument("--project",    default=".", help="Project root (default: .)")
    ga.add_argument("--spec",       default="SPEC.md", help="Path to SPEC.md")
    ga.add_argument("--similarity", type=float, default=0.6,
                    help="Similarity threshold for matching (default: 0.6)")
    ga.set_defaults(func=cmd_run_gap_analysis)

    # verify-spec
    vs = sub.add_parser(
        "verify-spec",
        help="Verify implementation complies with spec requirements (6-dimension check)",
    )
    vs.add_argument("--project", default=".", help="Project root (default: .)")
    vs.add_argument("--fix", action="store_true",
                    help="Show fix suggestions for each issue (no auto-fix)")
    vs.set_defaults(func=cmd_verify_spec)

    # migrate-trace-overlay (PR 2)
    mto = sub.add_parser(
        "migrate-trace-overlay",
        help="Wrap TRACEABILITY_MATRIX.md in AUTO-GEN sentinels (one-time)",
    )
    mto.add_argument("--project", default=".", help="Target project root path")
    mto.add_argument("--dry-run", action="store_true",
                     help="Print the change without writing files")
    mto.set_defaults(func=cmd_migrate_trace_overlay)

    # build-trace-attestation (PR 3)
    bta = sub.add_parser(
        "build-trace-attestation",
        help="Re-derive matrix and write git-anchored SHA-256 attestation",
    )
    bta.add_argument("--project", required=True)
    bta.add_argument("--overlay", default=None)
    bta.add_argument("--trace-dir", default=".methodology/trace")
    bta.add_argument("--write", action="store_true", default=True,
                     help="Write attestation to .methodology/trace/ (default: True; "
                          "pass --no-write for build-only)")
    bta.add_argument("--no-write", dest="write", action="store_false",
                     help="Build matrix but do NOT write attestation files")
    bta.set_defaults(func=cmd_build_trace_attestation)

    # verify-trace (PR 3)
    vt = sub.add_parser(
        "verify-trace",
        help="Re-derive and verify committed attestation (CI/gate use)",
    )
    vt.add_argument("--project", required=True)
    vt.add_argument("--overlay", default=None)
    vt.add_argument("--gate", type=int, default=None)
    vt.add_argument("--trace-dir", default=".methodology/trace")
    vt.set_defaults(func=cmd_verify_trace)

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
