"""Gate step prompt builders: GATE1 and GATE1-DELTA.

Round 45 站4. On 2026-08-11 a live P7 run blocked FR-09 with a false positive.
`b288c9d`'s own commit message names the cause: this template listed three of
the four dimensions `harness/gate_configs/gate1_per_fr.yaml` declares, and an
agent that read it verbatim produced a `gate1_result.json` whose
`architecture_constraints` block had no `tool_evidence` — which S3 refuses.

That fix typed the fourth dimension in and pinned it. This one removes the
copy: the dimension set, its thresholds and its weights are read from the YAML
at build time via `load_gate_dimensions`, the same reader Round 39 站3 built
for the dimension table. A fifth dimension in the YAML reaches the prompt
without anyone remembering to add it.

What is deliberately NOT in the YAML stays here as a declared mapping: how to
run a dimension's tool, and how to turn its output into a score. Those are
prose, and prose does not belong in a config file. A dimension with no entry
raises at build time — the failure mode this round exists to remove is a
prompt that silently says nothing about a dimension the gate will score.
"""

from pathlib import Path

from core.quality_gate.gate_thresholds import load_gate_dimensions
from core.state_io import load_quality_manifest

from cli.fr_prompts._shared import (
    _compute_fr_spec_data,
    _extract_srs_fr_section,
    _extract_test_spec_names,
)

# The three things about a dimension that are prose, not config: how to run its
# tool, what its tool_evidence is a snippet of, and how its output becomes a
# number. One entry per dimension, so adding a dimension is one edit here and
# one in the YAML — never one and a half.
#
# `schema_extra` is the only optional key: extra JSON fields the dimension's
# breakdown block carries. Only test_coverage has any today.
GATE1_DIMENSION_PROSE: dict[str, dict[str, str]] = {
    "linting": {
        "tool_hint": "ruff check ...",
        "evidence_of": "ruff stdout",
        "scoring": "ruff exit 0 → 100; else count violations: "
                   "max(0, 100 - violations×5)",
    },
    "type_safety": {
        "tool_hint": "pyright ...",
        "evidence_of": "pyright stdout",
        "scoring": "parse pyright JSON summary.errorCount: "
                   "max(0, 100 - errorCount×5)",
    },
    "test_coverage": {
        "tool_hint": "coverage run / pytest ...",
        "evidence_of": "coverage/pytest stdout",
        "scoring": "score = min(coverage_pct, spec_cov_pct).",
        "schema_extra": (
            '           "tests_passed": <int>,   // REQUIRED: count from pytest summary line\n'
            '           "tests_failed": <int>,   // REQUIRED: must be 0 — any failed test blocks the gate\n'
            '           "tests_skipped": <int>,  // REQUIRED: count skipped tests\n'
        ),
    },
    "architecture_constraints": {
        "tool_hint": "PYTHONPATH=src lint-imports",
        "evidence_of": "lint-imports stdout",
        "scoring": "lint-imports exit 0 → 100; any contract broken → 0",
    },
}


def _gate1_dimensions(project: Path) -> list[dict]:
    """The gate's dimensions, with the threshold each one is actually held to.

    `max(yaml, manifest override)` for every dimension — three of the four
    already went through this path and `architecture_constraints` carried a
    literal `100`, which is the same drift in miniature.
    """
    overrides = load_quality_manifest(project, lenient=True).get(
        "gate_score_overrides", {})
    dims = []
    for dim in load_gate_dimensions(1):
        name = dim.get("name", "")
        if name not in GATE1_DIMENSION_PROSE:
            raise KeyError(
                f"gate1_per_fr.yaml declares dimension {name!r} and "
                f"cli/fr_prompts/gate.py's GATE1_DIMENSION_PROSE has no entry "
                f"for it. The prompt would tell the agent to score a dimension "
                f"without saying how to run its tool or how to turn the output "
                f"into a number — add the entry."
            )
        dims.append({
            "name": name,
            "weight": float(dim.get("weight", 0)),
            "threshold": max(float(dim.get("threshold", 0)),
                             float(overrides.get(name, 0))),
            **GATE1_DIMENSION_PROSE[name],
        })
    return dims


