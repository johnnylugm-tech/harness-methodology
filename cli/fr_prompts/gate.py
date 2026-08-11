"""Gate step prompt builders: GATE1 and GATE1-DELTA."""

from pathlib import Path

from core.state_io import load_quality_manifest
from core.quality_gate.sab_parser import _GATE1_DIMENSION_STANDARD

from cli.fr_prompts._shared import _compute_fr_spec_data, _extract_test_spec_names


def build_gate1_prompt(fr_id: str, phase: int, project: Path, srs_path: Path, test_file: str, block_reason: str | None = None) -> str:
    """Build prompt for GATE1 and GATE1-DELTA steps."""
    spec = _compute_fr_spec_data(project, fr_id, test_file)
    spec_test_names = spec["spec_test_names"]
    spec_cov_pct = spec["spec_cov_pct"]
    missing_spec_count = spec["missing_spec_count"]

    spec_test_names, _ = _extract_test_spec_names(project, fr_id)
    spec_section = ""
    if spec_test_names:
        spec_section = (
            f"\n[TEST SPEC — required test cases for {fr_id}]\n"
            f"TEST_SPEC.md requires these EXACT test functions:\n"
            + "\n".join(f"  - {fn}" for fn in spec_test_names)
            + "\n\nWhen evaluating test_coverage, verify:\n"
            "  - EVERY required test EXISTS in the test file\n"
            "  - EVERY required test PASSES (not skipped, not failing)\n"
            "  - Missing or failing required test = test_coverage FAIL, "
            "regardless of raw coverage %\n\n"
        )

    block_section = ""
    if block_reason:
        block_section = (
            f"\n[PREVIOUS ATTEMPT BLOCKED — read carefully]\n"
            f"{block_reason}\n"
            f"Ensure the gate1_result.json you write this time satisfies the\n"
            f"tool_evidence requirement described in step 3 below.\n\n"
        )

    spec_section = ""
    if spec_test_names:
        spec_section = (
            f"\n[TEST SPEC — required test cases for {fr_id}]\n"
            f"TEST_SPEC.md requires these EXACT test functions:\n"
            + "\n".join(f"  - {fn}" for fn in spec_test_names)
            + f"\n\n{spec['spec_summary']}\n"
            f"→ score = min(coverage_pct, spec_cov_pct). Missing tests count as 0.\n"
            f"  All required tests MUST exist and pass — partial coverage = partial score.\n\n"
        )

    _gate_manifest = load_quality_manifest(project, lenient=True)
    _gate_overrides = _gate_manifest.get("gate_score_overrides", {})
    _lint_thresh = max(float(_GATE1_DIMENSION_STANDARD["linting"]), float(_gate_overrides.get("linting", 0)))
    _type_thresh = max(float(_GATE1_DIMENSION_STANDARD["type_safety"]), float(_gate_overrides.get("type_safety", 0)))
    _cov_thresh = max(float(_GATE1_DIMENSION_STANDARD["test_coverage"]), float(_gate_overrides.get("test_coverage", 0)))

    return (
        f"You are a Gate 1 evaluator. Your task: run Gate 1 evaluation for {fr_id}.\n"
        f"{spec_section}"
        f"{block_section}"
        f"[STOP RULE — follow when tools fail or you are unsure]\n"
        f"- If run-gate itself prints [BLOCKED] (SAB phantom/unregistered module, "
        f"manifest corruption — a PRECONDITION failure, the dimension tools never ran):\n"
        f"  → Do NOT write gate1_result.json and do NOT record any score=0\n"
        f"  → Report status INFRA_BLOCKED with the verbatim [BLOCKED] message\n"
        f"  → This is an infrastructure problem, not a code-quality verdict — "
        f"recording zeros here poisons the manifest and dispatches fixes at healthy code\n"
        f"- If a single dimension tool command fails to execute (error, not found, env issue):\n"
        f"  → Record score=0 for that dimension\n"
        f"  → Set tool_evidence = first 300 chars of the error output\n"
        f"  → Move on to the next dimension — do NOT retry the same command\n"
        f"- If finalize-gate prints [BLOCKED]:\n"
        f"  → Include the exact BLOCKED message in your output summary\n"
        f"  → Do NOT attempt to fix source code yourself — that is CODE-FIX's job\n"
        f"- Write gate1_result.json and call finalize-gate within 10 turns of starting.\n"
        f"  A low score with tool_evidence is always better than a timeout.\n\n"
        f"[TASK — follow EXACTLY in order]\n"
        f"1. Run: `python3 harness_cli.py run-gate --gate 1 --phase {phase} "
        f"--fr-id {fr_id} --project {project}`\n"
        f"   The output contains FR-SCOPED TOOL OVERRIDES — exact commands for each\n"
        f"   dimension.  Use those commands, not the generic ones in evaluate_dimension.md.\n\n"
        f"2. Run the four tool commands from step 1's FR-SCOPED TOOL OVERRIDES:\n"
        f"   a. linting:               ruff check ... (exact command shown in run-gate output)\n"
        f"   b. type_safety:           pyright ... (exact command shown in run-gate output)\n"
        f"   c. test_coverage:         coverage run / pytest ... (exact command shown in run-gate output)\n"
        f"   d. architecture_constraints: PYTHONPATH=src lint-imports (exact command shown in run-gate output)\n"
        f"   Save each tool's output to .sessi-work/round_1/tools/<dimension>.txt\n\n"
        f"3. Write `.sessi-work/gate1_result.json` with this EXACT schema:\n"
        f"   {{\n"
        f'     "gate": 1, "phase": {phase}, "fr_id": "{fr_id}",\n'
        f'     "overall_score": <float>,           // weighted avg of breakdown scores\n'
        f'     "quality_complete": true,            // true if overall_score >= 80\n'
        f'     "rounds_used": 1,\n'
        f'     "breakdown": {{\n'
        f'       "linting":               {{"score": <0-100>, "threshold": {_lint_thresh:.0f}, "tool_evidence": "<first 500 chars of ruff stdout>"}},\n'
        f'       "type_safety":           {{"score": <0-100>, "threshold": {_type_thresh:.0f}, "tool_evidence": "<first 500 chars of pyright stdout>"}},\n'
        f'       "test_coverage": {{\n'
        f'           "score": <0-100>, "threshold": {_cov_thresh:.0f},\n'
        f'           "tests_passed": <int>,   // REQUIRED: count from pytest summary line\n'
        f'           "tests_failed": <int>,   // REQUIRED: must be 0 — any failed test blocks the gate\n'
        f'           "tests_skipped": <int>,  // REQUIRED: count skipped tests\n'
        f'           "tool_evidence": "<first 500 chars of coverage/pytest stdout>"\n'
        f'       }},\n'
        f'       "architecture_constraints": {{"score": <0-100>, "threshold": 100, "tool_evidence": "<first 500 chars of lint-imports stdout>"}}\n'
        f'     }}\n'
        f"   }}\n"
        f"   overall_score = (linting.score × 0.25 + type_safety.score × 0.25 + test_coverage.score × 0.25 + architecture_constraints.score × 0.25).\n"
        f"   quality_complete = (overall_score >= 80) AND (every dimension score >= its threshold).\n"
        f"   CRITICAL: `tool_evidence` is REQUIRED for every dimension.\n"
        f"   If you omit it, finalize-gate will BLOCK with S3 error regardless of scores.\n"
        f"   Score fabrication (writing a score without running the tool) also causes S3 block.\n"
        f"   CRITICAL: `tests_failed` MUST be 0. finalize-gate parses tool_evidence for\n"
        f"   '{{N}} failed' and blocks immediately if any test is red — even at 96% coverage.\n\n"
        f"   Scoring formulas:\n"
        f"   - linting:      ruff exit 0 → 100; else count violations: max(0, 100 - violations×5)\n"
        f"   - type_safety:  parse pyright JSON summary.errorCount: max(0, 100 - errorCount×5)\n"
        f"   - test_coverage: score = min(coverage_pct, spec_cov_pct).\n"
        f"     spec_cov_pct = (existing_required_tests / total_required) × 100.\n"
        f"     Currently: {missing_spec_count} required tests missing → spec_cov_pct = {spec_cov_pct}% → score capped at {spec_cov_pct}.\n"
        f"     ALL required tests must exist and pass — partial spec coverage = partial score.\n\n"
        f"4. Run: `python3 harness_cli.py finalize-gate --gate 1 --phase {phase} "
        f"--fr-id {fr_id} --project {project}`\n"
        f"   If finalize-gate prints [BLOCKED], include the exact error in your output summary.\n\n"
        f"5. Report pass/fail and failing dimensions (if any).\n\n"
        f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "gate_score": <float>, '
        f'"pass": true/false, "failing_dims": [...], "commit": "<hash or null>", '
        f'"summary": "<under 50 chars>"}}'
    )
