"""DA score-threshold waiver adjudication (Round 21).

The defect these pin is not a typo — it is an ordering mistake. Until Round 21
the "is this waiver needed?" question was answered in cli/gate_cmds.py at
gate-prerequisite time, which is before finalize_gate runs the framework's own
independent CRG pass. The only waivable dimension is CRG-only, so at that point
its score is JSON null: the check compared a null against a threshold key that
never existed (`tool_score`, later `target`), and the safeguard was dead in both
directions.

taskq's P6 is the case that surfaced it. Its gate4_result.json requested
`da_waiver: {"architecture": true}` on the stated premise that CRG scores the
hub-and-spoke layout 0. The framework's own crg_baseline_p6.json for the same
tree records **100.0** — the premise was false, the waiver was unnecessary, and
`da_waiver_needs_human_review` was raised over nothing.
"""

import json
from pathlib import Path

import pytest

from core.quality_gate.da_waiver import CRG_ONLY_DIMENSIONS, WAIVABLE_DIMENSIONS

_FIXTURE = Path(__file__).parent / "fixtures" / "gate_results" / "taskq_gate4.json"


class TestNoThresholdCanBeWaived:
    """Round 38 站3 — a waiver request refuses, it never zeroes a threshold.

    Round 21 fixed *when* the necessity of a waiver is adjudicated. Round 38
    removes the question: taskq-renew's Gate 4 granted
    ``da_waiver: {"architecture": true}`` on evidence naming communities
    (``storage-load-sub1`` / ``sub2``) that exist only in the truncated
    11-of-47-file graph Round 37 diagnosed. The correct 47-file graph has no
    such communities. The waiver's premise was produced by the measurement
    defect it was granted to excuse.

    Worse, the waiver only ever reached one of the three enforcers.
    ``cmd_crg_arch_check`` — which CI runs on every push from phase 3, and
    which the workflow JS ANDs into ``gateNPass`` — has no waiver logic at
    all. So the framework's own prescribed remedy could not satisfy the
    framework's own check: finalize-gate passed, ``crg_rc`` stayed 1, and the
    gate loop burned its three rounds and errored.

    The remedy that does work is calibration — ``crg_excludes`` and
    ``crg_cohesion_healthy`` in ``.methodology/harness_config.json``. It is
    committed, so every enforcer reads it. That is the whole difference: a
    waiver is visible to one judge, a calibration to all of them.
    """

    def test_no_dimension_may_have_its_threshold_waived(self):
        assert WAIVABLE_DIMENSIONS == frozenset()

    def test_crg_only_dimensions_survives_as_its_own_concept(self):
        """The CRG-override path in harness_bridge still needs to know which
        dimensions the framework scores rather than the agent. That set did
        not become empty — only the waivable one did."""
        assert "architecture" in CRG_ONLY_DIMENSIONS

    def _project_with_waiver(self, tmp_path: Path, gate: int) -> Path:
        (tmp_path / ".sessi-work").mkdir()
        (tmp_path / ".sessi-work" / f"gate{gate}_result.json").write_text(
            json.dumps({
                "devil_advocate": {"architecture": True},
                "da_waiver": {"architecture": True},
                "devil_advocate_evidence": {
                    "architecture": {
                        "challenge": "x" * 200,
                        "response": "y" * 200,
                    }
                },
            }),
            encoding="utf-8",
        )
        return tmp_path

    def test_a_waiver_request_blocks_instead_of_zeroing_the_threshold(
        self, tmp_path: Path
    ):
        from cli.gate_cmds import _collect_da_waivers

        project = self._project_with_waiver(tmp_path, 3)
        assert _collect_da_waivers(project, 3) is True

    def test_the_refusal_names_the_calibration_path(self, tmp_path: Path, capsys):
        """A block whose message does not say what to do instead is the
        Round 24 defect verbatim."""
        from cli.gate_cmds import _collect_da_waivers

        project = self._project_with_waiver(tmp_path, 4)
        _collect_da_waivers(project, 4)
        err = capsys.readouterr().err
        assert "crg_excludes" in err
        assert "crg_cohesion_healthy" in err

    def test_a_gate_result_without_waivers_is_not_blocked(self, tmp_path: Path):
        from cli.gate_cmds import _collect_da_waivers

        (tmp_path / ".sessi-work").mkdir()
        (tmp_path / ".sessi-work" / "gate4_result.json").write_text(
            json.dumps({"devil_advocate": {"architecture": True}}), encoding="utf-8")
        assert _collect_da_waivers(tmp_path, 4) is False

    def test_finalize_gate_no_longer_accepts_waivers(self):
        """The parameter is the mechanism. While it exists, a caller can
        reintroduce threshold-zeroing without touching this module."""
        import inspect

        from harness.harness_bridge import HarnessBridge

        params = inspect.signature(HarnessBridge.finalize_gate).parameters
        assert "da_waivers" not in params


class TestAgainstRealTaskqGateResult:
    """Adjudicate against a gate result an actual pipeline run produced.

    Round 30's regression test hand-built its fixture from score.py's breakdown
    assembly — but score.py writes .sessi-work/round_N/ scoring output, not
    gate{N}_result.json. Both sides of that comparison came from the same head,
    so the suite stayed green while the check stayed dead. This class reads a
    verbatim copy of taskq's committed gate4_result.json instead.
    """

    @pytest.fixture
    def gate4(self) -> dict:
        return json.loads(_FIXTURE.read_text(encoding="utf-8"))

    def test_real_breakdown_uses_threshold_not_target(self, gate4):
        """The field name the dead check kept missing."""
        for dim, row in gate4["breakdown"].items():
            assert "threshold" in row, f"{dim} has no 'threshold'"
            assert "target" not in row, f"{dim} unexpectedly carries 'target'"

    def test_real_architecture_score_is_null(self, gate4):
        """CRG-only dimension: the agent writes null, the framework supplies it."""
        assert gate4["breakdown"]["architecture"]["score"] is None

    def test_the_real_taskq_waiver_request_is_refused(self, gate4, tmp_path, capsys):
        """End of the causal chain, as it stands after Round 38.

        This run asked for `da_waiver: {"architecture": true}` and got it. Two
        things were wrong with that and only one was fixed by Round 21. The
        premise was false — taskq's own crg_baseline_p6.json records 100.0 for
        the same commit, 6/6 healthy communities, while the challenge claimed
        CRG would report 0. And the grant was invisible to crg-arch-check, so
        even a true premise would have bought a local PASS and a red build.
        """
        from cli.gate_cmds import _collect_da_waivers

        requested = [d for d, on in gate4["da_waiver"].items() if on]
        assert requested == ["architecture"]  # what the run actually asked for

        (tmp_path / ".sessi-work").mkdir()
        (tmp_path / ".sessi-work" / "gate4_result.json").write_text(
            json.dumps(gate4), encoding="utf-8")
        assert _collect_da_waivers(tmp_path, 4) is True
        assert "not permitted" in capsys.readouterr().err
