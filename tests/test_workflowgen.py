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
        """Round 26: the pass-through property moved to `generate_raw`.

        `generate` is now raw + one post-emit step (the dispatch wrapper, injected
        in ONE place so all 118 call sites are decided together instead of in nine
        spec modules). Both halves are asserted, so a change to either the routing
        or the injection is visible here.
        """
        from scripts.workflowgen.generate_workflows import generate_raw

        raw = generate_raw(8)
        assert raw == phase_specs.generate_phase8()

        wrapped = generate(8)
        assert wrapped != raw
        assert "async function dispatch(" in wrapped
        # Every call site the raw text had is now routed through the wrapper, and
        # the only remaining raw call is the wrapper's own.
        assert wrapped.count("await dispatch(") == raw.count("await agent(")
        assert wrapped.count("await agent(") == 1

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


class TestNfrTypeLegalityInPhase1Checklist:
    """SRS.md's own NFR `type:` field (harness/templates/SRS.md §7) is parsed
    and validated nowhere else in the codebase, so Phase 1's own B-review
    checklist is the only gate that can catch an illegal value (e.g.
    `error_handling`, legal only as a `dimension:` name) before it gets
    locked into an approved, verbatim-transcribe SRS.md and only surfaces
    much later as a Phase 2 generate_sab.py --validate failure.
    """

    def test_srs_b_checklist_lists_full_nfr_type_vocabulary(self):
        from core.quality_gate.sab_parser import nfr_type_vocabulary_inline

        text = generate(1)
        for nfr_type in nfr_type_vocabulary_inline().split("/"):
            assert nfr_type in text, (
                f"srsBChecklist is missing NFR type {nfr_type!r} — Phase 1's "
                f"own B-review checklist must list the full sab_parser "
                f"vocabulary, not a stale subset"
            )

    def test_srs_b_checklist_has_type_legality_bullet(self):
        text = generate(1)
        assert "legal NFR-type vocabulary" in text
        assert "error_handling" in text, (
            "the bullet must call out that a semantically-plausible-but-"
            "illegal value like `error_handling` is still illegal as `type:`"
        )

    def test_every_generated_workflow_carries_the_backoff(self):
        for phase in (4, 5, 7, 8):
            text = generate(phase)
            assert "BACKOFF intervals" in text, f"phase{phase} lost the backoff instruction"


class TestFeatureFlagProse:
    """Round 36 — the workflow prose states a feature-flag default, so that
    statement is bound to the registry that decides it.

    47ec3fd flipped `_DEFAULTS["mutation_testing"]` False -> True and updated
    only the loader. The Gate 2/3/4 NOTE kept telling every orchestrator
    sub-agent the dimension was "disabled by default (mutation_testing=false)"
    — an instruction to write the opposite of the truth into a project's
    harness_config.json.
    """

    _FLAG_PHASES = (3, 4, 6)

    def test_note_states_the_live_default(self):
        from core.harness_config import _DEFAULTS

        enabled = _DEFAULTS["mutation_testing"]
        expect_word = "enabled" if enabled else "disabled"
        expect_kv = f"mutation_testing={'true' if enabled else 'false'}"
        for phase in self._FLAG_PHASES:
            text = generate(phase)
            assert "NOTE: mutation_testing is " in text, (
                f"phase{phase} lost the mutation_testing flag NOTE"
            )
            assert f"NOTE: mutation_testing is {expect_word} by default" in text, (
                f"phase{phase} tells the agent mutation_testing is "
                f"{'disabled' if enabled else 'enabled'} by default; "
                f"_DEFAULTS says {enabled}"
            )
            assert expect_kv in text, (
                f"phase{phase} NOTE does not spell the default as {expect_kv}"
            )

    # The one module allowed to spell the value: it is the renderer that
    # derives it from _DEFAULTS. Everything else must call the renderer.
    _RENDERER_MODULE = "spec_shared.py"

    def _spec_dir(self):
        from pathlib import Path

        return Path(__file__).resolve().parents[1] / "scripts" / "workflowgen"

    def test_no_spec_module_hand_writes_the_flag_default(self):
        """Completeness: the boolean is rendered from _DEFAULTS in one place.
        A fourth site hand-writing it is the drift class reopening."""
        offenders = [
            p.name for p in sorted(self._spec_dir().glob("spec_*.py"))
            if p.name != self._RENDERER_MODULE
            and ("mutation_testing=true" in p.read_text(encoding="utf-8")
                 or "mutation_testing=false" in p.read_text(encoding="utf-8"))
        ]
        assert not offenders, (
            f"{offenders} hand-write the mutation_testing default. Render it "
            f"from core.harness_config._DEFAULTS via "
            f"spec_shared.render_mutation_flag_note() instead — a literal "
            f"here is what 47ec3fd left stale in three files"
        )

    def test_the_exempt_module_is_the_one_that_renders_it(self):
        """The exemption above is granted to a renderer, not to a filename —
        if the renderer moves or is deleted, the exemption must not survive
        it as a free pass for hand-written values."""
        src = (self._spec_dir() / self._RENDERER_MODULE).read_text(encoding="utf-8")
        assert "def render_mutation_flag_note()" in src
        assert "_DEFAULTS" in src, (
            "the renderer no longer reads core.harness_config._DEFAULTS — it "
            "is a second hand-written copy again"
        )


