"""Tests for core.doctor — read-only cross-file state consistency checks.

doctor exists because framework state spans state.json, quality_manifest,
attestation, and the CLAUDE.md status block with no transaction across
them: the P8→9 incident left state.json advanced while HANDOVER.md never
regenerated. doctor reports (never repairs — fail-closed) so half-states
are found before they poison a run.

git-sync (check 6, 弱點強化 B2): state.json is a fast cache of progress
whose durable record is git history — every successful advance lands a
"handover: advance to Phase N" commit (message-level anchor: rebase-safe
where SHAs are not). doctor cross-checks the two so the split-brain the
B1 rollback prevents going forward is also DETECTED when it already
happened (crash between state write and commit, hand-edited state.json,
pre-B1 ghost states).
"""

import json
import subprocess

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


def _init_git(project):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(project), *args], capture_output=True, text=True,
        )
    git("init")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("commit", "--allow-empty", "-m", "baseline")
    return git


def _advance_commit(git, n):
    git("commit", "--allow-empty", "-m", f"handover: advance to Phase {n}")


class TestGitSync:
    def test_state_ahead_of_git_is_ghost_state_error(self, tmp_path):
        """The split-brain the report described: state advanced, commit never landed."""
        project = _project(
            tmp_path,
            state={"state": "RUNNING", "current_phase": 3},
            claude_md=GOOD_CLAUDE.replace("**1 — Requirements**", "**3 — Implementation**"),
        )
        git = _init_git(project)
        _advance_commit(git, 2)  # git history stops at Phase 2

        findings = run_doctor(project)
        ghost = [f for f in _errors(findings) if f.check == "git-sync"]
        assert ghost, "state.json ahead of git advance history must be an ERROR"
        assert "Phase 3" in ghost[0].message and "Phase 2" in ghost[0].message

    def test_state_behind_git_is_error(self, tmp_path):
        project = _project(
            tmp_path,
            state={"state": "RUNNING", "current_phase": 3},
            claude_md=GOOD_CLAUDE.replace("**1 — Requirements**", "**3 — Implementation**"),
        )
        git = _init_git(project)
        _advance_commit(git, 3)
        _advance_commit(git, 4)  # git advanced past what state claims

        findings = run_doctor(project)
        assert [f for f in _errors(findings) if f.check == "git-sync"], (
            "state.json behind the latest advance commit must be an ERROR"
        )

    def test_state_matching_git_is_clean(self, tmp_path):
        project = _project(
            tmp_path,
            state={"state": "RUNNING", "current_phase": 2},
            claude_md=GOOD_CLAUDE.replace("**1 — Requirements**", "**2 — Architecture**"),
        )
        git = _init_git(project)
        _advance_commit(git, 2)

        findings = run_doctor(project)
        assert not [f for f in findings if f.check == "git-sync"]

    def test_phase1_with_no_advance_commits_is_clean(self, tmp_path):
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE)
        _init_git(project)

        findings = run_doctor(project)
        assert not [f for f in findings if f.check == "git-sync"]

    def test_later_phase_with_no_advance_commits_is_warn_not_error(self, tmp_path):
        """Pre-convention projects have no advance commits — flag for a human,
        don't hard-fail."""
        project = _project(
            tmp_path,
            state={"state": "RUNNING", "current_phase": 3},
            claude_md=GOOD_CLAUDE.replace("**1 — Requirements**", "**3 — Implementation**"),
        )
        _init_git(project)

        findings = run_doctor(project)
        gs = [f for f in findings if f.check == "git-sync"]
        assert gs and gs[0].severity == "WARN"

    def test_non_git_project_has_no_git_sync_finding(self, tmp_path):
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE)
        findings = run_doctor(project)
        assert not [f for f in findings if f.check == "git-sync"]


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
