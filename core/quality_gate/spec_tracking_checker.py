#!/usr/bin/env python3
"""
Specification Tracking Checker
Check whether SPEC_TRACKING.md exists and is complete

Usage:
    from quality_gate.spec_tracking_checker import SpecTrackingChecker
    checker = SpecTrackingChecker("/path/to/project")
    result = checker.run()
"""

import sys
from typing import Dict, List
from pathlib import Path

from core.quality_gate.parsers import SpecTrackingParser


class SpecTrackingChecker:
    """Specification tracking completeness checker"""
    
    def __init__(self, project_root: str):
        self.project_root = Path(project_root)
        # Support multiple possible locations
        from core.utils.project_layout import ProjectLayout
        self.spec_file_candidates = [
            ProjectLayout(self.project_root).spec_tracking_path,
        ]
        self.template_file = Path(__file__).parent.parent / "templates" / "SPEC_TRACKING.md"
        self.spec_file = None
        for candidate in self.spec_file_candidates:
            if candidate.exists():
                self.spec_file = candidate
                break
        if self.spec_file is None:
            self.spec_file = self.spec_file_candidates[0]  # default to first
    
    def check_exists(self) -> bool:
        """Check whether SPEC_TRACKING.md exists"""
        return any(c.exists() for c in self.spec_file_candidates)
    
    def check_completeness(self) -> Dict:
        """Check specification tracking completeness"""
        if not self.check_exists():
            return {
                "complete": False,
                "missing": ["SPEC_TRACKING.md not found"],
                "errors": []
            }
        
        if not self.spec_file or not self.spec_file.exists():
            return {"complete": False, "missing": ["File not found"]}
        content = self.spec_file.read_text(encoding="utf-8")
        missing = []
        errors: list[str] = []
        
        # Check if FR tracking table exists (several heading variants are accepted)
        _table_names = ("Core Features", "Specification Status", "FR ID", "FR Tracking")
        if not any(self._has_table(content, name) for name in _table_names):
            missing.append("Core Features table")
        
        # Check if status column exists
        if "Status" not in content:
            missing.append("Status column")
        
        # Check if update log exists
        if not self._has_update_log(content):
            missing.append("Update log")
        
        # Check all entries have status
        entries_without_status = self._find_entries_without_status(content)
        if entries_without_status:
            for entry in entries_without_status:
                missing.append(f"Entry missing status: {entry}")
        
        return {
            "complete": len(missing) == 0,
            "missing": missing,
            "errors": errors
        }
    
    # ------------------------------------------------------------------
    # Parsing — delegated to SpecTrackingParser (crg-003)
    # ------------------------------------------------------------------

    def _has_table(self, content: str, table_name: str) -> bool:
        return SpecTrackingParser.has_table(content, table_name)

    def _has_update_log(self, content: str) -> bool:
        return SpecTrackingParser.has_update_log(content)

    def _find_entries_without_status(self, content: str) -> List[str]:
        return SpecTrackingParser.find_entries_without_status(content)
    
    def run(self) -> bool:
        """Run specification tracking check (backward-compatible, returns bool)"""
        if not self.check_exists():
            return False
        return self.check_completeness()["complete"]
    
    def run_enforcement(self) -> Dict:
        """
        Run specification tracking check (for Enforcement integration)
        
        Returns:
            Dict with keys:
                - exists: bool
                - completeness: int (0-100)
                - complete: bool
                - missing: List[str]
                - errors: List[str]
        """
        exists = self.check_exists()
        if not exists:
            return {
                "exists": False,
                "completeness": 0,
                "complete": False,
                "missing": ["SPEC_TRACKING.md not found"],
                "errors": []
            }
        
        completeness_result = self.check_completeness()
        if not self.spec_file or not self.spec_file.exists():
            return {"complete": False, "missing": ["File not found"]}
        content = self.spec_file.read_text(encoding="utf-8")
        stats = self._count_status(content)

        # completeness = fraction of entries that have *any* recognised status
        # (including DRAFT / In Progress / Not Started).
        # Note: find_entries_without_status() is unreliable for standard
        # Markdown tables (pre-existing known limitation); untracked is
        # retained as a term for future improvement but is effectively 0.
        all_tracked = sum(stats.values())
        untracked = len(self._find_entries_without_status(content))
        total_entries = all_tracked + untracked

        # Calculate completeness percentage
        completeness_pct = int((all_tracked / max(total_entries, 1)) * 100) if total_entries > 0 else 0
        
        return {
            "exists": True,
            "completeness": completeness_pct,
            "complete": completeness_result["complete"],
            "missing": completeness_result["missing"],
            "errors": completeness_result["errors"],
            "stats": stats
        }
    
    def print_report(self):
        """Print specification tracking report"""
        if not self.check_exists():
            print("❌ SPEC_TRACKING.md not found")
            print("   Run spec-track init (parent-system CLI) to initialize")
            return
        
        completeness = self.check_completeness()
        
        print("=" * 50)
        print("Specification Tracking Report")
        print("=" * 50)
        
        if completeness["complete"]:
            print("✅ Specification tracking complete")
        else:
            print("❌ Specification tracking incomplete")
        
        if completeness["missing"]:
            print("\nMissing items:")
            for item in completeness["missing"]:
                print(f"  • {item}")
        
        # Read and display status statistics
        if not self.spec_file or not self.spec_file.exists():
            return
        content = self.spec_file.read_text(encoding="utf-8")
        stats = self._count_status(content)
        if stats:
            print("\nStatus statistics:")
            for status, count in stats.items():
                print(f"  {status}: {count}")
    
    def _count_status(self, content: str) -> Dict[str, int]:
        return SpecTrackingParser.count_status(content)


