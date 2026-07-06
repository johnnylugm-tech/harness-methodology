"""Tests for core.doctor — read-only cross-file state consistency checks.

doctor exists because framework state spans state.json, quality_manifest,
attestation, and the CLAUDE.md status block with no transaction across
them: the P8→9 incident left state.json advanced while HANDOVER.md never
regenerated. doctor reports (never repairs — fail-closed) so half-states
are found before they poison a run.
"""

import json

from core.doctor import run_doctor


def _project(tmp_path, state=None, manifest=None, claude_md=None):
    meth = tmp_path / ".methodology"
    meth.mkdir(exist_ok=True)
    if state is not None:
        (meth / "state.json").write_text(
            state if isinstance(state, str) else json.dumps(state), encoding="utf-8"
        )
    if manifest is not None:
        (meth / "quality_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
    if claude_md is not None:
        (tmp_path / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
    return tmp_path


GOOD_STATE = {"state": "RUNNING", "current_phase": 1}
GOOD_CLAUDE = (
    "# Project: x\n<!-- harness:auto-start -->\n"
    "> Phase: **1 — Requirements** | Last Gate: **Gate None**\n"
    "<!-- harness:auto-end -->\n"
)


def _errors(findings):
    return [f for f in findings if f.severity == "ERROR"]


class TestCleanProject:
    def test_minimal_clean_project_has_no_errors(self, tmp_path):
        findings = run_doctor(_project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE))
        assert _errors(findings) == [], [f.message for f in findings]


class TestStateJson:
    def test_missing_state_is_error(self, tmp_path):
        (tmp_path / ".methodology").mkdir()
        assert _errors(run_doctor(tmp_path))

    def test_corrupt_state_is_error(self, tmp_path):
        findings = run_doctor(_project(tmp_path, state="{not json"))
        assert any("parse" in f.message.lower() for f in _errors(findings))

    def test_invalid_fsm_state_is_error(self, tmp_path):
        findings = run_doctor(
            _project(tmp_path, state={"state": "ZOMBIE", "current_phase": 1})
        )
        assert any("ZOMBIE" in f.message for f in _errors(findings))

    def test_out_of_range_phase_is_error(self, tmp_path):
        findings = run_doctor(
            _project(tmp_path, state={"state": "RUNNING", "current_phase": 99})
        )
        assert any("99" in f.message for f in _errors(findings))


class TestCrossFileConsistency:
    def test_manifest_generated_after_current_phase_is_error(self, tmp_path):
        findings = run_doctor(_project(
            tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE,
            manifest={"generated_at_phase": 3},
        ))
        assert any("generated_at_phase" in f.message for f in _errors(findings))

    def test_manifest_generated_earlier_is_fine(self, tmp_path):
        findings = run_doctor(_project(
            tmp_path, state={"state": "RUNNING", "current_phase": 5},
            claude_md=GOOD_CLAUDE.replace("**1 — Requirements**", "**5 — Verification**"),
            manifest={"generated_at_phase": 3},
        ))
        # phase 5 also needs attestation — provide it so only the manifest
        # relation is under test here
        trace = tmp_path / ".methodology" / "trace"
        trace.mkdir()
        (trace / "attestation.json").write_text("{}", encoding="utf-8")
        findings = run_doctor(tmp_path)
        assert not any("generated_at_phase" in f.message for f in _errors(findings))

    def test_claude_md_block_phase_mismatch_is_error(self, tmp_path):
        findings = run_doctor(_project(
            tmp_path,
            state={"state": "RUNNING", "current_phase": 2},
            claude_md=GOOD_CLAUDE,  # says Phase 1
        ))
        assert any("CLAUDE.md" in f.message for f in _errors(findings))

    def test_p5_without_attestation_is_error(self, tmp_path):
        findings = run_doctor(_project(
            tmp_path, state={"state": "RUNNING", "current_phase": 5},
        ))
        assert any("attestation" in f.message.lower() for f in _errors(findings))


class TestInterruptedTransaction:
    def test_leftover_journal_is_error(self, tmp_path):
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE)
        (project / ".methodology" / ".txn_journal.json").write_text(
            json.dumps({"pending": [
                {"tmp": "x.txn.tmp", "target": str(project / ".methodology" / "state.json")}
            ]}),
            encoding="utf-8",
        )
        findings = run_doctor(project)
        assert any("interrupted" in f.message.lower() for f in _errors(findings))

    def test_stray_txn_tmp_is_reported(self, tmp_path):
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE)
        (project / "HANDOVER.md.txn.tmp").write_text("x", encoding="utf-8")
        findings = run_doctor(project)
        assert any(".txn.tmp" in f.message for f in findings)
