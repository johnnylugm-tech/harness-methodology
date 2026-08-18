"""Which question a gate asks is declared by the gate, not by a phase number.

Round 57 站0. "Is this coverage number about one FR or about the whole
project?" had three answers on this tree, and they disagreed:

    harness_bridge.py:1975  S4                 `ctx.fr_id` truthy  -> any phase
    gate1_evidence.py:960   validate_fr_...    `fr_id and _is_phase3_per_fr`
    cli/phase_cmds.py:3481  _check_gate1_...   `completed_phase == 3`

So a Phase 7 tree with whole-project coverage 62% and an agent claiming 92:
S4 replaced the harness number with the FR's own 100.0 and let it through,
while `_check_gate1_live_coverage` judged the same run on 62% and blocked.
Two enforcers, one question, two answers.

The gate configs already declare the answer and nothing reads it::

    gate1_per_fr.yaml    scope: single_fr
    gate2_p3_exit.yaml   scope: full_phase
    gate3_p4_exit.yaml   scope: full_phase
    gate4_p6_full.yaml   scope: full_project

Gate 1 is `single_fr` at *every* phase it runs (P3/P4/P5/P7/P8/P9), so reading
the declaration removes all three phase conditions rather than adding a fourth.

Station 0 also measured a second thing the review did not name: the
`integration_coverage` half of the S4 condition can never fire through the
sanctioned path (it is not a Gate 1 dimension, and no workflow JS passes
`--fr-id` to gates 2/3/4 — grep, zero hits), and if it ever did it would
answer an integration-coverage question with the unit suite's `.coverage`.
It is removed rather than guarded.
"""

from __future__ import annotations

import pytest


class TestGateScopeIsReadable:
    """`scope:` in the gate YAML becomes something a judgement can consult."""

    def test_each_gate_reports_the_scope_its_yaml_declares(self):
        from core.quality_gate.gate_thresholds import gate_scope

        assert gate_scope(1) == "single_fr"
        assert gate_scope(2) == "full_phase"
        assert gate_scope(3) == "full_phase"
        assert gate_scope(4) == "full_project"

    def test_only_gate_one_is_a_per_fr_gate(self):
        from core.quality_gate.gate_thresholds import is_per_fr_gate

        assert is_per_fr_gate(1) is True
        assert [is_per_fr_gate(g) for g in (2, 3, 4)] == [False, False, False]

    def test_an_unknown_gate_is_a_caller_bug_not_a_silent_false(self):
        """Same contract as `gate_config_path` — Round 30 站3."""
        from core.quality_gate.gate_thresholds import gate_scope, is_per_fr_gate

        with pytest.raises(ValueError):
            gate_scope(9)
        with pytest.raises(ValueError):
            is_per_fr_gate(9)


class TestS4RescopesOnTheGatesDeclaration:
    """The predicate S4 consults, as a public pure function.

    Tested here rather than by patching five private seams around
    `finalize_gate` — `tests/test_patch_discipline.py` refuses that, and
    Round 54 站3 answered the same question the same way with
    `s4_score_verdict`.
    """

    def test_gate_one_with_an_fr_rescopes_test_coverage(self):
        from harness.harness_bridge import s4_rescopes_to_fr

        assert s4_rescopes_to_fr(
            gate_num=1, fr_id="FR-01", dim_name="test_coverage"
        ) is True

    def test_the_phase_is_not_part_of_the_question(self):
        """P7's Gate 1 is `single_fr` exactly as P3's is.

        This is the review's finding: S4 had no phase guard and
        `_check_gate1_live_coverage` had one, so P7 got two answers. The fix
        is that neither has one.
        """
        from harness.harness_bridge import s4_rescopes_to_fr

        assert s4_rescopes_to_fr(
            gate_num=1, fr_id="FR-01", dim_name="test_coverage"
        ) is True

    def test_a_full_phase_gate_never_rescopes_even_when_handed_an_fr(self):
        """`finalize-gate --gate 2 --fr-id FR-01` is reachable by hand."""
        from harness.harness_bridge import s4_rescopes_to_fr

        assert s4_rescopes_to_fr(
            gate_num=2, fr_id="FR-01", dim_name="test_coverage"
        ) is False

    def test_integration_coverage_is_never_rescoped_from_the_unit_coverage_file(self):
        """The mine the review did not name.

        `integration_coverage` is scored by `pytest-cov-integration` and lives
        only in gates 2/3/4. `fr_coverage_from_last_run` reads `.coverage` —
        the unit suite's data file. Re-scoring the integration dimension from
        it would answer a different question and call the answer the same
        thing.
        """
        from harness.harness_bridge import s4_rescopes_to_fr

        assert s4_rescopes_to_fr(
            gate_num=1, fr_id="FR-01", dim_name="integration_coverage"
        ) is False
        assert s4_rescopes_to_fr(
            gate_num=2, fr_id="FR-01", dim_name="integration_coverage"
        ) is False

    def test_no_fr_id_means_no_rescope(self):
        from harness.harness_bridge import s4_rescopes_to_fr

        assert s4_rescopes_to_fr(
            gate_num=1, fr_id=None, dim_name="test_coverage"
        ) is False


