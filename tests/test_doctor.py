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
from pathlib import Path

from core.doctor import run_doctor, _check_submodule_behind


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

    def test_many_near_miss_commits_do_not_hide_the_real_advance(self, tmp_path):
        """Round 2 Station G: the git log scan no longer caps at -n 200 (-n
        applies to grep-filtered results, not raw history depth — verified
        empirically against a real repo). This is why that matters: with
        enough commits that loosely match git's --grep but fail the strict
        _ADVANCE_SUBJECT regex, a re-introduced small -n cap would return
        only near-misses and silently lose the real advance commit."""
        project = _project(
            tmp_path,
            state={"state": "RUNNING", "current_phase": 2},
            claude_md=GOOD_CLAUDE.replace("**1 — Requirements**", "**2 — Architecture**"),
        )
        git = _init_git(project)
        _advance_commit(git, 2)  # the one real, strictly-matching advance commit
        for i in range(70):
            # loosely matches git's --grep (same prefix) but fails the strict
            # "^...Phase (\\d+)$" regex because of the trailing suffix.
            git("commit", "--allow-empty", "-m",
                f"handover: advance to Phase 2 (amended {i})")

        findings = run_doctor(project)
        assert not [f for f in findings if f.check == "git-sync"], (
            "70 near-miss commits after the real advance commit must not "
            "hide it — a re-introduced -n cap smaller than 71 would fail this"
        )


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


