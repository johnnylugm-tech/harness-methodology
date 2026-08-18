"""check commands: the gate's own verdict, and the two checks it calls.

Split out of cli/check_cmds.py in R49-B. `cmd_verify_gate` calls
`cmd_crg_arch_check` and `cmd_spec_coverage_check` directly, so the three
travel together — a family boundary drawn between a caller and its callee is
a boundary that will be crossed on every change.

`cmd_verify_ci` joins them because it answers the same shape of question about
a different judge: Round 37 put CI's verdict inside the framework's field of
view, and this is the command that reads it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.quality_gate.spec_coverage import _run_spec_coverage_check
from core.state_io import load_state

def cmd_spec_coverage_check(args: argparse.Namespace) -> int:
    """Spec Coverage Check — compare TEST_SPEC.md items against actual test files.

    Validates that every named test case declared in the P2 TEST_SPEC.md artifact
    has been implemented as a real test function in tests/.
    """
    project = Path(args.project).resolve()
    threshold = getattr(args, "threshold", 80.0)
    fr_id = getattr(args, "fr_id", None)
    code, _ = _run_spec_coverage_check(project, threshold, fr_id=fr_id, verbose=True)
    return code


def cmd_verify_gate(args: argparse.Namespace) -> int:
    """Run a gate's three verification checks and write the verdict down.

    Round 38 站4. The workflow used to ask an agent for three exit codes
    (`last_gate_ok`, `d4_rc`, `crg_rc`), AND them together, and keep nothing.
    A full-text search of taskq-renew's `.methodology/` for `crg_rc` after a
    complete P1-P8 run returns zero hits — so when its P6 recorded a baseline
    of 77.8 against a floor of 80 while gate4-verify passed on round one,
    nothing survived that could say which of the two was wrong.

    The three checks are unchanged; what changes is who runs them and what is
    left behind. The workflow now transcribes one number instead of three, and
    that number has a ledger entry behind it carrying the tree it was measured
    on — which `advance-phase` then re-derives before letting the phase through.
    """
    project = Path(args.project).resolve()
    gate = int(args.gate)
    phase = int(args.phase)

    _lg = load_state(project, lenient=True).get("last_gate")
    last_gate_ok = isinstance(_lg, int) and _lg >= gate
    print(f"[verify-gate] last_gate={_lg!r} (need >= {gate}): "
          f"{'OK' if last_gate_ok else 'NOT YET'}")

    spec_rc = cmd_spec_coverage_check(argparse.Namespace(
        project=str(project), threshold=args.spec_threshold, fr_id=None))
    print(f"[verify-gate] spec-coverage-check rc={spec_rc}")

    _baseline = project / ".methodology" / "crg_baseline_p4.json"
    crg_rc = cmd_crg_arch_check(argparse.Namespace(
        project=str(project), threshold=None,
        baseline=str(_baseline) if _baseline.is_file() else None,
        drift_threshold=args.drift_threshold))
    print(f"[verify-gate] crg-arch-check rc={crg_rc}")

    checks = {"last_gate_ok": last_gate_ok,
              "spec_coverage_rc": spec_rc, "crg_rc": crg_rc}
    ok = last_gate_ok and spec_rc == 0 and crg_rc == 0

    from cli.exit_codes import EX_GATE_VERIFY_FAILED, EX_OK
    from core.quality_gate.gate_verify import FAIL, PASS, record_verdict
    record = record_verdict(project, gate=gate, phase=phase, checks=checks,
                            verdict=PASS if ok else FAIL)
    print(f"[verify-gate] recorded {record['verdict']} for gate {gate} "
          f"on tree {record['delivered_tree_sha256'][:12]} "
          f"→ .methodology/gate_verify.jsonl")
    if ok:
        return EX_OK
    print(f"[BLOCKED] Gate {gate} verification failed: {checks}")
    print("  Fix the failing check above, then re-run this command. "
          "advance-phase will not accept a phase whose exit gate has no "
          "matching PASS for the current tree.")
    return EX_GATE_VERIFY_FAILED


def cmd_verify_ci(args: argparse.Namespace) -> int:
    """Read back what the push produced, and refuse to call red green.

    Round 37. taskq-renew pushed 52 times, 48 of them onto a red build, and
    nothing in the framework ever asked GitHub what happened — the pipeline
    kept declaring PASS through Phase 9. `push succeeded` and `the build is
    green` are two propositions; this enforces the second.

    An unobtainable verdict exits EX_CI_VERDICT_UNAVAILABLE, never 0: no gh,
    no network, or a run that has not appeared yet is INFRA, not a pass.
    """
    from core.ci_verdict import await_ci_verdict, render_block_message
    from cli.exit_codes import EX_CI_RED, EX_CI_VERDICT_UNAVAILABLE, EX_OK

    from cli._shared import head_sha

    project = Path(args.project).resolve()
    sha = getattr(args, "sha", None) or head_sha(project)
    verdict = await_ci_verdict(
        project, sha,
        wait_seconds=int(getattr(args, "wait", 0) or 0),
        runner=getattr(args, "runner", None),
    )

    if verdict.status == "green":
        print(f"[verify-ci] OK: {verdict.detail}")
        return EX_OK
    if verdict.status == "red":
        for line in render_block_message(verdict, sha):
            print(line)
        return EX_CI_RED
    print(f"[verify-ci] INFRA_BLOCKED: {verdict.detail}")
    print("  A verdict that could not be obtained is not a green verdict.")
    return EX_CI_VERDICT_UNAVAILABLE


def cmd_crg_arch_check(args: argparse.Namespace) -> int:
    """Non-interactive CRG architecture gate for CI (deterministic, no LLM).

    Builds/refreshes the graph and computes the architecture score via
    crg_independent — the same gate-blocking score used at finalize_gate, but
    runnable in CI because it needs no interactive session. Hard-fails (exit 1)
    when the score drops below --threshold, or (with --baseline) when structural
    drift vs that baseline reaches --drift-threshold. This closes the audit gap
    where CRG never ran in CI (architecture scoring was local-only).
    """
    project = Path(args.project).resolve()
    # Round 60 站2: the `crg_architecture` flag used to turn this absolute
    # floor into an unconditional pass. The flag is retired — CI's
    # architecture floor applies to every project, and a CRG that cannot
    # analyse the tree is an INFRA block with a repair route (Round 44 站3's
    # graph-coverage refusal), not a score nobody took.
    work_dir = project / ".sessi-work"
    try:
        from harness.crg_independent import run_independent_crg
        metrics = run_independent_crg(str(project), str(work_dir))
    except Exception as exc:  # CrgIndependentError / import → CRG is mandatory, block
        print(f"[crg-arch-check] BLOCKED: CRG architecture score unavailable: {exc}")
        return 1

    arch = metrics.get("architecture_score")
    if arch is None:
        arch = (metrics.get("community_cohesion") or {}).get("score") or 0.0
    # Round 38: the floor comes from the gate config that scores against it,
    # resolved through the project's phase. `--threshold` is an explicit
    # override for ad-hoc probing only — no caller passes it, so there is one
    # number and one place it lives.
    threshold = getattr(args, "threshold", None)
    if threshold is None:
        from core.quality_gate.crg_baseline import floor_for_phase
        _pv = load_state(project, lenient=True).get("current_phase")
        _phase = _pv if isinstance(_pv, int) else None
        threshold = floor_for_phase(_phase)
        _src = f"phase {_phase}" if _phase is not None else "phase unreadable"
        print(f"[crg-arch-check] floor {threshold:.0f} from gate config ({_src})")
    print(f"[crg-arch-check] architecture_score={arch:.1f} (threshold {threshold:.0f})")
    if arch < threshold:
        print(f"[crg-arch-check] FAIL: architecture {arch:.1f} < {threshold:.0f}")
        return 1

    baseline = getattr(args, "baseline", None)
    if baseline:
        bp = Path(baseline)
        if bp.is_file():
            try:
                from harness.ssi.scripts.crg_analysis import compute_structural_drift
                _bl = json.loads(bp.read_text(encoding="utf-8"))
                drift = compute_structural_drift(_bl, metrics)
                dthr = getattr(args, "drift_threshold", 0.4)
                print(f"[crg-arch-check] drift vs {bp.name}: {drift:.2f} (threshold {dthr:.2f})")
                if drift >= dthr:
                    print(f"[crg-arch-check] FAIL: architecture regression drift {drift:.2f} >= {dthr:.2f}")
                    return 1
            except Exception as exc:
                print(f"[crg-arch-check] WARN: drift check skipped — {exc}")
        else:
            print(f"[crg-arch-check] INFO: baseline {bp} not found — drift check skipped")
    print("[crg-arch-check] OK")
    return 0


def register(sub) -> None:
    """Wire the gate/CI subcommands onto the main subparser action.

    R49-B 站3: a command's flags now live beside its body, so adding one
    touches this file and nothing else. Moved verbatim out of
    cli/check_cmds.py's 295-line register().
    """
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