# ---------------------------------------------------------------------------
# PR 4: gate-dimension trace score
# Fuses 4a (FR → code → test) with 4b (TEST_SPEC → test) and 4c (NFR →
# test) into a single `traceability` dimension. 4a is 100% at G2/G3/G4
# over FRs with status ∈ {IN_PROGRESS, VERIFIED} (PENDING excluded from
# denominator). 4b and 4c share the 60/80/90% threshold ladder at
# G2/G3/G4. NFR-99 (placeholder for deferred/ambiguity markers per
# phase1_plan.md R-CANONICAL-INTERP-001) is excluded from the 4c
# denominator. Merged score = min(4a, 4b, 4c). Fail-closed.
# ---------------------------------------------------------------------------

TRACE_THRESHOLDS = {2: 100, 3: 100, 4: 100}  # 4a: 100% at G2/G3/G4
SPEC_COV_THRESHOLDS = {2: 60.0, 3: 80.0, 4: 90.0}  # 4b: unchanged

ACTIVE_STATUSES = {"in_progress", "verified"}


def _filter_active_frs(rt, missing: dict) -> tuple[set, set]:
    """Return (active_uncoded, active_untested) — sets of FR-IDs in the denominator.

    `missing` is the `missing_mappings` dict from `verify_completeness` (or
    the raw report). An FR is in the denominator iff its status in
    `rt.requirements` is IN_PROGRESS or VERIFIED.

    `verify_completeness` uses `total = len(self.requirements)` (all FRs);
    we must filter PENDING/UNIMPLEMENTED ourselves before computing the %.
    """
    active_uncoded = set()
    active_untested = set()
    for fr_id in missing.get("fr_without_code", []):
        req = rt.requirements.get(fr_id)
        if req is not None and req.status.value in ACTIVE_STATUSES:
            active_uncoded.add(fr_id)
    for fr_id in missing.get("fr_without_test", []):
        req = rt.requirements.get(fr_id)
        if req is not None and req.status.value in ACTIVE_STATUSES:
            active_untested.add(fr_id)
    return active_uncoded, active_untested