class TestGate1Evidence:
    """check 7 (弱點強化 Round 3 J): a manifest quality_complete claim must
    have a record in at least one of the O2 evidence channels
    (sentinel .flag / .finalized / gate_timestamps.jsonl). Deliberately
    any-phase: at-rest reconciliation optimizes for zero false positives;
    phase strictness stays at the enforcement sites (push-milestone
    p3-post-gate2, advance-phase).

    Round 20 站4 corrected this docstring's word "co-equal". The channels are
    not equals: a gate_timestamps row written by GATE1-DELTA's `already done →
    skip` branch is a shadow of the sentinel channel, because that branch only
    runs when a sentinel/commit was already found. Accepting it as a separate
    channel made `has_sentinel or fr_key in ts_frs` read like corroboration
    from two sources when there was one. Rows are now marked with their source
    and skip-written rows no longer satisfy a claim on their own.
    """

    MANIFEST = {"gate_results": {"gate1": {
        "FR-01": {"score": 95.0, "quality_complete": True},
    }}}

    def _findings(self, project):
        return [f for f in run_doctor(project) if f.check == "gate1-evidence"]

    def test_complete_claim_with_zero_evidence_is_error(self, tmp_path):
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE,
                           manifest=self.MANIFEST)
        found = self._findings(project)
        assert len(found) == 1
        assert found[0].severity == "ERROR"
        assert "FR-01" in found[0].message

    def test_sentinel_flag_satisfies_the_claim(self, tmp_path):
        from core.quality_gate.gate1_evidence import SENTINEL_FLAG_TEMPLATE
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE,
                           manifest=self.MANIFEST)
        sentinels = project / ".sessi-work" / "sentinels"
        sentinels.mkdir(parents=True)
        (sentinels / SENTINEL_FLAG_TEMPLATE.format(gate=1, phase=3, key="fr01")).touch()
        assert self._findings(project) == []

    def test_timestamps_row_satisfies_the_claim(self, tmp_path):
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE,
                           manifest=self.MANIFEST)
        (project / ".methodology" / "gate_timestamps.jsonl").write_text(
            json.dumps({"phase": 3, "gate": 1, "fr_id": "FR-01", "ts": 0}) + "\n",
            encoding="utf-8",
        )
        assert self._findings(project) == []

    def test_finalize_sourced_row_satisfies_the_claim(self, tmp_path):
        from core.quality_gate.gate1_evidence import EVIDENCE_SOURCE_FINALIZE
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE,
                           manifest=self.MANIFEST)
        (project / ".methodology" / "gate_timestamps.jsonl").write_text(
            json.dumps({"phase": 3, "gate": 1, "fr_id": "FR-01", "ts": 0,
                        "source": EVIDENCE_SOURCE_FINALIZE}) + "\n",
            encoding="utf-8",
        )
        assert self._findings(project) == []

    def test_skip_sourced_row_alone_does_not_satisfy_the_claim(self, tmp_path):
        """Round 20 站4's regression pin.

        A skip row means "run-fr-step found this FR already done and wrote a
        marker so advance-phase would not exit-14" — no gate ran. Its
        precondition is that real evidence exists in the sentinel channel, so
        treating it as independent corroboration is circular. taskq's Phase 4
        wrote five such rows in 3.1 seconds with zero dispatches behind them.
        """
        from core.quality_gate.gate1_evidence import EVIDENCE_SOURCE_SKIP
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE,
                           manifest=self.MANIFEST)
        (project / ".methodology" / "gate_timestamps.jsonl").write_text(
            json.dumps({"phase": 3, "gate": 1, "fr_id": "FR-01", "ts": 0,
                        "source": EVIDENCE_SOURCE_SKIP}) + "\n",
            encoding="utf-8",
        )
        found = self._findings(project)
        assert len(found) == 1 and found[0].severity == "ERROR", (
            "a skip-sourced row was accepted as independent evidence"
        )

    def test_skip_row_plus_a_real_sentinel_is_fine(self, tmp_path):
        """The normal case must stay quiet: skip rows appear precisely when a
        sentinel exists, and that combination is healthy, not suspicious."""
        from core.quality_gate.gate1_evidence import (
            EVIDENCE_SOURCE_SKIP,
            SENTINEL_FLAG_TEMPLATE,
        )
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE,
                           manifest=self.MANIFEST)
        sentinels = project / ".sessi-work" / "sentinels"
        sentinels.mkdir(parents=True)
        (sentinels / SENTINEL_FLAG_TEMPLATE.format(gate=1, phase=3, key="fr01")).touch()
        (project / ".methodology" / "gate_timestamps.jsonl").write_text(
            json.dumps({"phase": 3, "gate": 1, "fr_id": "FR-01", "ts": 0,
                        "source": EVIDENCE_SOURCE_SKIP}) + "\n",
            encoding="utf-8",
        )
        assert self._findings(project) == []

    def test_incomplete_claim_needs_no_evidence(self, tmp_path):
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE,
                           manifest={"gate_results": {"gate1": {
                               "FR-01": {"score": 40.0, "quality_complete": False},
                           }}})
        assert self._findings(project) == []

    def test_no_manifest_yields_no_findings(self, tmp_path):
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE)
        assert self._findings(project) == []


class TestEnforcementZombieKeys:
    """Check 8 (Round 9 站0): the EnforcementConfig dataclass that once read
    mode/platform/enforce_on_*/quality_gate_threshold from enforcement.json
    was removed as dead code — only hr_overrides and phase_truth are still
    consumed (phase_truth_verifier). A hand-edited zombie key must WARN so a
    dead setting can't masquerade as a working knob."""

    def _findings(self, project):
        return [f for f in run_doctor(project) if f.check == "enforcement-config"]

    def _write_enforcement(self, project, payload):
        (project / ".methodology" / "enforcement.json").write_text(
            payload if isinstance(payload, str) else json.dumps(payload),
            encoding="utf-8",
        )

    def test_zombie_key_warns_and_names_it(self, tmp_path):
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE)
        self._write_enforcement(project, {"quality_gate_threshold": 60.0,
                                          "phase_truth": {"pytest_timeout_seconds": 60}})
        found = self._findings(project)
        assert len(found) == 1 and found[0].severity == "WARN"
        assert "quality_gate_threshold" in found[0].message
        assert "phase_truth" not in found[0].message.split("have no consumer")[0]

    def test_live_keys_only_is_silent(self, tmp_path):
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE)
        self._write_enforcement(project, {
            "hr_overrides": {"HR-11_phase_truth_threshold": 80},
            "phase_truth": {"pytest_timeout_seconds": 120},
            # constitution stays live: constitution/profile.py load_profile()
            # merges this key into the on-demand constitution profile.
            "constitution": {"correctness": {"p1": 90}},
        })
        assert self._findings(project) == []

    def test_missing_file_is_silent(self, tmp_path):
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE)
        assert self._findings(project) == []

    def test_malformed_json_warns(self, tmp_path):
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE)
        self._write_enforcement(project, "{not json")
        found = self._findings(project)
        assert len(found) == 1 and found[0].severity == "WARN"


