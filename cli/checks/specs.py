"""check commands: one artifact read against another.

Split out of cli/check_cmds.py in R49-B. Six commands that all answer a
cross-artifact question — does the test suite match the spec, does the SRS
match the SAD, does a property test exist for the property that was declared.

They are grouped by that question, not by phase: the same command is called
from a workflow box, from a pre-push hook and by hand, and which artifact it
compares is the only thing that stays the same across those three.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from core.state_io import load_state
from core.utils.project_layout import ProjectLayout

def cmd_check_test_spec_consistency(args: argparse.Namespace) -> int:
    """P2 self-consistency gate — prove TEST_SPEC.md is not unsatisfiable.

    Correctness is locked in P2: for each declared case the sub-assertion
    predicates are evaluated / length-checked against that case's own concrete
    inputs. A contradiction (e.g. `" " in "ㄏㄢˋ"` False, or 4 one-char chunks
    for a 5-char input) means no implementation could satisfy the spec — FAIL,
    so P3 never implements an unsatisfiable catalog. The engine reads ONLY
    TEST_SPEC.md; it never opens any requirements source (SRS/SAD/SPEC).
    """
    project = Path(args.project).resolve()
    spec_path = ProjectLayout(project).test_spec_path
    if not spec_path.exists():
        print("[check-test-spec-consistency] 02-architecture/TEST_SPEC.md not found — skipping.")
        return 0

    from core.quality_gate.parsers import MalformedTableRowError, SpecAssertionParser
    from core.quality_gate.red_assertion_check import check_test_spec_consistency

    try:
        parsed = SpecAssertionParser.parse(spec_path.read_text(encoding="utf-8"))
    except MalformedTableRowError as exc:
        print(f"[FAIL] TEST_SPEC.md malformed table row: {exc}")
        print("\n[BLOCKED] TEST_SPEC.md self-consistency: table parsing failed — "
              "fix the malformed row above (likely a missing trailing '|') and re-run.")
        return 1
    fr_filter = getattr(args, "fr_id", None)
    if fr_filter:
        parsed = {k: v for k, v in parsed.items() if k == fr_filter}

    if not parsed:
        print("[check-test-spec-consistency] No Inputs + Sub-assertion tables (new "
              "schema) found — nothing to verify"
              + (f" [{fr_filter}]" if fr_filter else "") + ".")
        return 0

    # v2.13.0 (covers FR-05 P3 2026-07-16 lesson): sub-assertion predicate
    # LHS identifier must not shadow a Python stdlib top-level module/builtin.
    # The TDD-RED agent that mirrors a predicate as `json = "true"` shadows
    # `import json` and crashes `json.loads(...)` with AttributeError.
    from core.quality_gate.spec_assertion_naming import (
        scan_stdlib_name_collisions,
    )
    naming_violations = list(scan_stdlib_name_collisions(parsed))
    if naming_violations:
        total_naming = len(naming_violations)
        print(f"\n[FAIL] TEST_SPEC.md has {total_naming} sub-assertion naming "
              "collision(s) (v2.13.0 — stdlib shadow risk):")
        for fr_id, rule_id, predicate, suggested in naming_violations:
            print(f"  • {fr_id} sub-assertion {rule_id!r}: predicate {predicate!r} "
                  f"shadows stdlib; rename LHS to {suggested!r}")
        print("\n[BLOCKED] Fix the predicate(s) above in TEST_SPEC.md before P3 "
              "TDD-RED can run safely (the GREEN step would silently produce "
              "AttributeError when the test file imports the shadowed module).")
        return 1

    total_err = total_review = 0
    for fr_id, (cases, assertions) in sorted(parsed.items()):
        for v in check_test_spec_consistency(cases, assertions):
            if v.severity == "error":
                total_err += 1
                print(f"[FAIL] {fr_id} {v.check_type}: {v.message}")
            elif v.severity == "info":
                total_review += 1
                print(f"[review] {fr_id}: {v.message}")

    if total_err:
        print(f"\n[BLOCKED] TEST_SPEC.md self-consistency: {total_err} contradiction(s) — "
              "P3 must not implement an unsatisfiable spec. Fix TEST_SPEC.md (P2).")
        return 1

    # NFR Layering Hard Rule
    import yaml
    import re
    inventory_path = project / "TEST_INVENTORY.yaml"
    if inventory_path.exists():
        inv = yaml.safe_load(inventory_path.read_text(encoding="utf-8"))
        test_spec_text = spec_path.read_text(encoding="utf-8")
        
        deferred_match = re.search(r"(?i)^#{1,4}\s+[^\n]*Deferred[^\n]*\n(.*?)(?=\n#{1,4}\s+|$)", test_spec_text, re.MULTILINE | re.DOTALL)
        deferred_text = deferred_match.group(1) if deferred_match else ""

        missing_nfrs = []
        tests = inv.get("test_inventory", {}).get("tests", [])
        for tc in tests:
            if tc.get("nfr") and tc.get("layer") in ("unit", "static"):
                fn_name = tc.get("function_name", "")
                if fn_name and not re.search(fr'\b{re.escape(fn_name)}\b', deferred_text):
                    missing_nfrs.append(f"{tc['nfr']} ({fn_name}) - layer: {tc['layer']}")
                    
        if missing_nfrs:
            print("\n[FAIL] TEST_SPEC.md is missing Unit/Static NFRs in the 'Deferred to Downstream Phases' table:")
            for m in missing_nfrs[:5]:
                print(f"  • {m}")
            if len(missing_nfrs) > 5:
                print(f"  ... and {len(missing_nfrs) - 5} more.")
            print("\n[BLOCKED] You MUST isolate all Unit/Static NFRs in a section titled 'Deferred to Downstream Phases'.")
            print("          Do NOT place them in the Integration table. Create the Deferred table if it does not exist.")
            return 1

    print("[check-test-spec-consistency] OK — 0 contradictions"
          + (f"; {total_review} needs-review (P2 Agent B sign-off)" if total_review else "") + ".")
    return 0


def cmd_check_spec_alignment(args: argparse.Namespace) -> int:
    """Front-edge gate — prove SRS.md faithfully covers the canonical_spec (PRD).

    The one boundary nothing else machine-checks: canonical_spec → SRS. In
    INGESTION MODE (PROJECT_BRIEF.md declares canonical_spec) every canonical FR
    must appear in SRS.md and every SRS FR must trace back — a dropped or
    invented requirement FAILS, before P2 builds the wrong target. Elicitation
    mode (no canonical_spec) has no ground truth and is reported N/A. This
    mechanically enforces the ingestion prompt rule R-CANONICAL-INTERP-001 that
    today only Agent A/B (LLM) uphold. Distinct from check-test-spec-consistency
    (TEST_SPEC self-consistency) and preflight_fr_spec_consistency (SAD↔SPEC).
    """
    project = Path(args.project).resolve()
    from core.quality_gate.spec_alignment import (
        check_spec_alignment,
        resolve_canonical_spec,
    )

    if resolve_canonical_spec(project) is None:
        print("[check-spec-alignment] Elicitation mode (no canonical_spec declared) — N/A.")
        return 0

    violations = check_spec_alignment(project)
    errors = [v for v in violations if v.severity == "error"]
    reviews = [v for v in violations if v.severity == "info"]
    for v in errors:
        print(f"[FAIL] {v.rule_id} {v.check_type}: {v.message}")
    for v in reviews:
        print(f"[review] {v.rule_id}: {v.message}")
    if errors:
        print(f"\n[BLOCKED] canonical_spec ↔ SRS: {len(errors)} divergence(s) — "
              "fix SRS.md (P1) before P2. A dropped/invented requirement means the "
              "build target no longer matches the PRD.")
        return 1
    print("[check-spec-alignment] OK — SRS.md covers canonical_spec"
          + (f"; {len(reviews)} needs-review (Agent B sign-off)" if reviews else "") + ".")
    return 0


def cmd_check_property_spec(args: argparse.Namespace) -> int:
    """Lightweight property-declaration gate (Direction B).

    Opt-in: only FRs that declare a `**Properties**` table in TEST_SPEC.md are
    checked. Declared invariants are self-consistency-checked by the reused
    red_assertion engine (a false invariant is a spec contradiction), and —
    unless --no-require-execution — each property-declaring FR must have a
    property-based test (hypothesis @given / fast-check) actually executing it.
    Property *strength* is backed by the existing mutation_testing dimension,
    not re-scored here. No new gate dimension, no per-FR mutation.
    """
    project = Path(args.project).resolve()
    from core.quality_gate.property_check import check_property_spec

    require = not getattr(args, "no_require_execution", False)
    violations = check_property_spec(project, require_execution=require)
    errors = [v for v in violations if v.severity == "error"]
    reviews = [v for v in violations if v.severity == "info"]
    for v in errors:
        print(f"[FAIL] {v.rule_id} {v.check_type}: {v.message}")
    for v in reviews:
        print(f"[review] {v.rule_id}: {v.message}")
    if errors:
        print(f"\n[BLOCKED] property declarations: {len(errors)} issue(s) — a declared "
              "invariant that is false for its case, or is never executed by a property "
              "test, verifies nothing. Fix TEST_SPEC.md / add the hypothesis test.")
        return 1
    print("[check-property-spec] OK — declared property invariants consistent"
          + (" and executed" if require else "")
          + (f"; {len(reviews)} needs-review" if reviews else "") + ".")
    return 0


def cmd_check_artifact_consistency(args: argparse.Namespace) -> int:
    """P2/P3 gate — machine-catch P1/P2 artifact hallucinations (audit fix).

    check_forward_refs: a `NN-stage/FILE.md` reference must name a real framework
    deliverable (catches 02-architecture/ARCHITECTURE.md when the P2 deliverable
    is SAD.md). check_nfr_adr_coverage: every SRS NFR must appear in ADR.md's
    traceability TABLE (catches an NFR dropped from the table). check_module_fr_coverage:
    TRACEABILITY_MATRIX.md's own §5.3 reverse-coverage table must match its own
    AC-row citations, and SPEC_TRACKING.md must not claim an FR/NFR ownership the
    AC citations attribute to a different module. check_security_design: SAD.md
    §6's STRIDE-lite threat model (Round 10) — structural rules from P3, test-
    existence rule from P5; a bare invocation with no readable current_phase
    runs the structural rules only (same "no phase context" convention as
    forward_refs/module_fr_coverage). All decidable, no LLM. NFR coverage is
    only meaningful once ADR.md exists (P3+).
    """
    project = Path(args.project).resolve()
    from core.quality_gate.artifact_consistency import (
        check_forward_refs,
        check_module_fr_coverage,
        check_nfr_adr_coverage,
    )
    from core.quality_gate.security_design import check_security_design
    from core.quality_gate.srs_structure import check_srs_structure

    phase_val = load_state(project, lenient=True).get("current_phase")
    current_phase = phase_val if isinstance(phase_val, int) else None

    violations = (check_forward_refs(project)
                  # `--forward-refs-only` is a cheap pre-push fast-fail for
                  # invented filenames (Round 10's audit fix). Semantically
                  # only `check_forward_refs` belongs there; the other four
                  # checks (module_fr_coverage / nfr_adr_coverage /
                  # security_design / srs_structure) are cross-artifact
                  # consistency / structural checks that have their own
                  # callers and gates, and bundling them into a fast-fail
                  # route surfaces the wrong failure class to the workflow's
                  # P1 Forward Ref Check step (which then mis-reports e.g.
                  # an SRS-FR-BLOCK missing as "FWDREF: FAIL — invented
                  # filename ARCHITECTURE.md"). Keep the default (full)
                  # mode unchanged so all five still run; only the
                  # `--forward-refs-only` route narrows to check_forward_refs.
                  + ([] if getattr(args, 'forward_refs_only', False)
                     else (check_module_fr_coverage(project)
                           + check_nfr_adr_coverage(project)
                           + check_security_design(project, phase=current_phase)
                           # Round 42 站3: the SRS's machine-readable FR Block.
                           # The reason `check_security_design` keeps its phase
                           # rules inside itself is that two callers is two
                           # chances to disagree.
                           + check_srs_structure(project))))
    errors = [v for v in violations if v.severity == "error"]
    reviews = [v for v in violations if v.severity == "info"]
    for v in errors:
        print(f"[FAIL] {v.rule_id} {v.check_type}: {v.message}")
    for v in reviews:
        print(f"[review] {v.rule_id}: {v.message}")
    if errors:
        print(f"\n[BLOCKED] artifact consistency: {len(errors)} issue(s) — an invented "
              "filename (404s downstream automation) or an NFR missing from ADR's "
              "traceability table. Fix the P1/P2 artifact.")
        return 1
    print("[check-artifact-consistency] OK"
          + (f"; {len(reviews)} needs-review" if reviews else "") + ".")
    return 0


def cmd_check_test_mirrors_spec(args: argparse.Namespace) -> int:
    """P3 mirror gate — verify a RED test faithfully implements TEST_SPEC.md.

    Run AFTER the RED test is written (not before). Structure-only: no
    satisfiability, no eval of test logic. Correctness was locked in P2; this
    proves the test mirrors it. Divergence (a sub-assertion applied to a
    different case set, or a declared assertion missing) -> FAIL, so the fix is
    the test, never TEST_SPEC. Reads only TEST_SPEC.md and the test file.
    """
    project = Path(args.project).resolve()
    spec_path = ProjectLayout(project).test_spec_path
    fr_id = args.fr_id
    # Bug #26 fix: --test-file accepts nargs="+", so args.test_files is a list.
    # Iterate each file; aggregate violations across all files. The command
    # fails (exit 1) if any one file has an error-severity violation.
    test_files = [Path(f).resolve() for f in args.test_files]

    if not spec_path.exists():
        print("[check-test-mirrors-spec] 02-architecture/TEST_SPEC.md not found — skipping.")
        return 0

    from core.quality_gate.parsers import MalformedTableRowError, SpecAssertionParser
    from core.quality_gate.red_assertion_check import (
        check_test_mirrors_spec,
        check_test_mirrors_spec_js,
    )

    try:
        parsed = SpecAssertionParser.parse(spec_path.read_text(encoding="utf-8"))
    except MalformedTableRowError as exc:
        print(f"[FAIL] TEST_SPEC.md malformed table row: {exc}")
        print("\n[BLOCKED] check-test-mirrors-spec: table parsing failed — "
              "fix the malformed row above (likely a missing trailing '|') and re-run.")
        return 1
    if fr_id not in parsed:
        print(f"[check-test-mirrors-spec] {fr_id} has no Inputs+Sub-assertion tables "
              "in TEST_SPEC.md — nothing to mirror.")
        return 0

    cases, assertions = parsed[fr_id]
    all_errs = []
    all_reviews = []
    for test_file in test_files:
        if not test_file.exists():
            print(f"[check-test-mirrors-spec] test file not found: {test_file} — skipping.")
            continue
        test_source = test_file.read_text(encoding="utf-8")
        suffix = test_file.suffix.lower()
        if suffix in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
            dialect = {"ts": "typescript", "tsx": "tsx"}.get(suffix.lstrip("."), "javascript")
            violations = check_test_mirrors_spec_js(test_source, cases, assertions, dialect)
        else:
            violations = check_test_mirrors_spec(test_source, cases, assertions, fr_id=fr_id)
        errs = [v for v in violations if v.severity == "error"]
        reviews = [v for v in violations if v.severity == "info"]
        # Round 12 站3a+3c: spec_unsatisfiable ships as a WARNING (the spec
        # itself demands the impossible — R5 incident class; blocking would
        # deadlock the pipeline against a constraint no test can meet).
        # Operators may graduate it to "block" via
        # values.checker_enforcement = {"spec_unsatisfiable": "block"}
        # after an E2E run shows zero false kills.
        unsat = [v for v in violations if v.check_type == "spec_unsatisfiable"]
        if unsat:
            from core.harness_config import get_checker_enforcement
            _level = get_checker_enforcement(project, "spec_unsatisfiable")
            for v in unsat:
                print(f"[{'FAIL' if _level == 'block' else 'UNSATISFIABLE'}] "
                      f"{fr_id} ({test_file.name}): {v.message}")
            if _level == "block":
                all_errs.extend(unsat)
        for v in errs:
            print(f"[FAIL] {fr_id} ({test_file.name}) {v.check_type}: {v.message}")
        for v in reviews:
            print(f"[review] {fr_id} ({test_file.name}): {v.message}")
        all_errs.extend(errs)
        all_reviews.extend(reviews)
    if all_errs:
        print(f"\n[BLOCKED] {len(test_files)} test file(s) checked; {len(all_errs)} divergence(s) from "
              f"TEST_SPEC.md. P3 implements the spec verbatim — fix the test, not TEST_SPEC.")
        return 1
    print(f"[check-test-mirrors-spec] OK — {len(test_files)} test file(s) mirror {fr_id} in TEST_SPEC.md."
          + (f" {len(all_reviews)} item(s) need P3 reviewer sign-off." if all_reviews else ""))
    return 0


def cmd_verify_spec(args: argparse.Namespace) -> int:
    """Verify implementation complies with spec requirements (6-dimension check)."""
    from scripts.verify_spec_compliance import SpecComplianceChecker

    project = str(Path(args.project).resolve())
    print(f"\n{'='*60}\nverify-spec  project={project}\n{'='*60}")

    checker = SpecComplianceChecker(project)
    result = checker.check_all()

    print(f"\n{'─'*60}")
    print("Spec Compliance Report")
    print(f"{'─'*60}")
    print(f"  Score : {result['score']}")

    if result["passed"]:
        print("\n  PASSED:")
        for p in result["passed"]:
            print(f"    + {p}")

    if result["issues"]:
        print("\n  ISSUES:")
        for issue in result["issues"]:
            print(f"    - {issue}")
        if getattr(args, "fix", False):
            print("\n  FIX SUGGESTIONS:")
            for hint in checker.suggest_fixes(result["issues"]):
                print(f"    → {hint}")
            print("\n  [INFO] --fix shows suggestions only. Apply fixes manually.")

    return 0 if not result["issues"] else 1
