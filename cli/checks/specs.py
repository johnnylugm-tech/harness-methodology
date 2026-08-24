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
import re
from pathlib import Path

from core.state_io import load_state
from core.utils.project_layout import ProjectLayout

# ── NFR Layering Hard Rule (Round 73 站6) ──────────────────────────────────
#
# The rule requires every unit/static NFR test named in TEST_INVENTORY.yaml to
# appear in TEST_SPEC.md's "Deferred to Downstream Phases" section. It had two
# defects pointing opposite ways, and each hid the other:
#
#   1. the population was always empty — it read `tc["function_name"]`, and
#      `templates/TEST_INVENTORY.yaml` defines no such field. It defines no
#      `test_inventory:` key at all: that key is mandated by
#      `scripts/workflowgen/spec_phase1.py:735` so the LOADER can identify a
#      YAML artifact that has no H1, and nothing ever said what goes under it.
#      Seven projects wrote four spellings — `test_function` (renew, advance,
#      cc), `test_function_name` (api), `test_name` (super, new, run-all) —
#      and the old predicate selected 0 entries in every one of them.
#
#   2. the haystack was always empty — the section capture ran with
#      `re.MULTILINE | re.DOTALL`, where `$` matches at every line end, so the
#      lookahead was satisfied at the first newline and the group captured "".
#      Measured on the three projects that have the section: 0 characters,
#      with 17 / 0 / 11 `test_nfr` occurrences left outside it.
#
# Fixing (1) alone would have reported every unit/static NFR test in three
# delivered projects as missing from a section it is sitting in.
#
# The field name is not guessed from a list of spellings — an exception list
# with nothing reconciling it is the defect shape of this round's station 3.
# It is found the way station 1 finds a test name in a TEST_SPEC row: by what
# a value IS. Exactly one value in a declaration entry is an identifier
# beginning `test_`.
_TEST_IDENT = re.compile(r"^test_[A-Za-z0-9_]+$")
_NFR_ID = re.compile(r"^NFR-\d+$")

# Fields that can carry the entry's SUBJECT. `cross_ref_nfrs` deliberately is
# not one: reading any NFR-shaped token in the row turns 63 FR tests across
# five projects into NFR tests — `test_fr01_pydantic_validation_422` among
# them — and the rule would demand each be moved into a section for NFR tests.
_SUBJECT_FIELDS = ("nfr", "nfr_id", "fr", "fr_id", "requirement", "req")

_DEFERRED_LAYERS = ("unit", "static")


def deferred_section_text(test_spec_text: str) -> str:
    """The body of TEST_SPEC.md's "Deferred to Downstream Phases" section."""
    lines = test_spec_text.splitlines()
    out: list[str] = []
    depth = 0
    for line in lines:
        heading = re.match(r"^(#{1,6})\s+(\S.*)$", line)
        if heading:
            level = len(heading.group(1))
            if depth and level <= depth:
                break          # a sibling or higher heading closes the section
            if not depth and re.search(r"deferred", heading.group(2), re.IGNORECASE):
                depth = level  # sub-headings below this one stay inside
                continue
        if depth:
            out.append(line)
    return "\n".join(out)


def _entry_test_fn(entry: dict) -> str:
    """The one identifier-shaped value in an inventory entry, or ""."""
    found = [v for v in entry.values()
             if isinstance(v, str) and _TEST_IDENT.match(v.strip())]
    return found[0].strip() if len(found) == 1 else ""


def _entry_subject_nfr(entry: dict) -> str:
    """The NFR this entry is ABOUT — a singular field whose whole value is an id."""
    for key in _SUBJECT_FIELDS:
        value = entry.get(key)
        if isinstance(value, str) and _NFR_ID.match(value.strip()):
            return value.strip()
    return ""


def nfr_layering_targets(project: "str | Path") -> list[dict]:
    """The unit/static NFR tests this project's inventory declares.

    ``[{"tc_id", "nfr", "layer", "test_fn"}, …]``; `test_fn` is "" when no
    value in the entry is identifier-shaped, which the caller reports rather
    than dropping — silently shrinking the population is what `function_name`
    did for as long as it existed.
    """
    import yaml

    inventory_path = Path(project) / "TEST_INVENTORY.yaml"
    if not inventory_path.is_file():
        return []
    try:
        inv = yaml.safe_load(inventory_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f"[WARN] TEST_INVENTORY.yaml unreadable ({exc}) — the NFR "
              f"Layering Hard Rule is NOT being checked this run")
        return []
    section = inv.get("test_inventory") if isinstance(inv, dict) else None
    entries = (section or {}).get("tests") if isinstance(section, dict) else None
    if not isinstance(entries, list):
        return []

    targets: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        nfr = _entry_subject_nfr(entry)
        if not nfr or str(entry.get("layer", "")).lower() not in _DEFERRED_LAYERS:
            continue
        targets.append({
            "tc_id": str(entry.get("tc_id") or "(no tc_id)"),
            "nfr": nfr,
            "layer": str(entry.get("layer")),
            "test_fn": _entry_test_fn(entry),
        })
    return targets


def nfr_layering_violations(project: "str | Path") -> list[str]:
    """Declared unit/static NFR tests not found in the Deferred section.

    Measured across the corpus with both defects fixed: taskq-api 17 targets,
    taskq-advance 6, run-all-by-workflow 11, every one present — the rule
    executes and blocks nobody. taskq-renew, taskq-super, taskq-cc and
    taskq-new declare no unit/static NFR entry at all.
    """
    project = Path(project)
    spec_path = ProjectLayout(project).test_spec_path
    if not spec_path.is_file():
        return []
    deferred = deferred_section_text(
        spec_path.read_text(encoding="utf-8", errors="replace"))

    violations: list[str] = []
    for target in nfr_layering_targets(project):
        if not target["test_fn"]:
            violations.append(
                f"{target['nfr']} ({target['tc_id']}) - layer: "
                f"{target['layer']} — the entry carries no test function name, "
                f"so nothing here can check where it lives")
            continue
        if not re.search(rf"\b{re.escape(target['test_fn'])}\b", deferred):
            violations.append(
                f"{target['nfr']} ({target['test_fn']}) - layer: {target['layer']}")
    return violations


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
    missing_nfrs = nfr_layering_violations(project)
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
        check_ac_identifiers,
        check_ac_test_spec_coverage,
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
                           # Round 62: AC checks (Round 51) wired into CLI.
                           # Gated on phase>=3 to mirror phase_hooks.py:1164-1167 —
                           # TEST_SPEC.md is produced in Phase 2 but the population
                           # is only meaningful at P3+; at earlier phases both
                           # checks short-circuit to no-op on empty population.
                           + (check_ac_identifiers(project)
                              + check_ac_test_spec_coverage(project)
                              if current_phase is not None and current_phase >= 3
                              else [])
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


def register(sub) -> None:
    """Wire the cross-artifact check subcommands onto the main subparser action.

    R49-B 站3: a command's flags now live beside its body, so adding one
    touches this file and nothing else. Moved verbatim out of
    cli/check_cmds.py's 295-line register().
    """
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

    # verify-spec
    vs = sub.add_parser(
        "verify-spec",
        help="Verify implementation complies with spec requirements (6-dimension check)",
    )
    vs.add_argument("--project", default=".", help="Project root (default: .)")
    vs.add_argument("--fix", action="store_true",
                    help="Show fix suggestions for each issue (no auto-fix)")
    vs.set_defaults(func=cmd_verify_spec)