class TestCrashBundles:
    """Check 9 (Round 13 站3): core/errors.py's top-level crash boundary
    writes a bundle when harness-methodology crashes on its own bug. One
    sitting untriaged means nobody has looked at a confirmed harness bug
    yet — WARN (maintenance backlog item, not a state inconsistency that
    should block the current run)."""

    def _findings(self, project):
        return [f for f in run_doctor(project) if f.check == "crash-bundles"]

    def _write_bundle(self, project, name="crash_1.json", triaged=False):
        crash_dir = project / ".sessi-work" / "crash"
        crash_dir.mkdir(parents=True, exist_ok=True)
        (crash_dir / name).write_text("{}", encoding="utf-8")
        if triaged:
            (crash_dir / (name + ".triaged")).write_text("CR-01", encoding="utf-8")

    def test_untriaged_bundle_warns(self, tmp_path):
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE)
        self._write_bundle(project)
        found = self._findings(project)
        assert len(found) == 1 and found[0].severity == "WARN"
        assert "crash-triage" in found[0].message

    def test_triaged_bundle_is_silent(self, tmp_path):
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE)
        self._write_bundle(project, triaged=True)
        assert self._findings(project) == []

    def test_no_crash_dir_is_silent(self, tmp_path):
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE)
        assert self._findings(project) == []

    def test_count_reflects_multiple_untriaged(self, tmp_path):
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE)
        self._write_bundle(project, name="crash_1.json")
        self._write_bundle(project, name="crash_2.json")
        found = self._findings(project)
        assert len(found) == 1
        assert "2 untriaged" in found[0].message

    def test_mixed_triaged_and_untriaged_counts_only_untriaged(self, tmp_path):
        project = _project(tmp_path, state=GOOD_STATE, claude_md=GOOD_CLAUDE)
        self._write_bundle(project, name="crash_1.json", triaged=True)
        self._write_bundle(project, name="crash_2.json")
        found = self._findings(project)
        assert len(found) == 1
        assert "1 untriaged" in found[0].message


