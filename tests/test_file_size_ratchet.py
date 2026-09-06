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

import re
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
    "harness/gate_checks.py": 998,  # 2026-09-05: +23 — Round 99: `_mutation_artifact_violations` gains a shape-allowlist check on top of the existing `enforcer_sha` presence check. Found on taskq-wow's Gate 2: a hand-written `.methodology/mutation_score.json` carried a plausible `enforcer_sha` (passing the presence check) but used field names neither writer function emits (`message` with mutmut's own "timeout=/untested=" console vocabulary, `cache_path` instead of `cache_sha256`, no top-level `killed`/`survived`). The root cause was G2b's mutation-test-score re-run being a plain synchronous Bash call with no nohup/poll (fixed separately in scripts/workflowgen/), but this file's own defense against a fabricated artifact needed hardening regardless — 23 lines, most of it the two-branch shape check plus the comment recording the incident. Previous: 975.  # 2026-09-05: +57 — Round 97 站3: `lowered_cohesion_floor_reason` and `CRG_FLOOR_DETAIL_KEY`. 57 lines, of which ~40 are the measurement: the architecture score is taken against a floor the project sets, `crg_analysis.COHESION_HEALTHY` is 0.3, and 11 of 11 corpus projects had lowered it to 0.15-0.25 at 41-65 source files while the framework's own stated reason for going below the default is a package of at most 10 files. Round 42 站4 put both numbers in the gate result; nothing compared them. Previous: 918.  # 2026-09-05: +21 — Round 96 站0: `RED_SUITE_DETAIL_KEY`. A red suite blocked under `tool_score_fabrication`, whose registered headline reads "Claimed dimension score could not be reproduced by running the tool" — the agent claimed nothing, its tests are failing, and `core.lessons.record_gate_block` filed the wrong lesson under the same key. The 21 lines are the constant and the reason: this file already records why Round 35 站3 split `infra_fail` out of that key, and it is the same rule unapplied. Previous: 897.  # 2026-09-02: +74 — Round 91 站1: check 5 and `_scancode_provenance_problems`. 823 -> 897. Twelve lines are the check and its four returns; the other sixty-two are the two comment blocks carrying the measurement, because the number they justify is the reason to trust the rule. taskq-redo's Gate 4 licence evidence — 22893 bytes that json.loads rejects at line 1, scancode's real document with the warnings it writes to stderr interleaved through it — returned ZERO violations from checks 1-4 and scored 100.0 with score_source=artifact_verified, because check 3 is an OR over four bare words and the progress line "Scan files for: licenses" contains one. This dimension has no framework-produced number behind it (Round 35 站3's comment says mutmut "is the only member today"), so that file is the whole backing. The two candidate rules this replaced are recorded with their measurements: keying on the `.json` suffix would have rejected 32 of the 125 evidence files under gate_evidence/ that are ordinary tool text, and "must be valid JSON" still passes taskq-super's 45-byte hand-written summary. Asking `headers[0].tool_name` splits all twenty corpus files with nothing ambiguous between the groups. No split: the file is one family (what gate evidence must contain) and 897 is under the 900 god-file threshold — a split here would be splitting to satisfy a number. Previous: 823.  # 2026-08-28: -122 — Round 80 站12: two moves that had to happen together. OUT: the five per-dimension tables these checks read, to harness/gate_evidence_tables.py, byte-identical with their comments — a table changes when a dimension or a tool changes, a check when the rule does, and 172 of this file's lines were one regex table. Every table is a literal with zero references, so the import goes one way and cannot come back. IN: `_mutation_artifact_violations` (126 lines) from harness_bridge, which asks this file's own question of the one dimension the framework measures end to end itself. Moving it in alone would have taken the file to 1071; the tables had to come out first. This entry was granted at 945 one day earlier, above the 900 god-file threshold, on the argument that it was 861 lines taken OUT of a 5051-line file — true, and still headroom nobody had reviewed. Previous: 945.  # 2026-08-28: +945 — Round 80 站12: the five per-dimension tables these checks read moved to harness/gate_evidence_tables.py, byte-identical with their comments. They change for a different reason than the checks do — a table changes when a dimension or a tool changes, a check when the rule does — and 172 of this file's lines were one regex table. Every table is a literal with zero references, so the import goes one way and cannot come back; gate_checks re-exports them, which is how harness_bridge and everything downstream keep their existing names. This entry was granted at 945 one day earlier, above the 900 god-file threshold, on the argument that it was 861 lines taken OUT of a 5051-line file. That argument was true and the ceiling was still headroom nobody had reviewed. Previous: 945.  # 2026-08-28: +945 — Round 80 站8: new, and above the 900-line god-file threshold on the day it is created, so the decision is reviewed here rather than drifted into. It is 861 lines taken OUT of harness/harness_bridge.py (5051 -> 4190) plus a docstring and imports; nothing was written. 172 of those lines are `_TOOL_CONTENT_PATTERNS`, a per-dimension table, and 206 are `_check_tool_evidence` — data and one long check, not a second god file accumulating unrelated families. The alternative was leaving them in the 5051-line file, which is the trade this round exists to stop making. Previous: 0.
    "harness/harness_bridge.py": 3521,  # 2026-09-05: +21 — Round 97 站3: the raise site for the above, beside `crg_graph_incomplete` and `architecture_regression`. Previous: 3500.  # 2026-09-05: +22 — Round 96 站0/站3: the red-suite raise site uses its own details key, and `_record_coverage_denominator`'s return value stops being discarded — the omit list now travels into `breakdown.test_coverage.coverage_denominator` beside the percentage it qualifies (Round 42 站4). That producer wrote 153 of taskq-final's 428 ledger rows, one per gate run, and nothing read any of them. Previous: 3478.  # 2026-09-03: +13 — Round 92 站0b: `if tool == "gitleaks":` in `_run_harness_cross_validation`, mirroring the `if tool == "mutmut":` block immediately above it. gitleaks trusts whatever config resolves for its `--source` scan (no call site passes `--config`), and a clean run is indistinguishable from a run under a config that disables every rule — measured, two corpus projects' own `.gitleaks.toml` loaded ZERO rules (`[extend]` with no `useDefault = true` line) and scored secrets_scanning 100 nine times across Gate 2/3/4. `harness.tool_runners.scanner_is_alive` runs a synthetic three-rule canary through the same config resolution the real scan will use and blocks before the real scan, independently of what it reports. Previous: 3465.  # 2026-08-31: +89 — Round 83 站1: every score in a gate result now says where it came from. Three of the four writes are one line each (the replace-branches of `_override_traceability_dim_score`, `_override_adversarial_review_dim_score` and the CRG architecture override — each of whose APPEND branch had written `score_source` since the day it was added, so the framework recorded its own number as its own only when the agent had omitted the dimension), the fourth is the skip-list branch of `_run_harness_cross_validation` recording the new `artifact_verified`, and the fifth is the `_unsourced` block in `finalize_gate`. The rest is comment carrying the measurement: taskq-cc-new and taskq-new, the two projects that ran to completion after Round 67 站1 made `score_source` survive persistence, six committed gate results between them, and the SAME four dimensions (architecture, traceability, mutation_testing, license_compliance) carry a score and no source in every one — 0.28 / 0.31 / 0.33 of gates 2 / 3 / 4, published beside `weight_covered: 1.0` and `dimensions_unscored: []`, because `framework_measured` reads a blank as a measurement. Zero verdict drift, checked rather than asserted: recomputing `composite_over` and the per-dimension pass/fail over all six artifacts with the new labels reproduces every committed number to the last digit (91.5918 / 93.6810 / 94.4343 and 93.4020 / 95.6760 / 94.5900). Previous: 3376.  # 2026-08-31: +10 — `_run_harness_cross_validation`'s `agent_score < threshold` early-continue removed (S4 anti-fabrication was one-directional — it only re-verified self-reported PASSING scores, never self-reported FAILING ones). Round 35 站3's comment, immediately above, had already named the flaw for mutmut specifically; this generalizes it to every requires_tool_execution dimension. Confirmed on a real taskq-verify Gate 2 halt: a genuinely passing `security` dimension (bandit, committed evidence + an earlier round's own S4 check both at 96/PASS) was silently overwritten by a later round's self-reported `security: 0` carrying a fabricated technical explanation, and the guard skipped verification entirely because 0 < threshold — 3 rounds burned chasing a dimension that was never broken. The seven deleted lines of guard + comment are replaced by seventeen lines explaining the removal and citing the incident; `s4_score_verdict` (already correct, unchanged) now handles both directions uniformly. Previous: 3366.  # 2026-08-30: -411 — Round 82 站6: the sixteen stages `finalize_gate` runs (extracted by Round 81 站8 and left on the class) moved to harness/gate_stages.py as the `_FinalizeStages` mixin. A mixin and not module-level functions, because a method body sits at two indent levels under any class and under `class _FinalizeStages:` it sits at exactly the same two — so all sixteen are byte-identical and the sixteen `self._stage_*` call sites are untouched. Dedenting them would rewrite 386 lines, and a rewrite needs a behavioural golden this round does not have. `_FinalizeStages` is a namespace, not a type: nothing constructs it, nothing isinstance-checks it, HarnessBridge is its only subclass. Measured before the move: bases were `(object,)`, metaclass `type`, no `__init_subclass__`, and zero readers of `__mro__` / `__bases__` / `__qualname__` in the repo. Previous: 3777.  # 2026-08-30: -217 — Round 82 站5: the records a gate produces (`DimResult`, `GateResult`), the block it raises (`GateBlockedError`), the score-source vocabulary and the four functions that read a result — `framework_measured`, `declared_dimensions`, `measurement_scope`, `s4_block_details` — moved verbatim to harness/gate_result.py. Not a tidy-up: 站6 puts the sixteen `_stage_*` methods in a mixin, a mixin base is imported before the class body executes, and thirteen of those stages raise GateBlockedError. Without this move that module would have to import back into this one, and the version that "works" works because every one of these definitions happens to sit above the class — line order, not structure. `GateContext` (170 lines) deliberately stayed: no stage reads it, and moving code nothing needs is how a neutral module becomes a second god file. Four fingerprints unchanged, re-exports left behind, and the whole suite passed with zero test changes. Previous: 3994.  # 2026-08-29: +166 — Round 81 站8: `HarnessBridge.finalize_gate` 1150 -> 893, sixteen statement runs extracted to `_stage_*`. METHODS, not module-level functions, and that is what keeps the move byte-identical: a method body and a run inside another method both sit at TWO indent levels, so nothing was reindented. Fifteen are staticmethods — only one of the sixteen runs touches `self` — and fifteen of them raise rather than return, which is why tests/test_block_reason_registry.py (which scans this FILE, not that function) keeps working unchanged. The file grows by signatures and docstrings while the method loses 251. The generated fall-through is `raise GateBlockedError` rather than a return: this method's terminal `return result` travelled into `_stage_record_verdict`, and fail-closed is the right default for a gate where `return 0` is the right one for a CLI command. Previous: 3828.  # 2026-08-29: -236 — Round 81 站2/站3: `_atomic_write_gate_result` to harness/gate_io.py and `_crg_enrich_gate_findings` to harness/gate_crg.py, both byte-identical by AST source segment. Round 80 declined the second one because its closure pulled in the first — the shared writer eight call sites here use — and moving that looked like a refactor that would leave gate_crg and harness_bridge importing each other. The writer turned out to be a leaf: `json` and a guarded `core.atomic_io`, twelve lines. The enricher needs `DimResult` and `CRGBridge` in string annotations only (every rebuild goes through `dataclasses.replace`, which never names the type), so both are TYPE_CHECKING imports and the dependency goes one way. Previous: 4064.  # 2026-08-28: -126 — Round 80 站12: `_mutation_artifact_violations` joins the six Gate-evidence checks that left in 站8. It is S4 for mutation_testing — whether the artifact the gate reads is the framework's own measurement or an agent's prose — which is the same question the other six ask of their dimensions. Its dependency closure is itself; it needed `json` and `Path` and nothing defined here. Previous: 4190.  # 2026-08-28: -861 — Round 80 站8: the seven Gate-evidence checks and the five tables they read moved verbatim to harness/gate_checks.py, together with `path_escapes_root` and `_gate_dimension_names` because those are inside the set's dependency closure. The closure references nothing else defined here and no class here, so the import goes one way; `GateContext` appears only in three annotations and is imported under TYPE_CHECKING, which `from __future__ import annotations` makes free. Byte-identical by AST source segment, with each definition's leading comment block moved with it; re-exports left behind for every caller in harness/, cli/ and tests/. First harvest on an entry raised 56 times and lowered never — this file has been touched in 28 of the repo's 80 rounds. Previous: 5051.  # 2026-08-26: +90 — Round 77 站1/2/5/6: S4-B stops reading the agent's excerpt. S4 runs `pytest-cov` itself (gate1_per_fr.yaml declares `requires_tool_execution: true` for test_coverage) and holds the full stdout in `output`; forty lines later `_check_tests_failed` decided "are this FR's tests red?" by regex over `tool_evidence` — prose the agent pastes, capped by the same prompt at 500 characters. Round 67 / Round 72's mother pattern, in the one place where the framework had already spawned the subprocess whose answer it then ignored. Round 76 scoped that regex per FR and turned it fail-OPEN in the process: `if failed_paths: … return []` sits ahead of the `N failed` summary check, so one recognisable FAILED line waived every failure the regex could not see (measured: one FAILED line beside `20 failed, 59 passed` returns PASS for FR-08, while the identical evidence with no fr_id blocks). What is HERE is the plumbing and the raise sites — the `tool_runs` out-parameter on `_run_harness_cross_validation` (same shape as Round 74 站2's `_parse_test_spec(unread=…)`), three call-site blocks, and the `framework_run` branch in `_check_tests_failed` / `_parse_skip_counts`. The decision is core/quality_gate/fr_test_scope.py: the ownership predicate (one call to `test_suite_run.select_fr_outcomes`, the same one `fr_suite_verdict` scopes TDD-GREEN by, instead of Round 76's sixth hand-rolled copy of the convention), the waiver, its ledger row, and the recorded reason it is a record rather than a block. 258 lines that would otherwise be in this file. Round 76's own mechanism is deleted here — 69 lines removed against 159 added — and every unreadable shape it silently fell back on (ANSI, `-v`, `--no-summary`, a truncated list, a non-canonical fr_id spelling, a collection ERROR with no `::`) now falls back to the fail-closed rule instead of to a pass. Previous: 4961.  # 2026-08-25: +47 — Round 76: per-FR scope for `_check_tests_failed` (S4-B). The pre-fix parser took `(\d+)\s+failed` off the pytest summary line and blocked any FR whose gate had ANY red test, including sibling tests run for shared-source coverage — the FR-scoped pytest invocation (`cli/gate_cmds.py:_print_fr_scoped_overrides_python` → `shared_owner_test_files`) deliberately runs co-owners' tests to measure shared source files, and those failures are the OWNING FR's gate concern, not this one's. The fix parses the FAILED paths out of `tool_evidence`, counts only those whose path matches this FR's `test_fr{NN}*` convention, and logs sibling failures (visible, not blocking) so an operator can trace them. Real failure observed: FR-08's gate returned GATE1 FAIL score=99.5 with linting=100, type_safety=100, architecture_constraints=100, runner.py coverage 99%, and all 5/5 FR-08 spec tests passing — 20 sibling failures from FR-01/02 tests were the only thing blocking. Most of the +47 is the docstring (recording WHY the legacy regex was over-broad and how the per-FR scope preserves the fabrication-detection purpose the regex was built for), the per-FR pattern derivation (`f"test_fr{int(fr_num):02d}"`, single + three-digit cases), the sibling-vs-scoped split, the legacy fallback when evidence lacks parseable FAILED paths, and the WARN print that keeps sibling failures visible to the operator. The function's call site (line 3639, finalize_gate) now passes `fr_id=ctx.fr_id`. Universality preserved: when fr_id is None (legacy callers) or evidence has no FAILED paths (defensive fallback for agent deviations), the original "block on any failure" behavior is unchanged. Previous: 4914.  # 2026-08-25: +32 — Round 74 站3: `_parse_spec_names_for_fr` stops carrying its own copy of the row layer and calls `_is_header_row` / `_header_columns` / `_row_test_fn`. The code is a wash — an import, an `enumerate`, a `columns` dict, and four lines of hand-rolled `cols[1]` handling replaced by one call — and every added line is the docstring, which now records what this function is instead of what it said it was. It claimed "Canonical parser used by both prepare_gate() and _parse_test_spec()", a sentence that had not been true for as long as `_parse_test_spec` has lived in spec_coverage.py, and it kept BOTH defects Rounds 73 and 74 fixed in that reader while wearing the fixed one's name. The docstring also carries the measurement that keeps this honest: nine projects, zero difference after stripping parametrize suffixes, so this is a latent sibling and not a live wound. Previous: 4882.  # 2026-08-24: +65 — Round 73 站5: `declared_dimensions` and the third list in `measurement_scope`, plus the ledger row at the call site. The code is a manifest read, a set difference and a record_degradation; about forty lines are the comment carrying the measurement — six of the nine projects here pin an NFR to `architecture_constraints`, which appears in gate1_per_fr.yaml and in no other gate config, and every one of their Gate 4s published `weight_covered: 1.0` with `dimensions_unscored: []`. Both existing lists are built from the dimensions the GATE CONFIG produced, so a dimension the config never mentions was neither scored nor unscored but invisible, beside a composite a reader takes for the whole quality surface (Round 37: the denominator travels with the number). The comment also records why this does not block: the gate's dimension list is the framework's choice, not the project's, and NFR-06's substantive judgement is Round 73 站3's. Previous: 4817.  # 2026-08-24: +28 — Round 72 站2: `_mutation_artifact_violations` asks whether this framework wrote the artifact whose score it is about to accept. Its docstring already claimed "the framework's own artifact is the source"; nothing made it one. Five lines are the check and its `unverifiable` return (the same bucket as an absent file — same fact, same remedy); the rest is the comment carrying the taskq-new measurement: no stamp, a `generated_at` this code cannot emit, and a note excluding 685 mutants, scoring 72.1 against a threshold of 70 where the full denominator gives 24.6. Previous: 4789.  # 2026-08-23: +14 — Round 70 站1: the composite floor `_quality_complete` compares against stops being two `80` literals. Neither was ever the number this line used for Gate 1 — `GateConfig.from_dict` resolved `score_gate` to 1.0 by reading `gate: 1` as a threshold (measured on taskq-cc and taskq-api), and a dataclass field is always present so the getattr default was dead. Eight of the lines are the comment recording that. Previous: 4775.
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
    "cli/gate_cmds.py": 3336,  # 2026-09-06: +12 — Round 101 站1. `_check_sab_module_alignment` computes `unregistered = actual_modules - sab_modules` inline at two sites, which is `missing_modules` re-implemented, so the container-package rule this round adds to that function had to be read here too: without it Gate 1 demands a layer for the tree's own root package on every project while `amend_sab` refuses to invent one. 7 lines are the import, the `_containers` binding and its comment; 5 rewrite the post-auto-amend block's remedy, which said "run amend-sab manually to investigate" — from this round that branch is reached ONLY when amend-sab already refused, and re-running it produces the same refusal because it never reads SAD.md. Previous: 3324.  # 2026-09-06: +12 — Round 100 站1 (PHANTOM exit code split). `_check_sab_module_alignment` PHANTOM branch returns `EX_FR_STEP_PHANTOM_ABORT` (45) when `fr_id` is provided, distinct from the legacy `1` return for whole-gate runs (cmd_run_gate's own caller still gets the discriminator it expects). The new branch preserves the printed BLOCK lines verbatim so existing substring assertions on the gate print remain valid. Previous: 3312.
    # 2026-09-06: +8 — Round 99 站2: both spec-coverage block sites stop rendering the verdict as a percentage below a threshold. The Gate 1 site had its remedy removed in Round 87 站2 and its CAUSE left in; the Gate 2-4 site wrote the comparison with the threshold's variable name outside the string literal, which is why the first draft of this round's guard — keyed on the word "threshold" — read it as compliant. Both are +4: the replacement lines plus the comment recording that. Previous: 3304.  # 2026-09-05: +18 — Round 96 站2: `_gp_json["phase"] = args.phase` in the harness-computed patch block, +18 with the comment. The committed per-FR result carried whatever phase the agent's file had; measured on taskq-final, the Phase 6 run rewrote FR-01's result (enforcer_sha 0e9ce2e9 -> f4af8962) and left `"phase": 3`. Round 45 站3 reads that field to decide whether to compare the receipt's digest, so a stale label checks the one receipt that can never match and skips the three that describe the live verdict — 22 self-accusations on one run and zero verification of the verdict the next phase starts from. Previous: 3286.  # 2026-09-04: +68 — Round 95 站0/站2. Two changes, both restoring something a previous round removed by accident. (a) `_print_fr_scoped_overrides_py`'s per-FR coverage command gets its `coverage json --include=<fr files>` step back: Round 94 replaced a working `coverage run -m pytest T && coverage json --include=...` with a whole-tree `--cov-report=term-missing` on the strength of a double-instrumentation bug the removed command could not have — it never passed `--cov=` to pytest. Two things went with it. The FR scope: Gate 1 is single_fr and `s4_score_verdict`'s fabrication test is `harness < threshold <= agent`, so an agent reading a whole-tree TOTAL of 85 while its own two modules sit at 70 is blocked for fabricating a number this file printed the command for. And the schema: score.py R9 re-derives the percentage from `tool_outputs` and only recognises coverage/istanbul JSON, so a term table makes the one rule that checks a self-reported number skip in silence (measured: agent 100.0 against true 45.0, zero R9 issues). +49 lines, of which 34 are the comment carrying that measurement and the reason the runner swap itself is kept. (b) `cmd_gate4_tag` becomes idempotent on the tag name (`rev-parse --verify`, not `git tag -l`, which globs), +19 including the note on why not `git tag -f`: that removed the reason P6's step 2 carried a skip clause which was also skipping the `git push origin --tags` inside it. Previous: 3218.  # 2026-09-02: +4 — Round 87 站8: two remediation pointers, one per spec-coverage block site. 站2 removed the "Fix: add test cases for the uncovered TEST_SPEC.md sections" sentence from both because the producer now prints a better one — and `test_all_blocked_messages_in_hot_paths_carry_a_remediation_element` caught that the `[BLOCKED]` line itself was then left with nothing actionable in its immediate window, which is what workflow JS reads. The pointer names where the list is rather than restating the instruction. Previous: 3214.  # 2026-09-02: +56 — Round 87 站2: `_denominator_provenance` and its patch into the gate result. Net of a −4 subtraction: the "Fix: add test cases for the uncovered TEST_SPEC.md sections" sentence was live at BOTH the Gate 1 and the Gate 2-4 block sites, a third statement of an instruction whose producer now prints it once (`spec_coverage._undelivered_remedy`). So +60 added / −4 removed. Roughly 30 of the 60 are the docstring carrying the measurement that justifies the field: taskq-redo's TEST_SPEC.md read 97 declarations under the pre-R73 parser and 130 under the current one, moving 65/97 = 67.01% (PASS at 60) to 72/130 = 55.38% (BLOCKED), and no committed artifact recorded which parser produced either number. Previous: 3158.  # 2026-09-02: +7 — Round 87 站1: `_record_undelivered_tests` was the THIRD reader of "which declared tests are undelivered" and the only one still computing it presence-only, so the ledger row it writes would have contradicted the gate result it sits beside. 3 lines pass `spec_coverage._live_test_outcomes(...)` and carry the per-row `why`; 4 are the comment recording that a third statement of one rule is the defect this round is repairing, not a shape to reproduce. Previous: 3151.  # 2026-08-31: +30 — env-check's install-failure exit wrote nothing to env_check_result.json, so a stale `ready: true` from a prior successful run survived a later failed `run-env-check` alongside its RC=1 exit code — the two disagreed and a downstream reader (the workflow JS anti-fabrication cross-check) trusted the stale file. `cmd_run_env_check`'s `not _deps.ok` branch now writes a ready=false result with the blocked reason before returning 1. Previous: 3121.  # 2026-08-24: +93 — Round 73 站4: `_patch_verify_target_evidence` and its call site beside `_patch_mutation_score`. Roughly sixty of the lines are the docstring and the call-site comment, and they carry the measurement: taskq-new's committed Gate 4 records `execute_verification_target` at 100.0 with `score_source: framework` and a `tool_evidence` ending "NFR-12 satisfied", written by the agent because that field has no writer anywhere in this repository — over a Makefile whose `migrate-roundtrip` is a `reset_db()` call, with no `alembic upgrade head`, no round-trip, and a `test:` target `verify-system` does not depend on. The code is a read, a branch on `verify_target_findings`' status and a write; nothing new is measured, and the docstring says why WHICH steps a requirement asked for is still not decided here (Round 43: a check with no executor is written down, not invented). Previous: 3028.  # 2026-08-24: +20 — Round 72 站2: `_patch_mutation_score`'s evidence line branches on whether the artifact carries the provenance stamp. The unconditional string said "framework: compute_mutation_score → killed=… survived=… score=…" about any file with a `score` key; taskq-new's committed gate4_result.json carries it in front of a number rebuilt by hand from a stale cache with 685 mutants excluded by the author. Six lines are the branch and its alternative sentence; the rest is the comment recording that blocking a verdict while keeping its evidence line is Round 69's write-after-the-verdict one field over. Previous: 3008.  # 2026-08-23: +30 — code-review follow-up: `--force`'s sentinel bypass (f0de7ea, below) skipped run-gate's anti-fabrication check unconditionally for ANY FR/gate, not just the FR-99 recovery shape it was built for. Narrowed to require genuine, fr_id-matching gate{N}_result.json evidence on disk (reuses `_load_gate_result_json`, already used by `_collect_da_waivers`) before --force can skip the sentinel. Previous: 2978.
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
    "cli/phase_cmds.py": 2257,  # 2026-08-30: -559 — Round 82 站3: the seven steps advance-phase takes once its prechecks pass (`_advance_step_*`, extracted by Round 81 站7 and left here) moved verbatim to cli/advance_steps.py, with `_run_doctor_after_advance` — the last thing the command does, measured to have zero back-dependencies, and the only name here those seven read. Eight fingerprints unchanged; the golden was not regenerated. Fifteen imports the moved code was the only user of went with it. Four tests had to be repointed and the reason is worth recording: a re-export keeps the MOVED name patchable, but not the names that code READS — `monkeypatch.setattr(phase_cmds, "run_doctor", ...)` would still have succeeded against a copy nothing resolves, i.e. green and measuring nothing. The AttributeError is what caught it. Previous: 2816.  # 2026-08-30: -695 — Round 82 站2: the nine checks advance-phase runs before it will move a phase (`_precheck_*`, extracted by Round 81 站6 and left in this file) moved verbatim to cli/advance_prechecks.py, with `_MYPY_EXCLUDE_ARGS` — the only name here they read, whose single call site is inside one of them. Byte-identical by AST source-segment sha256: tests/golden/god_file_split/surface.json was NOT regenerated for this move, and nine unchanged fingerprints are the claim. Re-exports left behind, so every call site in `_advance_prechecks`, every test that patches them through this module, and tests/test_mypy_excludes_harness_submodule.py's import are untouched. Four imports the moved code was the only user of went with it. This is the harvest 站6 of Round 81 promised and could not take: that commit raised this entry by 243 while shrinking the function by 574, on the argument that the trade was worth it and the lines would leave later. Previous: 3511.  # 2026-08-29: +243 — Round 81 站6: `_advance_prechecks` 818 -> 238, its nine statement runs extracted to module-level `_precheck_*` helpers. The file GROWS while the function shrinks by 580: nine signatures, nine call sites, nine short docstrings and one block comment carrying the rule. That trade is the point — this ratchet has been raised 298 times and lowered 5 precisely because the thing that grew was a function and nothing was asking about functions (Round 80 站6). tests/test_function_size_ratchet.py is what asks now, and it forced the 818 -> 238 harvest in this same commit. Bodies byte-identical to what they replaced, checked against tests/golden/extraction/phase_cmds.py.before. Previous: 3268.    # 2026-08-28: -278 — Round 80 站7d: the six functions around the handover commit advance-phase makes (`_advance_commit_targets`, `_git_head_short`, `_uncommitted_deliverables`, `_porcelain_paths`, `_enforcer_moved_note`, `_advance_fsm`) moved verbatim to cli/advance_commit.py. Contiguous, zero in-module dependencies, byte-identical by AST source segment. Three imports the moved code was the only user of (`typing.Iterator`, `StateTransaction`, and the datetime pair the header initially duplicated) went with it. Total for 站7: 4233 -> 3268, the first sustained harvest on an entry raised 44 times and lowered never. Previous: 3546.  # 2026-08-28: -454 — Round 80 站7c: the nine tree-reading checks and repairs advance-phase runs (`_check_gate1_live_coverage`, `_gate1_per_fr_coverage_verdict`, `_check_gate_score_variance`, `_regen_traceability_views`, `_regen_and_stage_view`, `_broken_deliverable_anchors`, `_warn_if_view_lost_its_anchor`, `_scope_violation_scripts`, `_scope_debug_name_match`) plus the two `_SCOPE_*` frozensets only the last two read, moved verbatim to cli/advance_checks.py. They referenced nothing else defined in phase_cmds, which is why the block could move without a re-import cycle. Byte-identical by AST source segment; re-exports left behind for the five call sites that remain here. Previous: 4000.  # 2026-08-28: -233 — Round 80 站7: the nine P(N)->P(N+1) handoff validators, `_HANDOFF_VALIDATORS` and `_resolve_fr_ids_from_manifest` moved verbatim to cli/handoff_validators.py, byte-identical by AST source segment (tests/test_god_file_split_safety.py, whose net was woven for this file in its own commit first) with the argparse surface unchanged and re-exports left behind. Three imports that the moved functions were the only users of went with them. This is the first harvest on this entry: the ceiling was raised 44 times across this repo's history and lowered never, which is what happens when the thing that grew is a function and nothing is asking about functions — see Round 80 站6. Previous: 4233.  # 2026-08-27: +10 — Round 80 站5: the docstring of `_trace_dirty_state` corrected. It documented its second comparand as "the newest `tests/test_fr*.py`" and has not been that since the scan became language-aware — `iter_test_files` walks every test file the project declares, which is why adding any new test to this repo trips the probe. Two of the ten lines are the corrected sentence; the other eight record why the SENTENCE was corrected rather than the code (the wider scan is the intended one) and that a docstring naming a narrower population than the code reads is what Round 78 站6 measured. Growth is a comment, and 站7 of this round takes this file down by ~1400 lines. Previous: 4223.  # 2026-08-26: +19 — commit f6736760: mirror Round 72 站1 prev_record_pending onto prepare-commit-msg hook. Previous: 4204.  # 2026-08-26: +74 — Round 78 站3 rewrites this entry, because the two before it moved the integer and left the note. `c66402d1` raised 4130 -> 4200 for Plan E and wrote the arithmetic; `da8e70fd` then moved 4200 -> 4320 for Plan F and did not touch a word, so the note under a ceiling of 4320 read "raising the ceiling 4130 -> 4200" and the file sat 101 lines below its own limit. That headroom is the ratchet pre-authorising growth it exists to make visible (module docstring: "The product is diff-visibility of growth, not an absolute cap"). One entry now covers the whole span from the last honestly justified ceiling: Plan E's pragma audit (+41: an SSOT-rendered BLOCK and its path normalisation, `d5549c3a`), Plan F's phantom check (+50, `da8e70fd`), and Round 78 站1 taking 15 of those back out when the phantom decision moved to core/quality_gate/sab_amender.phantom_module_block — it read the source directory relative to the process cwd and reported every registered module as missing from anywhere but the project root, measured on all nine corpus projects. The ceiling is the file's actual length, which is the convention every honest entry here follows (4914=4914 at Round 74, 4961=4961 at f893c7ae, 5051=5051 at Round 77): a ceiling above the count is headroom nobody reviewed. Previous: 4130.  # 2026-08-25: +70 — SUPERSEDED (kept for the record, and because it is the note this round was written about): Plan E (Round 50+): early pragma audit in `_advance_prechecks`. The previous commit d5549c3a added ~41 lines to `cli/phase_cmds.py` for an SSOT-rendered BLOCK + path normalization; raising the ceiling 4130 → 4200 here keeps the file under the ratchet without splitting it (deliberate growth, justified by the same commit-set). Previous: 4130.  # 2026-08-24: +29 — Round 72 站7: the WRITE_SCOPE refusal for a tool's leftover copy in the delivered tree, beside 站6's and for the same reason — it reads the tree and nothing else. `_scope_violation_scripts` has guarded this ground since a workflow agent stranded `_diag_constitution.py`, but it asks `git status` for UNTRACKED files while the gate/release path commits with `git add -A`, so one commit later the file is tracked and that check can never see it again. taskq-new shipped two through P1-P8 and Gate 4. The scan is `core.utils.delivery_scope.backup_artifacts`. Previous: 4101.  # 2026-08-24: +46 — Round 72 站6: the WRITE_SCOPE refusal for a delivered test that reads evidence out of a directory `advance-phase` deletes at every transition. Fifteen lines are the finding loop and the operator message (which file, which line, and that `.methodology/gate_evidence/` survives); the rest is the comment recording the loop it ends — taskq-new's NFR-07/NFR-11 tests skip after every advance, Round 46 站1 turns the NFRs PARTIAL, completeness falls under 90%, and `cd47fae` (leaving P5) and `8b9a309` (leaving P7) are the same repair commit twice. Placed at the HEAD of `_advance_prechecks` because it is the only check there that reads nothing but the delivered tree. The scan is `core.evidence_retention.evidence_in_cleared_dirs`. One of the 46 lines is `tests/test_blocked_message_contract.py` catching this very message with no actionable element in its window. Previous: 4055.  # 2026-08-24: +50 — Round 72 站1: `_verify_entry_gate` gains `prev_record_pending` and reads the previous phase's record for CONTENT, not presence. Four lines are the keyword and the two-branch check; the rest is the comment recording the deadlock it ends — this function is called by `cmd_advance_phase` with `phase = completed_phase + 1` BEFORE that same function writes `phase_completed[completed_phase]`, so from `--completed 3` on it demanded a record only that call produces. taskq-new, the only project to run P4+ after Round 53 站5c landed, shipped six hand-written entries to get past it, the last reading `{"sha": "PLACEHOLDER_WILL_BE_REPLACED_ON_ADVANCE", "delivered_tree_sha256": "PLACEHOLDER"}` — which is why presence stopped being enough. The shape check itself is `core.harness_provenance.phase_record_defects`. Previous: 4005.  # 2026-08-22: +9 — Round 68 站2: the `record_runner_scope` call in `_regenerate_mutmut_scope`, placed ahead of that function's four early returns. One line is the call; the rest is the comment recording why it goes there — the function renders `paths_to_mutate` from the SAB and leaves `runner`, which decides which tests may kill a mutant and therefore what the mutation score is, entirely to the project. The runner is a fact about the project in every state this function can return in, and the project most likely to have hand-written one is the project with no SAB. The decision itself is core/quality_gate/mutmut_scope.record_runner_scope. Previous: 3996.  # 2026-08-18: +36 — Round 57 站1: `_gate1_per_fr_coverage_verdict` now runs at every phase Gate 1 runs, and reports two conditions apart. The growth is the second branch and its comment: a run blocked only by the whole-project floor must not read as a per-FR defect, because the operator would go looking for an FR to fix and find every one of them green. The `phase == 3` condition in front of the call is gone rather than generalised — Gate 1 declares `scope: single_fr` and this is Gate 1's check. Previous: 3960.  # 2026-08-17: +71 — Round 56 站6: `_gate1_per_fr_coverage_verdict`. `_check_gate1_live_coverage` is what returns 14 and stops advance-phase, and at Phase 3 — the per-FR TDD window, where Gate 1 is a per-FR gate — it asked one whole-project question and printed "whole-project coverage". Measured on taskq-cc: FR-01 at 97.06% on its own modules read as 8.5% because the SAB declares ten modules later FRs will activate, and the run spent three rounds dispatching CODE-FIX against a number that was never about FR-01. The loop itself is ten lines; the size is the per-FR report (an operator who is told "8.5%" cannot tell which FR to fix) and the docstring recording why an FR with no SAB scope falls back to the whole-project number — that figure carries every other FR's uncovered modules, so it is the strictly harsher answer, and falling back to a looser one would be the abstention Round 46 forbids. Previous: 3889.  # 2026-08-17: +64 — 7e26019: `PHASE_GATES` (phase -> the gate numbers that phase actually runs) plus `_phase_gate_tools`, which splits a missing-tool verdict into critical (a gate THIS phase runs needs it) and anticipated (a later phase will). `_cmd_run_phase_impl` used verify_all_gate_tools, so P1 entry demanded scancode — a tool only the P6 gate ever invokes — and a host whose pyicu ABI conflicts with the system ICU could not open Phase 1 at all. Most of the size is the PHASE_GATES table and the comment mapping each phase to its gates. Previous: 3825.  # 2026-08-16: +45 — Round 53 站5c: the P4+ entry gate gains a second condition, that phase N left a `phase_completed` record before phase N+1 may be entered. Four lines are the lookup and the refusal; the rest is the comment recording what the record holds (SHA, enforcer, delivered_tree_sha256 — Rounds 24, 26 and 44 each put a fact there) and why the ORDERING is deliberately not touched: the entry's `sha` is HEAD after the handover commit, which is what every consumer of it passes to `git merge-base --is-ancestor`, so writing it earlier would make it name the wrong commit. taskq-super reached Phase 9 with no entry for phase 5 and nothing objected. Previous: 3780.  # 2026-08-13: +7 — Round 50 站6: the advance-phase cleanup iterates core/evidence_retention.ADVANCE_CLEARED_DIRS instead of naming `.sessi-work` itself, so the list of directories a verdict may not cite and the list advance actually deletes are one statement. The body is the same backup/rmtree/restore block re-indented one level; the +7 is the loop, the import and the comment. Previous: 3773.  # 2026-08-13: +4 — Round 50 站4b: the comment on _regenerate_mutmut_scope recording why resolve_mutation_scope now takes project_root (a leaf module is a .py file, not a directory; eight real scope paths were discarded as non-existent while all eight modules were on disk) and the .is_dir() -> .exists() change beside it. Previous: 3769.  # 2026-08-12: +62 — Round 45 站5: `_run_doctor_after_advance`, called once at the end of cmd_advance_phase. `grep -rn "run_doctor"` over the repository found ONE call site — cli/project_cmds.py, which IS the doctor command — so Round 43 站4's enforcer provenance, Round 44 站4's milestone-tree check and Round 45 站3's per-FR evidence reconciliation had never been read at a phase boundary. The check itself is six lines; the size is the docstring recording the three ways the wiring is deliberately weak (runs after the advance, ERRORs only, exit code unchanged) and why — station 2 removed thirty false ERRORs from this same command four hours earlier, and a check with that history does not get to stop a pipeline. Previous: 3692.  # 2026-08-11: +155 — Round 44 站2: cmd_advance_phase refuses to record a phase on a tree git has not. `_uncommitted_deliverables` (git status --porcelain -z -uall, minus delivery_scope.is_harness_volatile and minus the maximal `_advance_commit_targets` — both existing single sources, neither restated), `_porcelain_paths` (the -z parser, including the rename record's second field), `_git_head_short`, the [BLOCKED] branch that names every file and writes one `milestone:uncommitted` degradation each, and the `delivered_tree_sha256` field in phase_completed beside enforcer_sha/enforcer_surface. Measured on taskq-advance: the entry obligation for FR-02/FR-06 was cleared at 13:14 by writing `@given` into two test files, `81bbeb4 handover: advance to Phase 4` recorded the phase at 13:17:55 without them, and they entered git at 13:32 — `git archive 81bbeb4 | grep -rl "@given"` is empty. Most of the size is the operator message (which of "commit it" or "gitignore it" applies depends on whether the file is a deliverable, and only the operator knows) and the docstring recording why the exemption set is those two sources rather than "all of .methodology/". 2026-08-11: +15 — Round 44: _MYPY_EXCLUDE_ARGS, a named constant used by _advance_prechecks's mypy subprocess.run call. `mypy .` from a consumer project's root has no submodule-awareness (unlike ruff, whose file-walker stops at a nested .git by default) and was walking straight into harness/tests/fixtures/mutmut_bare_cfg/03-development/tests/conftest.py — a fixture harness ships shaped like its own canonical layout — colliding with the consumer's real 03-development/tests/conftest.py ("Duplicate module named conftest"), a fatal error that aborted the whole type-check before examining anything else, for every project using the standard layout, the first time its own conftest.py existed and advance-phase's mypy step actually ran. Named as a module constant (not an inline literal) so it is independently testable — see tests/test_mypy_excludes_harness_submodule.py (sized to current 3537). 2026-08-07: +40 — Round 43 站4: _enforcer_moved_note, called from the obligation [BLOCKED] block. state.json has recorded enforcer_surface per completed phase since Round 19 站3 / Round 29 站4 and nothing compared it to the present, so a finding against a Phase-1 artifact on a project whose Phase 1 passed five rounds earlier read as "you broke this" when the truth could be "the bar moved". Diagnosis only — the verdict is not waived (grandfathering a rule to artifacts accepted before it existed is Round 38's no-waivable-threshold rule inverted: the framework could then never raise its own bar). Most of the size is the docstring making that distinction explicit, because the next reader's instinct will be to turn it into an exemption. 2026-08-07: +40 — Round 43 站2: cmd_advance_phase refuses to advance while the P(N+1) entry preview reports blocking findings. The block prints each finding by check/rule/file:line (R24 站1's rule that a [BLOCKED] carries the remediation, not a pointer to it), writes one `obligation:<check_id>` degradation record per finding, and returns EX_ADVANCE_ENTRY_OBLIGATIONS before _advance_fsm. Round 14 A computed this list and rendered it into HANDOVER.md, which has one producer and no reader; the state that produced — current_phase = N+1 while N+1's entry preflight fails — is what scripts/hooks/pre-push has to guess around by pattern-matching HEAD's subject. Partly offset by removing the obligations parameter from _advance_fsm and the HandoverGenerator.write call (dead once the advance cannot happen with obligations outstanding). The growth is the operator message and the comment recording why refusing beats advancing-and-warning. 2026-08-07: +27 — Round 43 站1: cmd_run_phase now owns the bounded traceability repair. preflight_all() returns, and if the traceability result is blocking-and-failed with an open FR gap the command calls PhaseHooks.repair_traceability_gap, re-runs that one check, and recomputes all_passed. The repair used to run inside preflight_traceability, which made preview_next_phase_blocking — documented as mutating no state — write to the project on every advance from P4 with an open gap. The lines here are the guarded call plus the comment recording why the caller, not the check, decides to write. 2026-08-06: +91 — Round 39: cmd_advance_phase now calls _verify_entry_gate at L526 (normal advance) and L430 (re-verify mode), before _advance_fsm and before `git add` at L802. Earlier Round 38's recovery helper (called from prepare-commit-msg hook) wrote to the working tree only — `git add` had snapshotted the pre-recovery state.json into the index first, so the commit materialized the orphan SHA, not the recovered one (observed on taskq-api 2026-08-05: cadbd6a state.json carried d061387). Calling the gate directly inside cmd_advance_phase means recovery writes happen before staging, so the handover commit captures the healed SHA. Mirror of cmd_run_phase:1695. The size is the two gate call blocks (~16 lines each) plus a docstring explaining why ordering matters. Re-verify mode (L430) gains the same call so a manual `git reset` followed by re-verify also self-heals state.json — the user's `不要改 taskq-api` constraint forces this defensive coverage. 2026-08-06: +26 — Round 39 secondary: _advance_prechecks for completed_phase >= 3 now also runs PhaseHooks.preflight_sab_check after the DriftDetector block. preflight_sab_check (phase_hooks.py:613-691) already validated allowed_dependencies — but only via preflight_all() in cmd_run_phase:1701 (pre-push), never at advance-phase. Without this wire, a hand-edited SAB.json or one from a SAD.md block that slipped past the now-extended validate_sab_block would reach the handover commit. Wrapped in try/except for resilience (parallel to existing DriftDetector pattern). The SAB-missing branch is a separate early-return that avoids tripping preflight_sab_check's "SAB.json not found" path at P3-entry — the DriftDetector block above already covers module existence, and we don't want advance-phase to be stricter than the canonical pre-push path. 2026-08-06: +43 — Round 38 self-heal dangling phase_completed SHA in _verify_entry_gate. push-checkpoint writes pre-push HEAD to state.json before its commit; an out-of-band `git reset HEAD~N` between the write and the commit leaves the recorded SHA as an orphan (confirmed in taskq-api: d061387 recorded as phase_completed[2].sha, unreachable from HEAD 3836985, both parented at 4355bb3). Recovery lives in core/quality_gate/phase_completed_recovery.py — captures explicit HEAD, searches `--grep phase{prev}(review-complete)` HEAD-reachable history, validates ancestry, lock+reload+compare+atomic_write, appends to top-level phase_completed_recovery_log. _verify_entry_gate's existing hard-fail at L1799-1802 is now preceded by a PASS-with-self-heal branch; the docstring block above it is what grew. cmd_advance_phase's post-commit writer at L834-898 now merges — not replaces — phase_completed[completed_phase] so a recovery audit set during prepare-commit-msg survives into the handover commit. Same lock contract as the rest of the file. The actual code is in the new helper; the lines added here are the import, the call-site + its reason string, and the merge-preserve metadata extraction. Net-neutral refactor considered and rejected: extracting the recovery protocol into a private helper inside this file would have hidden the gate's hard-fail reason behind a function call, which is the readability loss the existing inline structure was chosen to avoid. 2026-08-04: +89 — Round 34 站2: _broken_deliverable_anchors plus its [BLOCKED] branch. Round 33 站1 gave the H1 rule a single SOURCE; it had no single MOMENT, so a deliverable that satisfied the anchor at P1 and was rewritten at P4 satisfied nothing thereafter. Measured on run-all-by-workflow's TRACEABILITY_MATRIX.md: correct at dfd7abd, blank first line from fa21439 (the P3→P4 advance) onward, green through Gate 4 and P8 with last_gate 4, on four of five real projects. Placed after _regen_traceability_views so the views the framework owns are repaired first and only files it may not rewrite can reach the BLOCK. The size is the operator message (which of the two populations the file belongs to changes the remedy) and the docstring recording why the scan is registry-wide rather than phase-scoped. 2026-08-04: +26 — Round 33 站3: the P1-exit NFR vocabulary check. SRS.md states `type:` and `dimension:`; sab_parser is the only enforcement of the first and it runs in Phase 2, by which point the value sits in an approved deliverable SAD.md must transcribe verbatim — measured five B-review rounds to the HR-12 hard cap with no convergence possible. Placed before every other P1 check because a vocabulary error makes every downstream reading of the file wrong and the fix is one word. 2026-08-04: +42 — Round 33 站2: _warn_if_view_lost_its_anchor, called from _regen_and_stage_view. A view regenerated from SSOT replaces a peer-reviewed deliverable, and it did not inherit that deliverable's loader anchor: measured with the framework's own read-file, TRACEABILITY_MATRIX.md returned PREFIX_MISMATCH on 4 of 4 real projects because the H1 sat below the AUTO-GEN sentinel. Recorded in the degradation ledger rather than blocking — the anchor is only read on re-entry into Phase 1, and the defect was the framework's own, so blocking here would stop every existing project on our bug. The size is the docstring explaining that WARN-not-BLOCK choice; the check itself is six lines. 2026-08-03: +29 — Round 32 站2: the finalize-gate sentinel check reads a receipt and cross-checks it against gate_timestamps.jsonl and .gate1_scores.json (gate1_evidence.verify_finalize_evidence, the same function core/doctor.py calls) instead of asking .exists() of a file whose whole content was a timestamp. The growth is the second [BLOCKED] branch: "present but unbacked" is a different diagnosis from "absent" and needs its own message, because the remedy reads the same but the finding does not. 2026-08-02: +89 — Round 30 站2: _regenerate_mutmut_scope renders [mutmut] paths_to_mutate into setup.cfg from the SAB at the P2→P3 handoff, and the advance commit stages it. The scope is a decision; before this it lived in no artifact at all, and taskq-advance mutated 3384 lines against a SPEC that limited Gate 2 to 1846. The four ledger branches (unreadable SAB / no scope_layers / directories that do not exist / hand-edited value replaced) are the bulk of the size: each one is a distinct thing the next reader needs told, and collapsing them loses the diagnosis (sized to current 2953→3042). 2026-07-29: -28 — Round 25 站3: the fastapi/httpx advisory is deleted (unconditional, hardcoded to a Python web stack, WARN-only) and _check_submodule_drift moved to core.doctor._check_submodule_behind (advance-phase's only network call, blocking nothing). Harvested below the 2970 station 1 raised it to (2970→2942). 2026-07-29: +8 — Round 25 站1: the TDD block's `--cov-fail-under=100` became an explicit comparison against the exact coverage percentage from the shared suite run (core/quality_gate/test_suite_run.py), so the same measurement can also answer FrameworkEnforcer's 70/80 and Phase Truth's without three more executions of the same tests. The [BLOCKED] branch now distinguishes "tests failed" from "coverage short" instead of leaving pytest to render one verdict for both (sized to current 2962→2970). 2026-07-28: +41 — Round 23 站1: cmd_advance_phase gains an opt-in `--push` that publishes the handover commit it just made, plus its [BLOCKED]/no-rollback branch and the subparser flag. The push previously lived in every phase workflow's Sync box — prompt-layer only, so a human or CI caller never got it (same shape Round 22 站2 relocated for manifest integrity) (sized to current 2864→2905). 2026-07-27: +6 — Round 29: run-phase auto-skips the spawn-substrate preflight probe when CI/GITHUB_ACTIONS is set — CI never dispatches an interactive per-FR loop, so the probe (which requires the claude CLI, never present there) can only ever fail (sized to current 2825→2831). 2026-07-26: +80 — Round 14 A2/A4: cmd_advance_phase now previews P(N+1) entry blocking via PhaseHooks.preview_next_phase_blocking(), threads obligations into HandoverGenerator.write + _advance_fsm, and replaces "Ready to begin Phase N+1" with a pointer to the obligations table (sized to current 2768). 2026-07-26: +33 — Round 15 §2: new cmd_preview_next_phase() + preview-next-phase subparser — a read-only P(N+1) obligation query that never writes state.json/HANDOVER.md/a commit, usable before P(N) exit gate even passes (sized to current 2813).
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
    "cli/fr_cmds.py": 2172,  # 2026-09-06: +50 — Round 100 站1 (PHANTOM exit code split). `_classify_infra_or_harness_bug` direction-specific-first classifier (was: collapse-all to "INFRA"; now: PHANTOM / UNREGISTERED / INFRA / HARNESS_BUG). `_abort_dispatch_infra_or_harness_bug` 3-way return (was: HARNESS_BUG or 25; now: 70 or 45 or 25) with the new PHANTOM/UNREGISTERED print blocks naming each remediation channel. Previous: 2122.
    # 2026-09-06: +75 — fix(fr-step-no-progress): content-aware diff (git diff HEAD + untracked) + block_sig to eliminate RC=2 false positives on GREEN-pass idle rounds and consecutive same-file edits. Previous: 2047.  # 2026-09-05: +113 — Round 96 站0: the SUITE_TEST_FAILURE route. `BlockSignal` (a frozen dataclass carrying the block's kind, headline and items) plus the two regexes that read `_format_block_diagnostic`'s own rendered shape, a new dispatch branch, and `_classify_snapshot_failure` taking `block_kind` ahead of every snapshot heuristic. 113 lines, of which ~80 are the comments carrying the measurement: on taskq-final Phase 8, FR-07 spent 22 rounds and 9.5 hours being sent to COVERAGE-FIX for five tests that fail only in the whole-suite run, because the classifier reads a single-file snapshot while the gate blocks on the whole directory, and the channel that carried the reason (`_extract_block_reason`) required `[BLOCKED]` and a detail key on one line — a combination no line in the repository has. Previous: 1934.  # 2026-09-01: +47 — Round 85 站2: `fr_step_poll_plan` and `_FR_STEP_POLL_INTERVAL_S`. The per-FR GATE1 / GATE1-DELTA prompts used to name their own poll cap ("cap 40 polls", then "cap 96"), and both literals were below the worst case this loop produces — the comment they were copied from counted the CODE-FIX spawn per fix round and not the full GATE1 re-dispatch that follows it at the same indent. The function states that budget once, from the same fr_config > values > built-in precedence `cmd_run_fr_step` reads 1600 lines above, and takes the MAX across the manifest because one cap covers a phase while fr_config is per-FR. It lives here rather than in core/harness_config.py so `_STEP_RETRY_ATTEMPTS` stays a single statement; `load-context` imports it. 39 of the 47 lines are the docstring and the comment on skipping a malformed entry. Previous: 1887. # 2026-08-30: -562 — Round 82 站4: the four steps run-fr-step dispatches through (`_frstep_*`, extracted by Round 81 站9 and left here) moved verbatim to cli/fr_step_stages.py, and so did the two families they read — the dispatch-error readers and the idempotence family — because the alternative was that module importing back into this one. 357 lines of cargo for 218 of payload, recorded rather than hidden: each group is coherent (one answers "what did this dispatch failure mean", the other "has this step already been done"), which is why they are one module and not two invented boundaries. Eleven fingerprints unchanged; the three assignments that travelled are not `def`s and this repo's byte mechanism does not cover them. Nine orphaned imports went with the code. Previous: 2449.  # 2026-08-29: +56 — Round 81 站9: `cmd_run_fr_step` 940 -> 770, four statement runs extracted to `_frstep_*`. The smallest yield of the four mega-functions and the reason is in the code, not the method: only 182 of its 940 lines bind nothing their successors read. The rest threads state through a dispatch loop, and threading is exactly what this round refuses to do by hand. Previous: 2393.  # 2026-08-23: +11 — Round 70 站2: `_abort_dispatch_infra_or_harness_bug` returns EX_HARNESS_BUG for the HARNESS_BUG class and keeps 25 for INFRA. One line is the branch; ten are the docstring recording that the function computed `cls` and then discarded it, and that 70 is the crash boundary's existing code rather than a new one. Previous: 2382.
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
    "core/quality_gate/gate1_evidence.py": 1271,  # 2026-08-26: +85 — Round 78 站3 writes the entry `da8e70fd` did not. That commit moved this ceiling 1186 -> 1300 for Plan F and added no note at all, so the newest justification here stayed dated 2026-08-18 (Round 57 站3, "Previous: 1128") and said nothing about the change in force — 29 lines of unreviewed headroom under a sentence about a different number. The growth is Plan F's `ModuleScope`: `_fr_module_paths` used to return `Optional[list[str]]`, collapsing "this FR declared no modules" and "it declared one that is not on disk" into one `None`, and `validate_fr_coverage_immediate` fell through to the whole-project percentage for both — so a phantom deliverable was reported with a number that had nothing to do with it. The three-state record separates them and only the phantom branch changes behaviour; no-scope keeps its fall-through verbatim. Measured across the nine corpus projects and every FR (67 values): 67 concrete, 0 phantom, 0 no-scope — a latent repair, not a live wound, and the ceiling is the file's actual length. Previous: 1186.  # 2026-08-18: +58 — Round 57 站3: the `FrCoverage` record (percent / executed / coverable / files) plus `fr_coverage_record` and the split of `_coverage_for_paths` into the record producer and the thin percentage wrapper its one fallback-holding caller still needs. `_coverage_record_for_paths` already computed both sides of the ratio and threw them away, which is how S4 came to write a per-FR percentage into `score` while `tool_output` cited the whole-project audit whose last line reads TOTAL 62% (Round 42 站4 — the denominator travels with the number; Round 45 — a verdict may not outlive OR contradict its proof). `measured` is a property rather than a second `percent is None` comparison beside the first, which is how two readers come to disagree about whether 0.0 is a measurement. Previous: 1128.  # 2026-08-18: +26 — Round 57 站2: `_fr_module_paths` becomes one call to `cov_utils.resolve_fr_scoped_src_files`, the resolver two production sites already read, and `_coverage_for_paths` learns to expand the coverage-style globs that resolver emits for package-style SAB entries. The code shrank; the growth is the docstring recording what was measured before the swap — across all seven corpus projects and every FR (61 values) the two resolvers produced identical numbers, so this is a latent divergence being closed, not a live wound, and `**` is expanded with Path.glob rather than fnmatch because fnmatch would require an intervening directory and miss `executor/runner.py`. Previous: 1102.  # 2026-08-18: +12 — 57bce59: fr_coverage_from_last_run & _fr_module_paths accept Path | str to prevent S4 TypeError.  # 2026-08-17: +29 — Round 56 站6: `fr_coverage_from_last_run`, the public per-FR entry point the Phase 3 gate calls once per FR. `validate_fr_coverage_immediate(fr_id=…)` also scopes per FR but goes through `run_suite` first, which is right for its own caller and wrong for a gate asking about every FR in turn — Round 25 站1's one-execution invariant. This one executes nothing: the suite has already run and the answer is arithmetic over the `.coverage` on disk. It returns None rather than a number when the per-FR scope cannot be computed, so the caller can keep "could not measure" apart from "measured and failed" (Round 32 站4). Previous: 1061.  # 2026-08-17: +122 — 7e85f24: per-FR coverage scope at P3 gate (_coverage_for_paths, _fr_module_paths, _is_phase3_per_fr) so empty phantom modules stop dragging the score.  # 2026-08-12: +17 — Round 45 站6: _per_fr_result_problems skips the digest comparison when the artifact on disk belongs to a later phase than the receipt. gate_results/gate1/{fr}.json carries no phase — one slot per FR, rewritten by every phase that re-runs it — so taskq-advance's P8 run left five FRs holding phase-8 results beside phase-7 receipts. Without this the check would fire for every FR at every phase boundary forever. The size is the comment recording that measurement.  # 2026-08-12: first entry (was 858, under the 900 default) — Round 45 站3: `per_fr_result_path` as the SSOT the two cli/_shared.py resolvers and cli/gate_cmds.py's writer now call instead of spelling `.methodology/gate_results/gate{N}/{fr}.json` themselves, plus `_per_fr_result_problems` (dereference the receipt's result_sha256 against that file) and `_deleted_by` (name the commit that removed it). Most of the growth is the docstrings recording why a schema-1 receipt's digest is not compared — the alias it pointed at is overwritten by the next FR's finalize, so comparing it would manufacture one false accusation per FR. Station 2 also DELETED the two retention windows from this file, so the net is smaller than the additions.
    "core/quality_gate/red_assertion_check.py": 1059,  # 2026-09-02: +4 — Round 88 站1: `spec_ambiguity_notes` resolves the root it relativises against. One line of code; three of comment naming the measurement — it raised ValueError on 2 of the 9 frozen corpus P3 trees, one of four checks sharing the defect, of which Round 87 站9 fixed exactly one. Previous: 1055.  # 2026-09-02: +35 — Round 87 站7: `spec_ambiguity_notes`, the reader for a record the framework has been asking agents to write since `cli/fr_prompts/tdd.py` was authored ("Add `# SPEC_AMBIGUITY: <one-line>` comment in the test … and note the deviation") and never read — a full-tree search for that token found one occurrence, the line of prompt that asks for it. 14 lines are the collector; the rest is the docstring carrying what a reader would have seen: three notes across ten corpus projects, of which taskq-advance's records that "SPEC.md §7 maps 401 to `/errors/unauthenticated` and …" — a live contradiction in a canonical spec, noticed at the only moment in the pipeline where anyone holds the AC prose and the assertion open at once. This file is its home because its subject IS "what TEST_SPEC declares vs what the test asserts"; the note is the agent's own statement of that mismatch. Previous: 1020.  # 2026-07-26: +6 — Round 14 B1: SubAssertion gains fulfill_phase field (1 dataclass line + 5-line docstring note) for the Direction-B TEST_SPEC Properties fulfill-phase schema (sized to current 1011).
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
    "cli/project_cmds.py": 2259,  # 2026-09-05: +34 — Round 96 站1: `--gitleaks-only` and `_write_gitleaks_config`. Round 92 shipped templates/.gitleaks.toml through init-project, which runs once at creation — so no existing project could receive it. Measured on taskq-final, which bumped to Round 92's own commit and then added two more .gitleaksignore fingerprints, the treadmill that round's ledger had just called a workaround. The write moves into one function because the repair path and the install path must not disagree about where the file goes or when not to write it. Previous: 2225.  # 2026-09-03: +15 — Round 92 站1: init-project step [2a/11] delivers `templates/.gitleaks.toml`, the rule-scoped allowlist that keeps the framework's own `.methodology/*.json` audit trail (test names, coverage summaries — Round 90 made these committed) from tripping gitleaks' `generic-api-key` entropy rule, without disabling any other rule. Unlike [2/11]'s CI workflow this file is one projects already hand-author with their own allowlist entries (three corpus projects shipped their own before this template existed), so it is SKIP-if-exists with no `--overwrite` escape hatch — the asymmetry from the CI-workflow step is the reason this is its own numbered step rather than folded into it. Previous: 2210.  # 2026-08-21: +5 — Round 66 站3: `status --full`'s two pytest runs and the `gh repo view` fallback stop calling subprocess directly. The two pytest calls go through core.quality_gate.source_tree_lock.run_against_source_tree, which waits out any in-flight mutation window before measuring and reaps the run's xdist workers when the 30s/120s budget expires; `gh` gets run_isolated only, because reading GitHub metadata must not queue behind mutmut. Three of the five lines are the two local imports and the second call's continuation; the rest is the comment recording why the two sites use different primitives — the next reader's instinct will be to make them the same. The function-local `import subprocess` it replaced is gone. Previous: 2205.  # 2026-08-21: +13 — Round 65 站2: load-context reports `test_target` / `cov_target` from core.quality_gate.test_suite_run.resolve_targets, so the P4 coverage prompt reads where this project's tests are instead of naming `03-development/{tests,src}` itself. Five of the thirteen lines are the call and the two new payload keys; the rest is the comment recording why it is unguarded (resolve_targets is pure path resolution, and a load-context that cannot say where the tests are has nothing to hand the next agent). The prompt-side subtraction is larger than this addition — run-all.js shrank, and one prose restatement of the scoping rule left Phase 4 step 1 with it. 2026-08-06: +12 — Round 40 站1: the CI workflow path and the template path move to core/ci_template.py (one home for both), and the `already exists` branch now says whether the existing copy is still the template. 2026-07-28: +50 lines — fix/cli-init-project-step-10b: init-project step 10b auto-installs pyyaml+jsonschema from harness/requirements.txt into project .venv to eliminate first-call ModuleNotFoundError crashes. 2026-07-27: +13 — Round 29: _check_content_quality gained two per-file-type exemptions (MAINTENANCE_LOG.md from section-count floor, TEST_RESULTS.md from FR-ref rule).
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
    "core/doctor.py": 287,  # 2026-09-05: +8 — Round 96 站1: the gitleaks-scope check joins the CI-template drift check, one template over. Same reader, same WARN level, different predicate — `.gitleaks.toml` is project-owned so it asks whether the project is paying for not having one, not whether its copy matches. Previous: 279.  # 2026-08-31: +10 — Round 83 站4: check 14a, whether the commit this tree is SITTING ON is red on CI. One call line and nine of comment recording the measurement — doctor printed "0 error(s)" on a main whose Framework Self-Tests had been failing for three hours (6ba535e7 pushed 16:37 red, aacac81f fixed it 19:16), and CI's own UI was the only place that fact existed. The decision itself is core/doctor_checks/git_state._check_head_ci_verdict, which also records why `unavailable` produces no finding and why a consuming project is never asked. Previous: 269.  # 2026-08-29: +7 — Round 81 站4: the import and call for `_check_hook_wiring`, numbered 14b because it asks the same question check 14 asks, of the other half of the same command. `init-project` installs a CI workflow AND git hooks; doctor went back to the workflow and never to the hooks, which are the ones `git clone` drops — .git/hooks/ and core.hooksPath both live outside the object store. Six of the seven lines are the comment recording that asymmetry at the call site. The check itself is in core/doctor_checks/git_state.py and the predicate in core/git_hooks.py, where scripts/check_hook_wiring.sh now also reads it. Previous: 262.  # 2026-08-16: +2 — Round 53 站5c: the import and call for `_check_phase_record_gaps`. Two lines, because the check itself is in core/doctor_checks/verdicts.py where its four siblings live. Previous: 260.  # 2026-08-14: +9 — Round 52 站1: the import of _check_verify_target_recipe and its call in run_doctor, plus the six-line comment recording why it is WARN here while finalize_gate blocks on the same two shapes (doctor reports, it does not get to be a second enforcer — Round 38). The check itself is in core/doctor_checks/config_drift.py. Previous: 251.
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
    "scripts/plangen/blocks.py": 1749,  # 2026-09-02: +2 — Round 87 站5: the P3 per-FR [ORCH-POST] block gains the `review-fr-tests` command line plus the three-line note on what a REJECT means (fix the test, not the implementation). One line is the command; the three prose lines replace nothing, because the manual CLI path had no statement of this step at all. Previous: 1747.  # 2026-08-23: +2 — Round 70 站1: `_SPEC_COVERAGE_THRESHOLDS` gains its gate-1 entry (40.0) and the note recording why it was absent. The reader sat behind `if score_gate is not None` and gate 1 declared no score_gate, so declaring one walked into a KeyError on a branch that had never run for gate 1. Previous: 1745.
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
    "cli/checks/specs.py": 905,  # 2026-09-02: +905 — new entry, and it crosses the 900-line god-file threshold on the day it is granted, so the decision is reviewed here rather than drifted into. 751 -> 905: Round 87 站3's `deferred_inputs_violations` and its `_deferred_rows_with_inputs` parser. This IS their home — the NFR Layering Hard Rule that PRODUCES the Deferred table lives in this file, and so does `deferred_section_text`, the parser this check is one call away from; moving it to core/quality_gate/ would separate a check from the section-capture it reads, the same argument the artifact_consistency entry below makes. Roughly two thirds of the added lines are docstring carrying the measurements that justify the rule's exact shape: 772 declared values across eight corpus projects with 107 absent, narrowed to 569 checked / 64 absent (88.8% parity) once values embedded in identifiers are dropped, which is why the check is scoped to the Deferred table's new column instead of the FR tables' legacy content. A split needs a byte-equal net first (Round 49 織網再動刀). Previous: 0.
    "core/quality_gate/artifact_consistency.py": 994,  # 2026-09-05: +5 — Round 97 站0: `_srs_acceptance_criteria` becomes public `srs_acceptance_criteria` and `_adr_path` delegates to ProjectLayout. Five lines, and the reason they are worth it is measured: the report generator carried its own criteria parser that found ZERO acceptance criteria on all eleven corpus projects while this one finds 1,004, so every shipped VERIFICATION_REPORT.md reads "No acceptance criteria extracted" for every FR. Previous: 989.  # 2026-09-02: +3 — Round 88 站1: `check_ac_deferral_targets` resolves the root before relativising the TEST_SPEC path against it. One line of code, two of comment; it raised on 1 of the 9 frozen corpus P3 trees. Previous: 986.  # 2026-09-02: +9 — Round 87 站1: `check_ac_deferral_targets` stops asking `fn not in actual` and calls `spec_coverage.delivery_outcome`, the same helper the score uses. 3 lines are the import and the grading, 6 are the comment recording why the join belongs here: this check BLOCKS on "the criterion has no verifier" while `spec_coverage` SCORED the same condition, and a correctly-named stub satisfied both — one rule with two implementations is one rule with two answers. Previous: 977.  # 2026-08-31: +977 — new entry, and it crosses the 900-line god-file threshold on the day it is granted, so the decision is reviewed here rather than drifted into. 796 -> 977: `check_ac_deferral_targets` (the deferral -> declared-test join), `check_ac_verifier_is_nameable` (an AC may not name this framework as its own verifier), the `_BULLET` continuation fix, and the `_parse_deferrals` change that keeps the verifier clause it had been parsing and discarding. Roughly two thirds of those lines are docstring carrying the corpus measurements that justify each check (35/35 deferrals naming tests, 95/95 criteria naming the harness, 11 projects scanned). This IS their home: every sibling AC check lives here, and so do the parsers they read (`_srs_acceptance_criteria`, `_parse_deferrals`, the `_AC_*` regexes) — putting them elsewhere would separate a check from the parser it is one call away from. A split needs a byte-equal net first (Round 49 織網再動刀) and that is a round of its own, recorded in docs/PROPOSAL_ADJUDICATIONS.md with its re-open condition. Previous: 0.
    "core/phase_hooks.py": 2072,  # 2026-09-02: +8 — Round 87 站6: `check_test_seams` joins the phase>=3 branch of `preflight_artifact_consistency`, beside the AC checks that share its phase rule. 2 lines are the import and the call, 6 are the comment recording what it is the mirror of: `boundary_realism` (Round 51 站3) asks which boundaries the SUITE replaced, and nothing ever asked which production modules were reshaped so a test would pass. taskq-redo's `api/deps.py:181` disables the rate limiter whenever `TaskService.create` is not the object it was at import time — the program the gates scored is not the program that ships. Previous: 2064.  # 2026-09-01: +4 — Round 84 站1, and this raise is LATE: it belongs in `88e15a7d`, which shipped the growth without running the ratchet first (`pre-commit-check` runs the fast preflight only; the ratchet lives in the pytest suite). Recorded here rather than amended away. The growth is a net of three edits to `preflight_spec_alignment`: the elicitation-mode skip goes (-4, and with it the last reader of `resolve_canonical_spec`), the docstring loses its mode paragraph and gains a shorter one (-2), and the report gains a branch (+10) because an empty violation list has two causes that are not the same news — with neither SPEC.md nor SRS.md on disk the old wording, "SRS.md covers canonical_spec", was a claim about two files that were not there. Previous: 2060. # 2026-08-31: +25 — Round 83 站3: two call lines in preflight_artifact_consistency and the comments recording what they are for. `check_ac_deferral_targets` is the read Round 68 站1's ledger row was written for — the deferral names a test function, spec_coverage already knows which declared test functions exist, and nothing joined them (taskq-cc-new deferred 35 criteria to tests, 35 absent at P2, and AC-N10.1 / AC-N7.4 still absent when it left P8 with Gate 4 at 94.43 PASS). `check_ac_verifier_is_nameable` refuses an AC that names THIS framework as the thing that decides it — R-CANONICAL-INTERP-001 used to hand Agent A that sentence, and taskq-cc-new shipped it in 95 of 95 criteria. Both sit beside the existing AC checks because they ask the same artefact the same kind of question; a third hook for them would be a second phase rule to keep in step with this one. Previous: 2035.  # 2026-08-22: +47 — Round 69 站2 and 站5, one entry because they land in the same file on the same day. 站5 adds 5 lines: the `record_ac_deferrals` import and call in preflight_artifact_consistency, plus the three-line comment saying why an `info` finding still has to be written down (non-blocking is not free). 站2: +42 — Net +42 against a deletion: the dead `bvs_phase_order` obligation extractor (10 lines, unreachable since Round 15 §3 wrote it for a member that never carried the `blocking` key its consumer filters on) is gone, and what replaces it is prose. Two comment blocks carry the measurement — that the plan's stated reason for removing that member was wrong (BVSRunner compares `current_phase < PHASE_PREREQUISITES[N+1]`, i.e. `N < N`, so it does NOT fail on every preview; measured on a scratch taskq-cc at current_phase=3, zero violations) and the reason that survived it (an HR-03 skip and FSM FREEZE are the environmental kind the set already excludes). Plus `"blocking": True` on `preflight_previous_phase_artifacts`'s three exits and the docstring recording what it costs: with 02-architecture/SAD.md removed the preview went from 0 obligations to 2, both naming the file. Previous: 1988. # 2026-08-22: +29 — Round 67 站4: `preflight_submodule_pin_ci` and its PREFLIGHT_CHECKS entry. Eight lines are the call; the rest is the docstring carrying the measurement — of the eight projects on this machine, two pin a harness commit whose own Framework Self-Tests were red, and one of those is the commit Round 66 pushed and corrected an hour later. The decision itself is core/quality_gate/submodule_pin.py, which is where the three outcomes (red blocks / green passes / unobtainable is INFRA) are stated. Previous: 1959.  # 2026-08-17: +9 — Round 55 站1: preflight_artifact_consistency runs check_ac_identifiers and check_ac_test_spec_coverage at phase>=3. Both checks have existed since Round 51 and their only consumer was delivery_fingerprint.build_fingerprint, which counts them into a JSON field nothing blocks on — taskq-advance carried 86 acceptance criteria that no TEST_SPEC case cites through eight phases. Two call lines and two imports; the rest is the comment recording why the phase rule is the one already beside it (the citation lives in TEST_SPEC.md, which Phase 2 produces). Previous: 1950. 2026-08-07: +50 — Round 43 站1: the PR 9 auto-fix dispatch + re-verify + attestation refresh moved OUT of preflight_traceability into a sibling method, PhaseHooks.repair_traceability_gap, which cmd_run_phase calls. The block itself is a wash (~55 lines out, ~62 in); the growth is the method's docstring, which has to record that this is the one entry point on the class that writes to the project, and the note left where the block used to sit explaining why the check no longer repairs. preview_next_phase_blocking documents itself as mutating no state and ran preflight_traceability at phase>=5, so every advance from P4 on with an open trace gap dispatched AutoFixEngine against the real tree. Splitting the file was considered and rejected for this change: the repair belongs beside the check it repairs, and the two are read together. 2026-07-26: +167 — Round 14 A1 + B3: PhaseHooks gains preview_next_phase_blocking(next_phase) (~50 lines: Obligation dataclass, _DELAYED_BLOCKING_PREFLIGHTS frozenset, _obligations_from_preflight() helper covering property_spec / reliability_lint / generic fallback, simulation-driver method with stdout suppression), and preflight_property_spec rewires from hardcoded phase>=4 to dynamic max(fulfill_phase) across FRs with extracted SubAssertion.fulfill_phase (~100 lines including P3-skipped path that still carries fulfill_phase) — full carry-over obligation preview + proper back-compat (sized to current 1777). 2026-07-26: +104 — Round 15 §3: _obligations_from_preflight gains 8 per-check extractor branches (drift_detection / sab_check / traceability / fr_spec_consistency / artifact_consistency / config_liveness / previous_phase_artifacts / bvs_phase_order), replacing the generic "would block at phase N" fallback with actionable rule_id/file/line detail; preflight_artifact_consistency gains an additive `error_details` return key so its extractor has something to read (sized to current 1881).
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
    #
    # 2026-09-02: 81 -> 82. Round 87 站5 adds one re-export line,
    # `cmd_review_fr_tests`, for the same reason the other eight are here.
    "cli/check_cmds.py": 82,
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
    "scripts/plangen/phase_tasks.py": 1162,  # 2026-09-01: +1 — Round 84 站3. The P1 precondition becomes [CANONICAL-SPEC] and gains one line: "The location is fixed: `<project-root>/SPEC.md`, not a path declared elsewhere". That clause is the whole point of the round — a path declared inside another file was the one variable statement among six about where the canonical spec lives, and a plan that renames the step without saying where the file goes would leave the next project free to re-invent the declaration. The other five lines of that block were rewritten in place, not added. Previous: 1161. # 2026-08-24: +11 — Round 72 站5: the SAB-GENERATE note renders its field list from `SABSpec` instead of restating it. The hand-typed version said "all 14 fields" and named fourteen; `required_artifacts` (Round 68 站1) had been the fifteenth for two days, and it is the field whose absence the same round finds recorded 186 times in taskq-new's ledger. Three lines are the dataclasses import, the name list and the textwrap call; the rest is the comment. Previous: 1150.
    "core/quality_gate/spec_coverage.py": 943,  # 2026-09-06: +113 — new god file, Round 99 站1/站2. Previous: 830 (unlisted). Crossing the 900 threshold on the day it is granted, so the decision is reviewed here rather than drifted into. Three additions, all of them the file's own subject (what a TEST_SPEC declaration row says, and what the framework does when it cannot tell). 站1 is `_cell_identifier` (+42): the identifier normalisation was one expression whose two steps were order-dependent in a way nothing declared, and a cell written `` `test_x` [AC-1.1] `` — the shape the framework's own Step 1d asks for and its five-column template has no room for — kept a stranded backtick and was dropped. Measured on taskq-done: 109 of 120 rows gone, `declared=11`, and the P1 Naming Authority check reporting 91 of 91 inventory names "missing in TEST_SPEC.md — Agent A may have hallucinated names" about names that were present. About 30 of those lines carry that measurement. 站2 is `unreadable_declarations` (+24) and the block that reads it (+47): the population `_report_unread_rows` has recorded since Round 74 splits in two — a row stating it declares nothing, which MEASUREMENT_SINKS.yaml defends and must stay report-only, and a row declaring a test nobody can read, which until now was scored identically to a row that does not exist. Splitting this file was considered and rejected for this round: every line added is the parse or the verdict over the parse, and a split in the same commit as a behaviour change is the one thing tests/test_god_file_split_safety.py exists to stop. Previous: unlisted (830).
    "core/quality_gate/phase_truth_verifier.py": 908,  # 2026-08-24: new god file — Round 73 站2. +38 over the 870 this file has carried since Round 55, and roughly thirty of them are comment. The code is `_SKIP_ZERO_RE` plus `_demands_zero_skips`, replacing two literal phrases (`skipped count is **0**` / `report **0 skipped**`) that matched two of the eight projects here — and all three fixtures in tests/test_phase_truth_verifier.py wrote the phrase the rule wanted, so rule and fixtures shared a source and the guard stayed green while six projects went unenforced. The comment carries that measurement and the reason the skeleton is narrow rather than proximity-based: `bandit: 0 HIGH / 0 MEDIUM;不得 skip 任何 bandit 規則` is a real NFR-02 sentence in this corpus and a proximity rule would arm the zero-skip reconciliation against NFR-02. `_skip_sites` widens the same way (conftest.py, and `pytest.mark.skip` wherever it is NAMED) because taskq-new's ten skips are injected by `add_marker` from the project-root conftest. Splitting this file is Round 49's 織網-then-cut and a round of its own; the two checks that would move are `check_srs_mandatory_reconciliation` and its module-level helpers, which are read together.
    "core/quality_gate/mutation_enforcer.py": 1566,  # 2026-09-05: +11 — Round 99: `MUTATION_SCORE_PROVENANCE_KEY`'s comment gains the taskq-wow Gate 2 incident — a hand-written `mutation_score.json` passed the presence-only `enforcer_sha` check while using field names neither writer function emits, so `gate_checks._mutation_artifact_violations` now allowlists the two real shapes on top of presence (that file's own ceiling moves in the same commit). 11 lines, all comment. Previous: 1555.  # 2026-08-28: +48 — Round 80 站11: `mutmut_major_version` rewritten. 站2 asked `mutmut --version`, a flag mutmut 2.x DOES NOT HAVE — measured against a real 2.5.1: `Error: No such option '--version'`, exit 2 — so the probe answered None for the only major this module supports and the precondition refused the pinned tool. It now reads the shebang of the console script and asks THAT interpreter for the mutmut distribution version, which is the question rather than a proxy for it (2.x has a `version` subcommand and 3.x does not, so picking a spelling would be sniffing which flag happens to work). Most of the 48 lines are the docstring recording that measurement and the two resolution failures the code now handles (`#!/usr/bin/env python3`, and a file with no shebang at all). Previous: 1507.  # 2026-08-27: +96 — Round 80 站2. Two things this module claimed and did not do. (1) `mutmut_major_version` + the precondition that calls it (~55 lines, most of it the docstring recording that `mutmut --version` reported 3.3.1 on the machine this was written on while `importlib.metadata.version("mutmut")` reported 3.5.0 — different installs, and the framework runs the binary). `requirements.txt` has pinned `mutmut==2.5.1` and this file has read mutmut 2.x's sqlite `Mutant` table since Bug #108, with nothing checking the version; the JS path has asked `npx stryker --version` since it was written (line 848). A 3.x install has no such cache, counts (0, 0), and landed in (2). (2) the `total == 0` branch stops setting `score = 0.0` and returning True — the remaining ~40 lines are the refusal, the Stryker sibling's refusal, and the comment recording why the cache publish/cleanup block moved ABOVE the scoring: the first draft of this returned before it, which left a prior run's score readable at project root, and `test_stale_cache_removed_when_workdir_cache_absent` caught it. Ceiling equals the file's actual length, per Round 78 站3. Previous: 1411.  # 2026-08-24: +21 — Round 72 站2: `MUTATION_SCORE_PROVENANCE_KEY`. One line is the constant; the rest is the comment recording that both writers here have stamped `enforcer_sha` since Round 19 站3 and no reader ever looked, and why the rule is the key's PRESENCE — `enforcer_sha()` returns "unknown" with no git, so a value-shape rule fails real runs. Previous: 1390.  # 2026-08-24: +14 — commit d552fc35: R71-站1 custody-wrap `_compute_mutation_score`'s live mutmut run with `core.tree_custody.custody()`, identical to R53-站1's `run_mutation_precheck` pattern to restore source when subprocess is SIGKILLed. Previous: 1376.
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
    "scripts/workflowgen/spec_phase1.py": 1021,  # 2026-09-02: +7 — Round 86 站3: srsAPrompt finally carries DOC 1. Step 2 has said "It is DOC 1 below, already loaded for you" since f662bf99 and there was no DOC 1 in Agent A's prompt — canonicalSpecContent reached Agent B's srsBDocs and nothing else, so the agent asked to transcribe 100% of the canonical spec was the one never shown it. Two lines build and append the block, four are the comment recording that history, one is the docBlock renderer joining the assembly list. Previous: 1014.  # 2026-09-01: +15 — Round 84 站2. Agent B's DOC 1 stops being a summary of the canonical spec and becomes the canonical spec. B's checklist asks whether A transcribed 100% of it; its three DOCs were PROJECT_BRIEF.md, the draft SRS, and srs_vs_spec_diff.json — and `build_diff_report` emits `label`/`fr_id`/`score` per AC and no canonical text, so the question had no evidence behind it. Prompt PROSE net SHRANK: five precedence lines, the elicitation-mode authoring line, the SPEC.md-absent fallback line and one checklist line are gone (-8), replaced by one line naming project-root SPEC.md (+1); run-all.js measured 383879, 578 bytes SMALLER than before and 878 under its own ceiling, which is why RUNALL_MAX_BYTES does not move. The +12 is comment, and it is comment recording two measurements rather than restating the code: why DOC 1 was wrong (above), and why `expectPrefix` is `'# '` — a weaker anchor than the brief's `'# Project Brief'`, chosen because SPEC.md's H1 belongs to the project (eleven corpus projects, eleven different H1s) and R-NO-PRESCRIPTION-001 forbids dictating it, while the anchor must stay non-empty because `file_loader` reads a falsy prefix as "check nothing" and both the Python side and the JS side (the playbook §8.2 hallucination guard) run it. Three of the fifteen arrived after the first measurement: the DOC-1 rationale was drafted as a JS comment and moved to a Python `#` comment, because a paragraph for whoever edits the generator has no business costing prompt bytes in every generated file. Previous: 999. # 2026-08-22: +3 — one `B.render_preview_next_phase(1)` call site + its `_META_PHASES_1` list entry, the same one-line addition every phase file gets for Fix B (see js_blocks.py's own ratchet entry for the mechanism). Previous: 996. 2026-08-17: +12 — Round 55 站1: _AC_LABEL, interpolated from artifact_consistency.ac_label_shape() into the criteria-identifier instruction. The prompt spelled the label shape itself while _AC_BLOCK matched it literally, and the two disagreed about a qualifier: five of seven projects wrote `**Acceptance criteria (FR-01)**` and the parser attributed none of their criteria. One assignment; the rest is the comment recording the measurement. Previous: 984. 2026-08-14: +20 — off-by-one citation range block at pre-write + Agent B retry with stderr injection. Three defensive layers: (1) `buildBPrompt` SCHEMA REQUIREMENTS tells Agent B to `wc -l <path>` before writing range citations; (2) `cmd_write_approval` rejects unresolvable citations before they land on disk; (3) `runPeerReview`'s persist loop catches the reject, attaches the error to `b2.persist_error`, and re-dispatches Agent B next round with the cited file path + `wc -l` reminder. Fixes 2026-08 production halt on a 791-860 cite for an 859-line file. Previous: 964.
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
    "scripts/workflowgen/js_blocks.py": 2248,  # 2026-09-06: +21 — Round 100 站1 (PHANTOM exit code split). The new `frRc===45` detector in `render_terminal_abort_detectors` returns `{phantom_abort: true, condition_class: 'PHANTOM', ...}` symmetric to the existing 25 branch; inlined at 6 per-FR sites in run-all.js plus their standalone phase3-8 copies. Docstring at lines 700-714 updated from "three classes, three codes" to "four classes, four codes since Round 100 站1". A minor tweak to the comment avoided mentioning `amend-sab` by name (which would have inflated `tests/test_workflowgen.py::TestOrchPostIsPerPhaseNotPerFr`'s amend-sab count). Previous: 2227.
    # 2026-09-04: +38 — Round 95 站3: `render_advance_guard_step` and `advance_session_block_message`. The advance prompt's step-0 GUARD text existed twice — once here for P3/P4/P5/P7 and once hand-typed in spec_phase6.py, where the copy had grown an `OR a harness-v4-* tag exists` clause that a round which tagged and then died left permanently true (taskq-final, five retry rounds reporting "already advanced" without calling advance-phase). Round 93 fixed the copy; this removes it. The two helpers are 2 + 2 lines of body; the other 34 are the docstrings carrying that incident and the measurement that the extraction is byte-neutral — the same f-string reproduces all four existing phases' guard lines exactly, so only P6 moves. Previous: 2189.  # 2026-09-02: +19 — Round 86 站3: `render_doc_block`. Every "(full content — this IS the deliverable under review)" DOC label kept saying that above the relay ceiling, where it is no longer true, and a reviewer told it holds the whole file has no reason to read the rest. 7 of the 19 lines are the docstring and the two-line comment recording why the label is SHORT: the index payload already carries the path and the sed -n form, so repeating them here was 345 bytes of run-all the ceiling refused to accept. Previous: 2170. # 2026-09-02: +72 — Round 86 站2: `render_relay_frame`, plus the frame check inside loadFileViaPython's retry loop and makeDocSummary's one-line short-circuit. The loader moves a file through a sub-agent that cats it and re-emits it as its final message; Bash stdout above ~30KB is replaced by a 2KB preview plus a persisted-file path (measured 27,009 bytes intact, 35,300 and 49,300 replaced), so above that the agent never saw the content — and the two checks on the far side, `length >= 50` and a first-line anchor, both pass on a truncated prefix. Every corpus SRS.md is over that cliff (taskq-new's is 86,338) and omnibot-new's SPEC.md is 341,375. 15 of the 72 lines are the docstring, which carries the measurement and the limit: the END marker makes a truncated relay tellable from a short file, and does NOT authenticate one — an agent that never opened the file could emit a consistent pair of invented shas, which JS cannot detect without crypto or an out-of-band channel. Previous: 2098. # 2026-09-01: +58 — Round 85 站2: `render_fr_step_timeout_exit`, plus the verifier session guard wired into `render_per_fr_delta`. FR_STEP_SCHEMA has described `rc` as "-1 if it never finished" since Round 70 站3 and no code read the -1, so a step the wrapper killed at the poll cap reached verify_gate1_qc.py, found no manifest entry, and was filed as a Gate 1 quality failure. 40 of the 58 lines are the docstring, which carries why the check sits AFTER the manifest read instead of beside its three siblings in `render_terminal_abort_detectors` (a kill can land just after `quality_complete=True` was written, and then the FR really passed). Previous: 2040. # 2026-08-31: +4 — RECORD_BLOCK_FN_BLOCK gains optional `owner` parameter and passes `--owner` to `harness_cli.py record-block` when present (Round 79 站3-2). Previous: 2036.  # 2026-08-31: +49 — `render_deferred_fixes_step(gate_num, phase, d4_threshold)`, extracted from what used to be spec_phase4.py's Gate-3-only `_GATE3_DEFERRED_FIXES_STEP`. Gate 2 and Gate 4's `render_gate_loop()` calls both promised "write deferred_fixes.md" in their on_fail_error_msg without ever wiring a writer — confirmed on a real taskq-verify Gate 2 halt, whose halt payload named the file and left none written. The function body is unchanged content (relocated, gate/phase-parameterized instead of Gate-3-hardcoded); the growth is the function itself plus its docstring, since spec_phase4.py's copy is deleted net of its own remove. Previous: 1987.  # 2026-08-26: +8 — Round 79 站3: the RC=25 INFRA halt's message names the relaunch form, not just the repair. The repair mutates the project tree and nothing else, so the next launch's prompts are byte-identical to this one's — exactly the condition under which the runtime can serve the halt back from cache, and this halt is the only place an operator is holding that problem. Growth is two lines of message at the abort site plus a five-line docstring paragraph saying why; the draft's paragraph was twelve lines and was cut in half before this ceiling moved. Previous: 1979. # 2026-08-23: +34 — Round 70 站3: `render_terminal_abort_detectors` routes on run-fr-step's exit code instead of on regexes over the sub-agent's prose, and gains a third exit (25, INFRA — the one of the three with a repair route). FR_STEP_SCHEMA is the new payload. Most of the growth is the docstring recording why four regex revisions in five days could not have worked: the same generator's GATE1 failure case asks for a sentence containing no bracketed tag, and its R66 clause forbids writing one, while the detector required the banner's literal two lines. Previous: 1945.
    # 2026-08-22: +89 — Round 69 站1. `render_exit_gate_reverify_step(phase)` (56 lines, of which 38 are the docstring recording the measurement: taskq-cc's gate_verify.jsonl carries four gate-4 verdicts at commit 11673af2 across three tree digests, because P6 writes RELEASE_NOTES.md and FINAL_SIGN_OFF.md after the verdict and is told not to re-run the gate). The renderer is derived from EXIT_GATE_MAP and inserted by `render_advance_loop` itself rather than by its three call sites, so it is 5 lines of wiring and no per-phase copies. The rest is the `preview-next-phase-unmeasured` split in `render_preview_next_phase` — a null dispatch reply stops being indistinguishable from "obligations found" — plus its docstring paragraph. Previous: 1856. # 2026-08-22: +76 — new `render_preview_next_phase(phase)` (Fix B): a read-only `preview-next-phase` carry-over-obligation check + bounded 3-round fixer, wired before each of the 8 phases' own Push/Advance step so a `_DELAYED_BLOCKING_PREFLIGHTS` finding surfaces inside the phase's own loop instead of only at its `advance-phase` exit gate (see scripts/workflowgen/artifact_limits.py's RUNALL_MAX_BYTES entry for the full root-cause). One shared function, called once per phase file — the eight call sites are each +1-2 lines in their own files (spec_phase1.py's own entry above), not counted here. Previous: 1780. 2026-08-14: +72 — v33b P2 citation-validator fix (run-all.js halt on taskq-super). Added `render_citation_contract_line()` helper (single source of truth for the citation rule used by buildBPrompt and Phase 6's inline verdicts) and wired Phase 2's `render_generic_ab_loop` with try/catch + `b2.persist_error` capture + `=== PREVIOUS ROUND CITE REJECT ===` prepend (mirrors spec_phase1.py:244-298). 19 lines of helper + ~33 lines of abLoop + comments. Pure bug-fix growth; no new functionality beyond what Phase 1's existing pattern already does. Previous: 1708.
    "detection/drift_detector.py": 985,  # 2026-09-06: +18 — Round 101 站2. `ADVANCE_BLOCKING_SEVERITIES` (+13 with the comment) and the `unregistered` emission moving LOW -> MEDIUM with the five-line note on why. Round 98 gave that finding a reporting branch and a remedy in `_precheck_sab_consistency`, above a filter that keeps MEDIUM and higher, while the only site that emits it emits LOW: measured across the corpus, 21 unregistered findings and zero at MEDIUM+, so the branch had never run once. The constant lives beside the severities so the consumer's threshold and the emission stop being two statements of one decision. Previous: 967.  # 2026-09-05: +967 — new entry, and it crosses the 900-line god-file threshold on the day it is granted, so the decision is reviewed here rather than drifted into. 884 -> 967: Round 98 站1+站2. `_resolve_import_layer` ranks its three match rules by specificity instead of running them in one flat pass, and Check 3 records the delivered source files it had to abstain on. Roughly three quarters of the added lines are docstring and comment carrying the measurement that justifies the shape: every one of the twelve corpus projects registers its bare top-level package as a module of some layer (the framework's own `discover_modules_at` emits it), that entry matched every module in the project under the old flat pass, and 62%-91% of delivered source modules therefore resolved to None and were skipped — 21 of taskq-wow's 23 — while `score = 1 - drifted/checked` counts a skipped module in neither term, so a project whose layering half never ran read 100.0%. This IS their home: both changes are inside the one function that compares real imports to the SAB matrix, and the abstention record exists to keep that comparison's coverage visible from the same result object. A split needs a byte-equal net first (Round 49 織網再動刀). Previous: 0.
    "cli/advance_prechecks.py": 1062,  # 2026-09-06: +83 — Round 101 站3/站4. 979 -> 1062. Two P3-exit wires and one new function. 站3 is `_precheck_sab_placements_are_declared` (+41 with its docstring): `.methodology/SAB.json` is what `scripts/generate_sab.py` renders from SAD.md §5 — `sab_amender`'s own header calls SAD.md authoritative — and `amend_sab` writes the rendered file directly, which is the rule this repository enforces on its own generated workflow JS, inverted. Measured over the seventeen corpus projects: 117 placements exist only in SAB.json and 42 of those are not even derivable from the project's own layer names. It is a separate function, and it is called from `_precheck_sab_consistency` BEFORE the DriftDetector, because 7 of taskq-done's 11 CRITICAL findings are consequences of two of those placements and reporting them first sends the project to fix an import its own SAD.md never forbade. 站4 is +25 at the existing Round 99 manifest block: `env_contract.json`'s declared toolchain and the delivered manifest are both written by this framework and nothing compared them — on taskq-done the first names `pytest_asyncio`, the tests are `pytestmark = pytest.mark.asyncio`, and requirements.txt does not name it. The rest is the two blocks' remedy text and the `UNPLACEABLE_REMEDY` import. Previous: 979.  # 2026-09-06: +38 — Round 99 站2/站4. 941 -> 979. Two changes at the same P3 site. 站4 is the wire for `unfinished_scaffolded_manifest` (+27 with the comment): `scaffold_project_manifest_from_ssot` writes a project's requirements.txt, stamps it "REVIEW AND PIN VERSIONS BEFORE COMMIT" and files a `gate:env-repair` ledger row owned by `harness`, and neither statement had a reader — 8 of the 17 corpus projects carry that row and 5 of them still ship every dependency unpinned. The checking function lives in ssot_manifest.py beside the writer, so what is here is a call, a print and a return. 站2 is +11 net at the spec-coverage block: the message stopped naming a cause it cannot know. `_run_spec_coverage_check` returns 1 from four places and only one is the threshold; this site rendered all four as "spec-coverage 0.0% < threshold 80%" plus "implement missing test cases", which on taskq-done told a run to write tests that existed while 109 declaration rows simply could not be parsed. Previous: 941.  # 2026-09-05: +941 — new entry, and it crosses the 900-line god-file threshold on the day it is granted, so the decision is reviewed here rather than drifted into. 868 -> 941: Round 98 站3. `_precheck_sab_consistency` is extracted from `_precheck_p3_security_and_quality` (which sits at its own function ceiling and is the block that had to grow), and it stops filtering `_item.actual == "not found"` — a predicate only Check 1's missing-file item ever satisfies, so Check 2's `unregistered` and Check 3's `imports X (layer Y)` were discarded 100% of the time under a headline reading "SAB architecture violations". Measured: a taskq-wow tree synthesised to produce 15 CRITICAL architecture violations put 0 of them through that filter; with the resolver fixed the twelve corpus projects produce 147. The growth is the three per-kind remediation branches (three findings, three different fixes) and the docstring recording why 57 of the 147 carry a provenance line — their source layer was chosen by amend-sab's fallback heuristic, so the line to change may be in SAD.md §2 rather than in the code. Previous: 0.
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