class TestExcludedDimsRule:
    """Round 58 — the G2/G3/G4 orchestrator prompt tells the agent that
    off-list dimensions are out of scope this round, so a feature-flagged
    dim the gate excluded cannot be re-introduced by an LLM agent deciding
    to investigate an upstream bug.

    Measured on taskq-cc Gate 2 round 1: an agent confirmed in its own
    thinking that mutation_testing was excluded, then spent the rest of the
    round chasing a test_fr06 baseline failure because mutmut happens to
    hit it. The gate scoring excludes mutation_testing; the agent's
    responsibility is the dims on the list, not the ones the flag flipped
    out. The rule closes the loop — if a new disabled dim turns up (round
    59: a different feature flag, perhaps), the same rule covers it.
    """

    _RULE_PHASES = (3, 4, 6)  # gate 2 / gate 3 / gate 4 exit prompts

    def test_renderer_exists_and_returns_rule_text(self):
        from scripts.workflowgen.spec_shared import render_excluded_dims_rule

        text = render_excluded_dims_rule()
        assert "EXCLUDED DIMS" in text, "rule must lead with its name"
        assert "OUT OF SCOPE" in text, (
            "the rule must state the consequence (off-list = out of scope), "
            "so an LLM agent sees the consequence at the same offset as the "
            "render_mutation_flag_note line above it"
        )
        assert "Do NOT" in text or "do NOT" in text, (
            "the rule must name prohibited actions (do NOT evaluate / score / "
            "fix) so the instruction is a constraint, not a hint"
        )
        assert "harness_config.json" in text, (
            "the rule must name the knob so the agent knows where to flip if "
            "it disagrees with the exclusion"
        )
        # Convention check: helper returns escaped-newline-terminated text so
        # inlining it into a JS string literal via f-string concatenation
        # preserves formatting. If this trips, fix the renderer, not the test.
        assert text.endswith("\\n")

    def test_every_gate_prompt_carries_the_rule(self):
        from scripts.workflowgen.generate_workflows import generate

        for phase in self._RULE_PHASES:
            text = generate(phase)
            assert "EXCLUDED DIMS" in text, (
                f"phase{phase} gate prompt lost the EXCLUDED DIMS rule — "
                f"an LLM agent will once again be free to investigate dims "
                f"the feature flag removed, repeating the taskq-cc G2 r1 "
                f"waste pattern that this rule was added to fix"
            )

    # The one module allowed to spell the rule: it is the renderer. Everything
    # else must call the renderer via f-string concat (see spec_phase3/4/6 —
    # an inline hand-written copy is the drift class reopening).
    _RENDERER_MODULE = "spec_shared.py"

    def _spec_dir(self):
        from pathlib import Path

        return Path(__file__).resolve().parents[1] / "scripts" / "workflowgen"

    def test_no_spec_phase_module_hand_writes_the_rule(self):
        offenders = [
            p.name for p in sorted(self._spec_dir().glob("spec_phase*.py"))
            if "EXCLUDED DIMS" in p.read_text(encoding="utf-8")
        ]
        assert not offenders, (
            f"{offenders} hand-write the EXCLUDED DIMS rule. Render it from "
            f"spec_shared.render_excluded_dims_rule() instead — a literal "
            f"in a phase spec is what 47ec3fd left stale across the three "
            f"gate prompts, and the same pattern applies here"
        )

    def test_the_renderer_module_is_the_one_that_exports_it(self):
        src = (self._spec_dir() / self._RENDERER_MODULE).read_text(encoding="utf-8")
        assert "def render_excluded_dims_rule()" in src, (
            "the renderer was deleted from spec_shared.py — if the gate "
            "prompts still reference it, regeneration will break; the rule "
            "must live alongside render_mutation_flag_note() and "
            "render_framework_owned_note() so the three shared prose "
            "renderers sit together"
        )


class TestNoPromptDescribesADisabledDimension:
    """Round 60 站0/站3 — the state these two renderers describe is gone.

    `render_mutation_flag_note` tells the orchestrator how to switch a
    dimension off; `render_excluded_dims_rule` tells it how to behave once one
    is. Round 60 removes the switch, so both are statements about a mechanism
    that no longer exists (Round 39's rule: removing a mechanism means
    removing its statements).

    The EXCLUDED-DIMS text carried two defects of its own that go with it: the
    prohibition "or fix code issues you discover while evaluating OTHER dims"
    is unqualified and sits one line above "For any failing dim: fix the ROOT
    CAUSE in code", and "The flag was flipped on purpose (e.g. to sidestep a
    wall-time budget)" attributes a motive the framework never recorded.
    """

    _DEAD_PHRASES = ("EXCLUDED DIMS", "mutation_testing is enabled by default")

    def test_no_generated_workflow_mentions_a_disabled_dimension(self):
        from scripts.workflowgen.generate_workflows import GENERATORS, generate

        for phase in sorted(GENERATORS):
            text = generate(phase)
            for phrase in self._DEAD_PHRASES:
                assert phrase not in text, (
                    f"phase{phase} still tells the orchestrator about a "
                    f"dimension it may switch off: {phrase!r}"
                )

    def test_the_renderers_are_gone_from_the_shared_module(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "scripts" / "workflowgen"
               / "spec_shared.py").read_text(encoding="utf-8")
        for name in ("render_excluded_dims_rule", "render_mutation_flag_note"):
            assert f"def {name}(" not in src, (
                f"{name} describes a mechanism Round 60 removed"
            )
