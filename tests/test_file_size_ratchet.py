"""Production-file line-count ratchet — god-file growth must be deliberate.

Round 3 claims 2/4 residue: the repo's largest files (harness_bridge,
gate_cmds, phase_cmds, fr_cmds, ...) are safety-critical surfaces
deliberately NOT decomposed this round (the M2-M4 plangen split handled the
one with a proven drift wound). This ratchet does for file growth what
test_patch_discipline does for private patches, with one deliberate
difference spelled out here: line counts legitimately grow, so ceilings MAY
be raised — but only in the same commit as the growth, with the reason in
the commit message. The product is diff-visibility of growth, not an
absolute cap. A file not listed in _LINE_CEILING must stay below
_GOD_FILE_THRESHOLD entirely; lowering a ceiling after shrinking a file is
manual, same as the patch ratchet.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_SCAN_DIRS = ("cli", "core", "harness", "scripts", "detection")
_GOD_FILE_THRESHOLD = 900

# Snapshot 2026-07-11 (Round 3 Station L, after the M2-M4 plangen split —
# generate_full_plan.py itself is down to a ~250-line facade and off this
# list; the split's two large products are honestly listed).
_LINE_CEILING: dict[str, int] = {
    # 2026-07-12: +31 lines — env-check prompt teaches optional_missing vs
    # required distinction (fix false fabrication flag on vars with baked-in
    # config defaults). Example uses generic DATABASE_URL, not project-specific.
    # 2026-07-16: +79 lines — Round 12 站3b: _check_infra_fail_pollution
    # (+ finalize_gate interception before S3) rejects zero scores whose
    # evidence carries a run-gate PRECONDITION-block signature — the
    # 2026-07-16 phantom-module incident wrote 3 fake quality zeros into
    # the manifest and dispatched CODE-FIX at healthy code.
    # 2026-07-17: +11 lines — Round 13 站1: exception-swallow ratchet paydown —
    # SAD.md §5 parse failure now records a degradation entry (SAB baseline
    # consequence is significant enough for the persistent ledger, not just a
    # print) + _trigger_hooks failure now logs via the file's own local logger.
    # 2026-07-21: +9 lines — fix/spec-cap-list-set-mismatch: replaced the
    # hardcoded *.py-only rglob scan with the shared language-aware
    # _get_test_directories/_scan_test_functions helpers, and switched the
    # numerator from a dedupe-set to a row-based count (matches the
    # denominator's row-based list; a parametrized case legitimately repeats
    # its Test Function name across multiple TEST_SPEC.md rows, so a
    # dedupe-set numerator against a non-deduped list denominator
    # mathematically capped test_coverage below 100% even when every
    # required test existed).
    # 2026-07-26: +13 lines — traceability dim threshold-mismatch fix:
    # merged_pct (min of 4a/4b/4c, each with a DIFFERENT threshold) was being
    # compared against the flat 4a threshold (100%) in both the per-dim
    # override (_override_traceability_dim_score) and the YAML-sourced
    # _dim_thresholds lookup, so a Gate 2 run with 4b/4c legitimately ≥60%
    # but <100% was misreported as FAIL even though compute_trace_dimension's
    # own `passed` field said PASS. Now both consumers use the function's new
    # `threshold_effective` (the threshold of whichever component is binding
    # the min), and the YAML override is dropped for this dim so it can't
    # shadow the fix.
    # 2026-07-26: +6 lines — same fix, follow-up: preserve a
    # gate_score_overrides threshold floor-raise (applied to dims before this
    # override runs) instead of unconditionally overwriting threshold with
    # threshold_effective, which would have silently discarded a project's
    # intentional stricter-than-default traceability floor.
    # 2026-07-27: +11 lines — Round 24: EnvCheckContext.evaluation_prompt()'s
    # CLASSIFICATION RULE gains a third bucket for test/dev-only opt-in flags
    # (env vars a project's docs describe as off/disabled/rejected by default
    # in production) plus a second worked example. Closes the gap that let
    # the same documented env var on the same unchanged project state
    # classify as optional_missing in one workflow run and required in
    # another (observed: wf_8b3a3f79-12b vs wf_4fe2125c-48d) — the prior rule
    # only distinguished "has a documented default value" vs "no default",
    # with no bucket for "intentionally absent by design" opt-in flags.
    # 2026-07-27: +36 lines — Round 21 站1: DA-waiver necessity is adjudicated
    # HERE now (against the framework's own post-CRG scores) instead of in
    # cli/gate_cmds.py before scoring, where the one waivable dimension is
    # always null. Adds the adjudication call + its BLOCK path, and lifts the
    # thrice-repeated effective-threshold expression into one local helper so
    # the verdict, the failing-dimension report, and the waiver decision cannot
    # drift onto different bars.
    # 2026-07-27: +22 lines — Round 21 站2: finalize_gate validates the gate
    # result against harness_gate_result.schema.json before scoring from it.
    # The schema had never been loaded by any code, so it drifted into
    # describing a document no run produces while consumers guessed field
    # names — the direct cause of the dead DA-waiver safeguard above.
    # 2026-07-30: +53 lines — Round N: INFRA-fail detection + partial-pollution carve-out
    # 2026-07-31: +3 lines — gate_score_overrides floor-raise now also syncs
    # _dim_thresholds (previously only d.threshold was raised); _effective_
    # threshold() reads _dim_thresholds FIRST, so a project's SAD.md-declared
    # coverage/security/etc. floor was silently discarded whenever the gate
    # YAML also declared that dimension (i.e. every real gate run) — see
    # test_finalize_gate_override_not_discarded_when_yaml_declares_dimension.
    # 2026-08-01: +197 lines — Round 27 站1: an agent-reported `score: null` was
    # a free pass through five separate layers (S4 skipped it, the weighted
    # average dropped it from the denominator, _all_dims_pass treated it as
    # vacuously satisfying its own floor, and 15 of 32 tools had no content
    # pattern so any prose passed as evidence). None now means "the framework
    # must check": S4 runs the tool itself and either writes back a real score
    # or marks it framework_na. Most of the growth is the 14 new
    # _TOOL_CONTENT_PATTERNS entries and the comments recording which taskq-plus
    # Gate 4 evidence each layer let through.
    # 2026-08-01: +6 lines — Round 27 站1b: pytest-benchmark's content pattern
    # took three attempts (the two rejected drafts are recorded beside it), and
    # the comment explaining why is longer than the regex it guards.
    # 2026-08-01: +37 lines — Round 27 站3: S3 fingerprints each piece of
    # evidence as it clears it, and finalize_gate persists the digests into the
    # gate result. taskq-plus's Gate 4 cites 13 tool_output paths under the
    # gitignored .sessi-work/, all gone now, while the verdict that read them is
    # committed — the judgement was version-controlled and its evidence was not.
    # 2026-08-01: +19 lines — Round 27 站5: a reverted change, kept as a comment.
    # Settling d.threshold to _effective_threshold would have made block_reason
    # quote the number that judged; it also turns gate_score_overrides' floor
    # into a ceiling, which test_finalize_gate_override_is_floor_not_ceiling
    # caught. The note is longer than the line it replaces because the next
    # reader needs to know the diagnosis stands and only the fix was withdrawn.
    # 2026-08-03 (Round 32 站4): +73 lines — _run_harness_cross_validation
    # returns two lists instead of one. "the harness could not measure this"
    # and "the harness measured and the agent's number was false" were filed
    # under a single key whose registered remediation reads "Do NOT re-run
    # the gate — the score, not the run, is what failed"; a live P4
    # last_block.md shows a pyright timeout and a PYTHONPATH gap under
    # exactly that heading. The growth is the second accumulator, the
    # branch that used to `continue` in silence when a scorer returned None
    # (now a ledger line plus an infra_fail entry — Round 30's rule that an
    # abstention is not a pass), and s4_block_details — the (fabrication,
    # unverifiable) -> details mapping, made public so it can be pinned
    # without patching five private seams around finalize_gate (the
    # private-patch ratchet rejected the version of the test that did).
    # 2026-08-03 (Round 32 站5): +13 lines — DIMENSION_EXCLUSION_FILES gains
    # test_coverage, whose declaration can live in any of pytest.ini /
    # pyproject.toml / setup.cfg, so the registry value may now be a tuple
    # and S6 flattens it before walking. Measured: a project declaring nine
    # testpaths entries against sixteen collected test files, two of them
    # the FR tests for FR-02 and FR-07, with neither denominator recorded.
    # 2026-08-06 (Round 38 站1): +31 — the CRG-only override now fires on what
    # the gate config declares rather than on what the agent's breakdown
    # happens to contain, and appends the dimension when the agent omits it.
    # The old form let a gate result skip the framework's own architecture run
    # simply by leaving the row out. Most of the growth is the comment
    # recording that, since the omission never failed a test.
    # (Owed to 站1's commit 8e54c60, which shipped this ratchet red; caught
    # one commit late because only the tail of the suite output was read.)
    # 2026-08-07: +70 lines — Round 42 站4: `measurement_scope()` and its
    # docstring (the taskq-plus 0.86-weight vs taskq-renew 1.00-weight
    # measurement that makes the function necessary), plus the
    # architecture-calibration stash at the end of the CRG override where
    # crg_metrics.json is already in scope. Both sit where the values they
    # report are computed; carrying them to the write site instead would
    # mean loading the gate config and crg_metrics twice.
    # 2026-08-07: +10 more — Round 42 站5: the unhealthy-community printout
    # names the file when the community is one file's internals. Three of
    # the four remedies below it assume a community is a set of modules;
    # taskq-renew's storage-load-sub1/sub2 are Leiden splitting
    # task_store.py, so none of them applied and calibration was the only
    # lever left after R38 removed the waiver.
    "harness/harness_bridge.py": 4817,  # 2026-08-24: +28 — Round 72 站2: `_mutation_artifact_violations` asks whether this framework wrote the artifact whose score it is about to accept. Its docstring already claimed "the framework's own artifact is the source"; nothing made it one. Five lines are the check and its `unverifiable` return (the same bucket as an absent file — same fact, same remedy); the rest is the comment carrying the taskq-new measurement: no stamp, a `generated_at` this code cannot emit, and a note excluding 685 mutants, scoring 72.1 against a threshold of 70 where the full denominator gives 24.6. Previous: 4789.  # 2026-08-23: +14 — Round 70 站1: the composite floor `_quality_complete` compares against stops being two `80` literals. Neither was ever the number this line used for Gate 1 — `GateConfig.from_dict` resolved `score_gate` to 1.0 by reading `gate: 1` as a threshold (measured on taskq-cc and taskq-api), and a dataclass field is always present so the getattr default was dead. Eight of the lines are the comment recording that. Previous: 4775.
    # 2026-08-22: +14 — commit e58b4ba: recognize --benchmark-json evidence keys ("benchmarks", "machine_info", "stats") for S3-A pytest-benchmark check. Previous: 4761.  # 2026-08-22: +31 — Round 68 站1: the `required_artifact_missing` raise beside its two Round 54 / Round 67 站7 arch siblings. Eleven lines are the import, the call and the GateBlockedError; the rest is the comment recording the measurement and the limit. Measured on taskq-cc's committed Gate 4 (PASS, 95.28): `.env.example` is absent while SPEC §8 #26 is a grep over it, and `migrations/` + `alembic.ini` ship under 03-development/src while SAD.md:45 asserts the tree "matches SPEC.md §6 exactly" — every check in this framework reads one artifact against another and none had ever opened the tree. The limit is in the comment because the next reader will otherwise assume more than it does: the list is the project's, so declaring nothing is a ledger row rather than a block, and Round 57's "who declares the scope" is not solved here. The decision itself is core/quality_gate/required_artifacts.py, which also records why the rejected alternative (scraping backticked paths out of SRS.md/SAD.md — 68 candidates on taskq-cc, 46 unresolvable) was rejected with a number. Previous: 4730.  # 2026-08-22: +148 — Round 67 站2/3/7, three blocks that each turn a number the framework already had into something the verdict reads. 站2: `framework_measured` and `composite_over` (the predicate had two readers and neither was the verdict, so taskq-cc published weight_covered 0.88 beside a composite that recomputes exactly over 1.0, and PASS at 95.28 with test_coverage measured over a suite that replaces taskq_api.service.auth in five files), plus the raise that names the fixture, the file and the module. 站3: `_TOOL_REQUIRED_PATTERNS` and the fourth check in `_validate_tool_content` — gate_evidence/gate4/test_coverage.txt is 205 bytes of pytest-benchmark tail with no coverage number in it, accepted because `\d+ passed` is one of an OR. 站7: the `contract_coverage_blocking_reason` raise beside its Round 54 sibling, with the comment recording why the two are different questions. Roughly ninety of the lines are those comments; the code is three predicates and two raises. The file is a god file and this makes it bigger — Round 49 織網-then-cut is the shape a split needs and it is a round of its own, recorded in docs/PROPOSAL_ADJUDICATIONS.md. Previous: 4582.  # 2026-08-18: +67 — Round 57 站3: `per_fr_coverage_evidence` and the S4 write that cites it. Before this the per-FR re-score changed `score` and left `tool_output` pointing at the whole-project pytest-cov audit, so a recorded 100.0 cited a file whose last line reads TOTAL 62% and the scope switch existed only on stdout. The renderer is public and pure for the same reason `s4_score_verdict` is (tests/test_patch_discipline.py); the write is assignment rather than setdefault because the number is the framework's, so its citation is the framework's too; and an OSError leaves tool_output alone and says so, because evidence the verdict cannot cite must not be claimed. Previous: 4515.  # 2026-08-18: +35 — Round 57 站1: `s4_rescopes_to_fr`. The predicate is three lines; the rest is the docstring recording why `integration_coverage` left the condition — it is scored by `pytest-cov-integration` over a different run, appears only in gates 2/3/4, and `fr_coverage_from_last_run` reads the unit suite's `.coverage`, so the branch could not fire through any sanctioned path (no workflow JS passes `--fr-id` to those gates; measured, zero) and would have produced a wrong number if hand-invoked. Public and pure for the same reason `s4_score_verdict` is: tests/test_patch_discipline.py refuses a test that patches five private seams around finalize_gate. Previous: 4480.  # 2026-08-18: +31 — 22e2471: filter_enabled_dimensions in prepare_gate to exclude disabled dims before evaluation_prompt renders.  # 2026-08-18: +23 — 732c9ce: per-FR re-score for test_coverage / integration_coverage at P3 in S4 cross-validation.  # 2026-08-16: +68 — Round 54 站3: `s4_score_verdict` and its call site. Roughly fifty of the lines are the docstring, and they carry the measurement that justifies the change: on the exact tree taskq's Gate 4 judged (git archive c1af37e) the recorded error_handling score is 100.0, the framework's own scanner on that tree gives 80.0, and the agent's own evidence line (total=6 source files; with_handler=4) implies 66.7 — three numbers, and the verdict carried the only one nobody computed. The function is public and pure for the same reason `s4_block_details` is: tests/test_patch_discipline.py refuses a test that patches five private seams around finalize_gate. It also records why `score_source` is NOT overwritten when it already reads `stubbed_boundary` — Round 51 站3 sets that before S4 runs and measurement_scope reads it, so an unconditional write would revoke that round without a word. Previous: 4358.  # 2026-08-16: +30 — Round 54 站2: the `unconfigured` raise site beside Round 51 站2's existing `record_constraint_status` call, which now returns its rows instead of discarding them. Six lines are the call and the GateBlockedError; the rest is the comment recording what changed and what deliberately did not — `declared_only` is still never blocked, because the only way to satisfy a block on a constraint nothing can decide is to delete the declaration, which makes the SAB less true rather than the code better. The decision itself is core/quality_gate/arch_constraints.unconfigured_blocking_reason. Measured: 8 of the 23 constraints across the seven projects here are in that state, including taskq-super's two. Previous: 4328.  # 2026-08-16: +38 — Round 53 站5a: `_gate_dimension_names` and the scope check in front of `_verify_system_reach_block`. Measured on taskq-super: 116 `gate:verify-system-reach` rows, every one "no reach artifact", and correlating each row's ts against gate_timestamps.jsonl puts ALL 116 at Gate 1 — 18.5% of that project's ledger, owner `harness`, asking a gate a question its own config says it cannot answer. The helper stays separate from the two existing inline dimension-list branches because they need the full entries and this needs names; merging them would trade a duplicated branch for a parameter meaning "which shape do you want". Previous: 4290.  # 2026-08-14: +17 — Round 52 站3: the finalize_gate call that writes .methodology/delivery_fingerprint.json, its try/except and the ledger row for a failure to write one. The fingerprint is rendered entirely from producers that already ran (core/quality_gate/delivery_fingerprint.py); nothing new is measured here and nothing is judged — the comment says why, because the next reader's instinct will be to add a threshold to it. Previous: 4273.  # 2026-08-14: +78 — Round 52 站2: `_verify_system_reach_block` and its raise site. The measurement is in core/quality_gate/verify_system_reach.py; what is here is the branch on its status and the four ledger rows, and roughly half the lines are the docstring recording why the function branches on `status` rather than on the list — `unmet_obligations` omits the key when nothing was measured precisely so a `[]` cannot be read as "nothing outstanding" (Round 35 站2 on a check that blocks). The raise sits after S4 because S4 is what runs `system-verification`, and the reach artifact is written by that run rather than by a second execution of the target (Round 25). Previous: 4195.  # 2026-08-14: +25 — Round 52 站1: finalize_gate records what `make verify-system` will run and blocks on two of its shapes. Four lines are the import and the two calls; the rest is the comment naming the measurement — of the six projects on this machine two chain `test lint coverage` and never name the delivered package, and one invokes it behind `|| true`. The decision itself is core/quality_gate/verify_target.blocking_reason, which also says why a swallowed verdict on a non-product step goes to the ledger instead. Previous: 4170.  # 2026-08-14: +58 — Round 51 站4: `_record_coverage_denominator`. The computation is in core/quality_gate/cov_utils.coverage_denominator; what is here is the ledger row and the branch that decides what the row may claim. Half the lines are that branch's comment: coverage.json is written by a run that already applied the omit, so for five of the six projects on this machine the omitted files are not in the report and their statement count is unknowable from it — only taskq-api's report happens to contain them (63 of 839, 7.5%). Saying "0 statements" there would read as "the omit is free", and counting them a second way with an AST walk would produce a number that looks comparable to coverage.py's and is not. Previous: 4112.  # 2026-08-14: +96 — Round 51 站3: `_mark_stubbed_boundary_dimensions` plus SCORE_SOURCE_STUBBED_BOUNDARY and the set both selection sites now read. The predicate is one call to core/quality_gate/boundary_realism.stubbed_boundaries; the rest is the two registries that have to be legible where they are used — which dimensions are measured BY running the suite (two, and the comment says why test_assertion_quality and mutation_testing are not among them), and which score sources mean "not the framework measuring the delivered code" (a set rather than a second != comparison beside the first, which is how two readers come to disagree). Measured: taskq-api has 18 autouse fixtures replacing 2 SAB high-risk modules across 10 files including both *_e2e.py; the other five projects on this machine have zero. Previous: 4016.  # 2026-08-14: +10 — Round 51 站2: finalize_gate classifies the SAB's `architecture_constraints` and leaves the ones with no executor in the degradation ledger. Two of the ten lines are the call; the rest is the comment recording why it sits here — the list reaches CLAUDE.md and the evaluation prompt rendered 900 lines above and nothing else, so "the agent was told" has been the entire enforcement, and taskq-api's VERIFICATION_REPORT certified five constraints "honored at HEAD" while `app.py:39` imported create_engine and /v1/metrics was mounted with no auth dependency. Previous: 4006.  # 2026-08-13: +21 — Round 50 站6: S4's audit file moves out of `.sessi-work/` (deleted by advance-phase at every transition) into the directory core/evidence_retention.cited_evidence_dir names, under `.methodology/gate_evidence/`. Measured: taskq-api's Gate 4 recorded a cross-validation gap at 06:19 and published PASS at 06:29, and ten days later the question "which S4 branch did that run take" cannot be settled — the directory its own message sent the operator to no longer exists. Four of the lines are the write and the block message reading one relpath; the rest is the truncation the move makes necessary (the file is committed now, so it is bounded by the same values.gate_evidence_max_bytes ceiling Round 45 站1 set for cited evidence, not a second knob beside it) and the comment recording that. Previous: 3985.  # 2026-08-13: +48 — Round 50 站2 (raised in 站3's commit; 站2 ran only a subset of the suite before committing and did not see this — recorded rather than hidden). SCORE_SOURCE_AGENT_UNVERIFIED and its docstring, the `score_source` field on DimResult, the S4 write of that marker, and measurement_scope's selection reading it instead of `score is not None`. The code is roughly ten lines; the rest is the measurement that justifies it — a real Gate 4 published composite 95.2776 over `weight_covered: 1.0` with one of sixteen dimensions carrying an agent's number the framework had run the tool for and failed to reproduce. Previous: 3937.  # 2026-08-12: +12 — Round 46 站3: struck the half-sentence claiming p95 "is enforced inside the performance dimension's benchmark scorer" and replaced it with what is true. `_score_pytest_benchmark` applies a fixed 1000/3000ms penalty its own docstring calls "not NFR targets"; no line reads a p95 or a project budget. All 12 lines are comment — naming an enforcer that does not exist is how taskq-advance's FINAL_SIGN_OFF recorded "NFR-01 … Conditional PASS" beside performance = 100.0, and the correction has to be at least as legible as the claim was. Previous: 3925.  # 2026-08-12: +36 — Round 46 站2: every finalize now leaves a `gate:test-skips` ledger row when any test did not run, instead of printing a ratio WARN nobody stores. The parse was already there and is now `_parse_skip_counts`, read by two callers that answer different questions: the WARN keeps its 10% coverage-subset threshold, the ledger row has none (taskq-advance's 17 skips are 6.25% and never tripped the WARN while three of its NFRs had guards skipping themselves). Most of the +36 is the scope note in `_check_test_skip_ratio`'s docstring saying which of the two mechanisms enforces what, so the next reader does not make this one carry the other's weight. Previous: 3889.  # 2026-08-11: +23 — Round 45 站1: finalize_gate copies each dimension's cited tool_output under .methodology/gate_evidence/ before S3 reads it, and writes the re-pointed result back so the citation reaches the file cli/gate_cmds.py persists. Measured across five projects, 149 of 162 cited tool_output paths no longer resolve — every one under the gitignored .sessi-work/. The whole copy/skip/ledger decision lives in core/quality_gate/gate_evidence_store.py; what is here is one call, the conditional write-back, and the comment correcting Round 27 站3's claim that fingerprints and proof "cannot be separated by a cleanup of the gitignored work directory". Previous: 3866.  # 2026-08-11: +38 — Round 44 站3: the architecture dimension refuses to produce a score when the code-review-graph covers fewer files than the project delivers. Round 37 站2 forced one full rebuild and recorded whatever survived it; Round 42 站4c carried graph_files/source_files into the result's calibration block; a repository-wide grep found one producer and no consumer comparing them. taskq-advance logged four crg:graph-scope residuals in Phase 3 (41 graphed / 47 delivered) with architecture at 91.7. Raised as `infra_fail` (crg_graph_incomplete), not as a low score, because the project cannot fix CRG's parser and Round 32 站4's rule is that an unmeasurable dimension is the framework's debt, never a number the project may lower. The size is the GateBlockedError with its per-file list and the comment recording that distinction; the predicate itself is one call to crg_independent.graph_coverage_gap.
    # 2026-08-04 (Round 35 站3): +37 — `_mutation_artifact_violations` returns
    # two lists instead of one (a missing artifact is a run to repair, not a
    # claim to withdraw) and its call moved above the agent-score early exit,
    # which a self-reported failing score used to skip past. The rule the
    # hoist encodes is stated where it is enforced, since mutation is its only
    # member and a registry for one entry would be the wrong shape.
    # 2026-08-03 (Round 31 站6): +21 more — the S4 remediation text for a tool
    # that timed out is no longer the text for a tool that is missing. They
    # shared one sentence, and that sentence was "Install '<tool>'", which is
    # what a real Gate 2 told an agent about an installed pyright that had run
    # out of budget scanning 4917 files.
    # 2026-08-03 (Round 31 站4): +18 more — the scope-drift check in
    # _mutation_artifact_violations and the mutation_testing entry in
    # DIMENSION_EXCLUSION_FILES. A score is only meaningful over the scope it
    # was taken on, and setup.cfg's [mutmut] section is where both halves of
    # that denominator live — written by the party being scored.
    # 2026-08-03 (Round 31 站2): +5 net — _mutation_artifact_violations
    # (~65 lines) replaced _extract_mutmut_kill_rate (~30) and two branches
    # that had been unreachable since the guard above them started catching
    # every negative return code except -1. S4 for mutmut now reads the
    # framework's own score artifact instead of parsing the agent's prose.
    # 2026-07-12: +2 lines net — Round 6 站2: _check_sab_module_alignment's
    # unregistered-direction scan now delegates to sab_amender.
    # discover_modules_at() (removed inline loop, +docstring paragraph
    # explaining the delegation) instead of a locally re-implemented rglob
    # loop that had silently diverged (never skipped __pycache__).
    # 2026-08-07: +26 lines — Round 42 站2: `_record_undelivered_tests` puts
    # the declared-but-absent test names into the degradation ledger and onto
    # the namespace the gate-result patch block reads. The percentage they are
    # a ratio of has always been in the verdict (taskq-renew's `traceability`
    # score IS 81/89*100); the eight names behind it went to stdout only. The
    # helper lives beside `_finalize_gate_cross_checks`, its single caller, so
    # the one count feeds both records — moving it out would separate the
    # ledger write from the check whose numbers it reports.
    # 2026-08-07: +13 lines — Round 42 站4: the gate-result patch block
    # writes `measurement_scope` (what the composite was averaged over) and
    # the architecture entry's `calibration` (which cohesion floor and how
    # many files the CRG graph held). Both are read from the context, not
    # recomputed — a second derivation of a denominator is a second
    # denominator — so the lines here are the read and the two writes.
    # 2026-08-12: 2800 -> 2835 (+35). Round 47 站3+站5a. run-gate repairs
    # (it prepares an evaluation); finalize-gate deliberately does NOT (it
    # judges one — a tool that vanished between them means evidence and
    # verdict saw different trees), and that non-wiring is stated where a
    # reader would look for it. _finalize_env_result additionally folds the
    # framework's OWN gate-tool verdict into `ready`: measured 2026-08-12,
    # five live projects reported ready=true while missing 11-16 of the 16
    # tools the registry requires, because the contract's cli_tools list and
    # the gate configs had never met. The comment explaining why the two
    # lists are NOT merged (different probers, different namespaces) is most
    # of the size.
    # 2026-08-12: 2835 -> 2864 (+29). Round 47 站5b: run-env-check installs
    # the PROJECT's declared dependencies before evaluating, at phase 3+ only
    # (P1/P2 have no code to declare for, and demanding a manifest there would
    # block every project on its first command). The block carries its own
    # remediation and states, in the message itself, that it is the installer's
    # precondition and not a verdict on any NFR — the same file is required by
    # taskq-advance's NFR-07, whose enforcement is its own tests, and two
    # enforcers for one fact is the shape Round 38 had to undo.
    "cli/gate_cmds.py": 3028,  # 2026-08-24: +20 — Round 72 站2: `_patch_mutation_score`'s evidence line branches on whether the artifact carries the provenance stamp. The unconditional string said "framework: compute_mutation_score → killed=… survived=… score=…" about any file with a `score` key; taskq-new's committed gate4_result.json carries it in front of a number rebuilt by hand from a stale cache with 685 mutants excluded by the author. Six lines are the branch and its alternative sentence; the rest is the comment recording that blocking a verdict while keeping its evidence line is Round 69's write-after-the-verdict one field over. Previous: 3008.  # 2026-08-23: +30 — code-review follow-up: `--force`'s sentinel bypass (f0de7ea, below) skipped run-gate's anti-fabrication check unconditionally for ANY FR/gate, not just the FR-99 recovery shape it was built for. Narrowed to require genuine, fr_id-matching gate{N}_result.json evidence on disk (reuses `_load_gate_result_json`, already used by `_collect_da_waivers`) before --force can skip the sentinel. Previous: 2978.
    # 2026-08-23: +9 — commit f0de7ea: `--force` sentinel bypass in `_finalize_gate_preflight` for FR-99 recovery path. Previous: 2969.  # 2026-08-23: +12 — FR-99 per-FR-scope fix: `_cmd_finalize_gate_impl` calls `core.state_io.sync_missing_fr_traceability` for Gate 1 per-FR runs, before any scoring, so a Phase-3-only module path SAB.json already declares (e.g. a framework-owned placeholder FR) gets backfilled into quality_manifest.json's frozen Phase-2 `fr_module_traceability` snapshot instead of the FR silently scoring against the whole project. The call is 3 lines; the rest is an inline comment explaining why. Previous: 2957.  # 2026-08-22: +77 — Round 67 站1: `build_persisted_gate_result` and the persist call site. About fifteen lines are the function; the rest is the measurement that justifies it — taskq-cc's committed gate4_result.json carries sixteen dimensions and zero `score_source`, beside a `measurement_scope` naming two of them unscored, because the persist step re-read the agent's file from disk and copied back a fixed list of fields. The comment at the surviving per-dimension `score` loop is most of the remainder, and it records the thing this round got wrong first: the plan said delete that loop, and the check said no, because corrections like the spec cap live only on DimResult and were never written into `raw`. Previous: 2880.  # 2026-08-19: +16 — Round 60 站2: the run-gate precondition that refuses a config still switching a dimension off. Six lines are the block and its exit code; the rest is the comment recording why there is ONE enforcement point and why it is this one — run-gate is the entrance every gate passes through, and after the retirement the key changes nothing in the judgement, so finalize-gate needs no second copy (the Round 47 站3 asymmetry: run-gate PREPARES, finalize-gate JUDGES). Net of the Gate 4 B3 skip branch this round deleted (-9). Previous: 2864.  # 2026-08-11: +89 lines — Bug B fix for P7 FR-09
    # false-positive block: per-FR writer in _cmd_finalize_gate_impl grows
    # from ~10 lines to ~75 lines (fr_id consistency check + idempotency
    # guard that prevents a sub-agent from clobbering a sibling FR's per-FR
    # history file). The new code keeps the writer inline (surgical change
    # inside the existing function) rather than extracting a helper, to
    # minimize blast radius; if a future change takes this file over 2900,
    # split the writer into _write_per_fr_gate_result as the next step.
    # 2026-08-05: +16 net — _print_fr_scoped_overrides_py now runs every
    # co-owning FR's test file alongside fr_id's own one when a declared
    # source file (per fr_module_traceability) is shared by more than one
    # FR — e.g. a CLI dispatch module several FRs each own a slice of.
    # Measuring that file's coverage with only ONE owning FR's test suite
    # charged it for every OTHER owning FR's untested-by-this-suite lines,
    # regressing on an unrelated FR's later commit (root cause of an FR-02
    # Phase-5 Gate-1 false block: a Phase-3/4 100.0 self-report for
    # unchanged source+test files read 87.62 two phases later purely
    # because sibling FRs had grown the shared file). The new logic lives
    # in cov_utils.shared_owner_test_files(); this file only wires its
    # output into the printed pytest invocation and cov_note.
    # 2026-08-04 (Round 35 站2): +16 — _patch_mutation_score now understands
    # an artifact that says the framework could not measure (`score: null`).
    # There is no number to patch in then, and the evidence line must stop
    # describing a measurement that did not happen: on the project that
    # motivated this round the recorded evidence read "framework override
    # applies" while framework_override was absent, because it never ran.
    # 2026-08-03 (Round 32 站6): +34 lines — _clear_last_block_for.
    # last_block.md was written on every block and never removed, so on
    # the measured project a P4 Gate 1 BLOCK report sat beside a
    # state.json saying the phase had passed, with nothing to say which
    # was current. It is cleared only when the gate/phase/FR it names is
    # the one that just passed — a stale report for a gate that has NOT
    # since passed is still the current truth about that gate, and the
    # three-way match is most of the size.
    # 2026-08-03 (Round 32 站1): +18 lines — the finalize sentinel moved from
    # line 1920 to the end of cmd_finalize_gate and became a receipt. The old
    # write sat ~250 lines and FIVE blocking `return`s above the registries it
    # is supposed to agree with (post-flight ×2, the identical-scores
    # fabrication detector, Phase Truth ×2), so a gate that blocked still left
    # the file advance-phase reads as proof it passed. The growth is the
    # explanatory comment at the old site plus the receipt call and its
    # rationale at the new one; the format itself lives in gate1_evidence.
    # 2026-08-03 (Round 31 站2): +19 lines — _patch_mutation_score, the
    # mutation_testing half of the framework_override pattern the trace
    # dimension has used since PR 4. The agent has no standing to author a
    # number the framework computes itself; without this the verdict recorded
    # whatever the agent wrote even when S4 had the real figure in hand.
    # 2026-07-11: +26 lines — cmd_advance_phase now refreshes the
    # traceability attestation before its handover commit (mirrors the
    # existing push_cmds.py refresh in push-checkpoint/push-milestone), and
    # the P2-A SAB pre-check now matches DriftItem.actual instead of a dead
    # description substring.
    # 2026-07-12: +14 lines — Round 5 建議2站2: cmd_plan_phase/cmd_plan_all
    # migrate from cwd-relative `from scripts.generate_full_plan import` to
    # load_harness_script("generate_full_plan.py") (same P6/A1 bug class,
    # never swept for this module).
    # 2026-07-13: +25 lines — STAGE_PASS generation-order fix: the
    # "Always-regenerate Phase{N}_STAGE_PASS.md" block moved from mid-function
    # (right after HR-11) to the true end of _advance_prechecks (after Agent B
    # approvals / TDD / SAB drift / submodule guard all pass), so it can pass
    # truth_override=True to _generate_stage_pass instead of reading
    # state.json.phase_truth_passed before _advance_fsm has written it. Added
    # a small early "ensure exists" pass at the block's old position so the
    # internal Phase Auditor call (a few lines later) doesn't CRITICAL-fail
    # its own C2 check on a first-ever advance.
    # 2026-07-14: +7 lines — P2->P3 manifest regen's SRS.md fallback scan
    # (Fix 5, FR-heading bug class 6th site) now imports SRS_SUBSECTION_PREFIX
    # from core.quality_gate.parsers so `### 3.1 FR-01` TOC-numbered headings
    # are detected, matching the fix already applied at spec_alignment.py /
    # phase_hooks.py / spec_coverage.py / artifact_parsers.py; the fail-fast
    # error message's regex literal was updated to match.
    # 2026-07-16: +93 lines — Round 12 站0b: _run_substrate_probe (spawn-
    # substrate preflight at run-phase entry for PER_FR_GATE1_PHASES, with
    # 6h success cache + --skip-substrate-probe escape hatch). One 90s
    # probe replaces the 2026-07-16 failure mode where the per-FR loop
    # burned 140 dispatches / ~2.5h on FR-01 discovering spawned agents
    # could not execute pytest/git.
    # 2026-07-17: +23 lines — Round 13 站1: exception-swallow ratchet
    # paydown — 12 previously-unlogged broad excepts now print a [WARN]
    # (2 via core.degradation_ledger for the state.json-corruption cases).
    # 2026-07-17: +12 lines — Round 13 站2c: 8 agent-facing [BLOCKED] messages
    # gained a "Fix:"/re-run remediation line (blocked-message-contract scan).
    # +15: Round 18 站3 added _attestation_content_still_current, the slow-path
    # adjudication that stops the mtime probe from manufacturing no-op commits.
    # 2026-07-28 (Round 22 站2): +33 — _advance_prechecks runs
    # PhaseHooks.preflight_manifest_integrity() first (exit 27), with the
    # restore command in its [BLOCKED] message. Relocated from workflow JS,
    # where it cost one sub-agent dispatch per advance round AND left every
    # non-workflow caller (human, resumed session, CI) unprotected.
    # 2026-07-30: +11 lines — Round 26 站6: phase_completed records enforcer_sha
    # (the provenance gate results have carried since Round 19 站3) plus the comment
    # recording the taskq-plus mid-run patch that made the gap visible.
    # 2026-08-03: +18 — Round 30 站6 (WITHDRAWN, kept as a comment): the plan
    # was to exclude .sessi-work/ and __pycache__ from the framework's gitleaks
    # run. Two measurements killed it — `--exclude-path` does not exist in
    # gitleaks 8.30.1, and this invocation is git mode, which never sees a
    # gitignored path anyway (probe: "1 commits scanned, ~20 bytes" vs ~56 under
    # --no-git). The note is longer than the two lines it replaces because the
    # next reader needs the measurements, not just the absence.
    # 2026-08-06 (Round 38 站4): +36 — advance-phase reconciles the exit gate
    # against gate_verify.jsonl before letting a phase through. state.json's
    # last_gate says the gate was finalized; it says nothing about
    # spec-coverage or the architecture floor, which the workflow checked
    # separately and then discarded. Placement is load-bearing and most of the
    # growth is the comment saying so: the check sits ahead of every write
    # advance-phase performs (it writes setup.cfg for the P2→P3 mutation-scope
    # sync), because a check placed after that compares against a tree
    # advance-phase itself just changed.
    # 2026-08-12: 3754 -> 3769 (+15). Round 47 站3: run-phase repairs at every
    # phase entry before blocking.
    "cli/phase_cmds.py": 4055,  # 2026-08-24: +50 — Round 72 站1: `_verify_entry_gate` gains `prev_record_pending` and reads the previous phase's record for CONTENT, not presence. Four lines are the keyword and the two-branch check; the rest is the comment recording the deadlock it ends — this function is called by `cmd_advance_phase` with `phase = completed_phase + 1` BEFORE that same function writes `phase_completed[completed_phase]`, so from `--completed 3` on it demanded a record only that call produces. taskq-new, the only project to run P4+ after Round 53 站5c landed, shipped six hand-written entries to get past it, the last reading `{"sha": "PLACEHOLDER_WILL_BE_REPLACED_ON_ADVANCE", "delivered_tree_sha256": "PLACEHOLDER"}` — which is why presence stopped being enough. The shape check itself is `core.harness_provenance.phase_record_defects`. Previous: 4005.  # 2026-08-22: +9 — Round 68 站2: the `record_runner_scope` call in `_regenerate_mutmut_scope`, placed ahead of that function's four early returns. One line is the call; the rest is the comment recording why it goes there — the function renders `paths_to_mutate` from the SAB and leaves `runner`, which decides which tests may kill a mutant and therefore what the mutation score is, entirely to the project. The runner is a fact about the project in every state this function can return in, and the project most likely to have hand-written one is the project with no SAB. The decision itself is core/quality_gate/mutmut_scope.record_runner_scope. Previous: 3996.  # 2026-08-18: +36 — Round 57 站1: `_gate1_per_fr_coverage_verdict` now runs at every phase Gate 1 runs, and reports two conditions apart. The growth is the second branch and its comment: a run blocked only by the whole-project floor must not read as a per-FR defect, because the operator would go looking for an FR to fix and find every one of them green. The `phase == 3` condition in front of the call is gone rather than generalised — Gate 1 declares `scope: single_fr` and this is Gate 1's check. Previous: 3960.  # 2026-08-17: +71 — Round 56 站6: `_gate1_per_fr_coverage_verdict`. `_check_gate1_live_coverage` is what returns 14 and stops advance-phase, and at Phase 3 — the per-FR TDD window, where Gate 1 is a per-FR gate — it asked one whole-project question and printed "whole-project coverage". Measured on taskq-cc: FR-01 at 97.06% on its own modules read as 8.5% because the SAB declares ten modules later FRs will activate, and the run spent three rounds dispatching CODE-FIX against a number that was never about FR-01. The loop itself is ten lines; the size is the per-FR report (an operator who is told "8.5%" cannot tell which FR to fix) and the docstring recording why an FR with no SAB scope falls back to the whole-project number — that figure carries every other FR's uncovered modules, so it is the strictly harsher answer, and falling back to a looser one would be the abstention Round 46 forbids. Previous: 3889.  # 2026-08-17: +64 — 7e26019: `PHASE_GATES` (phase -> the gate numbers that phase actually runs) plus `_phase_gate_tools`, which splits a missing-tool verdict into critical (a gate THIS phase runs needs it) and anticipated (a later phase will). `_cmd_run_phase_impl` used verify_all_gate_tools, so P1 entry demanded scancode — a tool only the P6 gate ever invokes — and a host whose pyicu ABI conflicts with the system ICU could not open Phase 1 at all. Most of the size is the PHASE_GATES table and the comment mapping each phase to its gates. Previous: 3825.  # 2026-08-16: +45 — Round 53 站5c: the P4+ entry gate gains a second condition, that phase N left a `phase_completed` record before phase N+1 may be entered. Four lines are the lookup and the refusal; the rest is the comment recording what the record holds (SHA, enforcer, delivered_tree_sha256 — Rounds 24, 26 and 44 each put a fact there) and why the ORDERING is deliberately not touched: the entry's `sha` is HEAD after the handover commit, which is what every consumer of it passes to `git merge-base --is-ancestor`, so writing it earlier would make it name the wrong commit. taskq-super reached Phase 9 with no entry for phase 5 and nothing objected. Previous: 3780.  # 2026-08-13: +7 — Round 50 站6: the advance-phase cleanup iterates core/evidence_retention.ADVANCE_CLEARED_DIRS instead of naming `.sessi-work` itself, so the list of directories a verdict may not cite and the list advance actually deletes are one statement. The body is the same backup/rmtree/restore block re-indented one level; the +7 is the loop, the import and the comment. Previous: 3773.  # 2026-08-13: +4 — Round 50 站4b: the comment on _regenerate_mutmut_scope recording why resolve_mutation_scope now takes project_root (a leaf module is a .py file, not a directory; eight real scope paths were discarded as non-existent while all eight modules were on disk) and the .is_dir() -> .exists() change beside it. Previous: 3769.  # 2026-08-12: +62 — Round 45 站5: `_run_doctor_after_advance`, called once at the end of cmd_advance_phase. `grep -rn "run_doctor"` over the repository found ONE call site — cli/project_cmds.py, which IS the doctor command — so Round 43 站4's enforcer provenance, Round 44 站4's milestone-tree check and Round 45 站3's per-FR evidence reconciliation had never been read at a phase boundary. The check itself is six lines; the size is the docstring recording the three ways the wiring is deliberately weak (runs after the advance, ERRORs only, exit code unchanged) and why — station 2 removed thirty false ERRORs from this same command four hours earlier, and a check with that history does not get to stop a pipeline. Previous: 3692.  # 2026-08-11: +155 — Round 44 站2: cmd_advance_phase refuses to record a phase on a tree git has not. `_uncommitted_deliverables` (git status --porcelain -z -uall, minus delivery_scope.is_harness_volatile and minus the maximal `_advance_commit_targets` — both existing single sources, neither restated), `_porcelain_paths` (the -z parser, including the rename record's second field), `_git_head_short`, the [BLOCKED] branch that names every file and writes one `milestone:uncommitted` degradation each, and the `delivered_tree_sha256` field in phase_completed beside enforcer_sha/enforcer_surface. Measured on taskq-advance: the entry obligation for FR-02/FR-06 was cleared at 13:14 by writing `@given` into two test files, `81bbeb4 handover: advance to Phase 4` recorded the phase at 13:17:55 without them, and they entered git at 13:32 — `git archive 81bbeb4 | grep -rl "@given"` is empty. Most of the size is the operator message (which of "commit it" or "gitignore it" applies depends on whether the file is a deliverable, and only the operator knows) and the docstring recording why the exemption set is those two sources rather than "all of .methodology/". 2026-08-11: +15 — Round 44: _MYPY_EXCLUDE_ARGS, a named constant used by _advance_prechecks's mypy subprocess.run call. `mypy .` from a consumer project's root has no submodule-awareness (unlike ruff, whose file-walker stops at a nested .git by default) and was walking straight into harness/tests/fixtures/mutmut_bare_cfg/03-development/tests/conftest.py — a fixture harness ships shaped like its own canonical layout — colliding with the consumer's real 03-development/tests/conftest.py ("Duplicate module named conftest"), a fatal error that aborted the whole type-check before examining anything else, for every project using the standard layout, the first time its own conftest.py existed and advance-phase's mypy step actually ran. Named as a module constant (not an inline literal) so it is independently testable — see tests/test_mypy_excludes_harness_submodule.py (sized to current 3537). 2026-08-07: +40 — Round 43 站4: _enforcer_moved_note, called from the obligation [BLOCKED] block. state.json has recorded enforcer_surface per completed phase since Round 19 站3 / Round 29 站4 and nothing compared it to the present, so a finding against a Phase-1 artifact on a project whose Phase 1 passed five rounds earlier read as "you broke this" when the truth could be "the bar moved". Diagnosis only — the verdict is not waived (grandfathering a rule to artifacts accepted before it existed is Round 38's no-waivable-threshold rule inverted: the framework could then never raise its own bar). Most of the size is the docstring making that distinction explicit, because the next reader's instinct will be to turn it into an exemption. 2026-08-07: +40 — Round 43 站2: cmd_advance_phase refuses to advance while the P(N+1) entry preview reports blocking findings. The block prints each finding by check/rule/file:line (R24 站1's rule that a [BLOCKED] carries the remediation, not a pointer to it), writes one `obligation:<check_id>` degradation record per finding, and returns EX_ADVANCE_ENTRY_OBLIGATIONS before _advance_fsm. Round 14 A computed this list and rendered it into HANDOVER.md, which has one producer and no reader; the state that produced — current_phase = N+1 while N+1's entry preflight fails — is what scripts/hooks/pre-push has to guess around by pattern-matching HEAD's subject. Partly offset by removing the obligations parameter from _advance_fsm and the HandoverGenerator.write call (dead once the advance cannot happen with obligations outstanding). The growth is the operator message and the comment recording why refusing beats advancing-and-warning. 2026-08-07: +27 — Round 43 站1: cmd_run_phase now owns the bounded traceability repair. preflight_all() returns, and if the traceability result is blocking-and-failed with an open FR gap the command calls PhaseHooks.repair_traceability_gap, re-runs that one check, and recomputes all_passed. The repair used to run inside preflight_traceability, which made preview_next_phase_blocking — documented as mutating no state — write to the project on every advance from P4 with an open gap. The lines here are the guarded call plus the comment recording why the caller, not the check, decides to write. 2026-08-06: +91 — Round 39: cmd_advance_phase now calls _verify_entry_gate at L526 (normal advance) and L430 (re-verify mode), before _advance_fsm and before `git add` at L802. Earlier Round 38's recovery helper (called from prepare-commit-msg hook) wrote to the working tree only — `git add` had snapshotted the pre-recovery state.json into the index first, so the commit materialized the orphan SHA, not the recovered one (observed on taskq-api 2026-08-05: cadbd6a state.json carried d061387). Calling the gate directly inside cmd_advance_phase means recovery writes happen before staging, so the handover commit captures the healed SHA. Mirror of cmd_run_phase:1695. The size is the two gate call blocks (~16 lines each) plus a docstring explaining why ordering matters. Re-verify mode (L430) gains the same call so a manual `git reset` followed by re-verify also self-heals state.json — the user's `不要改 taskq-api` constraint forces this defensive coverage. 2026-08-06: +26 — Round 39 secondary: _advance_prechecks for completed_phase >= 3 now also runs PhaseHooks.preflight_sab_check after the DriftDetector block. preflight_sab_check (phase_hooks.py:613-691) already validated allowed_dependencies — but only via preflight_all() in cmd_run_phase:1701 (pre-push), never at advance-phase. Without this wire, a hand-edited SAB.json or one from a SAD.md block that slipped past the now-extended validate_sab_block would reach the handover commit. Wrapped in try/except for resilience (parallel to existing DriftDetector pattern). The SAB-missing branch is a separate early-return that avoids tripping preflight_sab_check's "SAB.json not found" path at P3-entry — the DriftDetector block above already covers module existence, and we don't want advance-phase to be stricter than the canonical pre-push path. 2026-08-06: +43 — Round 38 self-heal dangling phase_completed SHA in _verify_entry_gate. push-checkpoint writes pre-push HEAD to state.json before its commit; an out-of-band `git reset HEAD~N` between the write and the commit leaves the recorded SHA as an orphan (confirmed in taskq-api: d061387 recorded as phase_completed[2].sha, unreachable from HEAD 3836985, both parented at 4355bb3). Recovery lives in core/quality_gate/phase_completed_recovery.py — captures explicit HEAD, searches `--grep phase{prev}(review-complete)` HEAD-reachable history, validates ancestry, lock+reload+compare+atomic_write, appends to top-level phase_completed_recovery_log. _verify_entry_gate's existing hard-fail at L1799-1802 is now preceded by a PASS-with-self-heal branch; the docstring block above it is what grew. cmd_advance_phase's post-commit writer at L834-898 now merges — not replaces — phase_completed[completed_phase] so a recovery audit set during prepare-commit-msg survives into the handover commit. Same lock contract as the rest of the file. The actual code is in the new helper; the lines added here are the import, the call-site + its reason string, and the merge-preserve metadata extraction. Net-neutral refactor considered and rejected: extracting the recovery protocol into a private helper inside this file would have hidden the gate's hard-fail reason behind a function call, which is the readability loss the existing inline structure was chosen to avoid. 2026-08-04: +89 — Round 34 站2: _broken_deliverable_anchors plus its [BLOCKED] branch. Round 33 站1 gave the H1 rule a single SOURCE; it had no single MOMENT, so a deliverable that satisfied the anchor at P1 and was rewritten at P4 satisfied nothing thereafter. Measured on run-all-by-workflow's TRACEABILITY_MATRIX.md: correct at dfd7abd, blank first line from fa21439 (the P3→P4 advance) onward, green through Gate 4 and P8 with last_gate 4, on four of five real projects. Placed after _regen_traceability_views so the views the framework owns are repaired first and only files it may not rewrite can reach the BLOCK. The size is the operator message (which of the two populations the file belongs to changes the remedy) and the docstring recording why the scan is registry-wide rather than phase-scoped. 2026-08-04: +26 — Round 33 站3: the P1-exit NFR vocabulary check. SRS.md states `type:` and `dimension:`; sab_parser is the only enforcement of the first and it runs in Phase 2, by which point the value sits in an approved deliverable SAD.md must transcribe verbatim — measured five B-review rounds to the HR-12 hard cap with no convergence possible. Placed before every other P1 check because a vocabulary error makes every downstream reading of the file wrong and the fix is one word. 2026-08-04: +42 — Round 33 站2: _warn_if_view_lost_its_anchor, called from _regen_and_stage_view. A view regenerated from SSOT replaces a peer-reviewed deliverable, and it did not inherit that deliverable's loader anchor: measured with the framework's own read-file, TRACEABILITY_MATRIX.md returned PREFIX_MISMATCH on 4 of 4 real projects because the H1 sat below the AUTO-GEN sentinel. Recorded in the degradation ledger rather than blocking — the anchor is only read on re-entry into Phase 1, and the defect was the framework's own, so blocking here would stop every existing project on our bug. The size is the docstring explaining that WARN-not-BLOCK choice; the check itself is six lines. 2026-08-03: +29 — Round 32 站2: the finalize-gate sentinel check reads a receipt and cross-checks it against gate_timestamps.jsonl and .gate1_scores.json (gate1_evidence.verify_finalize_evidence, the same function core/doctor.py calls) instead of asking .exists() of a file whose whole content was a timestamp. The growth is the second [BLOCKED] branch: "present but unbacked" is a different diagnosis from "absent" and needs its own message, because the remedy reads the same but the finding does not. 2026-08-02: +89 — Round 30 站2: _regenerate_mutmut_scope renders [mutmut] paths_to_mutate into setup.cfg from the SAB at the P2→P3 handoff, and the advance commit stages it. The scope is a decision; before this it lived in no artifact at all, and taskq-advance mutated 3384 lines against a SPEC that limited Gate 2 to 1846. The four ledger branches (unreadable SAB / no scope_layers / directories that do not exist / hand-edited value replaced) are the bulk of the size: each one is a distinct thing the next reader needs told, and collapsing them loses the diagnosis (sized to current 2953→3042). 2026-07-29: -28 — Round 25 站3: the fastapi/httpx advisory is deleted (unconditional, hardcoded to a Python web stack, WARN-only) and _check_submodule_drift moved to core.doctor._check_submodule_behind (advance-phase's only network call, blocking nothing). Harvested below the 2970 station 1 raised it to (2970→2942). 2026-07-29: +8 — Round 25 站1: the TDD block's `--cov-fail-under=100` became an explicit comparison against the exact coverage percentage from the shared suite run (core/quality_gate/test_suite_run.py), so the same measurement can also answer FrameworkEnforcer's 70/80 and Phase Truth's without three more executions of the same tests. The [BLOCKED] branch now distinguishes "tests failed" from "coverage short" instead of leaving pytest to render one verdict for both (sized to current 2962→2970). 2026-07-28: +41 — Round 23 站1: cmd_advance_phase gains an opt-in `--push` that publishes the handover commit it just made, plus its [BLOCKED]/no-rollback branch and the subparser flag. The push previously lived in every phase workflow's Sync box — prompt-layer only, so a human or CI caller never got it (same shape Round 22 站2 relocated for manifest integrity) (sized to current 2864→2905). 2026-07-27: +6 — Round 29: run-phase auto-skips the spawn-substrate preflight probe when CI/GITHUB_ACTIONS is set — CI never dispatches an interactive per-FR loop, so the probe (which requires the claude CLI, never present there) can only ever fail (sized to current 2825→2831). 2026-07-26: +80 — Round 14 A2/A4: cmd_advance_phase now previews P(N+1) entry blocking via PhaseHooks.preview_next_phase_blocking(), threads obligations into HandoverGenerator.write + _advance_fsm, and replaces "Ready to begin Phase N+1" with a pointer to the obligations table (sized to current 2768). 2026-07-26: +33 — Round 15 §2: new cmd_preview_next_phase() + preview-next-phase subparser — a read-only P(N+1) obligation query that never writes state.json/HANDOVER.md/a commit, usable before P(N) exit gate even passes (sized to current 2813).
    # 2026-07-11: +35 lines — _fr_step_already_done's idempotency grep is now
    # scoped to the current phase's lineage boundary (read from tracked
    # state.json phase_completed), fixing a false "already done" skip on
    # reset-and-rerun projects (TDD-IMPROVE had no secondary evidence check).
    # 2026-07-12: +39 lines — dispatch failures now fail fast on the
    # deterministic "claude.ai connectors are disabled" signature (shared
    # _abort_dispatch_structurally_broken() helper, 2 call sites) instead of
    # exhausting max_fix_rounds against an environment that cannot ever
    # succeed (P3 2026-07-12 FR-04 GATE1: 5.4h silent retry loop before the
    # external workflow watchdog aborted).
    # 2026-07-12: +26 lines — COVERAGE-FIX's measurement command now scopes
    # to the FR's own owned source via the shared
    # core.quality_gate.cov_utils.resolve_fr_scoped_src_files() (same
    # resolver run-gate --fr-id already uses), instead of the whole
    # 03-development/src tree — the whole tree was unsatisfiable while
    # sibling FRs' stub modules sit at 0% coverage (P3 2026-07-12: FR-01/
    # FR-02 both BLOCKED after 2 no-progress rounds chasing the wrong
    # denominator).
    # 2026-07-13: +21 lines — Round 9 station 2: run-fr-step's tunables now
    # read the harness_config `values` section (permission_mode /
    # max_fix_rounds / fr_step timeout / step_max_turns overlay with
    # unknown-step WARN); precedence chain unchanged and locked by
    # tests/test_fr_cmds_values_wiring.py.
    # 2026-07-13: +3 lines — FIX-O: cmd_run_fr_step's first-dispatch error
    # branch (TDD-RED/GREEN/IMPROVE + GATE1's pre-fix-loop attempt) now calls
    # _is_connector_disabled_failure/_abort_dispatch_structurally_broken,
    # mirroring the two other dispatch sites in this file that already had it.
    # 2026-07-13: net -2 lines — Bug D: SRS.md path resolution was hard-coded
    # to the wrong location (.methodology/SRS.md) in two call sites while
    # _fr_step_preflight's own fallback list (never reused) had the correct
    # 01-requirements/SRS.md entry (P3 FR-05: resume-fr-phase's suggested
    # command and TDD-RED's prompt builder both failed preflight with "SRS.md
    # not found"). Both sites + preflight's own fallback loop now call the
    # existing ProjectLayout(project).srs_path (single source of truth already
    # used by 14+ other call sites across the harness — phase_cmds.py,
    # harness_bridge.py, spec_alignment.py, ...) instead of guessing among
    # hard-coded candidate strings; dropped the wrong --srs flag from
    # resume-fr-phase's printed command.
    # Bug F: TDD-RED's prompt gives sub-agents no instruction for a test file
    # that already exists but is uncommitted (P3 FR-05: a sub-agent found
    # test_fr05.py surviving a mid-flight reset, chose "review, don't
    # overwrite", and never ran the commit step) — step 1 now says explicitly
    # that an existing-but-uncommitted file still requires completing step 5.
    # 2026-07-15: +5 lines — Fix H-B (defense-in-depth for H-A): _DISPATCH_ERROR_STATUSES
    # gains two inner-JSON semantic no-op signatures ("AWAITING_CONFIRMATION",
    # "NOTHING_TO_DO") so direct callers that reflect an inner-agent status
    # string are caught even if AgentSpawner._validate_inner_json is bypassed
    # (cf. P3 2026-07-15 FR-03 TDD-RED where transport exit 0 + status="AWAITING_CONFIRMATION"
    # silently passed every per-FR slot at the cost of one wasted FR).
    # 2026-07-15: +6 lines — Fix H-C: import _COMMIT_REQUIRED_STEPS SSOT from
    # core.agent_spawner (the validator-side frozenset introduced in H-A) and
    # replace 2 inline commit-required lists in run-fr-step (lines 738-739 +
    # 750-751) so they stay in sync with the validator. Net: a single SSOT
    # defines "steps that must produce a commit" and every consumer reads it.
    # 2026-07-15: +1 line — Fix H-F: the fix-loop dispatch in run-fr-step
    # (the single caller that knows its current round) now passes
    # `retry_round=fix_round` to spawner.spawn() so sessions_spawn.log
    # entries identify which fix iteration produced them.
    # 2026-07-15: +36 lines — Fix H-H (P3 2026-07-15 round 4): the first
    # dispatch for TDD-RED/GREEN/IMPROVE/MIRROR/amend-sab/ORCH-POST (all
    # _COMMIT_REQUIRED_STEPS except GATE1/GATE1-DELTA, which already had
    # their own fix-round retry loop) previously had zero retry on any
    # dispatch ERROR — production sessions_spawn.log evidence showed this
    # permanently killed an FR's progress on a single transient failure.
    # Wraps the dispatch in a bounded (2-attempt) plain re-dispatch loop
    # (identical prompt, no failure classification — unlike GATE1's
    # CODE-FIX/LINT-FIX routing) plus the new _STEP_RETRY_ATTEMPTS constant.
    # 2026-07-16: +11 lines — Round 12 站0a/0d: dispatch-prompt commands
    # switched to allowlist-compatible `python3 -m` module forms (guarded by
    # tests/test_dispatch_prompt_command_forms.py), and
    # _resolve_phase3_context's docstring now records the measured
    # --setting-sources semantics matrix ("project" also loads the USER's
    # global CLAUDE.md — the leak that stalled headless agents on
    # 2026-07-16) with setting_sources pinned to "".
    # 2026-07-16: +6 lines — Round 12 站3b: GATE1 evaluator STOP RULE
    # gains the INFRA_BLOCKED branch (run-gate precondition block → do NOT
    # write zeros; report the verbatim BLOCK) ahead of the per-tool
    # score=0 rule.
    # 2026-07-17: +16 lines — Fix: GATE1's record_gate_timestamp() append to
    # gate_timestamps.jsonl is now landed with a scoped commit immediately,
    # instead of being left dirty for the very next dirty-tree guard to
    # misreport as "commit did not land" (FR-01 GATE1 false-FAIL repro).
    # 2026-07-17: +11 lines — Round 13 站1: exception-swallow ratchet
    # paydown — 10 previously-unlogged broad excepts now print a [WARN]
    # diagnostic (or, for the CRG-search/git-sha sites, a one-line reason).
    # 2026-07-17: +74 lines — Round 13 站2a/2b: _classify_infra_or_harness_bug
    # + _abort_dispatch_infra_or_harness_bug (HARNESS_BUG/INFRA short-circuit
    # in the fix-round loop — do not dispatch CODE-FIX at a problem no code
    # change can resolve) + the UNKNOWN-exhausted hint at loop exhaustion.
    # 2026-07-24: -839 lines — Round 17 站4 (finding D): _build_fr_step_prompt extracted into cli/fr_prompts/ façade package.
    # 2026-07-30: +60 lines — Round 26 站4: turn-budget escalation. `_max_turns`
    # gained the once-per-step doubling and `_note_turn_budget_kill` records the
    # kill (degradation ledger) and tells the caller the step ran out of room
    # rather than hitting a code defect — the two dispatch sites then re-dispatch
    # the SAME step instead of handing a cut-off evaluator to CODE-FIX. Both live
    # here because both are this function's retry decisions; the shared
    # classification they read is in core/agent_spawner.
    # 2026-08-02: +51 — Round 30 站5: _timeout_for / _note_wallclock_kill give
    # the wall-clock budget the same once-per-step escalation the turn budget
    # has had since Round 26. Round 29 站5 made a timeout visible and left the
    # retry at the identical ceiling; taskq-advance P3 then spent 12 of its 18
    # failed dispatches on 600s timeouts, four consecutive on FR-02, each
    # re-dispatched into the same wall. Sized as a mirror of the turn-budget
    # pair immediately above it (sized to current 2040 -> 2091).
    # 2026-08-06: +35 — Round 41 站1: `_fr_tests_say` plus the restructuring of
    # the TDD-RED / TDD-GREEN branches of `_fr_step_already_done` so both ask
    # the FR's own test family whether the step's defining condition holds
    # instead of inferring it from a commit message. The measurement itself
    # lives in core/quality_gate/test_suite_run.py (`fr_suite_verdict`); what
    # is added here is the decision that consumes it, which is where the
    # decision belongs. taskq-api's FR-04 sat behind that inference for 3h11m
    # and $6.02 with a red suite the framework had never asked about (sized to
    # current 2126 -> 2126).
    # 2026-08-06: +73 — Round 41 站2: `_reports_precondition_block` and
    # `_resolve_precondition_block` — a step that reports an unmet precondition
    # is checked against the framework's own suite run (Round 35's rule applied
    # to a new claim) and, when the claim holds, aborted with the remediation
    # instead of being re-dispatched into the identical refusal. Most of the
    # weight is the [BLOCKED] message itself, which exists precisely because
    # taskq-api's operator had none: eight identical failures and no sentence
    # saying what to do (sized to current 2199 -> 2199).
    # 2026-08-06: +56 — Round 41 站3: the pre-dispatch check that reads what
    # earlier PROCESSES already spent on this step, and `_abort_repeated_failure`
    # to explain the refusal. The memory itself is a new module
    # (core/step_failure_memory.py) rather than more of this file; what lands
    # here is the call site and its [BLOCKED] message (sized to current
    # 2255 -> 2255).
    # 2026-08-06: +12 — Round 41 站4: TDD-RED stops asking the tree once GREEN
    # has landed. GREEN's job is to destroy RED's evidence, so a tree-only
    # answer sent resume-fr-phase back to TDD-RED for every completed FR — a
    # loop introduced by 站1's fix for the other one, found by the new
    # black-box journey rather than by any unit test (sized to current
    # 2267 -> 2267).
    # 2026-08-12: Round 47 站3 wired env repair into the callers that PREPARE.
    # Each site is the same shape: detect (existing call, unchanged) -> repair
    # once -> RE-detect -> block with the true cause. The repair lives at the
    # call site rather than inside the check because a function whose contract
    # is "(ok, errors)" must not install things (Round 43 站1). Most of the
    # growth is the [BLOCKED] text, which had to stop pointing at two documents
    # for install commands and start naming the one SSOT, and the comment at
    # each site recording why THAT caller may repair.
    # cli/fr_cmds.py 2267 -> 2283 (+16): run-fr-step repairs before a GATE1/
    # CODE-FIX step, because a P3 run is hours long and a tool can vanish
    "cli/fr_cmds.py": 2393,  # 2026-08-23: +11 — Round 70 站2: `_abort_dispatch_infra_or_harness_bug` returns EX_HARNESS_BUG for the HARNESS_BUG class and keeps 25 for INFRA. One line is the branch; ten are the docstring recording that the function computed `cls` and then discarded it, and that 70 is the crash boundary's existing code rather than a new one. Previous: 2382.
    # 2026-08-23: +4 — Round 70 站1: the FR-99 recovery diagnostic's `score_gate` stops being a literal and reads `effective_score_gate(1)`, the same function finalize_gate resolves its own bar with. The literal had been wrong twice (100.0, then 80.0 while the verdict used 1.0). Previous: 2378.
    # 2026-08-23: +12 — code-review follow-up: `_detect_evaluator_passed_but_commit_uncommitted`'s `score_gate` default corrected from 100.0 to 80.0 (the real Gate 1 pass bar per cli/fr_prompts/gate.py's prompt text and harness_bridge.py's `_gt = ctx.config.get("score_gate", 80)`; ssi/scripts/score.py's 85 default is dead for this call path). Previous: 2366.
    # 2026-08-23: +1 — code-review follow-up: `_abort_no_progress_with_self_doubt` gained a `phase: int` parameter (was printing the step-name string into `--phase`, which argparse rejects as a non-int). Previous: 2365.
    # 2026-08-23: +82 — commit f0de7ea: `_detect_evaluator_passed_but_commit_uncommitted` helper + `[HARNESS-BUG]` recovery banner in `_abort_no_progress_with_self_doubt` for FR-99 non-convergence recovery, using `core.state_io.load_quality_manifest`. Previous: 2283.
    # 2026-07-21: +4 lines — review fix on fix/fr-step-already-done-cascade: reverted the sub-change that relaxed `if not committed: return False` to only fire for GATE1/GATE1-DELTA (it let TDD-RED/TDD-GREEN mark themselves done from a leftover, uncommitted artifact alone — reproduced live; commit evidence is a hard requirement again for every step). The GATE1 sentinel+quality_complete cascade already covers the phase-boundary scenario (FR-02 GREEN commits pre-dating the boundary) that relaxation was meant to fix, so no expressiveness is lost. Also replaced the multi-tag docstring scan's 4 unanchored substring patterns with a `[...]`-bracket-anchored, exact-tag-set match (`re.findall(r"\[([^\]]*)\]", text)` + membership check) — the substring version could false-positive match an unrelated prose comment like "# see FR-03, FR-09" with no enclosing brackets at all (also reproduced live).
    # 2026-07-21: +15 lines — fix/round-18-dispatch-ssot (Bug B): import `PRAGMA_NO_COVER_ALLOWLIST` alongside `PRAGMA_NO_COVER_GUIDANCE` and render the allowlist verbatim in the COVERAGE-FIX prompt so future widening of the tuple auto-propagates; replace the contradictory `raise NotImplementedError` example with one that matches the SSOT (`except BaseException: pass`).
    # 2026-07-21: +1 line — fix/round-18-dispatch-ssot (Bug A): add `"AMEND-SAB"` key to `_FR_STEP_COMMIT_PATTERNS` so `_fr_step_already_done` short-circuits a re-run whose amend-sab commit is already in git log.
    # 2026-07-24: +7 lines — Round 17 站1 (finding A): import _GATE_DIMENSION_STANDARD and source the GATE1 prompt's linting/type_safety/test_coverage threshold floor from it, instead of a hand-copied `max(90.0/85.0/80.0, override)` — a second, unbound copy of gate1_per_fr.yaml's thresholds (parity now enforced by tests/test_prompt_gate_parity.py). This peak is temporary: 站4's per-step façade split moves the ~719-line `_build_fr_step_prompt` out of this file and harvests the ceiling back down.
    # 2026-07-24: +45 lines — Round 17 站2 (finding B): `_abort_no_progress_with_self_doubt` — when the fix-round loop hits 2 consecutive no-progress rounds, record the inescapable loop to the degradation ledger (was a silent `return 2`, invisible to run-report) and emit a gate-bug self-doubt channel (a deterministic same-error loop that survives 2 fix rounds may be a harness gate-calculation bug like the #20 spec-cap class, to be reported [HARNESS-BUG] not code-fixed forever). This is a fix-round helper, not the prompt builder, so 站4's split does NOT harvest it — durable finding-B observability growth.
    # 2026-07-21: +25 lines — review fix on fix/sab-sync-improve-to-gate1: the AMEND-SAB delegation branch returns before the general post-step dirty-tree guard / `_COMMIT_REQUIRED_STEPS` check can ever run (early return), and that SSOT set stores this step lowercase while `step` here is always upper-cased, so neither backstop would fire even if reached. Added a dedicated post-call `git status --porcelain -- .methodology/SAB.json` check that BLOCKs (exit 6) when `cmd_amend_sab` mutated SAB.json but it was left uncommitted, instead of only fixing the (now-incorrect) comment that claimed those backstops applied.
    # 2026-07-21: +15 lines — fix/sab-sync-improve-to-gate1: deterministic `amend-sab` dispatch branch (`run-fr-step --step amend-sab` delegates to `cmd_amend_sab` without spawning an LLM; SSOT-bridged from `agent_spawner._COMMIT_REQUIRED_STEPS` into the argparse `choices`) + extended comment block.
    # 2026-07-18: +55 lines — Fix 1a: TDD-RED subprocess coverage ceiling warning in INTEGRATION FR GUIDELINES (~22 lines) + Fix 1b: COVERAGE-FIX subprocess detection with in-process test guidance (~28 lines) + Fix 2: dynamic threshold overrides from quality_manifest gate_score_overrides (~5 lines)
    # 2026-07-17: +30 lines — dirty-tree guard bug fix: pre-step `git status --porcelain` baseline (set, captured under the same `step in _COMMIT_REQUIRED_STEPS` gate as the post-step guard) + the guard itself swapped from whole-tree strip to sorted(post - pre) diff + extended comment block. Mirrors the pre-dispatch _pre_step_sha + detect_ghost_changes idiom below for working-tree granularity instead of commit-level.
    # 2026-07-12: +3 lines — Round 5 建議2站2: same load_harness_script
    # migration for the parse_srs_fr_sections/parse_sad_modules call sites.
    # 2026-07-13: +7 lines — audit-phase subparser gained a `description=`
    # clarifying it must run BEFORE advance-phase for a phase-scoped C10
    # result (no workflow JS ever calls it automatically).
    # 2026-07-24: +1 line — pyright type narrowing fix for L647 trig union.
    "core/quality_gate/gate1_evidence.py": 1186,  # 2026-08-18: +58 — Round 57 站3: the `FrCoverage` record (percent / executed / coverable / files) plus `fr_coverage_record` and the split of `_coverage_for_paths` into the record producer and the thin percentage wrapper its one fallback-holding caller still needs. `_coverage_record_for_paths` already computed both sides of the ratio and threw them away, which is how S4 came to write a per-FR percentage into `score` while `tool_output` cited the whole-project audit whose last line reads TOTAL 62% (Round 42 站4 — the denominator travels with the number; Round 45 — a verdict may not outlive OR contradict its proof). `measured` is a property rather than a second `percent is None` comparison beside the first, which is how two readers come to disagree about whether 0.0 is a measurement. Previous: 1128.  # 2026-08-18: +26 — Round 57 站2: `_fr_module_paths` becomes one call to `cov_utils.resolve_fr_scoped_src_files`, the resolver two production sites already read, and `_coverage_for_paths` learns to expand the coverage-style globs that resolver emits for package-style SAB entries. The code shrank; the growth is the docstring recording what was measured before the swap — across all seven corpus projects and every FR (61 values) the two resolvers produced identical numbers, so this is a latent divergence being closed, not a live wound, and `**` is expanded with Path.glob rather than fnmatch because fnmatch would require an intervening directory and miss `executor/runner.py`. Previous: 1102.  # 2026-08-18: +12 — 57bce59: fr_coverage_from_last_run & _fr_module_paths accept Path | str to prevent S4 TypeError.  # 2026-08-17: +29 — Round 56 站6: `fr_coverage_from_last_run`, the public per-FR entry point the Phase 3 gate calls once per FR. `validate_fr_coverage_immediate(fr_id=…)` also scopes per FR but goes through `run_suite` first, which is right for its own caller and wrong for a gate asking about every FR in turn — Round 25 站1's one-execution invariant. This one executes nothing: the suite has already run and the answer is arithmetic over the `.coverage` on disk. It returns None rather than a number when the per-FR scope cannot be computed, so the caller can keep "could not measure" apart from "measured and failed" (Round 32 站4). Previous: 1061.  # 2026-08-17: +122 — 7e85f24: per-FR coverage scope at P3 gate (_coverage_for_paths, _fr_module_paths, _is_phase3_per_fr) so empty phantom modules stop dragging the score.  # 2026-08-12: +17 — Round 45 站6: _per_fr_result_problems skips the digest comparison when the artifact on disk belongs to a later phase than the receipt. gate_results/gate1/{fr}.json carries no phase — one slot per FR, rewritten by every phase that re-runs it — so taskq-advance's P8 run left five FRs holding phase-8 results beside phase-7 receipts. Without this the check would fire for every FR at every phase boundary forever. The size is the comment recording that measurement.  # 2026-08-12: first entry (was 858, under the 900 default) — Round 45 站3: `per_fr_result_path` as the SSOT the two cli/_shared.py resolvers and cli/gate_cmds.py's writer now call instead of spelling `.methodology/gate_results/gate{N}/{fr}.json` themselves, plus `_per_fr_result_problems` (dereference the receipt's result_sha256 against that file) and `_deleted_by` (name the commit that removed it). Most of the growth is the docstrings recording why a schema-1 receipt's digest is not compared — the alias it pointed at is overwritten by the next FR's finalize, so comparing it would manufacture one false accusation per FR. Station 2 also DELETED the two retention windows from this file, so the net is smaller than the additions.
    "core/quality_gate/red_assertion_check.py": 1020,  # 2026-07-26: +6 — Round 14 B1: SubAssertion gains fulfill_phase field (1 dataclass line + 5-line docstring note) for the Direction-B TEST_SPEC Properties fulfill-phase schema (sized to current 1011).
    # 2026-07-17: +10 lines — Round 13 站1: exception-swallow ratchet
    # paydown — 13 previously-unlogged broad excepts now print a [WARN]
    # diagnostic.
    # 2026-07-30: +46 lines — Round 26 站3: `amend-sab --resolve-phantom` — the
    # SAB -> code direction the protocol's own BLOCK message offers ("(b) amend
    # SAB.json") and only ever had tooling for the opposite direction. The four
    # new flags and the refuse-or-delegate branch live beside cmd_amend_sab
    # because they are the same command's other half; the amendment logic itself
    # is in core/quality_gate/sab_amender.py with phantom_modules, which decides
    # what needs amending.
    # 2026-07-30: +66 lines — Round 26 站6: load-context reports enforcer skew.
    # `_enforcer_skew_warnings` compares the current harness commit against the one
    # recorded for every completed phase and appends to the warnings list this
    # command already carries. It lives here because load-context is the read every
    # phase passes through; a check placed anywhere else would be one more thing
    # nobody runs.
    # 2026-08-12: 2160 -> 2169 (+9). Round 47 站2: step [10b]'s body SHRANK by
    # 24 lines — the hand-rolled "check importability in THIS process, install
    # into some other one" logic is gone, replaced by a call into
    # scripts/bootstrap_env.py. The net growth is the new `bootstrap-env`
    # subcommand (its own function plus its parser), which exists so the same
    # implementation is reachable from the CLI and from env repair rather than
    # being init-project's private step.
    # 2026-08-12: 2169 -> 2192 (+23). Round 47 站3: init-project [11/11]
    # installs instead of printing three unpinned prose lines (which were also
    # the fourth and fifth copies of pins stated elsewhere). The `_gate_tool_gaps`
    # closure exists so the detect step is written once and run twice.
    "cli/project_cmds.py": 2210,  # 2026-08-21: +5 — Round 66 站3: `status --full`'s two pytest runs and the `gh repo view` fallback stop calling subprocess directly. The two pytest calls go through core.quality_gate.source_tree_lock.run_against_source_tree, which waits out any in-flight mutation window before measuring and reaps the run's xdist workers when the 30s/120s budget expires; `gh` gets run_isolated only, because reading GitHub metadata must not queue behind mutmut. Three of the five lines are the two local imports and the second call's continuation; the rest is the comment recording why the two sites use different primitives — the next reader's instinct will be to make them the same. The function-local `import subprocess` it replaced is gone. Previous: 2205.  # 2026-08-21: +13 — Round 65 站2: load-context reports `test_target` / `cov_target` from core.quality_gate.test_suite_run.resolve_targets, so the P4 coverage prompt reads where this project's tests are instead of naming `03-development/{tests,src}` itself. Five of the thirteen lines are the call and the two new payload keys; the rest is the comment recording why it is unguarded (resolve_targets is pure path resolution, and a load-context that cannot say where the tests are has nothing to hand the next agent). The prompt-side subtraction is larger than this addition — run-all.js shrank, and one prose restatement of the scoping rule left Phase 4 step 1 with it. 2026-08-06: +12 — Round 40 站1: the CI workflow path and the template path move to core/ci_template.py (one home for both), and the `already exists` branch now says whether the existing copy is still the template. 2026-07-28: +50 lines — fix/cli-init-project-step-10b: init-project step 10b auto-installs pyyaml+jsonschema from harness/requirements.txt into project .venv to eliminate first-call ModuleNotFoundError crashes. 2026-07-27: +13 — Round 29: _check_content_quality gained two per-file-type exemptions (MAINTENANCE_LOG.md from section-count floor, TEST_RESULTS.md from FR-ref rule).
    # 2026-07-21: +38 lines — fix/round-18-dispatch-ssot (Bug C): wrap cmd_amend_sab with a deterministic-tool sessions_spawn.log entry written from the mutation site (covers all callers — standalone amend-sab subcommand + run-fr-step amend-sab delegation + any future caller). Splits the function body into _cmd_amend_sab_impl returning (rc, outcome_tag) and a new _log_amend_sab_outcome helper that mirrors AgentSpawner._log_dispatch's swallowing try/except.
    # 2026-07-15 R3: cmd_amend_sab gained PHANTOM block + --strict (~50 lines)
    # 2026-07-16: Round 12 站0 — agent_spawner crossed the unlisted-file
    # threshold (900) with three additions: _extract_dispatch_error /
    # _denoise_cli_stderr (站0c error-capture denoise — closes the 76×
    # banner-only observability black hole), _UNATTENDED_PREAMBLE +
    # --append-system-prompt (站0d isolation against the measured
    # setting-sources user-CLAUDE.md leak), and preflight_substrate()
    # (站0b probe). All three are dispatch-substrate concerns that belong
    # with spawn(); splitting them out would separate the probe from the
    # substrate it measures.
    # 2026-07-17: +46 lines — Round 14 站0: claude -p envelope cost/turns/
    # usage fields (total_cost_usd, num_turns, duration_api_ms, usage
    # sub-keys) were parsed then discarded — _extract_envelope_metrics +
    # its two module constants, plus the _envelope threading through
    # spawn()'s three post-JSON-parse _log_dispatch call sites and
    # _log_dispatch's own new parameter, are the capture.
    # 2026-07-18: +45 lines — GATE1/GATE1-DELTA evaluation blocked empty commit
    # fix (pass=false/commit=null is a normal fail, not an ERROR) and the
    # sub-agent --allowedTools Bash,Read,Edit,Write fix for upstream bug 37442.
    # 2026-07-18 (Fix H-2): +53 lines — _extract_inner_result_json: the CLI
    # envelope's "result" field is a free-text string, not the sub-agent's
    # own JSON reply; _validate_inner_json and spawn()'s success-path
    # "commit" field were both reading status/commit/pass straight off the
    # envelope (always absent there), so this new helper unwraps it first —
    # see the function's own docstring for the sessions_spawn.log evidence.
    # 2026-07-26: +18 lines — Round 19 站1. Two INFRA signatures the CLI
    # actually emits ("stream idle timeout", "session limit") plus the comment
    # recording that they came from a real corpus rather than guesswork, and
    # _log_dispatch now writing `inner_status` — the field core.failure_modes
    # ._is_semantic_noop reads and which had never been emitted, leaving that
    # rule unable to match any real entry.
    # 2026-07-26: +33 lines — Round 19 站2: _envelope_metrics_from_stdout, and
    # wiring it into the non-zero-exit branch. That branch already json.loads()
    # its stdout to lift the error text (_extract_dispatch_error) and threw the
    # cost/token fields sitting in the same dict away, so failed dispatches
    # logged no cost: 2 of taskq's 19 failures carried one against 50 of 50
    # successes, while those failures burned 1.30h of wall clock.
    # 2026-07-30: +70 lines — Round 26 站2. Three additions, all on the
    # semantic-failure path that already lives beside spawn(): the
    # _INNER_BLOCKED_SIGNATURES registry (the INFRA_BLOCKED status the Gate 1
    # prompt orders and nothing consumed), `blocked_inner_status_in` (one
    # definition of that test, reachable both from a live inner-JSON read and
    # from re-derivation over an entry already on disk), and `_error_result`,
    # which exists so a synthetic diagnostic can no longer REPLACE the
    # sub-agent's reply — the rewrite that made Round 13's INFRA guard
    # unreachable in the one case it was built for. Not split out: all three are
    # dispatch-result classification, and separating them from
    # _validate_inner_json would put the registry in a different file from its
    # only reader, which is the shape of defect this station removed.
    # 2026-07-30: +25 lines — Round 26 站4: `turn_budget_exhausted` and the
    # TURN_BUDGET branch in _classify_dispatch_error. The max-turns literal now
    # has one home here; core/failure_modes._is_dispatch_timeout calls it instead
    # of restating it, which is how the two classifiers over this one output came
    # to disagree (log said EXECUTION_ERROR, MAST said dispatch_timeout, and the
    # deciding one was the blind one).
    # 2026-08-06: +3 — Round 41 站2: PRECONDITION_BLOCKED joins
    # _INNER_BLOCKED_SIGNATURES, with the incident that named it. One registry
    # member and its provenance; the routing it enables lives in cli/fr_cmds.py
    # (sized to current 1263 -> 1263).
    # 2026-08-06: +23 — Round 41 站3: the `transport_error` field on both
    # failure shapes plus its _log_dispatch clause, and the comment explaining
    # why a signature registry can never tell "the API returned 401" from "the
    # test asserts 401" — provenance is the only thing that can, and the field
    # IS that provenance (sized to current 1286 -> 1286).
    # 2026-08-12: first entry (was 882, under the 900 default) — Round 48 站2:
    # _check_open_workflow_blocks, which WARNs on unresolved harness-owned halts
    # so an operator running doctor after a dead run is told the route (the
    # harness repair workflow) rather than left to guess. The check is already
    # as narrow as it can be — the ledger query lives in
    # core/workflow_blocks.harness_owned_open_blocks and the full listing is
    # run-report's job, so doctor carries only the finding.
    #
    # Recorded honestly: this file was at 882 before the round, so ANY new check
    # crosses the 900 default. Prose was tightened once and then stopped, because
    # shaving a docstring to dodge a threshold is not what the threshold is for.
    # The next check added here should split the file instead of raising this.
    #
    # 2026-08-12 (same round, station 5): +12 — the same check gained an ERROR
    # branch for a block that was marked RESOLVED and came back at the same
    # coordinate. That is a different severity from an unresolved block (a
    # recorded verdict contradicted by the next run, versus a run that stopped),
    # and collapsing the two would lose the distinction the reconciliation
    # exists to make. Still inside the same function; the split note above
    # stands and now has one more caller behind it.
    #
    # 2026-08-13: 923 -> 251 (-672). R49-B did what the note above said the next
    # check should do. The fourteen checks moved into core/doctor_checks/ in four
    # families (config_drift, git_state, ledgers, verdicts); doctor.py keeps
    # run_doctor, which is the one thing that has to know about all of them, and
    # re-exports the names its callers already import. The entry stays at the new
    # size rather than being deleted: a file that just shed 672 lines is exactly
    # the file worth watching.
    "core/doctor.py": 262,  # 2026-08-16: +2 — Round 53 站5c: the import and call for `_check_phase_record_gaps`. Two lines, because the check itself is in core/doctor_checks/verdicts.py where its four siblings live. Previous: 260.  # 2026-08-14: +9 — Round 52 站1: the import of _check_verify_target_recipe and its call in run_doctor, plus the six-line comment recording why it is WARN here while finalize_gate blocks on the same two shapes (doctor reports, it does not get to be a second enforcer — Round 38). The check itself is in core/doctor_checks/config_drift.py. Previous: 251.
    "core/agent_spawner.py": 1286,
    # 2026-07-12: +2 lines — Round 5 exception-swallow ratchet: GitHubFetcher/
    # LocalFetcher.get_file_content now log the swallowed decode/read error.
    "scripts/phase_auditor.py": 1901,  # 2026-08-22: +45 — Round 69 站4. Net of a 17-line deletion inside check_c5_content_depth: the inline heading loop and the substring comparison are gone, replaced by two module-level pure functions (`_srs_in_scope_fr_ids`, `_matrix_fr_ids`) the tests can call without a fetcher. Most of the growth is the comment block recording what 54651a0 introduced and how it was measured — three defects live on all eight corpus projects: `(?:N)?FR-` invented FR-06..FR-09 on taskq, FR-09..FR-12 on taskq-plus/renew and FR-11/FR-12 everywhere else (all `### NFR-nn:` headings); the `-deferred` test ran against the heading INCLUDING its title so it never fired; and the matrix comparison was a substring test, which is why the first defect never surfaced as a false Missing:. After the fix every corpus project's SRS FR set equals its matrix FR set exactly. Previous: 1856.
    # 2026-08-22: 1848 -> 1856 (+8). Round 69 站1: C5's `_check_traceability_depth`
    # FR-coverage check used `re.findall(r"\\bFR-\\d+\\b", srs)` over the whole
    # SRS.md body, which caught unpadded section refs in
    # `<!-- DERIVED: SPEC §3 FR-1 -->`-style notes and inflated the expected
    # count to ~20 when only the ~12 actual headings are in scope — surfaced
    # on taskq-new P1 audit 2026-08-22 as `covers only 11/20 FRs (55%)`. Fix
    # extracts from `##/### (N)?FR-XX:` heading lines only, normalises
    # zero-pad, and skips `*-deferred` (existing test fixtures that author
    # `## FR-01 Login` instead of `### FR-01:` continue to pass — confirmed
    # against tests/test_phase_auditor.py::TestTraceabilityFrCoverage). Most
    # of the +8 is the comment recording the over-match; the regex block
    # itself is roughly the same size as the one it replaced.
    # 2026-07-13: +5 lines — Round 10 站4: P2 Agent B checklist (both
    # _AGENT_B_CHECKS[2] and the SAD.md deliverable's own "checks" list)
    # gains a SEC-block-complete item; P4 hunt step text notes threat_model
    # targets from bug-hunt-targets.
    # 2026-07-14 (+2): _deliverable_ab_block templates updated to 3-layer
    # defense — remaining "NO access / paste full content" → Bash-cat prose.
    # 2026-07-14 (+14, 2nd round): _dynamic_fr_template_block (the function
    # plan-all actually calls, dynamic=True always) was missing ORCH-POST
    # (spec-coverage-check 40% + amend-sab) and the NFR-annotation reminder
    # that only ever existed in the static-mode-only _fr_dev_steps/
    # _fr_carryforward_steps — added for parity; also dropped the invalid
    # `--phase` arg from its check-test-mirrors-spec line (same argparse bug
    # independently found in phase3-implementation.js).
    # 2026-07-14: +3 lines — _phase_advance_step now documents the Sync-phase
    # post-advance `git push origin main` (all 8 workflow JS files already do
    # this; the generated plan prose never described it — SSOT fix, generator
    # source, not the 8 generated phaseN_plan.md files by hand).
    # 2026-07-28 (Round 22 站1): +12 lines — _orch_post_once_step() extracted so
    # `amend-sab` (no --fr-id, idempotent by construction) runs ONCE after the FR
    # loop instead of once per FR. Net effect on the *generated* plan is a
    # reduction; the generator grows by the new helper + its rationale docstring.
    # 2026-08-06 (Round 39 站1): +1 — the two "add a da_waiver to bypass the
    # threshold" passages became "fix the structure, or calibrate
    # crg_excludes / crg_cohesion_healthy". Round 38 站3 removed the waiver in
    # code and left these telling the agent to use it; saying what to do
    # instead costs one line more than saying what to do.
    # 2026-08-06 (Round 39 站3): +25 — _GATE_META stops being a hand-written
    # table of every gate's dimensions and thresholds (4 data lines) filtered by
    # string matching on rendered tokens (36 lines), and becomes a renderer over
    # harness/gate_configs/*.yaml filtering by dimension *name* through
    # core.harness_config._DIM_TO_FEATURE. The removed copy was wrong in two
    # places at the time it was replaced (gate 1 listed 3 of 4 dimensions, gate 2
    # 11 of 12). Most of the growth is the comment recording that measurement;
    # the renderer itself is roughly the size of the filter it replaced.
    # 2026-08-12: 1720 -> 1745 (+25). Round 47 站4: _preflight_steps gains a
    # [PREFLIGHT-ENV] item for phase 1 — the interpreter every command below it
    # runs through, which nothing built before this round. Gated to phase 1 so
    # the plan text and phase1-requirements.js say the same thing; a project
    # entered directly at P3 on a venv-less checkout is recorded in
    # docs/PROPOSAL_ADJUDICATIONS.md as a next-round candidate, not widened here.
    "scripts/plangen/blocks.py": 1747,  # 2026-08-23: +2 — Round 70 站1: `_SPEC_COVERAGE_THRESHOLDS` gains its gate-1 entry (40.0) and the note recording why it was absent. The reader sat behind `if score_gate is not None` and gate 1 declared no score_gate, so declaring one walked into a KeyError on a branch that had never run for gate 1. Previous: 1745.
    # 2026-07-11: +3/+6 lines — new check_module_fr_coverage gate (module/FR-NFR
    # ownership drift between TRACEABILITY_MATRIX.md's own §5.3 and
    # SPEC_TRACKING.md's §5) wired into preflight_artifact_consistency
    # (phase_hooks.py) and cmd_check_artifact_consistency (check_cmds.py),
    # mirroring the existing check_forward_refs/check_nfr_adr_coverage wiring.
    # 2026-07-12: +1 line — Round 6 站1: preflight_sab_check now imports
    # sab_amender.sab_module_candidate() instead of a locally-duplicated
    # dict-unwrap inline.
    # 2026-07-13: +11 lines — Round 10 站3: preflight_artifact_consistency
    # now also runs check_security_design (SAD.md §6 STRIDE-lite threat
    # model completeness).
    # 2026-07-14: +3 lines — preflight_fr_spec_consistency now imports
    # core.quality_gate.parsers.SRS_SUBSECTION_PREFIX instead of a
    # locally-duplicated regex fragment (same SSOT fix as spec_alignment.py /
    # artifact_parsers.py / spec_coverage.py for the subsection-numbered
    # FR-heading bug class).
    # 2026-07-17: +1 line — Round 13 站1: exception-swallow ratchet paydown —
    # the attestation-check except now prints its error instead of silently
    # discarding it.
    # 2026-07-21: +12 lines — pragma-allowlist drift fix: _audit_pragma_no_cover's
    # allowlist is now a named PRAGMA_NO_COVER_ALLOWLIST/PRAGMA_NO_COVER_GUIDANCE
    # pair (single source of truth) instead of an inline string literal, so
    # cli/fr_cmds.py's COVERAGE-FIX prompt can import and interpolate the same
    # guidance GATE1 actually enforces instead of hand-writing a broader,
    # unsynchronized allowlist (was whitelisting `if __name__ == "__main__":`
    # pragmas that GATE1 always rejected — guaranteed no-progress BLOCKED rounds).
    "core/phase_hooks.py": 2035,  # 2026-08-22: +47 — Round 69 站2 and 站5, one entry because they land in the same file on the same day. 站5 adds 5 lines: the `record_ac_deferrals` import and call in preflight_artifact_consistency, plus the three-line comment saying why an `info` finding still has to be written down (non-blocking is not free). 站2: +42 — Net +42 against a deletion: the dead `bvs_phase_order` obligation extractor (10 lines, unreachable since Round 15 §3 wrote it for a member that never carried the `blocking` key its consumer filters on) is gone, and what replaces it is prose. Two comment blocks carry the measurement — that the plan's stated reason for removing that member was wrong (BVSRunner compares `current_phase < PHASE_PREREQUISITES[N+1]`, i.e. `N < N`, so it does NOT fail on every preview; measured on a scratch taskq-cc at current_phase=3, zero violations) and the reason that survived it (an HR-03 skip and FSM FREEZE are the environmental kind the set already excludes). Plus `"blocking": True` on `preflight_previous_phase_artifacts`'s three exits and the docstring recording what it costs: with 02-architecture/SAD.md removed the preview went from 0 obligations to 2, both naming the file. Previous: 1988. # 2026-08-22: +29 — Round 67 站4: `preflight_submodule_pin_ci` and its PREFLIGHT_CHECKS entry. Eight lines are the call; the rest is the docstring carrying the measurement — of the eight projects on this machine, two pin a harness commit whose own Framework Self-Tests were red, and one of those is the commit Round 66 pushed and corrected an hour later. The decision itself is core/quality_gate/submodule_pin.py, which is where the three outcomes (red blocks / green passes / unobtainable is INFRA) are stated. Previous: 1959.  # 2026-08-17: +9 — Round 55 站1: preflight_artifact_consistency runs check_ac_identifiers and check_ac_test_spec_coverage at phase>=3. Both checks have existed since Round 51 and their only consumer was delivery_fingerprint.build_fingerprint, which counts them into a JSON field nothing blocks on — taskq-advance carried 86 acceptance criteria that no TEST_SPEC case cites through eight phases. Two call lines and two imports; the rest is the comment recording why the phase rule is the one already beside it (the citation lives in TEST_SPEC.md, which Phase 2 produces). Previous: 1950. 2026-08-07: +50 — Round 43 站1: the PR 9 auto-fix dispatch + re-verify + attestation refresh moved OUT of preflight_traceability into a sibling method, PhaseHooks.repair_traceability_gap, which cmd_run_phase calls. The block itself is a wash (~55 lines out, ~62 in); the growth is the method's docstring, which has to record that this is the one entry point on the class that writes to the project, and the note left where the block used to sit explaining why the check no longer repairs. preview_next_phase_blocking documents itself as mutating no state and ran preflight_traceability at phase>=5, so every advance from P4 on with an open trace gap dispatched AutoFixEngine against the real tree. Splitting the file was considered and rejected for this change: the repair belongs beside the check it repairs, and the two are read together. 2026-07-26: +167 — Round 14 A1 + B3: PhaseHooks gains preview_next_phase_blocking(next_phase) (~50 lines: Obligation dataclass, _DELAYED_BLOCKING_PREFLIGHTS frozenset, _obligations_from_preflight() helper covering property_spec / reliability_lint / generic fallback, simulation-driver method with stdout suppression), and preflight_property_spec rewires from hardcoded phase>=4 to dynamic max(fulfill_phase) across FRs with extracted SubAssertion.fulfill_phase (~100 lines including P3-skipped path that still carries fulfill_phase) — full carry-over obligation preview + proper back-compat (sized to current 1777). 2026-07-26: +104 — Round 15 §3: _obligations_from_preflight gains 8 per-check extractor branches (drift_detection / sab_check / traceability / fr_spec_consistency / artifact_consistency / config_liveness / previous_phase_artifacts / bvs_phase_order), replacing the generic "would block at phase N" fallback with actionable rule_id/file/line detail; preflight_artifact_consistency gains an additive `error_details` return key so its extractor has something to read (sized to current 1881).
    # 2026-07-12: +7 lines — Round 5 建議2站1: _generate_sab_json now resolves
    # scripts/ via the shared harness_scripts_dir() SSOT instead of its own
    # (broken) Path(__file__).parent arithmetic.
    # 2026-07-13: +63 lines — Round 10 站3: cmd_check_artifact_consistency
    # now also runs check_security_design (reads current_phase from
    # state.json); cmd_bug_hunt_targets gains a 6th targeting source
    # (threat_model — SAD.md §6 threats' owner_module resolved to an
    # on-disk path, same candidate expansion preflight_sab_check uses).
    # 2026-07-13: +34 lines — T1-A (8-phase workflow-audit remediation):
    # new cmd_check_manifest_integrity + its subparser registration, a thin
    # CLI wrapper around PhaseHooks.preflight_manifest_integrity() so
    # workflow JS stops reimplementing (and getting wrong) this check inline.
    # 2026-07-16: +15 lines — Round 12 站3c: check-test-mirrors-spec
    # consults values.checker_enforcement for spec_unsatisfiable
    # (default warn; operator-promotable to block after a clean E2E run).
    # 2026-07-17: +6 lines — Round 13 站1: exception-swallow ratchet paydown —
    # 2 previously-unlogged broad excepts now print a [WARN] diagnostic.
    # 2026-08-05: +33 lines — Round 37 站3: cmd_verify_ci + its subparser. A
    # thin CLI surface only; the verdict logic lives in core/ci_verdict.py,
    # which is why this is +33 and not +150. Deliberate: taskq-renew pushed
    # onto a red build 48 times because no command existed to ask.
    # 2026-08-06 (Round 38 站2): +14 — crg-arch-check resolves the architecture
    # floor from the project's phase via the gate config instead of carrying
    # `default=80.0`, which was the last of nine restatements of that number
    # and the one that would have survived every caller dropping the flag.
    # 2026-08-06 (Round 38 站4): +72 — `verify-gate`: one command that runs the
    # gate's three checks and appends the verdict, with the digest of the tree
    # it measured, to gate_verify.jsonl. It replaces three commands and three
    # agent-transcribed exit codes that were written down nowhere — `crg_rc`
    # returns zero hits across taskq-renew's entire .methodology/ after a full
    # P1-P8 run.
    # 2026-08-06 (Round 39 站2): +7 — the crg_architecture early return now
    # records the skip in the degradation ledger. This is the switch that
    # turns CI's absolute architecture floor into an unconditional pass; its
    # only previous trace was an INFO line that dies with the CI log.
    # 2026-08-07: +6 lines — Round 42 站3: `check_srs_structure` joins the
    # artifact-consistency violation set at both of its callers. Adding it at
    # one caller and not the other is how `check_security_design` grew its
    # "keep the phase rules inside the check" rule, so both call sites move in
    # the same commit; the six lines here are the import and the addend.
    # 2026-08-12: +13 lines — 3dad941 narrowed `--forward-refs-only` back to
    # check_forward_refs alone. The entry above added check_srs_structure to
    # the shared set without touching that branch, so the workflow's fast-fail
    # step reported a missing SRS FR Block as an invented filename. Eleven of
    # the thirteen lines are the comment stating which route carries one check
    # and which carries five — the drift that entry warned about, happening to
    # that entry. The ceiling moves here rather than in 3dad941 because that
    # commit shipped without it and CI has been red since; splitting this file
    # is a subtraction round of its own.
    # 2026-08-13: 1682 -> 354 (-1328). R49-B 站2: the 24 command bodies and
    # their 4 helpers moved into cli/checks/ in six families (specs, gates,
    # trace, approvals, constitution, hunt). What is left is the argparse
    # wiring — register() is ~295 of the 354 — plus the re-exports harness_cli
    # and the tests import by name. Deliberately NOT split further: slicing
    # register() per family would make a command's flags live beside its body,
    # which is the better end state, but it is a separate operation with a
    # different failure mode (a subcommand silently unregistered) and it gets
    # its own commit rather than riding in on a body move.
    #
    # 2026-08-13: 354 -> 81. R49-B 站3 did that separate commit. The 24
    # add_parser blocks went to the family that owns the command each
    # dispatches to, so a command's flags and its body are now in one file.
    # What is left is the re-exports and a register() that names the six
    # families — which is the whole of what this module is for.
    "cli/check_cmds.py": 81,
    # 2026-07-12: +2 lines — Round 5 exception-swallow ratchet: _manifest_fr_ids
    # / _auto_fr_ids now log the swallowed parse error before returning [].
    # 2026-07-17: +5 lines — the sessions_spawn.log.lock gitignore entry
    # (same rationale as the sessions_spawn.log entry two lines above: the
    # lock-file sibling was missing from the pattern, surfacing as a second
    # permanently-dirty file in run-fr-step's dirty-tree guard).
    # 2026-07-17: +2 lines — Round 13 站1: exception-swallow ratchet paydown —
    # the artifact line-count except now prints its error before falling
    # back to the no-linecount checklist item.
    # 2026-08-05: +4 lines — .methodology/.mutation_exclusive.lock gitignore
    # entry (same rationale as sessions_spawn.log.lock above): the new
    # source_tree_lock.py flock sentinel that serialises mutmut's live-tree
    # mutation window against concurrent test-suite runs needs the same
    # ignore treatment or it surfaces as a permanently-dirty file.
    # 2026-08-05: +10 lines — .claude/worktrees/ gitignore entry, same
    # failure class as .venv/ above: Claude Code's Agent tool (isolation:
    # "worktree") creates linked git worktrees that `git add -A` records as
    # a gitlink with no .gitmodules entry, hard-failing actions/checkout@v4's
    # submodule step on every CI push (taskq-renew commit 0fc1e4e, FR-03
    # Gate1 / Phase 7).
    "harness/git_strategy.py": 1335,  # 2026-08-16: +18 — Round 53 站2: the custody precondition in front of `_commit`'s `add -A`. Three lines are the call and the refusal; the rest says why this site needs it and the five pathspec-scoped commit sites do not. `add -A` keeps its meaning — the milestone commit's content IS the phase's work — and what is refused is committing while the framework's own transient window is open, which is how taskq-super's `5535033 release(P6): Gate4 PASS` shipped a mutmut mutant and its .bak. Previous: 1317.
    # 2026-07-13: +28 lines — Round 10 站4: P2 tasks gain a
    # [SEC-WRITE]/[SEC-VALIDATE] step pair next to [SAB-WRITE], mirroring
    # the SAB block's own authoring-guidance shape.
    # 2026-07-14: +3 lines — 2nd-round workflow-JS-audit remediation: P4/P6
    # dims-count text (previously hardcoded "16"/"15" strings that drifted
    # from the yaml-derived enabled-dim count) now reads gate_meta[gate][1]
    # via two local `_g3_dims`/`_g4_dims` variables instead.
    # 2026-07-14: +4 lines — generate_phase8_tasks' P8→P9 block (phase 8 has
    # its own hand-written advance text, not the shared _phase_advance_step)
    # now documents the same Sync-phase post-advance push as blocks.py.
    # 2026-07-28 (Round 22 站1): +9 lines — P4/P5/P7/P8 each emit the
    # project-wide ORCH-POST-ONCE tail after their FR loop (P4's three FR-loop
    # branches share one insertion point at the loop's common exit). P3 is
    # deliberately excluded: its per-FR amend-sab runs BEFORE that FR's own
    # GATE1 so the Architecture Amendment Protocol sees the module the FR just
    # wrote — that one is not a repeat, it is the point.
    "scripts/plangen/phase_tasks.py": 1150,
    "core/quality_gate/mutation_enforcer.py": 1411,  # 2026-08-24: +21 — Round 72 站2: `MUTATION_SCORE_PROVENANCE_KEY`. One line is the constant; the rest is the comment recording that both writers here have stamped `enforcer_sha` since Round 19 站3 and no reader ever looked, and why the rule is the key's PRESENCE — `enforcer_sha()` returns "unknown" with no git, so a value-shape rule fails real runs. Previous: 1390.  # 2026-08-24: +14 — commit d552fc35: R71-站1 custody-wrap `_compute_mutation_score`'s live mutmut run with `core.tree_custody.custody()`, identical to R53-站1's `run_mutation_precheck` pattern to restore source when subprocess is SIGKILLed. Previous: 1376.
    # 2026-08-22: +14 — Round 68 站2: `HARNESS_MUTATION_BASELINE` in `_mutmut_subprocess_env`. One line is the export; thirteen are the docstring paragraph recording what it replaces. This function re-runs a project's own suite once per mutant and did so without telling the suite, so a test that genuinely cannot be in the mutant set had to detect the situation from a side effect — a real project's acceptance suite reads `PYTEST_DISABLE_PLUGIN_AUTOLOAD == "1"` as "you are the mutation baseline" four times, a proposition that variable does not state, in a suite whose own NFR forbids skips. Added beside the sandbox rather than instead of it: Bug #142's default-deny is still load-bearing. Previous: 1362.  # 2026-08-16: +34 — Round 53 站1: `_custody_paths` and the `custody(...)` clause on the existing `source_tree_lock` line. The restore and its verification live in core/tree_custody.py; what is here is the file set mutmut may touch and the comment saying why the lock was not enough. Station 0 measured that set on a fixture — the `.py` files under `[mutmut] paths_to_mutate` plus a `<file>.bak` sibling, nothing outside — and killing mutmut mid-run left `return a * 2` -> `return a / 2` alongside `calc.py.bak`, which is byte-for-byte what taskq-super's `5535033 release(P6): Gate4 PASS score=93.9` shipped. The `.bak` paths are in the set although they do not exist yet: FileSnapshot records an absent file as absent and deletes it on restore, which is the disposal `rate_repo.py.bak` never got. Previous: 1328.
    # 2026-08-05: +12 lines — both `mutmut run` subprocess calls
    # (run_mutation_precheck, _compute_mutation_score) now hold
    # source_tree_lock.py's exclusive lock for the subprocess's duration.
    # mutmut mutates paths_to_mutate files at their real, absolute project
    # path (cwd=workdir only isolates mutmut's own execution context, never
    # isolated the mutated files themselves — a real gap between what
    # evaluate_dimension.md documented and what the code did). A live Gate 2
    # run on taskq-renew hit exactly this: PhaseTruthVerifier.check_pytest
    # re-runs the real suite independently of SSI scoring, landed inside a
    # mutation window, observed a genuinely mutated file, and failed HR-11 —
    # even though the SSI composite and the mutation score itself both
    # passed. The dispatched agent misdiagnosed the mutation as an external
    # process injecting regressions and burned a full round "fixing" files
    # mutmut was about to mutate again. The lock makes any concurrent
    # test-suite run (test_suite_run._measure — the shared entry point for
    # PhaseTruthVerifier, FrameworkEnforcer, gate1_evidence, advance-phase
    # TDD-PRECHECK) wait for the mutation window to close instead of racing
    # it, at the cost of these 12 lines wrapping the two existing calls.
    # 2026-08-04 (Round 35 站2): +71 — _write_unmeasured_artifact, the
    # wrapper that calls it, and the docstrings recording why. Eleven abort
    # paths returned score 0.0 beside success=False, and every consumer reads
    # the number: "the framework could not measure" and "mutmut ran and every
    # mutant survived" arrived as one value. Same fix Round 32 站4 applied to
    # the tool scorers, now for the one dimension the framework measures
    # itself.
    # 2026-08-04 (Round 35 站1): +26 — _has_resolvable_testpaths plus the
    # finalisation that uses it, and the note recording what mutmut 2.5.1's
    # tests_dir actually does (it hashes the suite and excludes test files
    # from mutation; it never reaches the runner). The old branch wrote the
    # workdir's pytest target only for projects with no setup.cfg at all, so
    # a project whose setup.cfg carried just [coverage:run] got none — real
    # mutmut on tests/fixtures/mutmut_bare_cfg/ reproduces it.
    # 2026-08-03 (Round 31 站3): +49 more — the cache resume the Bug v26
    # timeout message had promised for a round without any code behind it,
    # plus _is_resumable_cache. Inheriting a cache means an unusable one
    # becomes the new run's problem, and a crashed run leaves exactly that
    # (test_stale_cache_removed_when_workdir_cache_absent caught it: a 14-byte
    # file made _count_mutmut_results raise "file is not a database").
    # 2026-08-03 (Round 31 站2): +80 lines — MUTATION_SCORE_ARTIFACT and
    # _write_score_artifact. mutation_testing is tier-1 and objective_primary,
    # and it was the only tier-1 dimension whose number the framework never
    # produced: compute_mutation_score had zero production callers and the
    # score that reached a live Gate 2 was prose an agent wrote by hand. The
    # artifact carries the denominator too (paths_to_mutate, paths_to_exclude,
    # mutated_files) because the party being scored writes the exclusion list.
    # 2026-08-02 (Bug v26 P3 Gate 2 plateau): +23 lines —
    # compute_mutation_score's TimeoutExpired branch now publishes the
    # partial workdir cache to the project root so the next call resumes
    # from it via mutmut 2.x's get_cached_mutation_statuses skip. Without
    # this, mutmut-on-Python-3.11 + SPEC §10 service+storage scope
    # routinely blows the 60-minute STALL_TIMEOUTS["mutation"] cap and
    # every retry starts from zero (which is exactly the failure shape
    # that bit run-all-by-workflow P1-P8 R1/R2/R3 in a row). The 23-line
    # block is the docstring + shutil.copy2 + message variant — kept
    # verbose because the message is what the LLM evaluator reads when
    # the gate fails again.
    # 2026-08-03 (Bug #142): +67 lines — mutmut's `mutmut run` subprocess
    # inherited the full parent environment with no override, so any
    # pytest11-autoloaded plugin installed in the invoking venv (e.g.
    # pytest-testmon) loaded into the mutant-evaluation pytest process.
    # testmon crashed fingerprinting a file inside mutmut's ephemeral
    # workdir (IsADirectoryError), burning three P3 Gate 2 rounds at
    # score=0 with zero mutants evaluated. _mutmut_subprocess_env sets
    # PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 (default-deny) and re-enables only
    # the plugins the resolved [mutmut] runner's own flags demonstrably
    # need (xdist for -n/--dist, pytest_cov for --cov) via PYTEST_ADDOPTS
    # — so taskq-advance's `-n 8 --dist=loadfile` parallelism keeps
    # working while unrelated autoloaded plugins no longer can.
    # 2026-07-17: new god file — Round 15 station5: phase_specs.py (formerly
    # 2985 lines, see git history for its full growth log) was split one
    # module per phase (spec_phase1.py .. spec_phase8.py, spec_shared.py for
    # the one cross-phase renderer); phase_specs.py itself is now a ~20-line
    # facade re-exporting generate_phase1..generate_phase8, well under the
    # threshold and removed from this table. Phase 1 (4 serial A/B sub-tasks
    # + Load Legal Artifacts + Forward Ref Check + a dedicated runPeerReview)
    # is the largest single-phase family and the only one landing over the
    # threshold post-split — kept as one file (not split further) to match
    # every other phase's one-phase-one-file shape; see spec_phase1.py's own
    # module docstring.
    # 2026-08-01: +2 lines — Defect D/F guard: srsAPrompt gains a
    # DIMENSION/AC-COVERAGE VALIDATION step (Agent A must check every NFR's
    # `dimension:` field against evaluate_dimension.md's actual current
    # roster, and each AC against what that dimension section actually
    # verifies, instead of blindly transcribing a canonical spec that can
    # cite a deprecated/nonexistent dimension) and srsBChecklist gains the
    # matching independent B-reviewer check.
    # 2026-08-01: +1 — render_persist_approval(use_schema_verdict=True) needs
    # VERDICT_SCHEMA declared as a top-level const (playbook §5.3: a
    # `schema:` value must be top-level, not nested), which phase1 never
    # needed before switching off the free-text-regex verdict path; one
    # `B.render_schemas(["VERDICT_SCHEMA"])` call added ahead of
    # render_persist_approval.
    # 2026-08-02 (Round 28 站1): 920 -> 921. One import line
    # (`from . import spec_shared as S`) so phase1's final return can carry the
    # shared phase-completion marker; the eight spec modules each gained the
    # same line, and this is the only one that was already at its ceiling.
    # 2026-08-03: 921 -> 937. srsBChecklist gained an NFR `type:` legality
    # bullet (mirroring the existing `dimension:` bullet) plus the
    # nfr_type_vocabulary_inline() import + module comment it needed — SRS.md's
    # own machine-readable `type:` field was validated nowhere in Phase 1
    # (only `dimension:` was), letting an illegal-but-plausible value (e.g.
    # `error_handling`) reach Phase 2 and cause a real 5-round HR-12
    # non-convergence (taskq-full SAD.md).
    # 2026-08-04: 937 -> 949 — Round 33 站1: the DELIVERABLE_ANCHORS import,
    # the four module-level anchor constants, and the comment recording why
    # they exist. Four of the seven seeded templates carried an H1 that did
    # not satisfy the diskPrefix written three times beside it in this very
    # file; the literals are now interpolated from
    # core.quality_gate.legal_artifacts so there is one place the anchor is
    # stated. Net effect on the generated JS is byte-identical apart from the
    # three prompt sentences that told the agent a substring match would do.
    # 2026-08-12: 949 -> 962 (+13). Round 47 站4: the P1 preflight step list goes
    # from 3 to 4. Step 0 cannot use PY (PY is what it creates) and cannot use
    # harness_cli.py (that entrypoint imports pyyaml transitively, so on a
    # machine lacking it the command that fixes the environment would itself
    # fail to start). The two-candidate path probe and the comment recording
    # both constraints are the growth.
    # 2026-08-12: 962 -> 963 (+1). The SRS A prompt's MANDATORY FR Block
    # directive, moved here from the two shipped .js files 3dad941 hand-edited.
    # One prompt line in the generator replaces the same line written twice in
    # generated output; splitting a file over it would be the wrong trade.
    "scripts/workflowgen/spec_phase1.py": 999,  # 2026-08-22: +3 — one `B.render_preview_next_phase(1)` call site + its `_META_PHASES_1` list entry, the same one-line addition every phase file gets for Fix B (see js_blocks.py's own ratchet entry for the mechanism). Previous: 996. 2026-08-17: +12 — Round 55 站1: _AC_LABEL, interpolated from artifact_consistency.ac_label_shape() into the criteria-identifier instruction. The prompt spelled the label shape itself while _AC_BLOCK matched it literally, and the two disagreed about a qualifier: five of seven projects wrote `**Acceptance criteria (FR-01)**` and the parser attributed none of their criteria. One assignment; the rest is the comment recording the measurement. Previous: 984. 2026-08-14: +20 — off-by-one citation range block at pre-write + Agent B retry with stderr injection. Three defensive layers: (1) `buildBPrompt` SCHEMA REQUIREMENTS tells Agent B to `wc -l <path>` before writing range citations; (2) `cmd_write_approval` rejects unresolvable citations before they land on disk; (3) `runPeerReview`'s persist loop catches the reject, attaches the error to `b2.persist_error`, and re-dispatches Agent B next round with the cited file path + `wc -l` reminder. Fixes 2026-08 production halt on a 791-860 cite for an 859-line file. Previous: 964.
    # 2026-07-15: new god file — Round 11 station4: js_blocks.py crossed the
    # threshold for the first time (769→1314) adding the shared A/B-review-
    # machine renderers (safePrevB2/makeDocSummary/scopeRules/buildBPrompt/
    # structuredBReview/persistApproval/loadFileViaPython/genericAbLoop) that
    # unify phase1/phase2/phase6's previously triplicated JSON-parsing +
    # B-review helpers, plus splitting RESOLVE_REPO_BLOCK/BUDGET_GUARD_BLOCK/
    # REPO_LOG_LINE apart (phase1/phase2/phase6 each interleave their own
    # extra consts between REPO resolution and the REPO/PY log line, in three
    # different places — the split lets every phase reuse the same resolver
    # function body without forcing one phase's ordering onto another), plus
    # render_rule_prose() (loads harness/prompts/rules/<id>.md via plangen/
    # blocks.py's own _load_rule SSOT instead of phase1's original JS
    # hand-duplicating that prose — see phase_specs.py's srsAPrompt/
    # srsBChecklist for the 3 call sites this replaced).
    # 2026-07-16: +8 lines — Round 12 站1: render_per_fr_delta gains the
    # pre_loop_state parameter (phase4's p4Mid declarations, dropped by the
    # station-3a migration — sim testbed catch) + docstring documenting it.
    # 2026-07-16: +7 lines — Round 12 站2a: render_per_fr_delta's GATE1
    # verify swaps the inline python one-liner for the verify_gate1_qc.py
    # helper dispatch (v2.13.3 pattern, cef32c4's deferred P4/P5/P7/P8
    # migration) with the verdict derived from echoed canonical stdout
    # only; the delta is the comment documenting the hallucination class.
    # 2026-07-25: +34 lines — render_gate_loop()'s Gate 2/3/4 completion
    # signal was corrected from manifest.gate_results.gate{N}.quality_complete
    # (set from the SSI score alone, before Phase Truth runs, never reverts)
    # to state.json.last_gate >= gate_num (only written by finalize-gate's
    # _update_state_checkpoint AFTER PhaseTruthVerifier passes for an exit
    # gate — the authoritative "truly finalized" signal advance-phase's own
    # precondition check already trusts). Touches both the precheck block
    # and the in-loop verify step, plus rewriting the precheck's shell
    # one-liner as a template literal (matching the existing ctxCheckCmd
    # pattern) instead of the old sextuple-escaped nested-quote string,
    # which a manual decode showed was silently mis-concatenating REPO.
    # 2026-07-26: +44 lines — Round 23: render_env_check rewritten to use the
    # bash-timeout-aware background poll pattern (observed on taskq phase5
    # wf_4fe2125c-48d: the chained run-env-check + finalize-env-check
    # legitimately runs past Claude Code Bash tool's 10-min default timeout;
    # Bash auto-moves to background + returns rc=124, which the sub-agent
    # mis-reports as the env-check exit code, masking that env_check_result.
    # json was actually written). Same GATE1-DELTA background dispatch idiom
    # reused (nohup + kill -0 poll loop + log tail). New ENV_CHECK_SCHEMA
    # (+ready field) added to _SCHEMA_DEFS for the Bug #127 cross-check.
    # Replaces the previous one-line synchronous Bash invocation in 5 phase
    # workflows (phase3/4/5/7/8).
    # 2026-07-28 (Round 22 站1/站2): +6 net — the ORCH-POST once-per-phase
    # block and the render_manifest_integrity_fn rationale docstring cost
    # more generator lines than the two removed call sites saved; the
    # GENERATED workflow JS shrank by ~150 lines across the 6 phase files.
    # 2026-07-28 (Round 22 站4): +6 — the two background-poll prompts spell out
    # a backoff sequence and why the flat first interval was wrong, replacing
    # one-line "sleep 60"/"Poll every 30s" instructions.
    # 2026-07-30: +6 — render_load_file_via_python()'s retry loop wraps its
    # `await agent(...)` call in try/catch (the same idiom persistApproval
    # already uses). Every sibling retry loop in this file already catches a
    # thrown agent()/dispatch() error and retries (the "Bug #2" convention);
    # this was the one outlier, and its uncaught throw crashed a live run-all.js
    # run 83 dispatches / 3h in on a transient "Connection closed mid-response"
    # API error, defeating the file's entire unattended-determinism purpose
    # (Round 23's stated rationale for run-all.js existing at all).
    # 2026-08-01: +20 — render_persist_approval()'s use_schema_verdict=True
    # branch stops trusting the sub-agent's self-reported `res.pass` boolean
    # and instead regex-matches the canonical `[write-approval] OK` string
    # inside res.reason (same verdict source gate1-verify- already uses,
    # closed against the wf_53d055ce-d0b hallucinated-pass class). Root
    # cause of a live run-all.js Phase 1 crash: a code-review-graph MCP
    # system-reminder derailed the persistApproval shell-wrapper sub-agent
    # into replying "Acknowledged..." instead of transcribing CLI stdout,
    # and the (then) phase1/phase2 free-text regex misread that as failure
    # 3/3 attempts despite harness_cli.py write-approval succeeding and
    # self-verifying (write+size+exists) all three times. Fix flips
    # phase1/phase2's use_schema_verdict False→True (spec_phase1.py L894,
    # spec_phase2.py L540) so all three callers share this one hardened
    # branch instead of leaving a third, less-safe variant behind — the
    # docstring above grew to explain why trusting `pass` was rejected.
    # 2026-08-02 (Round 28 站3): 1445 -> 1504. render_terminal_abort_detectors
    # moved here from spec_phase3.py, which shrank 520 -> 493 in the same
    # commit: the [HARNESS-BUG] and structurally-broken-dispatch exits existed
    # only in Phase 3's TDD loop, and the four phases that run their own per-FR
    # loop through render_per_fr_delta now share the one implementation. Net
    # +32 lines across the two files for four call sites that had none.
    # 2026-08-04 (Round 34 站1): 1504 -> 1518. render_anchor_check() inlines
    # js_src/anchor_check.mjs (same one-file-two-consumers arrangement as
    # render_json_utils), replacing the multiline `anchorRe` literal that used
    # to live inside render_load_file_via_python's string. The rule it encoded
    # — "any H1 line in the first 500 characters containing the phrase" —
    # accepted the "Acknowledged.\n\n# <anchor>" preamble that this very check
    # exists to reject (file_loader.py's Bug v5). A regex written as a string
    # literal in a generator can only be tested by grepping the generated file;
    # as a module it is executed by node --test against the same fixture the
    # Python side uses. The call site shrank 10 lines -> 4; the +14 net is the
    # new function plus the rationale for why the two layers check different
    # inputs but may not have different rules.
    # 2026-08-05: 1518 -> 1550. render_gate_loop() gains an optional
    # crg_threshold param: when set, the same round-final verify agent that
    # already independently re-checks D4's exit code (not the self-reported
    # prompt_steps text) also runs crg-arch-check and ANDs its exit code into
    # gate{N}Pass — CI's standalone "CRG Architecture Gate (P3+)" job enforces
    # architecture score >= threshold as an absolute floor on every push,
    # independent of any gate's weighted composite (observed: taskq-renew
    # Gate 4 passed at 93.6 composite with architecture=77.8 folded in — no
    # per-dimension floor exists in the composite math), and nothing in the
    # generated workflows exercised that absolute-floor semantics before this.
    # 2026-08-05: 1550 -> 1559. crg_verify_cmd gains an inline `pip install -q
    # code-review-graph==2.3.6 igraph==1.0.0` prefix: this dispatch is a
    # standalone subagent Bash call that can't assume an earlier step in the
    # same session already installed these (code-review-graph was pulled out
    # of requirements.txt in the same commit — joint resolution with semgrep
    # is ResolutionImpossible — so nothing else installs it by default; and
    # code-review-graph doesn't declare igraph as a pip dependency, so
    # without pinning it too CRG silently degrades to a coarse
    # directory-based grouping that scores differently from CI).
    # 2026-08-06 (Round 38 站2): +6 — `crg_threshold: float` became
    # `crg_check: bool`. The generator now decides only whether the check runs;
    # the number comes from the gate config. Growth is the docstring recording
    # that all three callers used to pass 80.0.
    # 2026-08-12: 1620 -> 1637 (+17). Round 47 站1: the inline
    # `pip install -q code-review-graph==2.3.6 igraph==1.0.0` inside
    # crg_verify_cmd was a hand-typed pin — the fourth copy of a version the
    # CI template, requirements.txt and cli/project_cmds.py each also stated,
    # two of them differently. It now renders from
    # harness/toolchains/bootstrap.py::pinned_spec, which needs a small helper
    # (_crg_standalone_specs) plus the comment recording why a workflow file
    # carries a pin at all. Generator output is byte-identical
    # (generate_workflows.py --check 9/9), so the growth buys parity, not
    # behaviour. Splitting js_blocks.py remains a separate question.
    # 2026-08-12: +33 — Round 48 站2: RECORD_BLOCK_FN_BLOCK, the single hoisted
    # helper every one of run-all's terminal exits calls before returning. It
    # lives here rather than in spec_runall.py for the same reason every other
    # shared block does — it is JS text, and the file that assembles run-all
    # imports its blocks, it does not author them. Sized deliberately: the
    # alternative (a recordBlock call at each of the 125 halt sites in the eight
    # phase generators) was measured at roughly 8 KB of run-all's remaining
    # 12,241-byte headroom, against 2,328 for this one.
    "scripts/workflowgen/js_blocks.py": 1979,  # 2026-08-23: +34 — Round 70 站3: `render_terminal_abort_detectors` routes on run-fr-step's exit code instead of on regexes over the sub-agent's prose, and gains a third exit (25, INFRA — the one of the three with a repair route). FR_STEP_SCHEMA is the new payload. Most of the growth is the docstring recording why four regex revisions in five days could not have worked: the same generator's GATE1 failure case asks for a sentence containing no bracketed tag, and its R66 clause forbids writing one, while the detector required the banner's literal two lines. Previous: 1945.
    # 2026-08-22: +89 — Round 69 站1. `render_exit_gate_reverify_step(phase)` (56 lines, of which 38 are the docstring recording the measurement: taskq-cc's gate_verify.jsonl carries four gate-4 verdicts at commit 11673af2 across three tree digests, because P6 writes RELEASE_NOTES.md and FINAL_SIGN_OFF.md after the verdict and is told not to re-run the gate). The renderer is derived from EXIT_GATE_MAP and inserted by `render_advance_loop` itself rather than by its three call sites, so it is 5 lines of wiring and no per-phase copies. The rest is the `preview-next-phase-unmeasured` split in `render_preview_next_phase` — a null dispatch reply stops being indistinguishable from "obligations found" — plus its docstring paragraph. Previous: 1856. # 2026-08-22: +76 — new `render_preview_next_phase(phase)` (Fix B): a read-only `preview-next-phase` carry-over-obligation check + bounded 3-round fixer, wired before each of the 8 phases' own Push/Advance step so a `_DELAYED_BLOCKING_PREFLIGHTS` finding surfaces inside the phase's own loop instead of only at its `advance-phase` exit gate (see scripts/workflowgen/artifact_limits.py's RUNALL_MAX_BYTES entry for the full root-cause). One shared function, called once per phase file — the eight call sites are each +1-2 lines in their own files (spec_phase1.py's own entry above), not counted here. Previous: 1780. 2026-08-14: +72 — v33b P2 citation-validator fix (run-all.js halt on taskq-super). Added `render_citation_contract_line()` helper (single source of truth for the citation rule used by buildBPrompt and Phase 6's inline verdicts) and wired Phase 2's `render_generic_ab_loop` with try/catch + `b2.persist_error` capture + `=== PREVIOUS ROUND CITE REJECT ===` prepend (mirrors spec_phase1.py:244-298). 19 lines of helper + ~33 lines of abLoop + comments. Pure bug-fix growth; no new functionality beyond what Phase 1's existing pattern already does. Previous: 1708.
}


def _production_line_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in _SCAN_DIRS:
        for path in sorted((REPO / d).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(REPO).as_posix()
            counts[rel] = len(
                path.read_text(encoding="utf-8", errors="replace").splitlines()
            )
    return counts


def _violations(counts: dict[str, int]) -> list[str]:
    out = []
    for rel, count in sorted(counts.items()):
        ceiling = _LINE_CEILING.get(rel)
        if ceiling is not None:
            if count > ceiling:
                out.append(
                    f"{rel}: {count} lines > ceiling {ceiling} — split the "
                    f"file, or if the growth is deliberate raise the ceiling "
                    f"in THIS commit and justify it in the commit message"
                )
        elif count >= _GOD_FILE_THRESHOLD:
            out.append(
                f"{rel}: {count} lines — new god file (unlisted, threshold "
                f"{_GOD_FILE_THRESHOLD}); split it or add a justified "
                f"ceiling entry"
            )
    return out


def test_production_file_line_ratchet():
    over = _violations(_production_line_counts())
    assert not over, (
        "god-file growth must be a reviewed decision, not a silent drift:\n  "
        + "\n  ".join(over)
    )


def test_comparator_fires_on_listed_growth():
    """Negative: one line over a listed ceiling must trip the ratchet."""
    rel = "cli/gate_cmds.py"
    assert _violations({rel: _LINE_CEILING[rel] + 1})


def test_comparator_fires_on_new_god_file():
    """Negative: an unlisted file at the threshold must trip the ratchet."""
    assert _violations({"cli/newly_huge.py": _GOD_FILE_THRESHOLD})


def test_comparator_quiet_at_or_under_limits():
    rel = "cli/gate_cmds.py"
    assert _violations({
        rel: _LINE_CEILING[rel],
        "cli/small.py": _GOD_FILE_THRESHOLD - 1,
    }) == []


def _ceiling_keys_in_source() -> list[str]:
    """The ceiling table's keys as WRITTEN, before Python collapses duplicates.

    `_LINE_CEILING` itself cannot answer this: a dict literal with the same key
    twice is legal and silently keeps the last value, so by the time the module
    is imported the evidence is gone.
    """
    import ast

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target = getattr(node, "target", None)
        if isinstance(node, ast.AnnAssign) and getattr(target, "id", "") == "_LINE_CEILING":
            value = node.value
            assert isinstance(value, ast.Dict)
            return [
                str(k.value) for k in value.keys
                if isinstance(k, ast.Constant)
            ]
    raise AssertionError("_LINE_CEILING is no longer an annotated dict literal")


def test_no_path_has_two_ceilings():
    """Round 70 站4. This table is the most-edited file in the repo — 162
    commits in thirty days — and every edit is a hand-written prepend: a new
    `"path": N,` line on top, the old entry demoted to a `#` comment below it.
    Forget the `#` and the file has two live entries for one path. Python keeps
    the LAST one, which is the older and therefore looser ceiling, and no test
    notices: the ratchet still compares a real number against a real limit and
    still passes.

    Not hypothetical. It happened while writing the 2026-08-23 code-review
    follow-up, was caught by re-reading the diff rather than by anything here,
    and would have silently re-opened whatever headroom the demoted entry had.
    """
    keys = _ceiling_keys_in_source()
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    assert not duplicates, (
        "these paths have more than one live entry in _LINE_CEILING; Python "
        "keeps the last, which is the older and looser ceiling: "
        + ", ".join(duplicates)
        + ". A demoted historical entry must start with '#'."
    )


def test_the_duplicate_check_reads_source_not_the_built_dict():
    """Negative: the guard has to fail on a literal the interpreter would
    accept. Reading `_LINE_CEILING` instead would make it unfalsifiable —
    `len(d) == len(set(d))` is true of every dict there has ever been."""
    import ast

    dup = ast.parse('D: dict = {"a": 1, "a": 2}').body[0]
    assert isinstance(dup, ast.AnnAssign) and isinstance(dup.value, ast.Dict)
    written = [k.value for k in dup.value.keys if isinstance(k, ast.Constant)]
    assert len(written) == 2 and len(set(written)) == 1
    assert len(eval(compile(ast.Expression(dup.value), "<t>", "eval"))) == 1
