"""
Smoke tests for harness/ssi/ embedded assets.
Verifies scripts are importable, prompts exist, schema is valid JSON.
"""

import json
from pathlib import Path
import pytest

SSI_DIR = Path(__file__).parent.parent / "harness" / "ssi"
SCRIPTS_DIR = SSI_DIR / "scripts"
PROMPTS_DIR = SSI_DIR / "prompts"
SCHEMAS_DIR = SSI_DIR / "schemas"

REQUIRED_SCRIPTS = [
    "checkpoint",
    "crg_analysis",
    "crg_integration",
    "issue_tracker",
    "report_gen",
    "score",
    "setup_target",
    "verify",
    "verify_tools",
]

REQUIRED_PROMPTS = [
    "evaluate_dimension.md",
    "verify_round.md",
    "crg_reconnaissance.md",
    "improvement_plan.md",
    "final_report.md",
]

REQUIRED_SCHEMAS = [
    "harness_gate_result.schema.json",
]


class TestSSIDirectory:
    def test_ssi_dir_exists(self):
        assert SSI_DIR.is_dir(), f"harness/ssi/ does not exist: {SSI_DIR}"

    def test_scripts_dir_exists(self):
        assert SCRIPTS_DIR.is_dir()

    def test_prompts_dir_exists(self):
        assert PROMPTS_DIR.is_dir()

    def test_schemas_dir_exists(self):
        assert SCHEMAS_DIR.is_dir()

    def test_init_exists(self):
        assert (SSI_DIR / "__init__.py").exists()

    def test_scripts_init_exists(self):
        assert (SCRIPTS_DIR / "__init__.py").exists()


class TestSSIScripts:
    @pytest.mark.parametrize("module_name", REQUIRED_SCRIPTS)
    def test_script_file_exists(self, module_name):
        script_path = SCRIPTS_DIR / f"{module_name}.py"
        assert script_path.exists(), f"Missing script: {script_path}"

    @pytest.mark.parametrize("module_name", REQUIRED_SCRIPTS)
    def test_script_is_valid_python(self, module_name):
        """Verify script parses without SyntaxError."""
        script_path = SCRIPTS_DIR / f"{module_name}.py"
        source = script_path.read_text(encoding="utf-8")
        try:
            compile(source, str(script_path), "exec")
        except SyntaxError as e:
            pytest.fail(f"SyntaxError in {module_name}.py: {e}")

    def test_install_script_exists(self):
        assert (SCRIPTS_DIR / "install_extended_tools.sh").exists()


class TestSSIPrompts:
    @pytest.mark.parametrize("filename", REQUIRED_PROMPTS)
    def test_prompt_exists(self, filename):
        path = PROMPTS_DIR / filename
        assert path.exists(), f"Missing prompt: {path}"

    @pytest.mark.parametrize("filename", REQUIRED_PROMPTS)
    def test_prompt_nonempty(self, filename):
        path = PROMPTS_DIR / filename
        assert path.stat().st_size > 0, f"Prompt is empty: {path}"


class TestSSISchemas:
    @pytest.mark.parametrize("filename", REQUIRED_SCHEMAS)
    def test_schema_exists(self, filename):
        path = SCHEMAS_DIR / filename
        assert path.exists(), f"Missing schema: {path}"

    @pytest.mark.parametrize("filename", REQUIRED_SCHEMAS)
    def test_schema_valid_json(self, filename):
        path = SCHEMAS_DIR / filename
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            assert isinstance(data, dict)
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON in {filename}: {e}")

    def test_gate_result_schema_required_fields(self):
        path = SCHEMAS_DIR / "harness_gate_result.schema.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        required = set(schema.get("required", []))
        expected = {"overall_score", "meets_target", "quality_complete",
                    "open_critical_count", "open_high_count", "breakdown"}
        assert expected.issubset(required), f"Schema missing required fields: {expected - required}"