def resolve_threshold_effective(
    *,
    pct_4a: float, pct_4b: float, pct_4c: float,
    threshold_4a: float, threshold_4b: float, threshold_4c: float,
) -> float:
    """The threshold to compare `merged_pct` against, so that
    ``merged_pct >= threshold_effective`` is exactly ``passed``.

    `merged_pct` is a min() over three components with DIFFERENT thresholds
    (4a=100%, 4b/4c=60/80/90% per gate), but consumers persist it as one score
    and compare it against one threshold — the generic shape every other
    dimension uses (cli/gate_cmds.py's gate result patch,
    harness_bridge._override_traceability_dim_score). Getting this number wrong
    makes those consumers contradict `passed`, which is what blocked taskq's
    Gate 2 for 3 rounds when the flat 4a threshold of 100 was used (7c60859).

    That fix used "the threshold of whichever component binds the min". Round
    19 站3 found it still breaks whenever the FAILING component is not the
    binding one:

        4a=95 (bar 100, FAILS) | 4b=60 (bar 60, passes, and is the min) | 4c=90
        -> binds on 4b, so effective=60, and 60 >= 60 reports PASS
        -> passed is False

    The rule that holds in general: if anything fails, compare against the
    HIGHEST failing bar. Then merged (being the min, hence <= the failing
    component's percentage, hence < its bar) is necessarily below it. If
    nothing fails, keep the binding component's threshold — merged is that
    component's value and clears it, and the printed number stays meaningful to
    a human reading the gate line.
    """
    failing = [
        threshold
        for pct, threshold in (
            (pct_4a, threshold_4a), (pct_4b, threshold_4b), (pct_4c, threshold_4c),
        )
        if pct < threshold
    ]
    if failing:
        return max(failing)
    merged = min(pct_4a, pct_4b, pct_4c)
    if merged == pct_4a:
        return threshold_4a
    if merged == pct_4b:
        return threshold_4b
    return threshold_4c


def _measured_outcomes(project_path) -> "dict[str, str] | None":
    """Per-function outcomes from the memoized suite run, or None.

    `_parse_junit_outcomes` returns {} on parse failure OR when pytest's
    collection phase aborted before any testcase ran (its own classname is
    empty, so the parser skips it). Per its docstring callers must treat {}
    as "no outcome data available", never as "zero tests ran" — otherwise
    the outcome-aware scanners report 0% coverage on a project whose tests
    cannot even be collected, masking the real failure (the test file itself)
    behind a spurious traceability miss.
    """
    from core.quality_gate.test_suite_run import run_suite
    suite_result = run_suite(project_path)
    if suite_result.ran and suite_result.test_outcomes:
        return suite_result.test_outcomes
    return None


def _fr_absent_witnesses(project_path) -> "dict[str, list[str]]":
    """`{FR-XX: ["<file>::<func> (skipped)", ...]}` for the active FR scan."""
    from core.traceability.scanner import scan_test_fr_absent_witnesses
    from core.utils.project_layout import ProjectLayout
    outcomes = _measured_outcomes(project_path)
    if outcomes is None:
        return {}
    return scan_test_fr_absent_witnesses(
        ProjectLayout(project_path).active_test_dir, outcomes, project_path,
    )