class TestSubmoduleBehindOrigin:
    """Round 25 站3b: relocated from cli/phase_cmds.py::_advance_prechecks.

    The check itself is unchanged — same behind_count, same three cases. What
    changed is where it runs: it was advance-phase's only network call, on the
    critical path of every phase transition, blocking nothing. Being a few
    commits behind origin does not make this phase's work wrong, so it belongs
    in the at-rest reconciliation command next to _check_git_sync.
    """

    """Phase 6 improvement #3: advance-phase postflight detects when the
    harness/ submodule HEAD is behind origin/main (e.g. CI auto-fix landed)
    and prints an actionable warning. Non-blocking by design.
    """

    def _setup_submodule(self, tmp_path: Path) -> tuple[Path, Path]:
        """Create a fake main repo + harness/ submodule with a bare 'origin'
        remote. Returns (project, sub) where project/harness is a real git
        submodule that can be ahead/behind by making local commits.

        Uses ``git update-ref`` instead of ``git push`` to populate the bare
        repo so the test is portable across CI environments where local
        transport push may be blocked by safe.directory or receive hooks.
        Commits are made BEFORE the bare clone so the bare repo already holds
        the commit objects at clone time.
        """
        import subprocess as sp
        proj = tmp_path
        (proj / ".gitmodules").write_text(
            '[submodule "harness"]\n\tpath = harness\n\turl = x\n'
        )
        sub = proj / "harness"
        sub.mkdir()
        for d in [proj, sub]:
            sp.run(["git", "-C", str(d), "init", "-q"], check=True)
            sp.run(["git", "-C", str(d), "config", "user.email", "t@t.com"], check=True)
            sp.run(["git", "-C", str(d), "config", "user.name", "T"], check=True)
        # Commit FIRST so bare clone gets the object
        (sub / "x").write_text("a")
        sp.run(["git", "-C", str(sub), "add", "."], check=True)
        sp.run(["git", "-C", str(sub), "commit", "-q", "-m", "init"], check=True)
        # Bare "origin" — cloned AFTER commit so it already has the object
        bare = tmp_path.parent / (tmp_path.name + "_origin.git")
        sp.run(["git", "clone", "--bare", str(sub), str(bare)],
               check=True, capture_output=True)
        sp.run(["git", "-C", str(sub), "remote", "add", "origin", str(bare)],
               check=True)
        # Sync bare/origin HEAD ref to match sub HEAD (transport-independent)
        head_sha = sp.run(
            ["git", "-C", str(sub), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        sp.run(
            ["git", "-C", str(bare), "update-ref", "refs/heads/main", head_sha],
            check=True,
        )
        return proj, sub


    def test_no_finding_when_in_sync(self, tmp_path):
        """HEAD == origin/main → nothing to report."""
        proj, _sub = self._setup_submodule(tmp_path)
        assert _check_submodule_behind(proj) == []

    def test_warns_when_origin_is_ahead(self, tmp_path):
        """origin has commit not in local → "behind" warning printed.

        Simulates a CI-authored commit landing on origin/main by writing
        a new commit object + updating the bare ref directly with
        ``git update-ref`` — no push transport required.
        """
        import subprocess as sp
        proj, sub = self._setup_submodule(tmp_path)
        bare = tmp_path.parent / (tmp_path.name + "_origin.git")

        # Build the "ci-fix" commit in a local clone of bare (no network needed)
        ci = tmp_path.parent / (tmp_path.name + "_ci")
        sp.run(["git", "clone", "-q", str(bare), str(ci)], check=True)
        sp.run(["git", "-C", str(ci), "config", "user.email", "ci@ci.com"], check=True)
        sp.run(["git", "-C", str(ci), "config", "user.name", "CI"], check=True)
        (ci / "y").write_text("ci-fix")
        sp.run(["git", "-C", str(ci), "add", "."], check=True)
        sp.run(["git", "-C", str(ci), "commit", "-q", "-m", "ci-fix"], check=True)
        ci_sha = sp.run(
            ["git", "-C", str(ci), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

        # Inject the new commit objects into bare via git fetch (local path, no network)
        sp.run(
            ["git", "-C", str(bare), "fetch", str(ci), "HEAD"],
            check=True, capture_output=True,
        )

        # Advance origin/main ref — transport-independent
        sp.run(
            ["git", "-C", str(bare), "update-ref", "refs/heads/main", ci_sha],
            check=True,
        )

        # Local sub is unchanged → HEAD still at "init", origin/main at "ci-fix"
        findings = _check_submodule_behind(proj)
        assert len(findings) == 1
        assert findings[0].severity == "WARN", "being behind origin blocks nothing"
        assert "1 commit(s) behind origin/main" in findings[0].message
        assert "sync-harness" in findings[0].message  # one-shot remediation


    def test_silent_when_fetch_fails(self, tmp_path):
        """No origin access (offline) → silently skip, no error."""
        proj = tmp_path
        sub = proj / "harness"
        sub.mkdir()
        (sub / ".git").mkdir()  # marker; no remote configured
        (proj / ".gitmodules").write_text(
            '[submodule "harness"]\n\tpath = harness\n\turl = x\n'
        )
        assert _check_submodule_behind(proj) == []
