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
    def test_defines_the_helper_without_calling_it(self):
        # Round 22 站2: the renderer emits the helper only. Its former entry
        # call re-ran PREFLIGHT_CHECKS[0], which the run-phase in the previous
        # phase box had just executed, and its Advance-loop call moved into
        # advance-phase itself (cli/phase_cmds.py::_advance_prechecks).
        text = B.render_manifest_integrity_fn(phase=8)
        assert "async function checkManifestIntegrity" in text
        assert "--phase 8" in text
        assert "await checkManifestIntegrity(" not in text
        assert "phase('Manifest Integrity')" not in text

    def test_only_the_two_phases_with_an_uncovered_call_site_define_it(self):
        # phase3's Gate-2 round loop and phase8's Final Push are the two call
        # sites advance-phase does not cover (a mid-loop fix can reintroduce
        # corruption before finalize-gate commits; Final Push is
        # push-milestone, not advance-phase). Everyone else dropped the helper.
        defines = {p for p in range(3, 9) if "checkManifestIntegrity" in generate(p)}
        assert defines == {3, 8}, f"unexpected set of phases defining the helper: {defines}"


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
        # Round 22 站2 removed the "Manifest Integrity" box: its entry call
        # duplicated PREFLIGHT_CHECKS[0], which run-phase had just executed in
        # the previous box. P8 still defines the helper (its Final Push, a
        # push-milestone path advance-phase does not cover, calls it) but the
        # helper is no longer a phase() of its own.
        assert titles == [
            "Entry & Preflight", "Env Check", "Load FRs",
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

        # Round 11 station4: all 8 phases are now migrated (GENERATORS covers
        # 1-8), so phase 1 is no longer a valid "unmigrated" example — use a
        # phase number outside the methodology's fixed 1-8 range instead.
        with pytest.raises(KeyError):
            generate(9)

    def test_generators_registry_has_expected_filenames(self):
        assert GENERATORS[8][1] == "phase8-config.js"


class TestOrchPostIsPerPhaseNotPerFr:
    """Round 22 站1 — `amend-sab` must be dispatched once per phase.

    It takes no `--fr-id` (cli/project_cmds.py::cmd_amend_sab) and is
    idempotent by construction (core/quality_gate/sab_amender.amend_sab), so
    the N-1 repeats an N-FR phase used to pay for were re-asking the same
    project-wide question. taskq's 5-FR run left 35 `tool:amend-sab` rows in
    sessions_spawn.log.
    """

    # P3 is deliberately excluded: its amend-sab runs inside the per-FR TDD
    # orchestrator prompt, BEFORE that FR's own GATE1, so the Architecture
    # Amendment Protocol sees the module the FR just wrote. That one is not
    # a repeat — it is the point.
    FR_LOOP_PHASES = (4, 5, 7, 8)

    def test_amend_sab_appears_once_per_generated_workflow(self):
        for phase in self.FR_LOOP_PHASES:
            text = generate(phase)
            assert text.count("amend-sab") == 1, (
                f"phase{phase} generates {text.count('amend-sab')} amend-sab "
                f"mentions; it is project-wide and idempotent, so exactly one "
                f"belongs in the whole workflow"
            )

    def test_orch_post_label_is_not_fr_scoped(self):
        for phase in self.FR_LOOP_PHASES:
            text = generate(phase)
            assert "label: 'orch-post'" in text
            assert "'orch-post-' +" not in text, (
                f"phase{phase} still builds a per-FR orch-post label — that "
                f"shape is one dispatch per FR"
            )

    def test_spec_coverage_still_runs_per_fr(self):
        # The dispatch count dropped; the information must not. The single
        # agent iterates gate1Pass in a bash loop.
        for phase in self.FR_LOOP_PHASES:
            text = generate(phase)
            assert "for FR in ' + gate1Pass.join(' ') + '" in text, (
                f"phase{phase} lost the per-FR spec-coverage loop — collapsing "
                f"the dispatch must not collapse the coverage checks"
            )
            assert "--fr-id $FR" in text


class TestPollBackoff:
    """Round 22 站4 — the first poll interval must not be the long one.

    Both background-poll sites used a flat first sleep sized for their worst
    case. Since Round 20 station1, `run-env-check` returns in about a second
    whenever env_contract.json is current (source docs unchanged -> the CLI
    verifies deterministically and spawns no sub-agent), and an unchanged FR
    hits GATE1-DELTA's in-CLI short-circuit just as fast. The flat interval
    made every phase wait a full minute, and the per-FR probe 30s x N, on
    commands that had already finished.
    """

    def test_env_check_poll_starts_short_and_backs_off(self):
        text = B.render_env_check(phase=5)
        assert "BACKOFF intervals, in seconds: 5, 10, 20, 30" in text
        assert "sleep <interval>" in text
        assert "sleep 60 &&" not in text, "flat 60s first poll is back"

    def test_delta_fastpath_poll_starts_short_and_backs_off(self):
        text = B.render_per_fr_delta(phase=7, forbidden_note="")
        assert "BACKOFF intervals, in seconds: 5, 10, then 30" in text
        assert "Cap 42 polls" in text
        assert "Cap 40 polls" not in text, "the pre-backoff fast-path cap is back"

    def test_the_full_per_fr_loop_keeps_its_flat_interval(self):
        # Deliberately NOT changed: that path can chain a full TDD cycle on
        # top of GATE1-DELTA's own retries, so it is genuinely long-running
        # and a short first sleep buys nothing.
        text = B.render_per_fr_delta(phase=7, forbidden_note="")
        assert "Poll every 30s" in text
        assert "Cap 60 polls" in text

    def test_every_generated_workflow_carries_the_backoff(self):
        for phase in (4, 5, 7, 8):
            text = generate(phase)
            assert "BACKOFF intervals" in text, f"phase{phase} lost the backoff instruction"
