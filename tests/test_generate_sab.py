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
  # Round 72 站5: `required_artifacts` must be stated. An empty list is the
  # decision "this spec names no mandatory files"; leaving the key out is
  # nobody having considered it, and `validate_sab_block` now says so.
  required_artifacts: []
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

    def test_generate_fails_on_unknown_nfr_type_before_writing(self, tmp_path):
        """Round 3 Station K: the DEFAULT generate path must run the same
        static validation as --validate. Pre-fix, an illegal NFR type
        generated a SAB.json whose NFR silently mapped to no gate dimension
        (unenforced until P6's --validate step, four phases later)."""
        _write_sad(tmp_path, _VALID_SAB + (
            "\n  nfr_traceability:\n"
            "    NFR-01:\n"
            "      type: preformance\n"
            "      target: 'p95 < 200ms'\n"
            "      module: app.api\n"
        ))
        result = _run_cli("--project", str(tmp_path))
        assert result.returncode == 1
        assert "preformance" in result.stderr
        assert not (tmp_path / ".methodology" / "SAB.json").exists()

    def test_generate_passes_with_legal_nfr_type(self, tmp_path):
        """Counterexample: a legal NFR type must not trip the new validation."""
        _write_sad(tmp_path, _VALID_SAB + (
            "\n  nfr_traceability:\n"
            "    NFR-01:\n"
            "      type: performance\n"
            "      target: 'p95 < 200ms'\n"
            "      module: app.api\n"
        ))
        result = _run_cli("--project", str(tmp_path))
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert (tmp_path / ".methodology" / "SAB.json").exists()


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


class TestGenerateSabNormalizes03DevelopmentPath:
    """SAD may declare a module as ``src/X.py`` while the file actually
    lives at ``03-development/src/X.py`` (the harness-scaffolded layout —
    see ``project_cmds.py:_init_phase_dirs``). generate_sab.py must rewrite
    the path so SAB.json always matches what's really on disk. Previously
    zero test coverage exercised this branch."""

    def test_rewrites_to_03_development_when_only_dev_path_exists(self, tmp_path):
        _write_sad(tmp_path, _VALID_SAB.replace(
            'modules: ["app.api"]', 'modules: ["src/app/api.py"]'
        ))
        dev_file = tmp_path / "03-development" / "src" / "app" / "api.py"
        dev_file.parent.mkdir(parents=True)
        dev_file.write_text("x = 1")
        result = _run_cli("--project", str(tmp_path))
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads((tmp_path / ".methodology" / "SAB.json").read_text())
        assert data["layers"][0]["modules"] == ["03-development/src/app/api.py"]

    def test_leaves_path_unchanged_when_it_already_exists_at_declared_location(self, tmp_path):
        _write_sad(tmp_path, _VALID_SAB.replace(
            'modules: ["app.api"]', 'modules: ["src/app/api.py"]'
        ))
        root_file = tmp_path / "src" / "app" / "api.py"
        root_file.parent.mkdir(parents=True)
        root_file.write_text("x = 1")
        result = _run_cli("--project", str(tmp_path))
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads((tmp_path / ".methodology" / "SAB.json").read_text())
        assert data["layers"][0]["modules"] == ["src/app/api.py"]

    def test_leaves_path_unchanged_when_neither_location_exists(self, tmp_path):
        _write_sad(tmp_path, _VALID_SAB.replace(
            'modules: ["app.api"]', 'modules: ["src/app/api.py"]'
        ))
        result = _run_cli("--project", str(tmp_path))
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads((tmp_path / ".methodology" / "SAB.json").read_text())
        assert data["layers"][0]["modules"] == ["src/app/api.py"]


class TestGenerateSabDictShapedModules:
    """Dict-shaped module entries ({"name": ..., "implemented_in": ...}) are
    the official schema form for a module whose logical name differs from
    its physical location (sab_parser.render_canonical_sab_template()).
    Pre-fix, the path-rewrite step's ``project / m`` raised TypeError on
    any dict entry, crashing generate_sab.py --overwrite at P3 ORCH-POST
    for every SAB.json using this documented form."""

    _DICT_SAB = """\
sab:
  version: "1.0"
  phase: 2
  project: "cli-test"
  layers:
    - name: interface
      modules:
        - name: "app.cli"
          implemented_in: "app.interface.cli"
      allowed_dependencies: []
  allowed_dependencies: []
  fr_module_traceability:
    FR-01: "app.interface.cli"
  required_artifacts: []  # Round 72 站5 — see _VALID_SAB above
"""

    def test_dotted_implemented_in_does_not_crash_and_round_trips(self, tmp_path):
        """The realistic case: implemented_in is a dotted name with no
        filesystem correspondence — same as this project's real SAB.json.
        Must not crash, and the dict entry is written back unchanged."""
        _write_sad(tmp_path, self._DICT_SAB)
        result = _run_cli("--project", str(tmp_path))
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads((tmp_path / ".methodology" / "SAB.json").read_text())
        assert data["layers"][0]["modules"] == [
            {"name": "app.cli", "implemented_in": "app.interface.cli"}
        ]

    def test_path_form_implemented_in_rewritten_to_03_development(self, tmp_path):
        """implemented_in may legitimately be a path-form string (see
        sab_amender's test_dict_implemented_in_path_form_normalises). When
        only the 03-development/ copy exists on disk, the dict entry must
        get the SAME rewrite treatment as an equivalent plain-string entry
        — this is the behavioral gap a naive "skip all dict entries" fix
        would silently leave unfixed."""
        _write_sad(tmp_path, self._DICT_SAB.replace(
            'implemented_in: "app.interface.cli"',
            'implemented_in: "src/app/cli.py"',
        ))
        dev_file = tmp_path / "03-development" / "src" / "app" / "cli.py"
        dev_file.parent.mkdir(parents=True)
        dev_file.write_text("x = 1")
        result = _run_cli("--project", str(tmp_path))
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads((tmp_path / ".methodology" / "SAB.json").read_text())
        assert data["layers"][0]["modules"] == [
            {"name": "app.cli", "implemented_in": "03-development/src/app/cli.py"}
        ]

    def test_mixed_dict_and_string_modules_both_handled(self, tmp_path):
        sab = self._DICT_SAB.replace(
            'modules:\n        - name: "app.cli"\n          implemented_in: "app.interface.cli"',
            'modules:\n        - name: "app.cli"\n          implemented_in: "app.interface.cli"\n        - "app.other"',
        )
        _write_sad(tmp_path, sab)
        result = _run_cli("--project", str(tmp_path))
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads((tmp_path / ".methodology" / "SAB.json").read_text())
        assert data["layers"][0]["modules"] == [
            {"name": "app.cli", "implemented_in": "app.interface.cli"},
            "app.other",
        ]

    def test_dict_with_no_usable_fields_does_not_crash(self, tmp_path):
        sab = self._DICT_SAB.replace(
            '- name: "app.cli"\n          implemented_in: "app.interface.cli"',
            '- foo: "bar"',
        )
        _write_sad(tmp_path, sab)
        result = _run_cli("--project", str(tmp_path))
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads((tmp_path / ".methodology" / "SAB.json").read_text())
        assert data["layers"][0]["modules"] == [{"foo": "bar"}]
