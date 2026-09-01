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


class TestPreviewNextPhase:
    """Fix B (Round 15 §2's `preview-next-phase` was never wired into any
    phase workflow): a read-only carry-over-obligation check + bounded
    3-round fixer, inserted before each phase's own Push/Advance step so a
    `_DELAYED_BLOCKING_PREFLIGHTS` finding surfaces inside that phase's own
    loop instead of only at its `advance-phase` exit gate. See
    scripts/workflowgen/artifact_limits.py's RUNALL_MAX_BYTES entry for the
    measured taskq-api incident this closes.
    """

    def test_checker_uses_schema_not_prose_regex(self):
        # sim_runner's generic happy-path responder synthesizes a passing
        # object for any schema call but only a fixed narrative string for a
        # schema-less one — a plain-text regex on that string never matches,
        # which is exactly the false-halt round50's sim caught before ship.
        text = B.render_preview_next_phase(2)
        assert "schema: VERDICT_SCHEMA" in text
        assert "PHASE-2 PRE-PUSH OBLIGATION CHECKER" in text
        assert "--phase 2" in text
        assert "Phase 3 entry" in text

    def test_fixer_forbids_harness_edits_and_fabricated_cases(self):
        text = B.render_preview_next_phase(2)
        assert "NOT harness/ (HR-17)" in text
        assert "Never fabricate a case to force a citation" in text
        assert "NOT phase-transition" in text

    def test_bounded_retry_then_escalate(self):
        text = B.render_preview_next_phase(2)
        assert "MAX_PREVIEW_FIX_ROUNDS = 3" in text
        assert "halt('preview-next-phase'" in text

    def test_present_before_advance_in_every_phase(self):
        # Every phase's own Advance-triggering step ("Advance", "Tag &
        # Advance", "Final Push") must come AFTER "Preview Next-Phase" in
        # both the meta phases list and the actual generated dispatch order
        # — the whole point is catching the obligation before that step
        # commits/advances, not after.
        advance_titles = {
            1: "Advance", 2: "Advance", 3: "Advance", 4: "Advance",
            5: "Advance", 6: "Tag & Advance", 7: "Advance", 8: "Final Push",
        }
        for phase, advance_title in advance_titles.items():
            text = generate(phase)
            preview_pos = text.find("phase('Preview Next-Phase')")
            advance_pos = text.find(f"phase('{advance_title}')")
            assert preview_pos != -1, f"phase {phase}: no Preview Next-Phase step"
            assert advance_pos != -1, f"phase {phase}: no {advance_title!r} step"
            assert preview_pos < advance_pos, (
                f"phase {phase}: Preview Next-Phase must come before "
                f"{advance_title!r}, found at {preview_pos} >= {advance_pos}"
            )


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
            "Preview Next-Phase", "Final Push", "Sync",
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
        # the only remaining raw call is the wrapper's own. Round 79 站1: the
        # `+ 1` d01adf0e added here was room for getEnvFingerprint()'s own
        # internal dispatch. That helper is gone, and with it the exception —
        # the wrapper spends no dispatch of its own again.
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
            # Round 70 站3: the INFRA abort's return payload names amend-sab as
            # the operator's repair route. That line is a `return { ... }`, not
            # a prompt — no agent reads it, so it cannot cause the repeat
            # dispatch this guard exists to prevent. Excluded by the flag it
            # carries rather than by loosening the count.
            lines = [
                ln for ln in generate(phase).splitlines()
                if "infra_abort: true" not in ln
            ]
            mentions = "\n".join(lines).count("amend-sab")
            assert mentions == 1, (
                f"phase{phase} generates {mentions} amend-sab dispatch "
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
        assert "BACKOFF intervals, in seconds: 5, 10, 20, 30, 60" in text
        assert "sleep <interval>" in text

    def test_both_per_fr_poll_sites_read_the_cap_rather_than_naming_one(self):
        """Round 85 站2 withdraws `test_the_full_per_fr_loop_keeps_its_flat_interval`.

        That test asserted `Poll every 30s` / `Cap 60 polls` on the full
        per-FR loop and its comment called the flat interval deliberate —
        "that path can chain a full TDD cycle on top of GATE1-DELTA's own
        retries, so it is genuinely long-running and a short first sleep buys
        nothing". The first half stayed true and the second did not: the same
        path also runs for FRs whose code is unchanged, which short-circuit
        inside the CLI, and Round 22 站4 had already measured that cost on the
        sibling probe. Both sites now carry the same backoff.

        The literal caps went with it, which is this round's actual subject.
        42 and 60 polls were 1215s and 1800s against a worst case the
        generator's own prose put at 2400s — and the real figure is higher
        still, because each fix round spawns the fixer AND re-dispatches a
        full GATE1. A cap that must cover `values.timeouts.fr_step` x
        `values.max_fix_rounds`, both of which a project can override per FR,
        cannot be a number typed into a prompt.
        """
        # Counted, not just present: the delta renderer has two poll sites (the
        # batched fast probe and the full per-FR loop) and phase3 has one. A
        # literal typed back into any of them removes its placeholder, so the
        # count is what makes this a scan rather than a spot check.
        for label, text, sites in (
            ("render_per_fr_delta",
             B.render_per_fr_delta(phase=7, forbidden_note=""), 2),
            ("phase3", generate(3), 1),
        ):
            assert text.count("Cap `fr_step_poll_cap` polls") == sites, label
            assert text.count("`fr_step_poll_interval_s`") == sites, label
            assert "Poll every 30s" not in text, label


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


class TestSessionBlockGuard:
    """Round 63 — the `length < 10` magic number misclassified the 9-char
    `SAB: PASS` reply from the sab-generation agent as a session-limit block
    on taskq-cc 2026-08-19 (workflow wf_018138d9-78c). The guard must check
    only true empty payloads (null / undefined / '' / non-string), never a
    short PASS string.
    """

    def test_helper_does_not_emit_length_magic_number(self):
        from scripts.workflowgen.spec_shared import render_session_block_guard

        js = render_session_block_guard(
            'sabReport', 'sab-generation', 2,
            message='Agent hit session/rate limit during sab-generation. Resume after quota reset — state.json is untouched.',
        )
        assert 'length < 10' not in js, (
            '< 10 magic number re-introduced in render_session_block_guard — '
            '9-char PASS strings will be misclassified as session-limit blocks'
        )
        assert "=== ''" in js
        assert "typeof" in js and "!== 'string'" in js
        assert 'session_limit_blocked: true' in js
        assert 'step: \'sab-generation\'' in js
        assert 'phase: 2' in js

    def test_helper_emits_extra_fields_inside_return_object(self):
        from scripts.workflowgen.spec_shared import render_session_block_guard

        js = render_session_block_guard(
            'frReport', 'FR-01', 3,
            extra_fields='fr_id: frId, gate1Pass',
            message='Agent hit session/rate limit during FR-01 TDD. Resume after quota reset.',
        )
        assert 'fr_id: frId, gate1Pass' in js, (
            'extra_fields not interpolated into the returned object literal'
        )
        assert 'step: \'FR-01\'' in js

    def test_no_generated_workflow_contains_length_lt_10(self):
        """The bug — the literal token sequence `length < 10` in a generated
        JS workflow will re-introduce the misclassification. Pin the absence.
        `length < 100` is a different check (review-reason min-length) and
        out of scope for this regression."""
        from scripts.workflowgen.generate_workflows import GENERATORS, generate
        import re

        pattern = re.compile(r'\blength\s*<\s*10\b')
        for phase in sorted(GENERATORS):
            text = generate(phase)
            assert pattern.search(text) is None, (
                f'phase{phase} generated workflow still contains the magic '
                f'number `length < 10` — short PASS strings will be '
                f'misclassified as session-limit blocks on resume'
            )

    def test_the_guard_has_one_producer_and_two_named_exceptions(self):
        """Round 64 站0 — the comment above the helper said eleven sites
        "all call this helper now". Measured: ten do. `spec_phase3.py` and
        two sites in `js_blocks.py` still hand-write the same JS, and the
        comment does not mention the `js_blocks.py` ones at all — so the
        next edit to the wording reaches ten of thirteen copies.

        Two sites legitimately do not return the helper's shape:
          - `spec_runall.py` — the driver READS a phase's outcome and
            re-emits its own, one level up;
          - the gate loop in `js_blocks.py` — sets a flag and `break`s out
            of the retry loop instead of returning from the dispatch site.

        Pinning the per-file count rather than the file names keeps those
        two honest: a third hand-written copy in either file is a failure.
        """
        from pathlib import Path

        wfgen = Path(__file__).resolve().parents[1] / "scripts" / "workflowgen"
        expected = {
            "spec_shared.py": 1,   # render_session_block_guard itself
            "spec_runall.py": 1,   # the driver re-emits its own, one level up
            "js_blocks.py": 1,     # the gate loop's flag-and-break shape
        }
        found = {}
        for path in sorted(wfgen.glob("*.py")):
            # The emitted JS literal, not the identifier: prose that names the
            # mechanism is not a second producer of it.
            count = path.read_text(encoding="utf-8").count("session_limit_blocked: true")
            if count:
                found[path.name] = count
        assert found == expected, (
            f"session_limit_blocked is written in {found}, expected {expected} "
            f"— every other site must go through render_session_block_guard"
        )


class TestDeferredFixesStepAllGates:
    """Regression guard: Gate 2's and Gate 4's on_fail_error_msg both promise
    "write deferred_fixes.md" on exhausted-retries FAIL, but only Gate 3 ever
    passed a `deferred_fixes_step` into `render_gate_loop`. Confirmed on a
    real taskq-verify Gate 2 halt (2026-08-31): the message named the file,
    advance-phase's deferred-fix check treats a missing file as nothing to
    enforce, and Stage-5 debt-closure silently never engaged. All three gates
    must write the file when they exhaust their round loop."""

    def test_render_deferred_fixes_step_is_gate_agnostic(self):
        for gate_num, phase, d4 in ((2, 3, 60.0), (3, 4, 60.0), (4, 6, 90.0)):
            text = B.render_deferred_fixes_step(
                gate_num=gate_num, phase=phase, d4_threshold=d4,
            )
            assert "deferred_fixes.md" in text
            assert f"Gate {gate_num}" in text
            assert f"deferred-fixes-g{gate_num}" in text
            assert str(d4) in text

    def test_all_three_gate_phases_write_deferred_fixes_on_exhaustion(self):
        for phase in (3, 4, 6):
            text = generate(phase)
            assert "DEFERRED-FIX RECORDER" in text, (
                f"phase {phase}'s generated workflow has no deferred-fix "
                f"recorder — its on_fail_error_msg still promises a file "
                f"render_gate_loop never wires a writer for"
            )
            assert ".methodology/deferred_fixes.md` with" in text

    def test_runall_inlines_all_three_gates_deferred_fixes_writers(self):
        from scripts.workflowgen.spec_runall import generate_runall
        text = generate_runall()
        for gate_num in (2, 3, 4):
            assert f"deferred-fixes-g{gate_num}" in text, (
                f"run-all.js is missing gate {gate_num}'s deferred-fix "
                f"recorder — a phase file and the composite have drifted"
            )


class TestGateExhaustionOwnerThreading:
    """Regression guard for Round 79 站3-4.

    A gate-exhaustion halt ("Gate N did not PASS in 3 rounds") is always
    PROJECT-owned — verify-gate's own manifest-based PASS/FAIL judgement is
    the only way `render_gate_loop`'s halt can fire, and a genuine dispatch
    crash never reaches it (run-all.js's outer try/catch intercepts that
    first as 'workflow-crash'). `core.fault_owner._TEXT_RULES` used to guess
    this from prose and misfired on two unrelated halts (see
    tests/test_fault_owner.py::test_advance_and_deliverable_exhaustion_stay_unknown).
    The fix instead has `render_gate_loop` state the owner directly and
    run-all.js's driver forward it — these tests pin both halves so neither
    can silently drift back to text-guessing."""

    def test_all_three_gate_phases_name_project_on_exhaustion(self):
        for phase in (3, 4, 6):
            text = generate(phase)
            assert "owner: 'project'" in text, (
                f"phase {phase}'s generated gate-exhaustion halt no longer "
                f"states owner: 'project' — render_gate_loop's halt() call "
                f"has drifted from the Round 79 站3-4 fix"
            )

    def test_the_per_fr_gate1_halt_names_project_too(self):
        """Round 85 站2 — the one gate halt Round 79 站3-4 did not reach.

        `render_gate_loop`'s halt got `owner: 'project'`; the per-FR Gate 1
        halt (`spec_phase3` and `render_per_fr_delta`) kept none, so Round 85
        站1 tried to classify it from its text and answered INFRA. It has the
        same justification as its sibling and now says so at the producer:
        `passed` comes only from `verify_gate1_qc.py` reading the project's
        manifest, and the two paths that could reach the halt without a
        manifest verdict — a poll-cap kill and a rate-limited verifier — exit
        before it (`render_fr_step_timeout_exit`, `render_session_block_guard`).
        """
        for phase in (3, 4, 5, 7, 8):
            text = generate(phase)
            assert (f"halt('gate1', {{ error: 'Phase {phase}: Gate 1 FAILED "
                    f"for FR(s): '") in text, phase
            gate1_halt = text[text.index(f"halt('gate1', {{ error: 'Phase {phase}"):]
            assert "owner: 'project'" in gate1_halt.split("})")[0], (
                f"phase {phase}'s per-FR Gate 1 halt no longer states "
                f"owner: 'project' — without it the halt falls back to the "
                f"text classifier, which is what Round 85 站1 got wrong"
            )

    def test_a_killed_step_exits_before_the_gate1_halt_can_claim_it(self):
        """The `owner: 'project'` above is only true because these two exist.

        A run killed at the poll cap reports rc -1 and leaves no manifest
        entry; a rate-limited verifier returns null and leaves an empty
        reason. Both used to land in `gate1Fail` and would now be stamped
        `project`. Each phase that runs a per-FR Gate 1 loop must carry both
        exits, and the timeout one must be read AFTER `passed` is computed —
        a kill can land just after `quality_complete=True` was written.
        """
        for phase in (3, 4, 5, 7, 8):
            text = generate(phase)
            assert "fr_step_timeout: true" in text, phase
            assert "if (!passed && frRc === -1)" in text, phase
            assert (text.index("passed")
                    < text.index("if (!passed && frRc === -1)")), phase
            assert "session/rate limit verifying" in text, (
                f"phase {phase}'s Gate 1 verifier dispatch has no session "
                f"guard — a quota cap there reads as a Gate 1 quality failure"
            )

    def test_runall_forwards_outcome_owner_to_record_block(self):
        from scripts.workflowgen.spec_runall import generate_runall
        text = generate_runall()
        assert "owner: 'project'" in text, (
            "run-all.js no longer inlines a gate-exhaustion halt naming "
            "owner: 'project' — the three phase bodies it composes have "
            "drifted from render_gate_loop's fix"
        )
        start = text.index("if (outcome && outcome.error) {")
        end = text.index("\n  }\n", start)
        body = text[start:end]
        assert "recordBlock(n, String(outcome.halt_step || 'phase-error'), " \
               "String(outcome.error), outcome.owner)" in body, (
            "run-all.js's generic outcome.error branch no longer forwards "
            "outcome.owner to recordBlock — a halt object that states its "
            "own owner (e.g. render_gate_loop's) would fall back to the "
            "text classifier and lose that signal"
        )


class TestRelayReceipt:
    """Round 86 站2 — the relay carries a receipt, and clears what it replaces.

    `loadFileViaPython` moves a file through a sub-agent: `read-file` writes
    it, the agent `cat`s it and re-emits it as its final message. Bash stdout
    above ~30KB is replaced by a 2KB preview plus a persisted-file path
    (measured 2026-09-02: 27,009 intact, 35,300 and 49,300 replaced), so
    above that the agent never saw the content — and the JS-side checks it
    faced, `length >= 50` and a first-line anchor, both pass on a truncated
    prefix. The frame's END marker is what makes a short relay tellable from
    a short file; sim_runner.test.mjs's round86 fixtures cover that side.
    These two cover what a runtime simulation cannot see: a shell command.
    """

    def test_the_loader_clears_its_outputs_before_read_file_writes_them(self):
        """contentOut is a fixed path and read-file skips it on failure.

        Without the `rm -f`, the agent's `cat` returns a PREVIOUS run's
        leftover for that path — a payload with a valid anchor and, since
        this round, a valid frame too. Nothing downstream can tell it apart
        from this run's read, which is the same shape as the defect the
        frame exists to close: the judgement reading something other than
        what the framework just produced.
        """
        for phase in (1, 2):
            text = generate(phase)
            assert "const pythonCmd = 'rm -f ' + contentOut + ' ' + jsonOut + ' && '" in text, (
                f"phase {phase}'s loader no longer clears contentOut/jsonOut "
                f"before read-file runs — a failed read leaves the previous "
                f"run's file in place for the agent to cat"
            )

    def test_the_ceiling_the_js_checks_is_the_one_python_enforces(self):
        from scripts.file_loader import RELAY_MAX_BYTES

        for phase in (1, 2):
            text = generate(phase)
            assert f"const RELAY_MAX_BYTES = {RELAY_MAX_BYTES}" in text, (
                f"phase {phase} states a relay ceiling other than "
                f"scripts.file_loader.RELAY_MAX_BYTES — two numbers for one "
                f"limit is how the JS came to check startsWith('#') on a "
                f"payload Python already knew the size of"
            )
            assert "--relay --relay-max-bytes ' + RELAY_MAX_BYTES" in text, (
                f"phase {phase}'s read-file call no longer passes the ceiling "
                f"it checks against, so the two halves could disagree"
            )
