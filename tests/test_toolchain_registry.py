"""Toolchain registry — completeness invariant + python passthrough behavior.

R8 (harness/ssi/scripts/score.py) forbids a null tool_score for any dimension.
This suite enforces the registry-level counterpart: every language registered
in DIMENSION_TOOLS must resolve EVERY requires_tool_execution dimension of
every gate config to a registered, runnable ToolSpec. A partially wired
language cannot ship — adding "go": {...} with 13 of 14 dimensions fails here.
"""

import json
from pathlib import Path

import pytest
import yaml

from harness.toolchains import (
    DIMENSION_TOOLS,
    TOOL_SPECS,
    detect_language,
    detect_test_runner,
    get_project_language,
    get_project_test_runner,
    get_tool_spec,
    resolve_tool_id,
    supported_languages,
)
from harness.tool_runners import compute_tool_score, run_tool

REPO_ROOT = Path(__file__).parent.parent
GATE_CONFIG_DIR = REPO_ROOT / "harness" / "gate_configs"


def _load_gate_dimensions():
    """[(gate_file, dim_dict)] for every requires_tool_execution dimension."""
    out = []
    for cfg_path in sorted(GATE_CONFIG_DIR.glob("gate*.yaml")):
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        for dim in cfg.get("dimensions", []):
            if dim.get("requires_tool_execution", False):
                out.append((cfg_path.name, dim))
    return out


GATE_DIMS = _load_gate_dimensions()


def _all_gate_dimension_names() -> set:
    """Every dimension name any gate declares, tool-scored or framework-owned.

    GATE_DIMS above keeps only requires_tool_execution ones, which is right for
    the R8 wiring checks but wrong as a denominator for "does the roster agree"
    (Round 27 站4) — traceability and adversarial_review would drop out of the
    comparison entirely and the check would have nothing to say about them.
    """
    names = set()
    for cfg_path in sorted(GATE_CONFIG_DIR.glob("gate*.yaml")):
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        names.update(d["name"] for d in cfg.get("dimensions", []))
    return names


