"""Shared helpers for FR prompt generation."""

import json
import re
from pathlib import Path

from core.quality_gate.spec_coverage import _parse_test_spec
from core.utils.project_layout import ProjectLayout


def _extract_srs_fr_section(srs_path: Path, fr_id: str) -> str:
    """Extract a single FR's full markdown section from SRS.md.

    Returns text between '### FR-XX: ...' header and the next '### FR-' or '---'.
    Falls back to empty string if the section is not found.
    """
    if not srs_path or not srs_path.exists():
        return ""
    content = srs_path.read_text(encoding="utf-8")
    pat = re.compile(
        rf"(### {re.escape(fr_id)}:[^\n]+\n)(.*?)(?=\n---\n|\n### FR-\d+|$)",
        re.DOTALL,
    )
    m = pat.search(content)
    return (m.group(1) + m.group(2)).strip() if m else ""


def _sab_binding_block(project: Path, fr_id: str, src_dir: str) -> str:
    """The module paths Gate 1 will enforce for `fr_id`, rendered for a prompt.

    Round 26. Gate 1's Architecture Amendment Protocol blocks on a PHANTOM — a
    module SAB.json declares that the codebase does not have
    (cli/gate_cmds.py::_check_sab_module_alignment, narrowed to this FR's own
    modules by _filter_phantoms_for_fr). Phase 2 fixes those dotted names before
    any code exists, and until now the implementing agent was never shown them:
    `build_tdd_green_prompt` took no SAB input at all. The divergence therefore
    surfaced at the gate rather than at the keyboard, and the framework's only
    exits are "write code at the declared name" or "a human edits SAB.json".

    taskq-plus P3 paid it twice in one phase — `fix(FR-02): relocate executor to
    service/ to satisfy SAB phantom check`, and FR-05, where SAB declared
    `taskq_plus.cli.main` while the TDD agent had written a flat
    `taskq_plus/cli.py`: three GATE1 dispatches on the same phantom, then the FR
    restarted from RED to rewrite the layout. Every one of the five FRs gated in
    that phase needed a SAB amendment.

    Returns "" when there is no SAB, no traceability entry for this FR, or the
    file is unreadable — a prompt must not fail to render because an optional
    artifact is missing (the gate keeps its own, independent check).
    """
    from core.quality_gate.sab_amender import normalize_sab_module_to_dotted

    sab_path = project / ".methodology" / "SAB.json"
    if not sab_path.is_file():
        return ""
    try:
        sab = json.loads(sab_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(sab, dict):
        return ""

    entries = (sab.get("fr_module_traceability") or {}).get(fr_id)
    if isinstance(entries, str):
        entries = [entries]
    if not isinstance(entries, list):
        return ""

    dotted = [d for d in (normalize_sab_module_to_dotted(e) for e in entries) if d]
    if not dotted:
        return ""

    lines = [
        "[SAB — BINDING MODULE PATHS]",
        f"`.methodology/SAB.json` assigns these modules to {fr_id}. Gate 1's "
        "Architecture Amendment Protocol BLOCKS when a declared module does not "
        "exist on disk, so these names are a constraint on your implementation, "
        "not a suggestion:",
    ]
    for name in dotted:
        rel = name.replace(".", "/")
        lines.append(f"  - {name}  ->  {src_dir}/{rel}.py   (or {src_dir}/{rel}/__init__.py)")
    lines += [
        "Either on-disk shape satisfies the check — a leaf module or a package of "
        "that dotted name. A DIFFERENT name does not.",
        "If a declared decomposition is genuinely wrong for this FR, do NOT quietly "
        "implement something else and do NOT hand-edit SAB.json: run",
        f"  python3 harness_cli.py amend-sab --project {project} "
        "--resolve-phantom <declared> --to <actual> --reason \"<why>\"",
        "which records the amendment in 02-architecture/ADR.md. An architecture "
        "changed without a reason on record is indistinguishable from an "
        "implementation that drifted.",
        "",
    ]
    return "\n".join(lines) + "\n"


def _extract_test_spec_names(project: Path, fr_id: str) -> tuple[list[str], str]:
    r"""Parse TEST_SPEC.md and return (test_names, formatted_note) for a given FR.

    Returns ([], "") when TEST_SPEC.md is missing or has no entries for this FR.

    Reuses `spec_coverage._parse_test_spec()` — the same parser
    `finalize-gate`'s S4 spec-coverage check uses — instead of a second,
    independently-maintained line-by-line parser. The prior local parser
    only reset `current_fr` on a `### FR-XX` heading; any other heading
    (e.g. `### NFR Integration (...)`) left `current_fr` stuck on the
    last FR seen, silently leaking later sections' test names into this
    FR's spec_test_names list (Bug Fix Spec-Cov-Section-Boundary,
    2026-07-21 — FR-05 example: denominator inflated from 11 to 16 by
    NFR-03/NFR-08/NFR-09/smoke-test rows bleeding in after the
    `### NFR Integration` heading, which the old regex
    `^###\s+([A-Z]+-\d+)` did not recognise as a section boundary).
    """
    test_spec_path = ProjectLayout(project).test_spec_path
    if not test_spec_path.exists():
        return [], ""

    items = _parse_test_spec(test_spec_path)
    spec_rows = [i["test_fn"] for i in items if i["fr_id"] == fr_id]
    if spec_rows:
        note = (
            f"\n[TEST SPEC — match these EXACT names]\n"
            f"TEST_SPEC.md at `02-architecture/TEST_SPEC.md` defines "
            f"{len(spec_rows)} test cases for {fr_id}. Write ALL of them "
            f"using these EXACT function names:\n"
            + "\n".join(f"  - {fn}" for fn in spec_rows)
            + "\nDo NOT invent names. spec-coverage-check uses exact match.\n"
        )
        return spec_rows, note
    return [], ""


def _compute_fr_spec_data(project: Path, fr_id: str, test_file: str) -> dict:
    """Compute spec test coverage data needed by GATE1, CODE-FIX, COVERAGE-FIX.

    `spec_cov_pct` / `missing_spec_count` / `spec_summary` are computed
    row-for-row against `spec_test_names` (mirrors
    `spec_coverage._run_spec_coverage_check()`'s `covered = [i for i in
    items if i["test_fn"] in actual_fns]` — same list, same basis for
    numerator and denominator). `existing_spec_tests` stays a `set` of
    covered UNIQUE names — CODE-FIX (below) only ever does `in`
    membership checks against it, which are unaffected by de-duplication.

    Bug Fix Spec-Cov-Asymmetric-Dedup (2026-07-21): previously the
    numerator counted `len(existing_spec_tests)` (a `set`, de-duplicated)
    against a denominator of `len(spec_test_names)` (a `list`, NOT
    de-duplicated). TEST_SPEC.md's v2.13.0 "Multi-scenario expansion"
    rule (02-architecture/TEST_SPEC.md:224) deliberately repeats the same
    function name across N parametrize rows (e.g. FR-05's
    `test_fr05_07_exit_code_map` appears 5 times for 5 exit codes). Those
    5 rows always collapsed to 1 in the set-based numerator while still
    counting 5 in the list-based denominator, manufacturing
    `min(N-1, N)` permanently-unsatisfiable "missing" entries regardless
    of actual test coverage.
    """
    spec_test_names, _ = _extract_test_spec_names(project, fr_id)
    test_file_path = project / test_file
    existing_spec_tests: set[str] = set()
    covered_row_count = 0
    if spec_test_names and test_file_path.exists():
        try:
            tf_content = test_file_path.read_text(encoding="utf-8")
            _actual_fns = set()
            for line in tf_content.splitlines():
                m2 = re.match(r"^\s*(?:async\s+)?def\s+(test_\w+)\s*\(", line)
                if m2:
                    _actual_fns.add(m2.group(1))
            for fn in spec_test_names:
                raw_fn = fn.strip("`").strip()
                raw_fn = re.sub(r"\[.*\]$", "", raw_fn)
                raw_fn = re.sub(r"\(\)$", "", raw_fn)
                if raw_fn in _actual_fns:
                    existing_spec_tests.add(fn)
                    covered_row_count += 1
        except (OSError, UnicodeDecodeError):
            pass
    spec_cov_pct = (
        round(covered_row_count / max(len(spec_test_names), 1) * 100)
        if spec_test_names else 100
    )
    missing_spec_count = len(spec_test_names) - covered_row_count
    spec_summary = (
        f"SPEC COVERAGE: {covered_row_count}/{len(spec_test_names)} "
        f"({spec_cov_pct}%) — {missing_spec_count} missing"
        if spec_test_names else ""
    )
    return {
        "spec_test_names": spec_test_names,
        "existing_spec_tests": existing_spec_tests,
        "spec_cov_pct": spec_cov_pct,
        "missing_spec_count": missing_spec_count,
        "spec_summary": spec_summary,
    }
