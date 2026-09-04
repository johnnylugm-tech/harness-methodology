"""FR TDD prompt builders package.

Extracts prompt generation logic out of cli/fr_cmds.py into step-specific builders
under byte-level golden snapshot protection (tests/test_fr_prompt_snapshots.py).
"""

from pathlib import Path

from core.canonical_form import fr_num_str
from core.utils.project_layout import ProjectLayout

from cli.fr_prompts._shared import (
    _compute_fr_spec_data,
    _extract_srs_fr_section,
    _extract_test_spec_names,
)
from cli.fr_prompts.fix import (
    build_code_fix_prompt,
    build_coverage_fix_prompt,
    build_infra_fix_prompt,
    build_lint_fix_prompt,
    build_test_fix_prompt,
)
from cli.fr_prompts.gate import build_gate1_prompt
from cli.fr_prompts.tdd import (
    build_tdd_green_prompt,
    build_tdd_improve_prompt,
    build_tdd_red_prompt,
)

__all__ = [
    "_build_fr_step_prompt",
    "_extract_srs_fr_section",
    "_extract_test_spec_names",
    "_compute_fr_spec_data",
]


def _build_fr_step_prompt(step: str, fr_id: str, phase: int,
                           project: Path, srs_path: Path | None,
                           failing_dims: list | None = None,
                           tool_snapshot: str | None = None,
                           block_reason: str | None = None,
                           suite_only_failures: "list[str] | None" = None) -> str:
    """Build a minimal need-to-know prompt for a single FR TDD step.

    Dispatches to step-specific builders. Shared pre-computation (test_file,
    src_dir, srs_path normalisation) done once here.
    """
    step = step.upper()
    num_str = fr_num_str(fr_id)
    _layout = ProjectLayout(project)
    test_dir_str = _layout.get_relative_str(_layout.active_test_dir)
    test_file = f"{test_dir_str}/test_fr{num_str}.py"
    src_dir = "03-development/src"

    if srs_path is None:
        srs_path = ProjectLayout(project).srs_path

    if step == "TDD-RED":
        return build_tdd_red_prompt(fr_id, phase, project, srs_path, test_file, src_dir)

    if step == "TDD-GREEN":
        return build_tdd_green_prompt(fr_id, phase, project, srs_path, test_file, src_dir)

    if step == "TDD-IMPROVE":
        return build_tdd_improve_prompt(fr_id, phase, project, srs_path, test_file, src_dir)

    if step in ("GATE1", "GATE1-DELTA"):
        return build_gate1_prompt(fr_id, phase, project, srs_path, test_file, block_reason)

    if step == "TEST-FIX":
        return build_test_fix_prompt(
            fr_id, phase, project, srs_path, test_file, src_dir, tool_snapshot,
            suite_only_failures=suite_only_failures, test_dir=test_dir_str,
        )

    if step == "COVERAGE-FIX":
        return build_coverage_fix_prompt(fr_id, phase, project, srs_path, test_file, src_dir, tool_snapshot)

    if step == "INFRA-FIX":
        return build_infra_fix_prompt(fr_id, phase, project, srs_path, test_file, src_dir, tool_snapshot)

    if step == "LINT-FIX":
        return build_lint_fix_prompt(fr_id, phase, project, srs_path, test_file, src_dir, tool_snapshot)

    if step == "CODE-FIX":
        return build_code_fix_prompt(fr_id, phase, project, srs_path, test_file, src_dir, failing_dims, tool_snapshot)

    return f"[ERROR] Unknown step: {step}"