def build_gate1_prompt(fr_id: str, phase: int, project: Path, srs_path: Path, test_file: str, block_reason: str | None = None) -> str:
    """Build prompt for GATE1 and GATE1-DELTA steps.

    Round 51 站1. Round 45 站4 removed this template's hand-copied dimension
    list and rendered it from `gate1_per_fr.yaml` instead. What it renders is
    the roster — four dimensions, their tools, thresholds and weights. The
    requirement was never in it: `srs_path` arrived as a parameter and was
    dropped, so the per-FR verdict has been a statement about four tools, not
    about the FR.

    The dimension scores stay exactly as they are. The section below is
    context for the evaluator's summary, not a fifth score — a dimension
    without a tool is a dimension without evidence, and Round 32 站4 settled
    that an unmeasurable thing is the framework's debt, never a number.
    """
    srs_section = _extract_srs_fr_section(srs_path, fr_id) if srs_path else ""
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

    _dims = _gate1_dimensions(project)
    _label_w = max(len(d["name"]) for d in _dims) + 2

    _tool_lines = "".join(
        f"   {chr(ord('a') + i)}. {d['name'] + ':':<{_label_w}}"
        f"{d['tool_hint']} (exact command shown in run-gate output)\n"
        for i, d in enumerate(_dims)
    )
    _schema_lines = "".join(
        (
            f'       "{d["name"]}": {{\n'
            f'           "score": <0-100>, "threshold": {d["threshold"]:.0f},\n'
            f'{d["schema_extra"]}'
            f'           "tool_evidence": "<first 500 chars of '
            f'{d["evidence_of"]}>"\n'
            f'       }}{"," if i < len(_dims) - 1 else ""}\n'
        )
        if "schema_extra" in d else
        (
            f'       "{d["name"]}": {{"score": <0-100>, '
            f'"threshold": {d["threshold"]:.0f}, '
            f'"tool_evidence": "<first 500 chars of '
            f'{d["evidence_of"]}>"}}'
            f'{"," if i < len(_dims) - 1 else ""}\n'
        )
        for i, d in enumerate(_dims)
    )
    _formula = " + ".join(
        f"{d['name']}.score × {d['weight']:g}" for d in _dims
    )
    # test_coverage's formula needs this FR's actual numbers, so its tail is
    # computed here rather than sitting in the static prose table — and it has
    # to follow its own dimension's line, not whichever one renders last.
    _scoring_tail = {
        "test_coverage": (
            f"     spec_cov_pct = (existing_required_tests / total_required) × 100.\n"
            f"     Currently: {missing_spec_count} required tests missing → "
            f"spec_cov_pct = {spec_cov_pct}% → score capped at {spec_cov_pct}.\n"
            f"     ALL required tests must exist and pass — partial spec "
            f"coverage = partial score.\n"
        ),
    }
    _scoring_lines = "".join(
        f"   - {d['name']}: {d['scoring']}\n" + _scoring_tail.get(d["name"], "")
        for d in _dims
    )

    return (
        f"You are a Gate 1 evaluator. Your task: run Gate 1 evaluation for {fr_id}.\n\n"
        f"[FR REQUIREMENTS — what {fr_id} was supposed to deliver]\n"
        f"{srs_section or f'See SRS.md for {fr_id} requirements'}\n\n"
        f"The four dimension scores below are the verdict. This section is not a "
        f"fifth score — it is what your summary is about. If the tools all pass "
        f"over an implementation that does not do what the requirement says, say "
        f"so in `summary` and name the gap; do not lower a dimension score to "
        f"express it.\n"
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
        f"2. Run the {len(_dims)} tool commands from step 1's FR-SCOPED TOOL OVERRIDES:\n"
        f"{_tool_lines}"
        f"   Save each tool's output to .sessi-work/round_1/tools/<dimension>.txt\n\n"
        f"3. Write `.sessi-work/gate1_result.json` with this EXACT schema:\n"
        f"   {{\n"
        f'     "gate": 1, "phase": {phase}, "fr_id": "{fr_id}",\n'
        f'     "overall_score": <float>,           // weighted avg of breakdown scores\n'
        f'     "quality_complete": true,            // true if overall_score >= 80\n'
        f'     "rounds_used": 1,\n'
        f'     "breakdown": {{\n'
        f"{_schema_lines}"
        f'     }}\n'
        f"   }}\n"
        f"   overall_score = ({_formula}).\n"
        f"   quality_complete = (overall_score >= 80) AND (every dimension score >= its threshold).\n"
        f"   CRITICAL: `tool_evidence` is REQUIRED for every dimension.\n"
        f"   If you omit it, finalize-gate will BLOCK with S3 error regardless of scores.\n"
        f"   Score fabrication (writing a score without running the tool) also causes S3 block.\n"
        f"   CRITICAL: `tests_failed` MUST be 0. finalize-gate parses tool_evidence for\n"
        f"   '{{N}} failed' and blocks immediately if any test is red — even at 96% coverage.\n\n"
        f"   Scoring formulas:\n"
        f"{_scoring_lines}\n"
        f"4. Run: `python3 harness_cli.py finalize-gate --gate 1 --phase {phase} "
        f"--fr-id {fr_id} --project {project}`\n"
        f"   If finalize-gate prints [BLOCKED], include the exact error in your output summary.\n\n"
        f"5. Report pass/fail and failing dimensions (if any).\n\n"
        f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "gate_score": <float>, '
        f'"pass": true/false, "failing_dims": [...], "commit": "<hash or null>", '
        f'"summary": "<under 50 chars>"}}'
    )