# Entries whose newest note predates the `+N … Previous: M` form this table
# settled into. Down only: the next round that touches one of these writes the
# arithmetic and drops it from here. Nothing may be ADDED — a new entry has no
# history to be grandfathered out of.
_UNPARSEABLE_JUSTIFICATION: frozenset[str] = frozenset({
    "core/quality_gate/red_assertion_check.py",
    "core/agent_spawner.py",
    "cli/check_cmds.py",
    "core/quality_gate/phase_truth_verifier.py",
})

# Entries are `<date>: +N — <prose>. Previous: M.` repeated, newest first.
# The newest one runs from its own date up to the next entry's date, and its
# `Previous:` is the LAST one inside that window.
#
# Both halves of that rule were forced by a real misread, and the second by
# this table's own inconsistency:
#
#   * a note may quote an older `Previous:` in its prose — the entry added by
#     this very station does, while explaining what went wrong — so a lazy
#     `.*?Previous:` stops too early.
#   * some entries are separated by `  # <date>:` and others by just
#     `. <date>:` with no `#` (scripts/workflowgen/spec_phase1.py), so
#     splitting on `#` swallows the whole history into one window and the
#     last `Previous:` is then the OLDEST number in the entry.
#
# Anchoring on the dates themselves is the only thing both shapes share.
# Round 80 站7: the delta is signed. Round 78 站3 wrote this for raises,
# which is all this table had ever recorded as its NEWEST note — the one
# decrease in it (`2026-07-29: -28`, Round 25 站3) has always sat behind a
# later raise, so `_newest_note` never had to read one. Round 80 站7 is the
# first entry whose newest note is a harvest, and a guard that cannot
# express the direction the ratchet is FOR would have sent it to
# _UNPARSEABLE_JUSTIFICATION instead.
_ENTRY_START = re.compile(r"\d{4}-\d\d-\d\d:\s*([+-]\d+)\b")
_PREVIOUS = re.compile(r"Previous:\s*(\d+)")


