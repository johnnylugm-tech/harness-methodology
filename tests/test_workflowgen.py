"""Unit tests for scripts/workflowgen/ — the shared JS block renderers and
the per-phase assembly layer.
"""
from __future__ import annotations

import re

from scripts.workflowgen import js_blocks as B
from scripts.workflowgen import phase_specs
from scripts.workflowgen.generate_workflows import GENERATORS, generate


class TestSchemaRenderer:
    def test_render_schemas_selects_only_requested_names(self):
        text = B.render_schemas(["VERDICT_SCHEMA", "RC_SCHEMA"])
        assert "const VERDICT_SCHEMA" in text
        assert "const RC_SCHEMA" in text
        assert "const CTX_SCHEMA" not in text
        assert "const DELTA_FAST_SCHEMA" not in text
        assert "const PHASE_SCHEMA" not in text

    def test_render_schemas_all_five_available(self):
        text = B.render_schemas([
            "VERDICT_SCHEMA", "RC_SCHEMA", "CTX_SCHEMA",
            "DELTA_FAST_SCHEMA", "PHASE_SCHEMA",
        ])
        for name in ("VERDICT_SCHEMA", "RC_SCHEMA", "CTX_SCHEMA", "DELTA_FAST_SCHEMA", "PHASE_SCHEMA"):
            assert f"const {name}" in text


class TestPhaseHeader:
    def test_render_phase_header_includes_phase_call(self):
        text = B.render_phase_header("Env Check")
        assert "phase('Env Check')" in text

    def test_render_phase_header_is_boxed_comment(self):
        text = B.render_phase_header("Sync")
        assert "// Phase: Sync" in text


class TestEntryPreflight:
    def test_includes_gate_and_phase_numbers(self):
        text = B.render_entry_preflight(phase=8, gate_num=4, gate_owner_phase=6, prev_phase=7)
        assert "PHASE-8 PREFLIGHT ORCHESTRATOR" in text
        assert "gate4" in text
        assert "--phase 8" in text
        assert "--from-phase 7" in text
        assert "return to Phase 6" in text

    def test_extra_note_is_included(self):
        text = B.render_entry_preflight(
            phase=5, gate_num=3, gate_owner_phase=4, prev_phase=4,
            extra_note="- DO NOT generate BASELINE docs.\\n",
        )
        assert "DO NOT generate BASELINE docs" in text


class TestManifestIntegrity:
    def test_defines_function_and_first_call(self):
        text = B.render_manifest_integrity_phase(phase=8)
        assert "async function checkManifestIntegrity" in text
        assert "checkManifestIntegrity('Manifest Integrity', 'manifest-integrity')" in text
        assert "--phase 8" in text


class TestArtifactsCommit:
    def test_path_allowlist_rendered_explicitly(self):
        text = B.render_artifacts_commit(
            paths=["05-verification", ".methodology"],
            commit_msg="chore(p5): baseline artifacts",
            phase=5,
        )
        assert "git -C ' + REPO + ' add 05-verification .methodology" in text
        assert "chore(p5): baseline artifacts" in text
        assert "git add -A" not in text
        assert "|| true" in text


class TestRenderJsonUtils:
    def test_strips_export_keyword(self):
        text = B.render_json_utils()
        assert "export" not in text

    def test_preserves_all_three_functions(self):
        text = B.render_json_utils()
        for fn in ("balancedJsonAt", "extractLastJson", "parseAgentJson"):
            assert f"function {fn}" in text


class TestPhase8Generation:
    def test_generate_phase8_is_syntactically_plausible_js(self):
        text = phase_specs.generate_phase8()
        assert text.startswith("// Phase 8")
        assert "export const meta = {" in text
        assert text.count("export const meta") == 1

    def test_meta_is_first_statement_after_header_comments(self):
        """Runtime hard-error: meta must be the first statement (playbook
        §3) — only `//` comment lines may precede it."""
        text = phase_specs.generate_phase8()
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            assert stripped.startswith("export const meta"), (
                f"first non-comment, non-blank line must start the meta "
                f"object, got: {stripped!r}"
            )
            break

    def test_meta_phases_list_matches_declared_titles(self):
        text = phase_specs.generate_phase8()
        m = re.search(r"phases:\s*\[(.*?)\n  \],", text, re.DOTALL)
        assert m is not None
        titles = re.findall(r"title:\s*'([^']*)'", m.group(1))
        assert titles == [
            "Entry & Preflight", "Env Check", "Manifest Integrity", "Load FRs",
            "Per-FR Delta", "Config Docs", "Artifacts Commit", "Archive",
            "Final Push", "Sync",
        ]

    def test_no_forbidden_runtime_apis(self):
        """Playbook §4 hard/warning bans — checked here at the unit level
        against actual code (comment lines stripped first, since the
        RESOLVE_REPO_BLOCK legitimately documents why process.env can't be
        read, in prose). tests/test_workflow_js_conventions.py (station5)
        applies the same scan to every file under .claude/workflows/."""
        text = phase_specs.generate_phase8()
        code_only = "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("//")
        )
        for banned in ("import(", "require(", "process.env", "Date.now()", "Math.random()"):
            assert banned not in code_only, f"forbidden runtime API {banned!r} found in generated code"


class TestGenerateFacade:
    def test_generate_dispatches_to_registered_generator(self):
        assert generate(8) == phase_specs.generate_phase8()

    def test_unmigrated_phase_raises_key_error(self):
        import pytest

        with pytest.raises(KeyError):
            generate(1)

    def test_generators_registry_has_expected_filenames(self):
        assert GENERATORS[8][1] == "phase8-config.js"