class TestRegistryCompleteness:
    def test_gate_configs_found(self):
        assert len(list(GATE_CONFIG_DIR.glob("gate*.yaml"))) == 4
        assert GATE_DIMS, "no tool-scored dimensions found in gate configs"

    @pytest.mark.parametrize("language", sorted(DIMENSION_TOOLS))
    def test_language_covers_every_gate_dimension(self, language):
        """Every tool-scored gate dimension must resolve to a ToolSpec (R8)."""
        missing = []
        for cfg_name, dim in GATE_DIMS:
            dim_name = dim["name"]
            if language == "python":
                tool_ids = [dim.get("tool")]
            else:
                entry = DIMENSION_TOOLS[language].get(dim_name)
                if entry is None:
                    missing.append(f"{cfg_name}:{dim_name} — no DIMENSION_TOOLS entry")
                    continue
                if isinstance(entry, dict):
                    assert "default" in entry, (
                        f"{language}/{dim_name}: runner-variant entry needs a "
                        f"'default' key"
                    )
                    tool_ids = list(entry.values())
                else:
                    tool_ids = [entry]
            for tool_id in tool_ids:
                spec = get_tool_spec(tool_id)
                if spec is None:
                    missing.append(f"{cfg_name}:{dim_name} — tool '{tool_id}' unregistered")
                    continue
                assert spec.check_cmd.strip(), f"{tool_id}: empty check_cmd"
                assert spec.cmd or spec.in_process or spec.skip_inline, (
                    f"{tool_id}: not runnable (no cmd, not in-process, not skip-list)"
                )
        assert not missing, (
            f"language '{language}' is partially wired (R8 violation):\n  "
            + "\n  ".join(missing)
        )

    def test_python_dimension_tools_match_gate_yaml(self):
        """DIMENSION_TOOLS['python'] must agree with the gate YAML tool fields."""
        for cfg_name, dim in GATE_DIMS:
            expected = DIMENSION_TOOLS["python"].get(dim["name"])
            assert expected == dim.get("tool"), (
                f"{cfg_name}:{dim['name']} — registry says {expected!r}, "
                f"YAML says {dim.get('tool')!r}"
            )

    def test_all_tool_ids_match_spec_keys(self):
        for tool_id, spec in TOOL_SPECS.items():
            assert spec.tool_id == tool_id

    # Dimensions the framework computes for itself at finalize time, so the
    # agent-facing prompt has nothing to tell an agent about them.
    # `adversarial_review` is also framework-owned but keeps a section saying
    # exactly that, which is why it is not listed here.
    _FRAMEWORK_ONLY_DIMENSIONS = {"traceability"}

    def test_the_three_dimension_registries_agree(self):
        """Round 27 站4 — gate YAML, DIMENSION_TOOLS and evaluate_dimension.md.

        The two above already reconcile the first two. The third — the prompt's
        `### <dimension>` sections — had no check at all, and was missing
        architecture_constraints, execute_verification_target and
        integration_coverage: three dimensions the gates score and no agent was
        ever told how to score.

        That gap had already been consumed. Commit 40bedac added a Phase-1 step
        validating each NFR's declared `dimension:` against this file's headers,
        so taskq-plus's `dimension: architecture_constraints` — a name gate 1
        scores at weight 0.25 — was about to be reported to its author as a
        dimension that does not exist. A check is only as good as the roster it
        reads.
        """
        import re

        md = (Path(__file__).resolve().parent.parent / "harness" / "ssi" /
              "prompts" / "evaluate_dimension.md").read_text(encoding="utf-8")
        documented = set(re.findall(r"^### ([a-z_]+)", md, re.M))
        in_yaml = _all_gate_dimension_names()
        in_registry = set(DIMENSION_TOOLS["python"])

        assert not in_registry - documented, (
            "dimensions the registry scores with no section in "
            f"evaluate_dimension.md: {sorted(in_registry - documented)} — an "
            "agent asked to score them has no instructions, and Phase 1's "
            "dimension validation will call them nonexistent"
        )
        assert (in_yaml - documented) <= self._FRAMEWORK_ONLY_DIMENSIONS, (
            "gate dimensions with no section in evaluate_dimension.md: "
            f"{sorted((in_yaml - documented) - self._FRAMEWORK_ONLY_DIMENSIONS)}"
        )
        assert not documented - in_yaml, (
            "evaluate_dimension.md documents dimensions no gate scores: "
            f"{sorted(documented - in_yaml)}"
        )

    def test_framework_only_dimensions_really_are_framework_only(self):
        """The allowlist above is an exemption, so it must be earned: anything
        on it must be a dimension no agent is asked to run a tool for."""
        for _cfg, dim in GATE_DIMS:
            if dim["name"] in self._FRAMEWORK_ONLY_DIMENSIONS:
                assert not dim.get("requires_tool_execution", False), (
                    f"{dim['name']} is exempted from needing prompt instructions "
                    f"but requires_tool_execution is true — an agent IS asked to "
                    f"run a tool for it"
                )


class TestResolution:
    def test_python_passes_yaml_tool_through(self):
        assert resolve_tool_id("linting", "python", yaml_tool="ruff") == "ruff"
        # YAML absence passes through too — callers keep legacy None handling
        assert resolve_tool_id("linting", "python", yaml_tool=None) is None
        # Even an unregistered YAML tool passes through (legacy contract)
        assert resolve_tool_id("custom", "python", yaml_tool="my-tool") == "my-tool"

    def test_unregistered_language_resolves_none(self):
        assert resolve_tool_id("linting", "go", yaml_tool="ruff") is None

    def test_supported_languages_mirror_dimension_tools(self):
        assert set(supported_languages()) == set(DIMENSION_TOOLS)
        assert "python" in supported_languages()


