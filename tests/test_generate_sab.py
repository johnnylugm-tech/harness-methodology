"""CLI integration tests for scripts/generate_sab.py."""
import json
import subprocess
import sys
from pathlib import Path

_HARNESS_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_HARNESS_ROOT / "scripts" / "generate_sab.py"), *args],
        capture_output=True, text=True,
    )


def _write_sad(project: Path, block_yaml: str) -> Path:
    sad = project / "02-architecture" / "SAD.md"
    sad.parent.mkdir(parents=True, exist_ok=True)
    sad.write_text(
        f"<!-- SAB:START -->\n```yaml\n{block_yaml}\n```\n<!-- SAB:END -->"
    )
    return sad


_VALID_SAB = """\
sab:
  version: "1.0"
  phase: 2
  project: "cli-test"
  layers:
    - name: api
      modules: ["app.api"]
      allowed_dependencies: []
  allowed_dependencies: []
  fr_module_traceability:
    FR-01: "app.api"
"""


class TestGenerateSabValidate:
    def test_validate_passes_on_canonical_block(self, tmp_path):
        from core.quality_gate.sab_parser import render_canonical_sab_template
        _write_sad(tmp_path, render_canonical_sab_template(project="cli-test"))
        result = _run_cli("--validate", "--project", str(tmp_path))
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "PASSED" in result.stdout

    def test_validate_fails_on_unknown_nfr_type(self, tmp_path):
        _write_sad(tmp_path, _VALID_SAB + (
            "\n  nfr_traceability:\n"
            "    NFR-99:\n"
            "      type: madeuptype\n"
            "      target: 'n/a'\n"
            "      module: x\n"
        ))
        result = _run_cli("--validate", "--project", str(tmp_path))
        assert result.returncode == 1
        assert "madeuptype" in result.stderr

    def test_validate_fails_on_missing_sad(self, tmp_path):
        result = _run_cli("--validate", "--project", str(tmp_path))
        assert result.returncode == 1
        assert "SAD.md not found" in result.stderr


class TestGenerateSabGenerate:
    def test_generate_writes_sab_json(self, tmp_path):
        _write_sad(tmp_path, _VALID_SAB)
        result = _run_cli("--project", str(tmp_path))
        assert result.returncode == 0, f"stderr: {result.stderr}"
        out = tmp_path / ".methodology" / "SAB.json"
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["project"] == "cli-test"
        assert data["phase"] == 2
        assert len(data["layers"]) == 1

    def test_generate_intercepts_runtime_error_no_stacktrace(self, tmp_path):
        """When the SAB block has an invalid phase (string), CLI must exit 1
        with a friendly message — NOT a raw RuntimeError stacktrace."""
        _write_sad(tmp_path, "sab:\n  version: '1.0'\n  phase: not-a-number\n  project: broken\n")
        result = _run_cli("--project", str(tmp_path))
        assert result.returncode == 1
        assert "RuntimeError" not in result.stderr, (
            "Stacktrace leaked — main() must catch RuntimeError"
        )
        assert "SAB block" in result.stderr or "Invalid 'phase'" in result.stderr

    def test_generate_does_not_write_on_failure(self, tmp_path):
        """Output file must NOT be created when parsing fails."""
        _write_sad(tmp_path, "sab:\n  phase: 'bad'\n")
        result = _run_cli("--project", str(tmp_path))
        assert result.returncode == 1
        assert not (tmp_path / ".methodology" / "SAB.json").exists()

    def test_generate_fails_on_missing_sad(self, tmp_path):
        result = _run_cli("--project", str(tmp_path))
        assert result.returncode == 1
        assert "SAD.md not found" in result.stderr


class TestGenerateSabDropsInitModules:
    """__init__.py-sourced entries can never resolve: `_check_sab_module_alignment`
    (harness_cli.py) and `discover_modules()` (sab_amender.py) both exclude
    __init__.py from their on-disk scan by convention, so a SAB layer that
    still lists one is a permanent, unresolvable phantom. generate_sab.py
    must drop these at generation time regardless of what SAD.md's SAB block
    literally lists."""

    def test_dotted_init_entry_is_dropped(self, tmp_path):
        _write_sad(tmp_path, _VALID_SAB.replace(
            'modules: ["app.api"]', 'modules: ["app.api", "app.__init__"]'
        ))
        result = _run_cli("--project", str(tmp_path))
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads((tmp_path / ".methodology" / "SAB.json").read_text())
        assert data["layers"][0]["modules"] == ["app.api"]

    def test_path_form_init_entry_is_dropped(self, tmp_path):
        _write_sad(tmp_path, _VALID_SAB.replace(
            'modules: ["app.api"]', 'modules: ["app.api", "app/__init__.py"]'
        ))
        result = _run_cli("--project", str(tmp_path))
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads((tmp_path / ".methodology" / "SAB.json").read_text())
        assert data["layers"][0]["modules"] == ["app.api"]

    def test_non_init_modules_survive_the_filter(self, tmp_path):
        """Sanity check: the filter targets __init__ specifically, not
        every module — a module merely containing "init" as a substring
        (e.g. app.initializer) must not be dropped."""
        _write_sad(tmp_path, _VALID_SAB.replace(
            'modules: ["app.api"]', 'modules: ["app.api", "app.initializer"]'
        ))
        result = _run_cli("--project", str(tmp_path))
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads((tmp_path / ".methodology" / "SAB.json").read_text())
        assert data["layers"][0]["modules"] == ["app.api", "app.initializer"]
