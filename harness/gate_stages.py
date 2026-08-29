"""The sixteen stages `finalize_gate` runs, as a mixin.

Round 82 站6. Round 81 站8 extracted these out of `HarnessBridge.finalize_gate`
(1150 -> 899 lines) and left them on the class. Here they leave, and the sixteen
`self._stage_*(...)` call sites do not change.

WHY A MIXIN AND NOT MODULE-LEVEL FUNCTIONS

Because a method's body sits at two indent levels under any class, and under
`class _FinalizeStages:` it sits at exactly the same two — so the move needs no
reindentation and every body is byte-identical to what it replaced. Dedenting
these to module-level functions would rewrite 386 lines, and a rewrite needs a
behavioural golden this round does not have and will not fake. That trade is
deliberate: byte-identity over class-design purity. `_FinalizeStages` is a
namespace, not a type — nothing constructs it, nothing checks `isinstance`
against it, and `HarnessBridge` is its only subclass.

Measured before the move: `HarnessBridge.__bases__` was `(object,)`, its
metaclass is `type`, it defines no `__init_subclass__`, and nothing in the
repository reads `__mro__`, `__bases__` or `__qualname__`. The one observable
difference is that `_stage_x.__qualname__` now says `_FinalizeStages._stage_x`,
which is what lets tests/test_god_file_split_safety.py find the body here while
still resolving the class through harness_bridge.

This module imports harness/gate_result.py and never harness/harness_bridge.py.
That is the whole reason 站5 exists: a mixin base is imported before the class
body executes, so the reverse direction would be a cycle that resolves only by
where the definitions happen to sit in the file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from harness.decision_log import DecisionContext, DecisionLogEntry
from harness.effort_tracker import EffortRecord
from harness.gate_io import _atomic_write_gate_result
from harness.gate_result import (
    SCORE_SOURCE_STUBBED_BOUNDARY,
    DimResult,
    GateBlockedError,
    GateResult,
    declared_dimensions,
    measurement_scope,
    s4_block_details,
)

if TYPE_CHECKING:  # the host's own types, needed only by the declarations below
    from harness.decision_log import DecisionLogWriter
    from harness.effort_tracker import EffortTracker
    from harness.harness_bridge import GateContext


class _FinalizeStages:
    """Mixin: one method per stage of `HarnessBridge.finalize_gate`."""

    if TYPE_CHECKING:
        # What this mixin needs from whatever class it is mixed into. Fifteen
        # of the sixteen stages are staticmethods and need nothing;
        # `_stage_record_verdict` reaches for four things `HarnessBridge`
        # owns. Declared here rather than silenced at the four call sites,
        # because a `# type: ignore` on those lines would edit bodies this
        # round's whole safety argument says are byte-identical — and because
        # the requirement was previously implicit and is now written down.
        _log: "DecisionLogWriter"
        _effort: "EffortTracker"

        def _update_quality_manifest(
            self, gate_num: int, fr_id: "str | None", result: GateResult,
            project_root: "str | None" = None,
        ) -> None: ...

        def _trigger_hooks(self, ctx: "GateContext", event_name: str) -> None: ...

    @staticmethod
    def _stage_shape_contract(_shape, ctx) -> None:
        """Shape contract — extracted verbatim from `finalize_gate`.

        Round 81 站8. See the note above the first `_stage_*` for what
        makes this a move rather than a rewrite.
        """
        if not _shape.valid:
            _shape_msg = _shape.as_block_message(ctx.gate_num)
            print(f"\n[BLOCKED] {_shape_msg}", file=sys.stderr)
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num, score=0.0, dimensions=[],
                    open_critical=1, open_high=0,
                    quality_complete=False, rounds_used=0,
                ),
                details={"malformed_gate_result": list(_shape.violations)},
            )


    @staticmethod
    def _stage_infra_fail_pollution(_infra_violations, ctx) -> None:
        """Infra fail pollution — extracted verbatim from `finalize_gate`.

        Round 81 站8. See the note above the first `_stage_*` for what
        makes this a move rather than a rewrite.
        """
        if _infra_violations:
            print("\n[INFRA_FAIL] gate result rejected — infrastructure failure "
                  "recorded as quality zero(s); NOT a code-quality verdict:")
            for _v in _infra_violations:
                print(f"  - {_v}")
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num,
                    score=0.0,
                    dimensions=[],
                    open_critical=len(_infra_violations),
                    open_high=0,
                    quality_complete=False,
                    rounds_used=0,
                ),
                details={"infra_fail": _infra_violations},
            )


    @staticmethod
    def _stage_persist_cited_evidence(ctx, persist_cited_evidence, raw, result_path) -> None:
        """Persist cited evidence — extracted verbatim from `finalize_gate`.

        Round 81 站8. See the note above the first `_stage_*` for what
        makes this a move rather than a rewrite.
        """
        if persist_cited_evidence(Path(ctx.project_root), ctx.gate_num, raw):
            # The re-pointing has to reach the file, not just this dict: the
            # digest block below re-reads `result_path` from disk, and
            # cli/gate_cmds.py copies that same file to
            # .methodology/gate{N}_result.json. Written only when something
            # actually moved, so a run that changes nothing leaves the agent's
            # bytes alone.
            _atomic_write_gate_result(result_path, raw)


    @staticmethod
    def _stage_tool_evidence(_evidence_digests, _tool_violations, ctx, raw, result_path) -> None:
        """Tool evidence — extracted verbatim from `finalize_gate`.

        Round 81 站8. See the note above the first `_stage_*` for what
        makes this a move rather than a rewrite.
        """
        if _evidence_digests:
            # Persist immediately, in the same shape the CRG enrichment below
            # already uses (read → add field → atomic write): the digest is only
            # true of the files as they are RIGHT NOW, and a later BLOCK on some
            # other check must not throw away the record of what this run read.
            try:
                _gr = json.loads(result_path.read_text(encoding="utf-8"))
                if isinstance(_gr, dict):
                    _gr["evidence_digest"] = _evidence_digests
                    _atomic_write_gate_result(result_path, _gr)
                    raw["evidence_digest"] = _evidence_digests
            except (OSError, json.JSONDecodeError) as _exc:
                print(f"[WARN] could not record evidence digests: {_exc}",
                      file=sys.stderr)
        if _tool_violations:
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num,
                    score=0.0,
                    dimensions=[],
                    open_critical=len(_tool_violations),
                    open_high=0,
                    quality_complete=False,
                    rounds_used=0,
                ),
                details={"tool_evidence_missing": _tool_violations},
            )


    @staticmethod
    def _stage_declared_constraints(_constraint_reason, ctx) -> None:
        """Declared constraints — extracted verbatim from `finalize_gate`.

        Round 81 站8. See the note above the first `_stage_*` for what
        makes this a move rather than a rewrite.
        """
        if _constraint_reason:
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num, score=0.0, dimensions=[],
                    open_critical=1, open_high=0,
                    quality_complete=False, rounds_used=0,
                ),
                details={"arch_constraint_unconfigured": [_constraint_reason]},
            )


    @staticmethod
    def _stage_coverage_denominator(_coverage_reason, ctx) -> None:
        """Coverage denominator — extracted verbatim from `finalize_gate`.

        Round 81 站8. See the note above the first `_stage_*` for what
        makes this a move rather than a rewrite.
        """
        if _coverage_reason:
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num, score=0.0, dimensions=[],
                    open_critical=1, open_high=0,
                    quality_complete=False, rounds_used=0,
                ),
                details={"arch_contract_coverage": [_coverage_reason]},
            )


    @staticmethod
    def _stage_required_artifacts(_artifact_reason, ctx) -> None:
        """Required artifacts — extracted verbatim from `finalize_gate`.

        Round 81 站8. See the note above the first `_stage_*` for what
        makes this a move rather than a rewrite.
        """
        if _artifact_reason:
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num, score=0.0, dimensions=[],
                    open_critical=1, open_high=0,
                    quality_complete=False, rounds_used=0,
                ),
                details={"required_artifact_missing": [_artifact_reason]},
            )


    @staticmethod
    def _stage_verify_target(_vt_reason, ctx) -> None:
        """Verify target — extracted verbatim from `finalize_gate`.

        Round 81 站8. See the note above the first `_stage_*` for what
        makes this a move rather than a rewrite.
        """
        if _vt_reason:
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num, score=0.0, dimensions=[],
                    open_critical=1, open_high=0,
                    quality_complete=False, rounds_used=0,
                ),
                details={"verify_target": [_vt_reason]},
            )

        # ── S4: Harness cross-validation (Solution B) ────────────────────────
        # For each Tier 1/2 dimension where the agent claims a passing score,
        # the harness independently runs the tool and computes its own score.
        # If harness_score < threshold but agent_score ≥ threshold, the gate is
        # blocked with a fabrication violation.
        # Slow tools (mutmut, scancode) are skipped here; S3-A covers them.
        print("\n[S4] Running harness cross-validation...")


    @staticmethod
    def _stage_s4_cross_validation(_s4_fabrication, _s4_unverifiable, ctx) -> None:
        """S4 cross validation — extracted verbatim from `finalize_gate`.

        Round 81 站8. See the note above the first `_stage_*` for what
        makes this a move rather than a rewrite.
        """
        if _s4_fabrication or _s4_unverifiable:
            _s4_details = s4_block_details(_s4_fabrication, _s4_unverifiable)
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num,
                    score=0.0,
                    dimensions=[],
                    open_critical=len(_s4_fabrication) + len(_s4_unverifiable),
                    open_high=0,
                    quality_complete=False,
                    rounds_used=0,
                ),
                details=_s4_details,
            )


    @staticmethod
    def _stage_system_reach(_reach, ctx) -> None:
        """System reach — extracted verbatim from `finalize_gate`.

        Round 81 站8. See the note above the first `_stage_*` for what
        makes this a move rather than a rewrite.
        """
        if _reach:
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num, score=0.0, dimensions=[],
                    open_critical=len(_reach), open_high=0,
                    quality_complete=False, rounds_used=0,
                ),
                details={"stubbed_boundary_never_run": _reach},
            )


    @staticmethod
    def _stage_spec_coverage_cap(_spec_cap, _spec_names, dims, raw) -> None:
        """Spec coverage cap — extracted verbatim from `finalize_gate`.

        Round 81 站8. See the note above the first `_stage_*` for what
        makes this a move rather than a rewrite.
        """
        for dim_name, dim_data in raw.get("breakdown", {}).items():
            # `dict.get(k, default)` only substitutes when the key is absent;
            # an explicit JSON `null` still surfaces as None. DimResult.score is
            # Optional[float] and every downstream reader in this function already
            # guards `is not None` — preserve None here instead of coercing to 0.0,
            # which used to make a legitimately-inapplicable dimension (e.g.
            # pytest-benchmark with no benchmarks) look like a real 0-score failure.
            raw_score = dim_data.get("score")
            score = float(raw_score) if raw_score is not None else None
            raw_thresh = dim_data.get("threshold")
            threshold = float(raw_thresh) if raw_thresh is not None else 0.0
            if dim_name == "test_coverage" and _spec_names and score is not None:
                score = min(score, _spec_cap)
            dims.append(DimResult(
                name=dim_name,
                score=score,
                threshold=threshold,
                issues=dim_data.get("issues", []),
                # Written by S4 above (_mark_framework_na, the framework-score
                # write-back, the unverifiable branch). Reading it here is what
                # carries the provenance into the verdict and its denominator
                # instead of leaving it in the breakdown dict nobody consults.
                score_source=dim_data.get("score_source"),
            ))


    @staticmethod
    def _stage_absent_dimensions(_absent, _overall_score, ctx, dims) -> None:
        """Absent dimensions — extracted verbatim from `finalize_gate`.

        Round 81 站8. See the note above the first `_stage_*` for what
        makes this a move rather than a rewrite.
        """
        if _absent:
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num, score=_overall_score, dimensions=dims,
                    open_critical=0, open_high=0, quality_complete=False,
                ),
                details={"dimension_absent": [
                    f"{_name}: declared in the gate config, absent from the "
                    f"result — no score, no N/A, no entry"
                    for _name in _absent
                ]},
            )


    @staticmethod
    def _stage_stubbed_boundaries(_boundary_findings, _overall_score, _unmeasured, ctx, dims) -> None:
        """Stubbed boundaries — extracted verbatim from `finalize_gate`.

        Round 81 站8. See the note above the first `_stage_*` for what
        makes this a move rather than a rewrite.
        """
        if _unmeasured:
            _stub_by_module = {
                f["module"]: f for f in reversed(_boundary_findings or [])
            }
            _unmeasured_detail: list[str] = []
            for _name in _unmeasured:
                _src = next((d.score_source for d in dims if d.name == _name), None)
                if _src == SCORE_SOURCE_STUBBED_BOUNDARY and _stub_by_module:
                    _who = "; ".join(
                        f"{f['fixture']} in {f['file']} replaces {f['module']}"
                        for f in sorted(
                            _stub_by_module.values(),
                            key=lambda f: (f["file"], f["fixture"]),
                        )
                    )
                    _unmeasured_detail.append(
                        f"{_name}: measured over a suite that replaces a "
                        f"declared high-risk boundary ({_who}) — the score is "
                        f"not a measurement of the delivered code. Remove the "
                        f"autouse replacement, or stop declaring the module "
                        f"high-risk in the SAB if it is not"
                    )
                else:
                    _unmeasured_detail.append(
                        f"{_name}: carries a score the framework ran the tool "
                        f"for and could not reproduce (score_source={_src}). "
                        f"The number is the agent's claim standing alone"
                    )
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num, score=_overall_score, dimensions=dims,
                    open_critical=0, open_high=0, quality_complete=False,
                ),
                details={"dimension_not_measured": _unmeasured_detail},
            )


    @staticmethod
    def _stage_dimension_thresholds(_all_dims_pass, _dim_passes, _dim_weights, _effective_threshold, ctx, dims) -> None:
        """Dimension thresholds — extracted verbatim from `finalize_gate`.

        Round 81 站8. See the note above the first `_stage_*` for what
        makes this a move rather than a rewrite.
        """
        if not _all_dims_pass and dims:
            _failing = [f"{d.name}={d.score:.1f}" for d in dims
                        if d.score is not None and d.score < _effective_threshold(d)]
            _unverified_na = [d.name for d in dims if not _dim_passes(d) and d.score is None]
            if _unverified_na:
                _failing += [f"{n}=N/A (unverified)" for n in _unverified_na]
            print(f"\n[harness] {len(_failing)} dimension(s) below individual threshold: {', '.join(_failing)}")

        # Round 42 站4b: what the composite was averaged over, computed where
        # the weights already are. Stashed on the context rather than
        # recomputed at the write site, for the reason `spec_coverage_report`
        # takes its rows as an argument: a second derivation of the
        # denominator is a second denominator.
        ctx.measurement_scope = measurement_scope(  # type: ignore[attr-defined]
            dims, _dim_weights,
            declared=declared_dimensions(ctx.project_root),
        )


    @staticmethod
    def _stage_declared_absent(_declared_absent, ctx, raw) -> None:
        """Declared absent — extracted verbatim from `finalize_gate`.

        Round 81 站8. See the note above the first `_stage_*` for what
        makes this a move rather than a rewrite.
        """
        if _declared_absent:
            from core.degradation_ledger import record_degradation
            record_degradation(
                ctx.project_root, "gate:dimension-declared-absent",
                f"{len(_declared_absent)} dimension(s) the quality manifest "
                f"pins an NFR to are not in gate {ctx.gate_num}'s config",
                why=("the composite is an average over the gate's dimensions, "
                     "and these were never among them: "
                     + ", ".join(_declared_absent)),
                data={"dimensions": _declared_absent, "gate": ctx.gate_num},
                owner="harness",
            )

        # Round 67 站1: the corrected result, for whoever persists it. Every
        # write into `raw` above — S4's framework score, `_mark_framework_na`,
        # `_mark_stubbed_boundary_dimensions` — lived only here, because the
        # persist step in `cli/gate_cmds.py` re-read the agent's file from disk
        # and copied a fixed list of fields back onto it. Measured on
        # taskq-cc's committed gate4_result.json: sixteen dimensions, zero
        # `score_source`, beside a `measurement_scope` naming two of them as
        # unscored. Stashed rather than re-derived, same rule as the line
        # above: a second derivation is a second source.
        ctx.finalized_result = raw  # type: ignore[attr-defined]


    def _stage_record_verdict(self, _gate_passes, ctx, result, t0, time) -> "GateResult | None":
        """Record verdict — extracted verbatim from `finalize_gate`.

        Round 81 站8. See the note above the first `_stage_*` for what
        makes this a move rather than a rewrite.
        """
        self._update_quality_manifest(ctx.gate_num, ctx.fr_id, result, project_root=ctx.project_root)

        self._effort.record(EffortRecord(
            phase=ctx.phase, gate_num=ctx.gate_num, agent_id="GATE",
            operation="gate_finalize", duration_s=time.time() - t0,
        ))
        self._log.write(DecisionLogEntry(
            ctx=DecisionContext(agent_id="GATE", phase=ctx.phase, fr_id=ctx.fr_id),
            decision="GATE_PASS" if result.quality_complete else "GATE_BLOCK",
            reasoning=(
                f"Gate {ctx.gate_num}: score={result.score:.1f}, "
                f"critical={result.open_critical}, high={result.open_high}"
            ),
            scores={"gate_score": result.score},
        ))

        if not _gate_passes:
            self._trigger_hooks(ctx, "on_gate_fail")
            raise GateBlockedError(ctx.gate_num, result)

        self._trigger_hooks(ctx, "after_gate_pass")

        return result
