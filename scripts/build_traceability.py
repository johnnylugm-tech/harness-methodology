#!/usr/bin/env python3
"""
build_traceability.py — ASPICE Full Traceability Matrix Builder
================================================================
Populates RequirementTraceability model from live artifacts (SAD.md,
Python [FR-XX] annotations, test files) and auto-generates
TRACEABILITY_MATRIX.md with ASPICE SWE.3 compliance reporting.

Usage:
    python3 scripts/build_traceability.py --project . [--sad SAD.md]
    python3 scripts/build_traceability.py --project . --format aspice --export report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Set

# Ensure harness-methodology root is on path for core imports
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent))

from core.requirement_traceability import (  # noqa: E402
    RequirementTraceability,
    TraceStatus,
)
from core.traceability.overlay import (  # noqa: E402
    render_merged_markdown,
)
from core.utils.project_layout import ProjectLayout  # noqa: E402
from core.traceability.scanner import (  # noqa: E402
    extract_fr_ids_from_sad,
    extract_nfr_ids_from_srs,
    scan_python_fr_annotations,
    scan_test_fr_coverage,
    scan_test_nfr_coverage,
    scan_sad_fr_modules,
)


# ---------------------------------------------------------------------------
# Matrix builder
# ---------------------------------------------------------------------------

def build_traceability(
    project: Path,
    sad_path: Optional[Path] = None,
) -> RequirementTraceability:
    """Populate a RequirementTraceability model from live project artifacts."""
    project_id = project.resolve().name
    rt = RequirementTraceability(project_id=project_id)

    if sad_path is None:
        sad_path = ProjectLayout(project).sad_path

    # 1. Extract FRs from SAD.md (source of truth)
    sad_frs = extract_fr_ids_from_sad(sad_path)
    sad_modules = scan_sad_fr_modules(sad_path)

    # 2. Scan code for [FR-XX] annotations
    code_fr_map = scan_python_fr_annotations(project)

    # 3. Scan tests for FR coverage
    has_03_tests = (ProjectLayout(project).phase3_development_dir / "tests").is_dir()
    has_root_tests = (project / "tests").is_dir()
    # Bug M26 fix: previously the fallback `else project / "tests"`
    # evaluated even when neither directory existed, silently producing
    # zero test coverage with no diagnostic. Now emit a warning on the
    # returned model so the report reflects the missing test layer.
    if has_03_tests:
        tests_dir = ProjectLayout(project).phase3_development_dir / "tests"
    elif has_root_tests:
        tests_dir = project / "tests"
    else:
        tests_dir = project / "tests"  # used for the scan; result is empty
        # Use setattr to attach a transient warning to the model without
        # modifying the upstream class definition.
        setattr(rt, "no_tests_warning",
                f"No tests directory found under {project}/03-development/tests "
                f"or {project}/tests — coverage report is empty.")
    test_fr_map = scan_test_fr_coverage(tests_dir)

    # 4. Merge all FR IDs
    all_frs: Set[str] = set(sad_frs)
    all_frs.update(code_fr_map.keys())
    all_frs.update(test_fr_map.keys())
    all_frs.update(sad_modules.keys())

    # 5. Populate model
    for fr_id in sorted(all_frs):
        srs_section = "SAD.md" if fr_id in sad_frs else None

        # Determine status from coverage completeness
        has_code = fr_id in code_fr_map
        has_test = fr_id in test_fr_map
        has_module = fr_id in sad_modules
        if has_code and has_test:
            status = TraceStatus.VERIFIED
        elif has_code or has_module:
            status = TraceStatus.IN_PROGRESS
        elif fr_id in sad_frs:
            status = TraceStatus.PENDING
        else:
            status = TraceStatus.NOT_IMPLEMENTED

        rt.add_requirement(
            req_id=fr_id,
            title=f"Requirement {fr_id}",
            srs_section=srs_section,
            description="",
            priority="HIGH",
            metadata={
                "sad_mapped": fr_id in sad_frs,
                "code_files": code_fr_map.get(fr_id, []),
                "test_files": test_fr_map.get(fr_id, []),
                "sad_modules": sad_modules.get(fr_id, []),
            },
        )
        rt.requirements[fr_id].status = status

        # Add code components
        for file_path in code_fr_map.get(fr_id, []):
            rt.add_code_component(file_path=file_path, fr_id=fr_id)

        # Add test coverage
        for test_file in test_fr_map.get(fr_id, []):
            rt.add_test_coverage(test_file=test_file, fr_id=fr_id)

    # NFR coverage: scan SRS.md + test files; stored on rt for matrix rendering.
    srs_path = ProjectLayout(project).srs_path
    nfr_ids = extract_nfr_ids_from_srs(srs_path)
    test_nfr_map = scan_test_nfr_coverage(ProjectLayout(project).active_test_dir) if nfr_ids else {}
    # Use setattr to avoid Pyright complaints about unknown attribute.
    setattr(rt, "nfr_data", {
        "nfr_ids": sorted(nfr_ids),
        "nfr_test_coverage": {nfr: test_nfr_map.get(nfr, []) for nfr in sorted(nfr_ids)},
    })

    return rt


# ---------------------------------------------------------------------------
# Report generators
# ---------------------------------------------------------------------------

def generate_markdown_matrix(rt: RequirementTraceability, output_path: Path,
                            overlay_path: Optional[Path] = None) -> None:
    """Generate TRACEABILITY_MATRIX.md from atomic + overlay (PR 2).

    If the existing file has `<!-- AUTO-GEN:START/END -->` sentinels, content
    above START is preserved (manual intro); content between sentinels is
    replaced. If no sentinels exist, the file is treated as legacy and
    fully replaced — the user is expected to run `migrate-trace-overlay`
    first if any manual content needs to survive.
    """
    if overlay_path is None:
        overlay_path = output_path.parent / "TRACEABILITY_MATRIX.overlay.yaml"
    markdown, errors = render_merged_markdown(rt, overlay_path)
    if errors:
        for err in errors:
            print(f"  overlay error: {err}", file=sys.stderr)

    # Append NFR section if build_traceability populated nfr_data on rt.
    nfr_data = getattr(rt, "nfr_data", {})
    nfr_ids = nfr_data.get("nfr_ids", [])
    if nfr_ids:
        nfr_cov = nfr_data.get("nfr_test_coverage", {})
        lines = ["\n## Non-Functional Requirements\n",
                 "| NFR ID | Test Coverage | Status |",
                 "|--------|--------------|--------|"]
        for nfr_id in nfr_ids:
            tests = nfr_cov.get(nfr_id, [])
            status = "VERIFIED" if tests else "PENDING"
            test_names = ", ".join(Path(t).name for t in tests) if tests else "—"
            lines.append(f"| {nfr_id} | {test_names} | {status} |")
        markdown = markdown + "\n" + "\n".join(lines) + "\n"

    intro = ""
    if output_path.exists():
        existing = output_path.read_text(encoding="utf-8", errors="replace")
        if "<!-- AUTO-GEN:START -->" in existing:
            head = existing.split("<!-- AUTO-GEN:START -->", 1)[0]
            intro = head.rstrip() + "\n\n"
        else:
            # Legacy file without sentinels. Print a one-time warning so the
            # user knows to run `migrate-trace-overlay` if they had manual
            # content above the auto-gen block. We do NOT preserve the
            # legacy content here — it's all regenerable from atomic+overlay.
            print(
                f"  WARN: {output_path.name} has no AUTO-GEN sentinels; "
                f"running `migrate-trace-overlay` first preserves any "
                f"manual intro.",
                file=sys.stderr,
            )
    output_path.write_text(intro + markdown, encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="ASPICE Full Traceability Matrix Builder"
    )
    parser.add_argument("--project", required=True, help="Project root path")
    parser.add_argument("--sad", default=None, help="Path to SAD.md (default: <project>/SAD.md)")
    parser.add_argument("--output-matrix", default=None,
                        help="Output path for TRACEABILITY_MATRIX.md")
    parser.add_argument("--export", default=None, help="Export JSON report to file")
    parser.add_argument("--format", default="standard",
                        choices=["standard", "aspice"],
                        help="Export format (default: standard)")
    parser.add_argument("--json", action="store_true",
                        help="Print completeness report as JSON to stdout")
    args = parser.parse_args(argv)

    project = Path(args.project).resolve()
    if not project.is_dir():
        print(f"ERROR: project directory not found: {project}", file=sys.stderr)
        return 2

    sad_path = Path(args.sad) if args.sad else None
    rt = build_traceability(project, sad_path=sad_path)

    # JSON stdout
    if args.json:
        report = rt.export_report(format=args.format)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    # Export JSON
    if args.export:
        rt.save(args.export)
        print(f"Report saved to {args.export}")

    # Generate TRACEABILITY_MATRIX.md
    matrix_path = Path(args.output_matrix) if args.output_matrix else project / "TRACEABILITY_MATRIX.md"
    generate_markdown_matrix(rt, matrix_path)
    print(f"Traceability matrix written to {matrix_path}")

    # Print summary
    c = rt.verify_completeness()
    print("\nASPICE Compliance Summary:")
    print(f"  Requirements: {c['total_requirements']}")
    print(f"  SRS Coverage:  {c['srs_coverage']}")
    print(f"  Code Coverage: {c['code_coverage']}")
    print(f"  Test Coverage: {c['test_coverage']}")
    print(f"  Total Links:   {c['total_links']}")

    missing = c.get("missing_mappings", {})
    if missing.get("fr_without_code"):
        print(f"  FRs without code: {missing['fr_without_code']}")
    if missing.get("fr_without_test"):
        print(f"  FRs without test: {missing['fr_without_test']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