def compute_trace_dimension(project, gate: int) -> dict:
    """Compute the `traceability` gate dimension (PR 4).

    Returns a dict suitable for inclusion in the gate's dimension scores:
      {
        "name": "traceability",
        "4a_fr_to_test_pct": 100.0,
        "4b_test_spec_pct": 85.0,
        "4c_nfr_to_test_pct": 100.0,
        "merged_pct": 85.0,
        "passed": True/False,
        "threshold_4a": 100,
        "threshold_4b": 60.0/80.0/90.0,
        "threshold_effective": 100/60.0/80.0/90.0,  # threshold of whichever
            # component (4a/4b/4c) is binding merged_pct — compare merged_pct
            # against THIS, not threshold_4a, or a passing 4b/4c misreads as FAIL
        "active_uncoded": [...],   # FRs in denominator without code
        "active_untested": [...],  # FRs in denominator without test
        "nfr_untested": [...],     # NFRs from SRS.md without any test reference
        "blocking": True/False,
        "error": str | None,
      }
    """
    from core.traceability.scanner import check_traceability
    from scripts.build_traceability import build_traceability

    threshold_4a = TRACE_THRESHOLDS.get(gate, 100)
    threshold_4b = SPEC_COV_THRESHOLDS.get(gate, 60.0)

    result: dict = {
        "name": "traceability",
        "4a_fr_to_test_pct": 0.0,
        "4b_test_spec_pct": 0.0,
        "4c_nfr_to_test_pct": 100.0,
        "merged_pct": 0.0,
        "passed": False,
        "threshold_4a": threshold_4a,
        "threshold_4b": threshold_4b,
        "active_uncoded": [],
        "active_untested": [],
        "fr_absent_witnesses": [],
        "nfr_untested": [],
        "nfr_absent_witnesses": [],
        "blocking": True,
        "error": None,
    }

    # 4a: FR → code → test, PENDING excluded from denominator
    try:
        project_path = Path(project) if not isinstance(project, Path) else project
        _rt, report = check_traceability(project_path)  # noqa: F841 (rt not needed; we re-build)
        # Need the original rt (with status metadata) — re-build via the
        # high-level entry so we get the same model as `preflight_traceability`.
        rt_full = build_traceability(project_path)
        # `missing_mappings` lives under `completeness` in the report shape
        # produced by scanner.check_traceability.
        completeness = report.get("completeness", {}) or {}
        missing = completeness.get("missing_mappings", {}) or {}
        active_uncoded, active_untested = _filter_active_frs(rt_full, missing)
        active_ids = {
            req_id for req_id, req in rt_full.requirements.items()
            if req.status.value in ACTIVE_STATUSES
        }
        total_active = len(active_ids)
        fr_absent = {
            fr: witnesses
            for fr, witnesses in _fr_absent_witnesses(project_path).items()
            if fr in active_ids
        }
        if total_active == 0:
            if rt_full.requirements and gate >= 2:
                # FRs are defined in SAD.md but all still PENDING at Gate 2+:
                # no [FR-XX] annotations found in code — real traceability failure.
                pct_4a = 0.0
                result["active_uncoded"] = sorted(rt_full.requirements.keys())
            else:
                # Truly empty project (no FR definitions) — vacuous pass OK.
                pct_4a = 100.0
        else:
            # F-2.1 fix: an FR with `has_module` (SAD table mapping) but no
            # actual code/test appears in BOTH `fr_without_code` and
            # `fr_without_test`. Subtracting both counts double-counts
            # the same FR. Use the set union (incomplete-FR set) so
            # each incomplete FR is counted once.
            # Round 46 站1: same rule as 4c below. An FR whose `[FR-XX]`
            # reference (or whose `test_frNN.py` file) contains a test that
            # did not run is not complete — `scan_test_fr_coverage` credits
            # per file, so one passing sibling used to cover for it.
            incomplete = active_uncoded | active_untested | set(fr_absent)
            complete = total_active - len(incomplete)
            pct_4a = round(max(0, complete) / total_active * 100, 2)
        result["4a_fr_to_test_pct"] = pct_4a
        if total_active > 0:
            result["active_uncoded"] = sorted(active_uncoded)
            result["active_untested"] = sorted(active_untested)
            result["fr_absent_witnesses"] = sorted(
                f"{f} ← {w}" for f in fr_absent for w in fr_absent[f]
            )
    except Exception as e:
        result["error"] = f"4a: {e}"
        return result

    # 4b: TEST_SPEC → test (delegated to existing D4 spec-coverage)
    try:
        from core.quality_gate.spec_coverage import _run_spec_coverage_check
        _sc_code, sc_pct = _run_spec_coverage_check(project_path, threshold_4b)  # noqa: F841
        result["4b_test_spec_pct"] = sc_pct
    except Exception as e:
        print(f"[WARN] spec_tracking_checker 4b (TEST_SPEC → test): {e}", file=sys.stderr)
        result["4b_test_spec_pct"] = 0.0
        result["error"] = (result["error"] or "") + f" 4b: {e}"

    # 4c: NFR → test coverage (Gate 2+)
    # Each NFR-XX ID in SRS.md must be referenced in at least one test file.
    nfr_pct = 100.0
    nfr_untested: list = []
    nfr_absent_witnesses: list = []
    if gate >= 2:
        try:
            from core.traceability.scanner import (
                extract_nfr_ids_from_srs,
                scan_test_nfr_absent_witnesses,
                scan_test_nfr_coverage,
            )
            from core.utils.project_layout import ProjectLayout
            srs_path = ProjectLayout(project_path).srs_path
            nfr_ids = extract_nfr_ids_from_srs(srs_path)
            # F-2.2: NFR-99 is the placeholder convention for deferred
            # / TBD / ambiguity markers (see phase1_plan.md L96,
            # R-CANONICAL-INTERP-001). It is not a real NFR that requires
            # test coverage — exclude from the 4c denominator.
            nfr_ids = {n for n in nfr_ids if n != "NFR-99"}
            if nfr_ids:
                # Defect A fix: outcome-aware coverage. run_suite is
                # memoized per-process (Round 25 SSOT) — this reuses the
                # same measurement check_traceability() above already took.
                test_outcomes = _measured_outcomes(project_path)
                test_nfr_map = scan_test_nfr_coverage(
                    ProjectLayout(project_path).active_test_dir,
                    test_outcomes=test_outcomes, project_root=project_path,
                )
                # Round 46 站1: a requirement with a witness that did not run
                # is not covered. `scan_test_nfr_coverage` grants credit per
                # FILE, so one passing sibling used to cover for every skipped
                # guard in the same file — taskq-advance shipped NFR-05/07/09
                # VERIFIED that way while the tests asserting the missing
                # README, the missing SBOM and the zero-skip rule all skipped
                # themselves. Recomputed on that project this moves 4c from
                # 12/12 = 100.0 to 9/12 = 75.0, under Gate 4's 90.
                absent = (
                    scan_test_nfr_absent_witnesses(
                        ProjectLayout(project_path).active_test_dir,
                        test_outcomes, project_path,
                    )
                    if test_outcomes is not None else {}
                )
                covered = {
                    n for n in nfr_ids if n in test_nfr_map and n not in absent
                }
                nfr_pct = round(len(covered) / len(nfr_ids) * 100, 2)
                nfr_untested = sorted(nfr_ids - covered)
                nfr_absent_witnesses = sorted(
                    f"{n} ← {w}" for n in nfr_ids & set(absent) for w in absent[n]
                )
        except Exception as e:
            # Fail-closed: NFR scan errors (malformed/unreadable SRS) must not
            # silently pass as 100% coverage. Unlike 4a/4b which also set their
            # dimension to 0.0, here we additionally force passed=False to
            # guarantee the gate fails, since the merged_pct guard (min of the
            # three) would otherwise still pass if 4a/4b happened to be high.
            print(f"[WARN] spec_tracking_checker 4c (NFR → test): {e}", file=sys.stderr)
            nfr_pct = 0.0
            result["passed"] = False
            result["error"] = (result["error"] or "") + f" 4c: {e}"
    result["4c_nfr_to_test_pct"] = nfr_pct
    result["nfr_untested"] = nfr_untested
    # Named, not just counted: "NFR-07 is 25% short" sends nobody anywhere,
    # "NFR-07 ← …::test_sbom_license_field (skipped)" names the file, the
    # function and what happened to it.
    result["nfr_absent_witnesses"] = nfr_absent_witnesses

    # Threshold for 4c matches 4b per gate (60%/80%/90% at G2/G3/G4)
    threshold_4c = threshold_4b

    # Merged: min of all three dimensions — fail-closed
    merged = min(result["4a_fr_to_test_pct"], result["4b_test_spec_pct"], nfr_pct)
    result["merged_pct"] = merged
    result["passed"] = (
        pct_4a >= threshold_4a
        and result["4b_test_spec_pct"] >= threshold_4b
        and nfr_pct >= threshold_4c
    )
    result["threshold_effective"] = resolve_threshold_effective(
        pct_4a=pct_4a,
        pct_4b=result["4b_test_spec_pct"],
        pct_4c=nfr_pct,
        threshold_4a=threshold_4a,
        threshold_4b=threshold_4b,
        threshold_4c=threshold_4c,
    )
    return result


def main():
    """Command-line entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Specification Tracking Checker")
    parser.add_argument("project_root", nargs="?", default=".",
                       help="project root directory (default: current directory)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    
    args = parser.parse_args()
    
    checker = SpecTrackingChecker(args.project_root)
    
    if not args.json:
        result = checker.run()
        checker.print_report()
        return 0 if result else 1
    else:
        completeness = checker.check_completeness()
        print(completeness)
        return 0 if completeness["complete"] else 1


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(main())