class TestRunToolRegistryBehavior:
    """run_tool/compute_tool_score behavior preserved after registry refactor."""

    def test_skip_list_tools_return_minus_one(self, tmp_path):
        # mutmut was previously skip_inline=True; commit 631782b activated it (skip_inline=False)
        assert run_tool("scancode", str(tmp_path)) == ("", -1)
        assert run_tool("code-review-graph", str(tmp_path)) == ("", -1)

    def test_unknown_tool_returns_minus_one(self, tmp_path):
        assert run_tool("definitely-not-a-tool", str(tmp_path)) == ("", -1)

    def test_in_process_scanner_runs(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text(
            "def test_ok():\n    assert 1 == 1\n", encoding="utf-8"
        )
        out, rc = run_tool("ast-assertions", str(tmp_path))
        assert rc == 0
        assert json.loads(out)["total"] == 1

    def test_compute_score_resolves_scorer_via_registry(self):
        assert compute_tool_score("ruff", "[]", 0) == 100.0
        assert compute_tool_score("unknown-tool", "whatever", 0) is None
        # Negative harness-internal codes never score
        assert compute_tool_score("ruff", "", -1) is None


class TestLanguageDetection:
    def test_tsconfig_wins_over_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
        assert detect_language(tmp_path) == "typescript"

    def test_package_json_alone_is_javascript(self, tmp_path):
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        assert detect_language(tmp_path) == "javascript"

    def test_pyproject_is_python(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
        assert detect_language(tmp_path) == "python"

    def test_mixed_manifests_are_ambiguous(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        assert detect_language(tmp_path) is None

    def test_no_manifest_defaults_to_python(self, tmp_path):
        assert detect_language(tmp_path) == "python"

    def test_runner_detection(self, tmp_path):
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({"devDependencies": {"vitest": "^3.0.0"}}),
                       encoding="utf-8")
        assert detect_test_runner(tmp_path) == "vitest"
        pkg.write_text(json.dumps({"devDependencies": {"jest": "^29.0.0"}}),
                       encoding="utf-8")
        assert detect_test_runner(tmp_path) == "jest"
        pkg.write_text(json.dumps(
            {"devDependencies": {"vitest": "^3.0.0", "jest": "^29.0.0"}}),
            encoding="utf-8")
        assert detect_test_runner(tmp_path) is None  # ambiguous
        pkg.write_text("{}", encoding="utf-8")
        assert detect_test_runner(tmp_path) is None

    def test_runner_detection_via_scripts(self, tmp_path):
        # Script-based detection: a project with no devDependencies but a
        # `test: vitest run` script must still detect as vitest (and jest
        # likewise). Exhaustive coverage of the pkg.get("scripts") branch.
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({"scripts": {"test": "vitest run"}}),
                       encoding="utf-8")
        assert detect_test_runner(tmp_path) == "vitest"
        pkg.write_text(json.dumps({"scripts": {"test": "jest"}}),
                       encoding="utf-8")
        assert detect_test_runner(tmp_path) == "jest"
        # Script alone, both vitest and jest in same string → ambiguous.
        pkg.write_text(json.dumps(
            {"scripts": {"test": "jest -- && vitest run"}}),
            encoding="utf-8")
        assert detect_test_runner(tmp_path) is None
        # Non-string script values are ignored (no crash).
        pkg.write_text(json.dumps({"scripts": {"test": ["vitest"]}}),
                       encoding="utf-8")
        assert detect_test_runner(tmp_path) is None

    def test_state_json_language_roundtrip(self, tmp_path):
        meth = tmp_path / ".methodology"
        meth.mkdir()
        assert get_project_language(tmp_path) == "python"  # pre-v2.8 default
        (meth / "state.json").write_text(
            json.dumps({"state": "RUNNING", "language": "typescript",
                        "test_runner": "vitest"}),
            encoding="utf-8",
        )
        assert get_project_language(tmp_path) == "typescript"
        assert get_project_test_runner(tmp_path) == "vitest"

    def test_corrupt_state_json_defaults_to_python(self, tmp_path):
        meth = tmp_path / ".methodology"
        meth.mkdir()
        (meth / "state.json").write_text("{not json", encoding="utf-8")
        assert get_project_language(tmp_path) == "python"
        assert get_project_test_runner(tmp_path) is None


class TestCheckToolForDim:
    def test_unsupported_language_blocks_with_clear_message(self):
        from harness.tool_checks import check_tool_for_dim
        ok, diag = check_tool_for_dim("linting", "ruff", "go")
        assert ok is False
        assert "no 'go' toolchain entry" in diag

    def test_python_unknown_tool_falls_back_to_dim_table(self):
        from harness.tool_checks import check_tool_for_dim
        # 'harness-trace' has no ToolSpec and 'traceability' no fallback entry
        # → no tool requirement (legacy behavior preserved)
        ok, diag = check_tool_for_dim("traceability", "harness-trace")
        assert ok is True and diag == ""