def _newest_note(line: str) -> "tuple[int, int] | None":
    """`(added, previous)` from the newest entry on *line*, or None."""
    starts = list(_ENTRY_START.finditer(line))
    if not starts:
        return None
    window = line[starts[0].end():starts[1].start() if len(starts) > 1 else len(line)]
    previous = _PREVIOUS.findall(window)
    if not previous:
        return None
    return int(starts[0].group(1)), int(previous[-1])


def _ceiling_entries_in_source() -> "list[tuple[str, int, str]]":
    """(path, ceiling, the source line the value sits on) for every entry."""
    import ast

    source = Path(__file__).read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        target = getattr(node, "target", None)
        if isinstance(node, ast.AnnAssign) and getattr(target, "id", "") == "_LINE_CEILING":
            value = node.value
            assert isinstance(value, ast.Dict)
            return [
                (str(k.value), int(v.value), lines[v.lineno - 1])
                for k, v in zip(value.keys, value.values)
                if isinstance(k, ast.Constant) and isinstance(v, ast.Constant)
                and isinstance(v.value, int)
            ]
    raise AssertionError("_LINE_CEILING is no longer an annotated dict literal")


def test_each_ceiling_equals_the_arithmetic_of_its_own_newest_note():
    """Round 78 站3. The number and the story it sits beside must agree.

    Every entry is `N,  # <date>: +D — <why>. Previous: P.` and the whole
    product of this table is `diff-visibility of growth` (module docstring) —
    which the note is, and the integer is not. `da8e70fd` moved two integers
    and left both notes alone:

        cli/phase_cmds.py                    4320   note says 4130 + 70 = 4200
        core/quality_gate/gate1_evidence.py  1300   note is dated 2026-08-18,
                                                    Round 57 站3, Previous: 1128
                                                    — no mention of Plan F at all

    Nothing could see it. `test_production_file_line_ratchet` compares a real
    count against a real limit either way, and Round 70 站4's duplicate-key
    guard is about a different mistake. The measured cost was 101 lines of
    headroom on phase_cmds.py and 29 on gate1_evidence.py: growth this table
    exists to make visible, pre-authorised in advance by a number nobody had
    justified.

    Round 36 / Round 39 / Round 64's shape — a statement that outlived the
    thing it described — in the file the repo edits most.
    """
    wrong = []
    for path, ceiling, line in _ceiling_entries_in_source():
        if path in _UNPARSEABLE_JUSTIFICATION:
            continue
        note = _newest_note(line)
        assert note, (
            f"{path}: the newest note is not in the `+N … Previous: M` form, "
            f"and this path is not in _UNPARSEABLE_JUSTIFICATION. Write the "
            f"arithmetic — it is what makes the raise reviewable."
        )
        added, previous = note
        if previous + added != ceiling:
            wrong.append(
                f"{path}: ceiling {ceiling}, but its own newest note says "
                f"{previous} + {added} = {previous + added}"
            )
    assert not wrong, (
        "a ceiling was moved without its justification moving with it — the "
        "number the next reader reviews is not the number in force:\n  "
        + "\n  ".join(wrong)
    )