class TestThePerFrNumberCarriesItsEvidence:
    """Round 45: a verdict may not outlive its proof — nor contradict it.

    Before this round S4 wrote the per-FR number into `score` and left
    `tool_output` pointing at the whole-project pytest-cov audit file. A
    reader of `gate1_result.json` saw `test_coverage: 100.0` cited to a file
    whose last line reads `TOTAL ... 62%`. The scope switch existed only as a
    line on stdout.
    """

    def test_the_evidence_names_the_fr_its_modules_and_both_sides_of_the_ratio(self):
        from core.quality_gate.gate1_evidence import FrCoverage
        from harness.harness_bridge import per_fr_coverage_evidence

        record = FrCoverage(
            percent=100.0, executed=37, coverable=37,
            files=["03-development/src/pkg/api.py"],
        )
        body = per_fr_coverage_evidence(
            fr_id="FR-01", dim_name="test_coverage", tool="pytest-cov",
            record=record, whole_project_score=62.0,
            whole_project_audit="test_coverage_harness.txt",
        )

        assert "FR-01" in body
        assert "03-development/src/pkg/api.py" in body
        assert "37" in body, "the denominator travels with the number (R42 站4)"
        assert "100.0" in body
        assert "62.0" in body, (
            "the whole-project number it replaced has to stay readable, or "
            "the next reader cannot tell what changed"
        )
        assert "test_coverage_harness.txt" in body, (
            "the audit file the number was NOT taken from must still be "
            "dereferenceable from the one it was"
        )

    def test_a_record_with_no_measured_lines_is_not_a_percentage(self):
        """Round 32 站4 — could-not-measure is not measured-and-failed."""
        from core.quality_gate.gate1_evidence import FrCoverage

        empty = FrCoverage(percent=None, executed=0, coverable=0, files=[])
        assert empty.measured is False

        real = FrCoverage(percent=0.0, executed=0, coverable=12, files=["a.py"])
        assert real.measured is True, (
            "0% over twelve measurable statements is a measurement; it is the "
            "worst one, not the absent one"
        )


class TestTheWiring:
    """The renderer above is pure; this drives the branch that calls it.

    Two of the review's five findings were "a change that alters a gate
    verdict shipped with no test". A pure-function test proves the decision
    and proves nothing about whether the decision is reached.
    """

    @pytest.fixture
    def gate1_project(self, tmp_path):
        import json

        import coverage

        src = tmp_path / "03-development" / "src" / "pkg"
        src.mkdir(parents=True)
        (tmp_path / "03-development" / "tests").mkdir(parents=True)
        (src / "__init__.py").write_text("", encoding="utf-8")
        (src / "api.py").write_text(
            "def handle(x):\n"
            "    y = x + 1\n"
            "    return y\n",
            encoding="utf-8",
        )
        # A second module no FR claims, uncovered — this is what drags the
        # whole-project number down and what the per-FR scope must exclude.
        (src / "unclaimed.py").write_text(
            "def never_called():\n"
            "    return 1\n",
            encoding="utf-8",
        )
        meth = tmp_path / ".methodology"
        meth.mkdir()
        (meth / "quality_manifest.json").write_text(
            json.dumps({
                "fr_ids": ["FR-01"],
                "quality_targets": {"min_coverage": 80.0},
                "fr_module_traceability": {"FR-01": "pkg.api"},
            }),
            encoding="utf-8",
        )
        data = coverage.CoverageData(basename=str(tmp_path / ".coverage"))
        data.add_lines({
            str(src / "api.py"): [1, 2, 3],
            str(src / "unclaimed.py"): [1],
        })
        data.write()
        return tmp_path

    def _run(self, project, *, gate_num, fr_id, agent_score, harness_pct):
        from unittest import mock

        from harness.harness_bridge import GateContext, _run_harness_cross_validation

        ctx = GateContext(
            gate_num=gate_num, config={}, project_root=str(project),
            phase=7, fr_id=fr_id, ssi_scripts_dir="", ssi_prompts_dir="",
            ssi_schemas_dir="", work_dir=str(project / ".sessi-work"),
            sab_data={},
        )
        raw = {"breakdown": {"test_coverage": {"score": agent_score}}}
        with mock.patch("harness.tool_runners.run_tool",
                        return_value=("TOTAL   4   2   50%", 0)), \
                mock.patch("harness.tool_runners.compute_tool_score",
                           return_value=harness_pct):
            _run_harness_cross_validation(ctx, raw)
        return raw["breakdown"]["test_coverage"]

    def test_the_recorded_score_and_its_citation_are_the_same_measurement(
        self, gate1_project,
    ):
        """Gate 1 at Phase 7: the FR's own module is 100%, the tree is 50%."""
        entry = self._run(
            gate1_project, gate_num=1, fr_id="FR-01",
            agent_score=100.0, harness_pct=50.0,
        )

        assert entry["score"] == 100.0
        assert entry["coverage_scope"] == "per_fr"
        assert entry["coverage_scope_fr"] == "FR-01"

        cited = gate1_project / entry["tool_output"]
        assert cited.is_file(), "the verdict must cite a file that exists"
        body = cited.read_text(encoding="utf-8")
        assert "FR-01" in body
        assert "pkg/api.py" in body.replace("\\", "/")
        assert "3/3" in body, "executed/coverable, not just the quotient"
        assert "100.0%" in body and "50.0%" in body, (
            "the number recorded and the number it replaced both stay readable"
        )
        assert "unclaimed" not in body, (
            "a module no FR claims is not this FR's denominator"
        )

    def test_a_full_phase_gate_keeps_the_whole_project_number_and_audit(
        self, gate1_project,
    ):
        """The negative control: gate 2 handed an fr_id changes nothing."""
        entry = self._run(
            gate1_project, gate_num=2, fr_id="FR-01",
            agent_score=100.0, harness_pct=50.0,
        )

        assert entry["score"] == 50.0, "the framework's whole-project number"
        assert "coverage_scope" not in entry
        assert entry["tool_output"].endswith("test_coverage_harness.txt")