def test_no_new_path_may_join_the_grandfathered_list():
    """The exemption is a debt, not a door. Every name in it must be a real
    entry in the table (so a deleted file cannot leave a permanent hole), and
    the list only ever shrinks."""
    keys = {path for path, _, _ in _ceiling_entries_in_source()}
    orphans = sorted(_UNPARSEABLE_JUSTIFICATION - keys)
    assert not orphans, (
        f"_UNPARSEABLE_JUSTIFICATION names {orphans}, which _LINE_CEILING no "
        f"longer lists — drop them from the exemption in the same commit")
    assert len(_UNPARSEABLE_JUSTIFICATION) <= 4, (
        f"{len(_UNPARSEABLE_JUSTIFICATION)} grandfathered entries > ceiling 4. "
        f"These predate the `Previous:` convention; a raise touching one of "
        f"them writes the arithmetic and removes it from the list.")


def test_the_arithmetic_check_would_have_caught_the_two_it_was_written_for():
    """Negative space. The parser has to reject the exact shapes measured on
    `da8e70fd`, not just accept the well-formed ones."""
    moved_value = ('    "cli/phase_cmds.py": 4320,  # 2026-08-25: +70 — Plan E '
                   '… raising the ceiling 4130 → 4200. Previous: 4130.')
    assert _newest_note(moved_value) == (70, 4130)  # 4200, not 4320

    untouched_note = ('    "core/quality_gate/gate1_evidence.py": 1300,  '
                      '# 2026-08-18: +58 — Round 57 站3: … Previous: 1128.')
    assert _newest_note(untouched_note) == (58, 1128)  # 1186, not 1300

    good = '    "harness/harness_bridge.py": 5051,  # 2026-08-26: +90 — … Previous: 4961.'
    assert _newest_note(good) == (90, 4961)  # 5051

    # Only the NEWEST entry is read, and prose may quote an older one.
    two_entries = ('    "x.py": 30,  # 2026-08-26: +10 — the note before this '
                   'said "Previous: 5". Previous: 20.  # 2026-08-01: +15 — … '
                   'Previous: 5.')
    assert _newest_note(two_entries) == (10, 20)


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
