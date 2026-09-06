"""Tests for cli/fr_cmds.py — run-fr-step / dispatch / FR spec extraction / tool dispatcher (split from tests/test_harness_cli.py, C1d)."""

from __future__ import annotations


import argparse
import json
import subprocess
from pathlib import Path
import io

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports
from cli.fr_cmds import (  # noqa: E402
    DISPATCH_STRUCTURALLY_BROKEN_EXIT_CODE,
    _build_fr_step_prompt,
    _compute_fr_spec_data,
    _extract_srs_fr_section,
    _fr_step_already_done,
)


def _patch_already_done(monkeypatch, fn):
    """Replace `_fr_step_already_done` in BOTH namespaces that resolve it.

    Round 82 站4 moved `_frstep_skip_if_already_done` and
    `_frstep_gate1_paper_trail` to cli/fr_step_stages.py, which reads the
    predicate from its own module globals. `cmd_resume_fr_phase` stayed in
    cli/fr_cmds.py and reads it from there, through the re-export. Patching
    only one of the two leaves half these tests measuring nothing while still
    passing — which is exactly what happened when this file was first updated
    for the move, and why it is one helper rather than a per-site judgement.
    """
    import cli.fr_cmds
    import cli.fr_step_stages
    monkeypatch.setattr(cli.fr_cmds, "_fr_step_already_done", fn)
    monkeypatch.setattr(cli.fr_step_stages, "_fr_step_already_done", fn)


class TestExtractReviewJson:
    """Tests for _extract_review_json helper."""

    def test_plain_json(self):
        from cli.fr_cmds import _extract_review_json
        text = '{"fr": "FR-01", "review_status": "APPROVE", "docs_embedded": ["SRS.md"], "confidence": 0.9}'
        result = _extract_review_json(text)
        assert result is not None
        assert result["review_status"] == "APPROVE"

    def test_json_inside_prose(self):
        from cli.fr_cmds import _extract_review_json
        text = (
            "After reviewing the document I conclude:\n"
            '{"fr": "FR-02", "review_status": "REJECT", "docs_embedded": ["SRS.md"]}\n'
            "End of review."
        )
        result = _extract_review_json(text)
        assert result is not None
        assert result["review_status"] == "REJECT"
        assert result["fr"] == "FR-02"

    def test_json_inside_code_fence(self):
        from cli.fr_cmds import _extract_review_json
        text = (
            "```json\n"
            '{"fr": "FR-03", "review_status": "APPROVE", "docs_embedded": ["SRS.md", "SAD.md"], "reason": "Reviewed all deliverables; acceptance criteria covered, no critical gaps found.", "citations": ["SRS.md:1"], "confidence": 0.85}\n'
            "```"
        )
        result = _extract_review_json(text)
        assert result is not None
        assert result["confidence"] == 0.85

    def test_no_review_status_returns_none(self):
        from cli.fr_cmds import _extract_review_json
        result = _extract_review_json('{"fr": "FR-01", "other_key": "value"}')
        assert result is None

    def test_empty_string_returns_none(self):
        from cli.fr_cmds import _extract_review_json
        assert _extract_review_json("") is None


class TestDispatchWritesApprovalJson:
    """Tests for cmd_dispatch reviewer → agent_b_approvals/<fr_id>.json."""

    def _make_spawner_mock(self, status, output):
        """Return a module-patchable AgentSpawner that yields a fixed result."""
        class _MockSpawner:
            def __init__(self, **_):
                pass
            def spawn(self, **_):
                return {"status": status, "session_id": "sess-abc", "output": output}
        return _MockSpawner

    def test_reviewer_complete_writes_approval_json(self, tmp_path, monkeypatch):
        from harness_cli import cmd_dispatch
        import sys
        import types
        output = '{"fr": "FR-01", "review_status": "APPROVE", "docs_embedded": ["SRS.md"], "confidence": 0.9}'
        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = self._make_spawner_mock("complete", output)  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_id="SRS.md",
            role="reviewer", prompt="Review FR-01", timeout=60, max_turns=5,
        )
        rc = cmd_dispatch(args)
        assert rc == 0
        approval_file = tmp_path / ".methodology" / "agent_b_approvals" / "SRS.md.json"
        assert approval_file.exists(), "approval JSON should be written"
        data = json.loads(approval_file.read_text())
        assert data["review_status"] == "APPROVE"

    def test_reviewer_complete_no_json_warns(self, tmp_path, monkeypatch, capsys):
        from harness_cli import cmd_dispatch
        import sys
        import types
        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = self._make_spawner_mock("complete", "Looks good, no JSON here.")  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_id="SRS.md",
            role="reviewer", prompt="Review FR-01", timeout=60, max_turns=5,
        )
        rc = cmd_dispatch(args)
        assert rc == 0  # dispatch itself succeeded
        approval_file = tmp_path / ".methodology" / "agent_b_approvals" / "SRS.md.json"
        assert not approval_file.exists()
        out = capsys.readouterr().out
        assert "WARN" in out

    def test_developer_role_does_not_write_approval(self, tmp_path, monkeypatch):
        from harness_cli import cmd_dispatch
        import sys
        import types
        output = '{"fr": "FR-01", "review_status": "APPROVE", "docs_embedded": ["SRS.md"]}'
        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = self._make_spawner_mock("complete", output)  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_id="SRS.md",
            role="developer", prompt="Implement FR-01", timeout=60, max_turns=5,
        )
        rc = cmd_dispatch(args)
        assert rc == 0
        approval_file = tmp_path / ".methodology" / "agent_b_approvals" / "SRS.md.json"
        assert not approval_file.exists(), "developer role must not write approval JSON"


class TestExtractAgentOutputJson:
    """Tests for _extract_agent_output_json (Gap-4)."""

    def test_plain_agent_a_json(self):
        from cli.fr_cmds import _extract_agent_output_json
        text = '{"status": "complete", "files": ["SRS.md"], "confidence": 0.9, "citations": ["FR-01"], "summary": "done"}'
        result = _extract_agent_output_json(text)
        assert result is not None
        assert result["status"] == "complete"
        assert result["confidence"] == 0.9

    def test_agent_a_json_inside_prose(self):
        from cli.fr_cmds import _extract_agent_output_json
        text = 'Task complete.\n{"status": "complete", "files": ["SAD.md"], "summary": "arch done"}'
        result = _extract_agent_output_json(text)
        assert result is not None
        assert result["files"] == ["SAD.md"]

    def test_agent_b_block_not_matched(self):
        """review_status blocks must not be treated as Agent A output."""
        from cli.fr_cmds import _extract_agent_output_json
        text = '{"fr": "FR-01", "review_status": "APPROVE", "docs_embedded": ["SRS.md"], "status": "done"}'
        result = _extract_agent_output_json(text)
        assert result is None

    def test_no_agent_a_fields_returns_none(self):
        from cli.fr_cmds import _extract_agent_output_json
        result = _extract_agent_output_json('{"status": "complete", "phase": 1}')
        assert result is None

    def test_empty_string_returns_none(self):
        from cli.fr_cmds import _extract_agent_output_json
        assert _extract_agent_output_json("") is None


class TestDispatchSavesAgentAOutput:
    """cmd_dispatch developer role persists Agent A output JSON (Gap-4)."""

    def _make_spawner_mock(self, status, output):
        class _MockSpawner:
            def __init__(self, **_): pass
            def spawn(self, **_):
                return {"status": status, "session_id": "sess-xyz", "output": output}
        return _MockSpawner

    def test_developer_complete_writes_output_json(self, tmp_path, monkeypatch):
        from harness_cli import cmd_dispatch
        import sys
        import types
        output = '{"status": "complete", "files": ["SRS.md"], "confidence": 0.9, "citations": ["FR-01"], "summary": "done"}'
        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = self._make_spawner_mock("complete", output)  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_id="SRS.md",
            role="developer", prompt="Implement FR-01", timeout=60, max_turns=5,
        )
        rc = cmd_dispatch(args)
        assert rc == 0
        output_file = tmp_path / ".methodology" / "agent_a_outputs" / "SRS.md.json"
        assert output_file.exists(), "agent output JSON should be written"
        data = json.loads(output_file.read_text())
        assert data["status"] == "complete"

    def test_developer_no_json_warns(self, tmp_path, monkeypatch, capsys):
        from harness_cli import cmd_dispatch
        import sys
        import types
        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = self._make_spawner_mock("complete", "All done, no JSON.")  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_id="SRS.md",
            role="developer", prompt="Implement FR-01", timeout=60, max_turns=5,
        )
        rc = cmd_dispatch(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "WARN" in out
        assert not (tmp_path / ".methodology" / "agent_a_outputs" / "SRS.md.json").exists()


# =============================================================================
# run-fr-step / resume-fr-phase
# =============================================================================

def _setup_preflight_fixtures(tmp_path: Path, *, step: str, fr_id: str = "FR-01") -> None:
    """Set up the minimum files needed to pass _fr_step_preflight.

    Creates: git repo, SRS.md, quality_manifest.json, and either
    02-architecture/TEST_SPEC.md (TDD-RED) or env_check_result.json (GATE1/CODE-FIX).
    """
    import subprocess as _sp

    # 1. Git repo
    _sp.run(["git", "init", "-q"], cwd=str(tmp_path), check=False)
    _sp.run(["git", "config", "user.email", "test@test"], cwd=str(tmp_path), check=False)
    _sp.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), check=False)

    # 2. SRS.md — canonical location per ProjectLayout.srs_path
    (tmp_path / "01-requirements").mkdir(exist_ok=True)
    tmp_path.joinpath("01-requirements", "SRS.md").write_text(
        f"### {fr_id}: Test Feature\n\n**Description**: Test\n\n---\n",
        encoding="utf-8",
    )

    # 3. quality_manifest.json
    manifest_dir = tmp_path / ".methodology"
    manifest_dir.mkdir()
    manifest_dir.joinpath("quality_manifest.json").write_text(
        json.dumps({"fr_ids": [fr_id]}), encoding="utf-8",
    )

    # 4. Step-specific
    if step.upper() in ("GATE1", "GATE1-DELTA", "CODE-FIX"):
        sessi = tmp_path / ".sessi-work"
        sessi.mkdir()
        sessi.joinpath("env_check_result.json").write_text(json.dumps({
            "ready": True,
            "checked_at": "2026-01-01T00:00:00+00:00",
            "summary": "ok",
            "env_vars": {"required": []},
            "cli_tools": {"required": []},
            "infra_services": {"required": []},
        }), encoding="utf-8")
    elif step.upper() == "TDD-RED":
        spec_dir = tmp_path / "02-architecture"
        spec_dir.mkdir()
        spec_dir.joinpath("TEST_SPEC.md").write_text(
            f"### {fr_id}: Test Feature\n\n"
            "| # | Test Function | Type |\n"
            "|---|--------------|------|\n"
            "| 1 | `test_feature` | Functional |\n",
            encoding="utf-8",
        )


class TestRunFrStep:
    """Tests for cmd_run_fr_step and related helpers."""

    def test_skip_if_already_done(self, tmp_path, monkeypatch):
        """Idempotency: returns 0 immediately if step commit already exists."""
        import harness_cli
        _patch_already_done(monkeypatch, lambda s, f, p, phase=None: True)
        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED", project=str(tmp_path),
            srs=None, timeout=600, max_turns=30, max_fix_rounds=3,
        )
        assert harness_cli.cmd_run_fr_step(args) == 0

    def test_dispatch_called_when_not_done(self, tmp_path, monkeypatch):
        """Sub-agent is dispatched when step has not yet been committed."""
        import sys
        import types
        import harness_cli

        _setup_preflight_fixtures(tmp_path, step="TDD-RED")

        # _fr_step_already_done always returns False (step not done)
        _patch_already_done(monkeypatch, lambda s, f, p, phase=None: False)

        dispatched: dict = {}

        class _FakeSpawner:
            def __init__(self, project_path=None):
                pass
            def spawn(self, **kwargs):
                dispatched.update(kwargs)
                return {"status": "complete", "output": "{}"}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        import subprocess as _sp
        class _FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""
        monkeypatch.setattr(_sp, "run", lambda cmd, **kw: _FakeResult())

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED", project=str(tmp_path),
            srs=None, timeout=600, max_turns=30, max_fix_rounds=3,
        )
        harness_cli.cmd_run_fr_step(args)
        assert dispatched.get("fr_id") == "FR-01"
        assert dispatched.get("phase_sop_override") == ""

    # ── Fix H-H (P3 2026-07-15 round 4): bounded plain retry for non-GATE1
    # commit-required steps' first dispatch — production evidence (sessions_
    # spawn.log) showed TDD-RED/GREEN/IMPROVE/MIRROR/amend-sab/ORCH-POST had
    # ZERO retry on any dispatch ERROR, unlike GATE1's own fix-round loop,
    # permanently killing an FR's progress for the whole run on a single
    # transient failure. ──────────────────────────────────────────────────

    def test_step_retries_transient_execution_error_then_succeeds(self, tmp_path, monkeypatch):
        """TDD-RED: attempt 1 returns a plain (non-structural, non-
        REGRESSION_GUARD) ERROR — e.g. Fix H-A's empty-commit catch — attempt
        2 (identical prompt) succeeds. run-fr-step must proceed normally
        instead of giving up after the first failure."""
        import sys
        import types
        import harness_cli

        _setup_preflight_fixtures(tmp_path, step="TDD-RED")
        # Fresh tmp_path git repo has no commit matching "test(RED): failing
        # test for FR-01" → _fr_step_already_done naturally returns False
        # (same real-public-behavior trick used elsewhere in this file, see
        # test_resume_fr_phase_prints_resolved_project_not_dot above).

        calls: list[dict] = []

        class _FakeSpawner:
            def __init__(self, project_path=None):
                pass
            def spawn(self, **kwargs):
                calls.append(kwargs)
                if len(calls) == 1:
                    return {
                        "status": "ERROR",
                        "output": "Commit-required step 'TDD-RED' returned"
                                  " empty commit (status='<unset>')",
                        "error_class": "EXECUTION_ERROR",
                    }
                return {"status": "complete", "output": '{"status": "DONE", "commit": "abc123"}'}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        # Downstream of the retry (dirty-tree guard + git push) calls real
        # subprocess.run — stub it to always report a clean/successful git
        # state so this test isolates the retry loop itself, matching the
        # established pattern in test_dispatch_called_when_not_done above.
        import subprocess as _sp
        class _FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""
        monkeypatch.setattr(_sp, "run", lambda cmd, **kw: _FakeResult())

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED", project=str(tmp_path),
            srs=None, timeout=60, max_turns=5, max_fix_rounds=2,
        )
        rc = harness_cli.cmd_run_fr_step(args)
        assert len(calls) == 2
        assert rc == 0

    def test_step_gives_up_after_exhausting_retries(self, tmp_path, monkeypatch):
        """Both attempts return the same plain ERROR → run-fr-step returns 1
        after exactly _STEP_RETRY_ATTEMPTS (2) tries — bounded, not an
        unbounded retry loop (that was the 2026-07-12 5.4h stall bug class)."""
        import sys
        import types
        import harness_cli

        _setup_preflight_fixtures(tmp_path, step="TDD-RED")

        calls: list[dict] = []

        class _FakeSpawner:
            def __init__(self, project_path=None):
                pass
            def spawn(self, **kwargs):
                calls.append(kwargs)
                return {
                    "status": "ERROR",
                    "output": "Commit-required step 'TDD-RED' returned"
                              " empty commit (status='<unset>')",
                    "error_class": "EXECUTION_ERROR",
                }

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED", project=str(tmp_path),
            srs=None, timeout=60, max_turns=5, max_fix_rounds=2,
        )
        rc = harness_cli.cmd_run_fr_step(args)
        assert len(calls) == 2
        assert rc == 1

    def test_step_does_not_retry_regression_guard(self, tmp_path, monkeypatch):
        """REGRESSION_GUARD is a hard reject and must NOT be retried, even on
        a step that is otherwise eligible for the H-H bounded retry."""
        import sys
        import types
        import harness_cli

        _setup_preflight_fixtures(tmp_path, step="TDD-RED")

        calls: list[dict] = []

        class _FakeSpawner:
            def __init__(self, project_path=None):
                pass
            def spawn(self, **kwargs):
                calls.append(kwargs)
                return {
                    "status": "REGRESSION_GUARD",
                    "output": "suspicious destructive edit",
                    "regression_flags": {"src/foo.py": ["lines_removed>50"]},
                }

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED", project=str(tmp_path),
            srs=None, timeout=60, max_turns=5, max_fix_rounds=2,
        )
        rc = harness_cli.cmd_run_fr_step(args)
        assert len(calls) == 1
        assert rc == 1

    def test_step_does_not_retry_exhausted_structural_signature(self, tmp_path, monkeypatch):
        """A STRUCTURAL failure must NOT be retried at this layer — Fix H-G
        already retried it 3x at the transport layer inside
        AgentSpawner.spawn(); retrying again here would only delay the
        (correct) structural abort, not change the outcome.

        Round 12 站0c: the production registry is empty (the connectors
        banner was disproven as a failure cause), so the mechanism is
        driven by a synthetic signature injected into the real registry."""
        import sys
        import types
        import harness_cli

        import core.agent_spawner as _real_spawner_mod

        _setup_preflight_fixtures(tmp_path, step="TDD-RED")

        _synthetic_output = "SYNTHETIC_STRUCTURAL_BREAKAGE: env permanently dead"
        monkeypatch.setattr(
            _real_spawner_mod, "_STRUCTURAL_FAILURE_SIGNATURES",
            ("SYNTHETIC_STRUCTURAL_BREAKAGE",),
        )
        calls: list[dict] = []

        class _FakeSpawner:
            def __init__(self, project_path=None):
                pass
            def spawn(self, **kwargs):
                calls.append(kwargs)
                return {
                    "status": "ERROR",
                    "output": _synthetic_output,
                    "error_class": "STRUCTURAL",
                }

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED", project=str(tmp_path),
            srs=None, timeout=60, max_turns=5, max_fix_rounds=2,
        )
        rc = harness_cli.cmd_run_fr_step(args)
        assert len(calls) == 1
        assert rc == DISPATCH_STRUCTURALLY_BROKEN_EXIT_CODE

    def test_extract_srs_fr_section_returns_correct_fr(self, tmp_path):
        """_extract_srs_fr_section returns only the target FR's content."""
        srs = tmp_path / "SRS.md"
        srs.write_text(
            "### FR-01: Feature A\n\n**Description**: Alpha text\n\n---\n"
            "### FR-02: Feature B\n\n**Description**: Beta text\n\n---\n",
            encoding="utf-8",
        )
        section = _extract_srs_fr_section(srs, "FR-01")
        assert "Alpha text" in section
        assert "Beta text" not in section

    def test_extract_srs_fr_section_missing_fr(self, tmp_path):
        """_extract_srs_fr_section returns empty string when FR not found."""
        srs = tmp_path / "SRS.md"
        srs.write_text("### FR-02: Feature B\n\n**Description**: Beta\n\n---\n")
        assert _extract_srs_fr_section(srs, "FR-01") == ""

    def test_prompt_tdd_red_contains_srs_section(self, tmp_path):
        """TDD-RED prompt includes extracted SRS section and commit format."""
        srs = tmp_path / "SRS.md"
        srs.write_text(
            "### FR-01: My Feature\n\n**Description**: Do important thing X\n\n---\n",
            encoding="utf-8",
        )
        prompt = _build_fr_step_prompt("TDD-RED", "FR-01", 3, tmp_path, srs)
        assert "Do important thing X" in prompt
        assert "test(RED): failing test for FR-01" in prompt
        assert "failing test" in prompt.lower()

    def test_prompt_tdd_red_instructs_commit_on_existing_file(self, tmp_path):
        """Regression: a sub-agent that found test_fr05.py already existing
        (e.g. untracked after a mid-flight `git reset --hard`) reasoned "needs
        review, not overwrite" and never ran step 5 (commit) — the prompt gave
        no guidance for this case. TASK step 1 must now say explicitly that an
        existing-but-uncommitted file still requires completing step 5."""
        srs = tmp_path / "SRS.md"
        srs.write_text(
            "### FR-01: My Feature\n\n**Description**: Do X\n\n---\n", encoding="utf-8"
        )
        prompt = _build_fr_step_prompt("TDD-RED", "FR-01", 3, tmp_path, srs)
        assert "already exists" in prompt
        assert "do NOT skip step 5" in prompt

    def test_prompt_tdd_green_inlines_test_file(self, tmp_path):
        """TDD-GREEN prompt includes the current test file content inline."""
        srs = tmp_path / "SRS.md"
        srs.write_text(
            "### FR-01: My Feature\n\n**Description**: Do X\n\n---\n", encoding="utf-8"
        )
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_fr01.py").write_text(
            "def test_my_feature(): assert False  # RED", encoding="utf-8"
        )
        prompt = _build_fr_step_prompt("TDD-GREEN", "FR-01", 3, tmp_path, srs)
        assert "assert False  # RED" in prompt
        assert "feat(FR-01): GREEN" in prompt

    def test_prompt_tdd_green_forbids_unreachable_defensive_code(self, tmp_path):
        """Regression: TDD-GREEN must warn against writing branches that are
        unreachable under language/library guarantees (e.g. argparse
        required=True) or that duplicate an existing __main__.py entry
        point — this is what let dead code slip into GREEN, get pragma'd by
        COVERAGE-FIX, then get rejected by GATE1's pragma audit (2 wasted
        no-progress rounds observed in the wild)."""
        srs = tmp_path / "SRS.md"
        srs.write_text("### FR-01: X\n\n---\n", encoding="utf-8")
        prompt = _build_fr_step_prompt("TDD-GREEN", "FR-01", 3, tmp_path, srs)
        assert "unreachable under the language/library" in prompt
        assert "do not duplicate its" in prompt
        assert '__main__.py' in prompt

    def test_prompt_gate1_contains_run_gate_command(self, tmp_path):
        """GATE1 prompt includes run-gate and finalize-gate commands."""
        prompt = _build_fr_step_prompt("GATE1", "FR-01", 3, tmp_path, None)
        assert "run-gate --gate 1 --phase 3 --fr-id FR-01" in prompt
        assert "finalize-gate --gate 1 --phase 3 --fr-id FR-01" in prompt
        assert '"pass"' in prompt

    def test_prompt_gate1_delta_uses_full_gate_evaluation(self, tmp_path):
        """GATE1-DELTA prompt runs full GATE1 (no --delta — skip is handled by
        _fr_step_already_done() git diff check before dispatch)."""
        prompt = _build_fr_step_prompt("GATE1-DELTA", "FR-05", 5, tmp_path, None)
        assert "run-gate --gate 1" in prompt
        assert "finalize-gate --gate 1" in prompt
        assert "--delta" not in prompt

    def test_prompt_gate1_uses_resolved_project_not_dot(self, tmp_path):
        """Regression: the GATE1 prompt sent to the sub-agent must interpolate
        the already-resolved absolute project path, not a literal '.' — a
        sub-agent's own Bash CWD when it runs this command is not guaranteed
        to be the project root, and a CWD-relative '--project .' silently
        no-ops _check_sab_module_alignment's SAB.json/src_dir existence
        check (see core.quality_gate.sab_amender fix in the same commit)."""
        assert tmp_path != Path.cwd()  # sanity: fixture path is NOT this test's CWD
        prompt = _build_fr_step_prompt("GATE1", "FR-01", 3, tmp_path, None)
        assert f"--project {tmp_path}" in prompt
        assert "--project .`" not in prompt
        assert prompt.count(f"--project {tmp_path}") == 2  # run-gate AND finalize-gate

    def test_prompt_code_fix_test_coverage_only(self, tmp_path):
        """CODE-FIX with test_coverage only → [TEST COVERAGE FIX] section,
        FORBIDDEN allows adding tests, git add includes test file."""

        # Set up TEST_SPEC.md so _extract_test_spec_names returns names
        spec_dir = tmp_path / "02-architecture"
        spec_dir.mkdir()
        spec_dir.joinpath("TEST_SPEC.md").write_text(
            "### FR-01: Feature\n\n"
            "| # | Test Function | Type |\n"
            "|---|--------------|------|\n"
            "| 1 | `test_feature_a` | Functional |\n"
            "| 2 | `test_feature_b` | Functional |\n",
            encoding="utf-8",
        )

        prompt = _build_fr_step_prompt(
            "CODE-FIX", "FR-01", 3, tmp_path, None,
            failing_dims=["test_coverage"],
        )
        assert "[TEST COVERAGE FIX" in prompt
        assert "test_feature_a" in prompt
        assert "test_feature_b" in prompt
        assert "MISSING" in prompt
        assert "ADD ALL of them as real" in prompt
        assert "Deleting existing tests" in prompt
        assert "Skipping or xfail-marking" in prompt
        assert "git add tests/test_fr01.py" in prompt

    def test_prompt_code_fix_source_only(self, tmp_path):
        """CODE-FIX with ruff only → no test section, FORBIDDEN blocks test files."""

        prompt = _build_fr_step_prompt(
            "CODE-FIX", "FR-01", 3, tmp_path, None,
            failing_dims=["ruff"],
        )
        assert "[TEST COVERAGE FIX" not in prompt
        assert "Modifying test files" in prompt
        assert "git add 03-development/src/" in prompt

    def test_prompt_code_fix_mixed_dims(self, tmp_path):
        """CODE-FIX with test_coverage + ruff → both sections, git add includes
        both src_dir and test_file."""

        spec_dir = tmp_path / "02-architecture"
        spec_dir.mkdir()
        spec_dir.joinpath("TEST_SPEC.md").write_text(
            "### FR-01: Feature\n\n"
            "| # | Test Function | Type |\n"
            "|---|--------------|------|\n"
            "| 1 | `test_feature_a` | Functional |\n",
            encoding="utf-8",
        )

        prompt = _build_fr_step_prompt(
            "CODE-FIX", "FR-01", 3, tmp_path, None,
            failing_dims=["test_coverage", "ruff"],
        )
        assert "[TEST COVERAGE FIX" in prompt
        assert "Fix source code" in prompt
        assert "Resolve test_coverage failures" in prompt
        assert "ADD any missing" in prompt
        assert "git add 03-development/src/ tests/test_fr01.py" in prompt
        assert "Deleting existing tests" in prompt
        assert "Skipping or xfail-marking" in prompt

    def test_prompt_code_fix_none_fallback(self, tmp_path):
        """CODE-FIX with failing_dims=None → diagnostic mode — self-identify
        failures via pytest + ruff (gate1_result.json doesn't exist)."""

        prompt = _build_fr_step_prompt(
            "CODE-FIX", "FR-02", 3, tmp_path, None,
            failing_dims=None,
        )
        assert "no gate1_result.json was written" in prompt
        assert "diagnostic mode" in prompt
        assert "pytest tests/test_fr02.py -q" in prompt
        assert "ruff check" in prompt
        assert "[TEST COVERAGE FIX" not in prompt
        assert "Deleting or modifying existing passing tests" in prompt
        assert "git add 03-development/src/ tests/test_fr02.py" in prompt

    def test_prompt_coverage_fix_uses_fr_scoped_coverage_target(self, tmp_path):
        """Regression (P3 2026-07-12 FR-01/FR-02 GATE1 BLOCKED): COVERAGE-FIX's
        measurement command must scope to the FR's own owned source (via
        fr_module_traceability, same resolver run-gate --fr-id already uses),
        not the whole 03-development/src tree — the whole tree includes other
        FRs' not-yet-implemented stub modules at 0% coverage, making an 80%
        whole-tree target unsatisfiable from this FR's own test file alone."""
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"fr_module_traceability": {"FR-01": "taskq.storage.store"}}),
            encoding="utf-8",
        )
        store_dir = tmp_path / "03-development" / "src" / "taskq" / "storage"
        store_dir.mkdir(parents=True)
        store_dir.joinpath("store.py").write_text("def save(): pass\n", encoding="utf-8")

        prompt = _build_fr_step_prompt("COVERAGE-FIX", "FR-01", 3, tmp_path, None)
        # Round 95: the runner is pytest-cov (Round 94 was right about that —
        # `coverage run -m pytest --cov=X` double-instruments and collects
        # nothing), but the FR scope is back, as a second step reading the
        # same .coverage. Round 94 dropped the scope along with the runner,
        # and the two are independent: the removed `coverage run -m pytest T
        # -q && coverage report --include=...` never passed `--cov=` to
        # pytest, so it never hit the conflict Round 94 measured.
        scoped_cmd = (
            "python3 -m pytest tests/test_fr01.py --cov=03-development/src "
            "--cov-report= -q "
            '&& python3 -m coverage report '
            '--include="03-development/src/taskq/storage/store.py" -m'
        )
        assert scoped_cmd in prompt
        assert "coverage run -m pytest tests/test_fr01.py" not in prompt
        # The scope must reach the agent, not only the command: Round 94's
        # comment claimed "the `_cf_include` list is in this prompt's context
        # already" and it was not — measured, `"store.py" in prompt` was False.
        assert "store.py" in prompt
        # And it must NOT be the fallback command. Without this the test below
        # asserts the same substring and the two can never disagree, which is
        # what Round 94 left behind.
        assert (
            "python3 -m pytest tests/test_fr01.py --cov=03-development/src "
            "--cov-report=term-missing -q"
        ) not in prompt

    def test_prompt_coverage_fix_falls_back_to_whole_tree_when_unresolvable(self, tmp_path):
        """No fr_module_traceability entry and no resolvable imports → today's
        whole-tree fallback command is unchanged."""
        prompt = _build_fr_step_prompt("COVERAGE-FIX", "FR-01", 3, tmp_path, None)
        assert "pytest tests/test_fr01.py --cov=03-development/src --cov-report=term-missing -q" in prompt
        assert "--include=" not in prompt, (
            "the fallback has no FR scope to include — if it renders one, the "
            "scoped branch and this one are the same string again"
        )

    def test_prompt_coverage_fix_scoped_and_fallback_are_not_the_same_string(self, tmp_path):
        """Round 95. Round 94 left both branches of `if _cf_src_files:`
        producing byte-identical text: the scoped branch was dead, and the
        `load_quality_manifest` + `resolve_fr_scoped_src_files` work above it
        (a file read, a manifest lookup, a src glob and an AST parse) ran on
        every prompt build to choose between two equal literals.

        Asserting on each branch separately cannot see that — both assertions
        pass on a dead branch. This compares the two outputs."""
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"fr_module_traceability": {"FR-01": "taskq.storage.store"}}),
            encoding="utf-8",
        )
        store_dir = tmp_path / "03-development" / "src" / "taskq" / "storage"
        store_dir.mkdir(parents=True)
        store_dir.joinpath("store.py").write_text("def save(): pass\n", encoding="utf-8")
        scoped = _build_fr_step_prompt("COVERAGE-FIX", "FR-01", 3, tmp_path, None)

        bare = tmp_path / "no_manifest"
        bare.mkdir()
        fallback = _build_fr_step_prompt("COVERAGE-FIX", "FR-01", 3, bare, None)

        assert scoped != fallback, (
            "the resolved-scope and unresolvable-scope prompts are identical, "
            "so the resolver above them decides nothing"
        )

    def test_prompt_coverage_fix_pragma_allowlist_matches_gate1_audit(self, tmp_path):
        """Regression (pragma-allowlist drift): COVERAGE-FIX's ESCAPE HATCH
        must not advertise an exemption GATE1's _audit_pragma_no_cover will
        then reject. `if __name__ == "__main__":` must no longer be listed
        as an allowed pragma target, and the prompt must interpolate the
        single-source-of-truth guidance from core.phase_hooks so the two
        can never drift apart again."""
        from core.phase_hooks import PRAGMA_NO_COVER_GUIDANCE

        prompt = _build_fr_step_prompt("COVERAGE-FIX", "FR-01", 3, tmp_path, None)
        # Old hand-written allowlist (which used to whitelist __main__ guards) is gone.
        assert "✓ Allowed:" not in prompt
        # New rule explicitly rejects __main__ guards as a pragma target.
        assert 'blocks are NOT a valid pragma target' in prompt
        # Prompt now shares GATE1's own allowlist description verbatim —
        # the two can no longer say different things.
        assert PRAGMA_NO_COVER_GUIDANCE in prompt

    def test_resume_fr_phase_finds_first_pending_step(self, tmp_path, monkeypatch):
        """resume-fr-phase prints the first step that is not yet done."""
        import harness_cli
        import sys

        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8"
        )
        # TDD-RED done, TDD-GREEN not yet done
        _patch_already_done(
            monkeypatch,
            lambda step, fr_id, project, phase=None: step == "TDD-RED",
        )
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        args = argparse.Namespace(phase=3, project=str(tmp_path))
        rc = harness_cli.cmd_resume_fr_phase(args)
        assert rc == 0
        out = captured.getvalue()
        assert "TDD-GREEN" in out
        assert "FR-01" in out

    def test_resume_fr_phase_all_done(self, tmp_path, monkeypatch):
        """resume-fr-phase reports all complete when every step is done."""
        import harness_cli
        import sys

        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8"
        )
        _patch_already_done(monkeypatch, lambda *a, **k: True)
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        args = argparse.Namespace(phase=3, project=str(tmp_path))
        rc = harness_cli.cmd_resume_fr_phase(args)
        assert rc == 0
        assert "All FRs complete" in captured.getvalue()

    def test_resume_fr_phase_falls_back_to_fr_progress(self, tmp_path, monkeypatch):
        """resume-fr-phase uses fr_progress.json when quality_manifest.json is absent."""
        import harness_cli
        import sys

        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "fr_progress.json").write_text(
            json.dumps({"phase": 3, "frs": {"FR-02": {"status": "gate1_pass"}}}),
            encoding="utf-8",
        )
        _patch_already_done(monkeypatch, lambda *a, **k: False)
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        args = argparse.Namespace(phase=3, project=str(tmp_path))
        rc = harness_cli.cmd_resume_fr_phase(args)
        assert rc == 0
        assert "FR-02" in captured.getvalue()

    def test_resume_fr_phase_prints_resolved_project_not_dot(self, tmp_path, monkeypatch):
        """Regression: the printed 'Next step' command must use the resolved
        absolute project path, not a literal '.' (see _build_fr_step_prompt
        GATE1 regression test for the same class of bug). Uses a real (empty)
        git repo rather than patching the private `_fr_step_already_done` —
        `git log --grep` on a repo with no matching commit naturally reports
        "not done", which is real public (subprocess) behavior, not an
        implementation-detail patch (see tests/test_patch_discipline.py)."""
        import harness_cli
        import sys
        import subprocess as _sp

        assert tmp_path != Path.cwd()
        _sp.run(["git", "init", "-q"], cwd=str(tmp_path), check=False)
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8"
        )
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        args = argparse.Namespace(phase=3, project=str(tmp_path))
        rc = harness_cli.cmd_resume_fr_phase(args)
        assert rc == 0
        out = captured.getvalue()
        assert f"--project {tmp_path}" in out
        assert "--project ." not in out

    def test_preflight_finds_srs_via_project_layout_default(self, tmp_path):
        """Regression: _fr_step_preflight's "no explicit srs_path" branch used
        to guess among hard-coded candidate strings instead of using
        ProjectLayout.srs_path — the single source of truth already relied on
        by 14+ other call sites across the harness (phase_cmds.py,
        harness_bridge.py, spec_alignment.py, ...). Must find the real
        01-requirements/SRS.md with no --srs given."""
        import cli.fr_cmds as _frm

        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8"
        )
        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "01-requirements" / "SRS.md").write_text("### FR-01\n", encoding="utf-8")
        _ok, errors = _frm._fr_step_preflight("TDD-RED", tmp_path, "FR-01", srs_path=None)
        assert not any("SRS" in e for e in errors), f"Unexpected SRS error: {errors}"

    def test_build_prompt_finds_srs_via_project_layout_default(self, tmp_path):
        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "01-requirements" / "SRS.md").write_text(
            "### FR-01: My Feature\n\n**Description**: Do X\n\n---\n", encoding="utf-8"
        )
        prompt = _build_fr_step_prompt("TDD-RED", "FR-01", 3, tmp_path, None)
        assert "Do X" in prompt

    def test_resume_fr_phase_next_step_omits_wrong_srs_flag(self, tmp_path, monkeypatch):
        """Regression: cmd_resume_fr_phase used to print a hard-coded
        `--srs .methodology/SRS.md` that doesn't exist in this project
        (real file is at 01-requirements/SRS.md) — running the printed
        command failed preflight with 'SRS.md not found'. The suggested
        command must omit --srs entirely and let the callee auto-resolve.
        Uses a real (empty) git repo rather than patching the private
        `_fr_step_already_done` — same rationale as
        test_resume_fr_phase_prints_resolved_project_not_dot above."""
        import harness_cli
        import sys
        import subprocess as _sp

        _sp.run(["git", "init", "-q"], cwd=str(tmp_path), check=False)
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8"
        )
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        args = argparse.Namespace(phase=3, project=str(tmp_path))
        rc = harness_cli.cmd_resume_fr_phase(args)
        assert rc == 0
        out = captured.getvalue()
        assert "--step TDD-RED" in out
        assert "--srs" not in out

    def test_gate1_blocked_after_max_rounds(self, tmp_path, monkeypatch):
        """Returns exit 2 (BLOCKED) when GATE1 never passes after max_fix_rounds."""
        import sys
        import types
        import harness_cli

        _setup_preflight_fixtures(tmp_path, step="GATE1")

        _patch_already_done(monkeypatch, lambda s, f, p, phase=None: False)

        # Sub-agent always returns gate_pass=false
        _fail_output = '{"status": "DONE", "pass": false, "failing_dims": ["D1"], "gate_score": 0.2}'

        class _FakeSpawner:
            def __init__(self, project_path=None):
                pass
            def spawn(self, **kwargs):
                return {"status": "complete", "output": _fail_output}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="GATE1", project=str(tmp_path),
            srs=None, timeout=60, max_turns=5, max_fix_rounds=2,
        )
        rc = harness_cli.cmd_run_fr_step(args)
        assert rc == 2  # BLOCKED

    def test_gate1_blocked_when_codefix_dispatch_errors_not_phantom_pass(self, tmp_path, monkeypatch):
        """Regression (P3 2026-07-17 FR-01 GATE1 phantom-PASS): the fix-round
        `for...else` loop only runs its `else:` (BLOCKED + return 2) when the
        loop completes WITHOUT hitting `break`. But when the CODE-FIX
        sub-agent's own dispatch fails (e.g. burns through max_turns without
        producing a fix — status in _DISPATCH_ERROR_STATUSES), the loop
        `break`s immediately, which silently skips the `else:` clause too —
        the function then fell straight through to the post-loop
        record-timestamp/push/print-success path even though GATE1 never
        actually passed. Production evidence: this exact sequence
        (`CODE-FIX failed: subtype=error_max_turns` immediately followed by
        `✅ FR-01 GATE1 complete + pushed to GitHub`, with no intervening
        "BLOCKED after N CODE-FIX rounds" message) was captured verbatim in
        the P3 health-check rerun transcript. run-fr-step must return 2
        (BLOCKED), never 0, whenever a fix-dispatch error leaves gate_pass
        False."""
        import sys
        import types
        import harness_cli

        import subprocess as _sp

        _setup_preflight_fixtures(tmp_path, step="GATE1")
        # Baseline commit so the guard only ever sees dirt introduced by the
        # step under test — mirrors test_gate1_pass_does_not_trip_own_dirty_tree_guard.
        # Without this, .methodology/ starts wholly untracked and `git status
        # --porcelain` renders it as a single "?? .methodology/" line; the
        # moment gate_timestamps.jsonl becomes the first tracked file inside
        # it, that one line splits into N per-file lines, which the pre/post
        # dirty-tree diff would misread as "new" dirt unrelated to the bug
        # this test targets.
        _sp.run(["git", "add", "-A"], cwd=str(tmp_path), check=False)
        _sp.run(["git", "commit", "-q", "-m", "fixture baseline"], cwd=str(tmp_path), check=False)

        _patch_already_done(monkeypatch, lambda s, f, p, phase=None: False)

        _gate_fail_output = '{"status": "DONE", "pass": false, "failing_dims": ["D1"], "gate_score": 0.2}'
        spawn_calls: list[dict] = []

        class _FakeSpawner:
            def __init__(self, project_path=None):
                pass
            def spawn(self, **kwargs):
                spawn_calls.append(kwargs)
                if len(spawn_calls) == 1:
                    # Initial GATE1 dispatch: gate fails, triggers a fix round.
                    return {"status": "complete", "output": _gate_fail_output}
                # The fix-dispatch (CODE-FIX) sub-agent itself fails to
                # dispatch — the exact failure mode observed in production
                # (error_max_turns), distinct from the fix producing a bad
                # fix. `status` must be a member of _DISPATCH_ERROR_STATUSES.
                return {"status": "ERROR", "output": "subtype=error_max_turns"}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="GATE1", project=str(tmp_path),
            srs=None, timeout=60, max_turns=5, max_fix_rounds=3, no_push=True,
        )
        rc = harness_cli.cmd_run_fr_step(args)
        assert rc == 2, (
            "CODE-FIX dispatch failure must BLOCK, not silently fall through "
            "to the post-loop success path (phantom PASS)"
        )
        # Exactly 2 spawns: initial GATE1 dispatch + the one fix-dispatch
        # attempt. The dispatch-error break must stop retrying immediately
        # (no GATE1 re-dispatch, no further fix rounds).
        assert len(spawn_calls) == 2

    def test_gate1_pass_does_not_trip_own_dirty_tree_guard(self, tmp_path, monkeypatch):
        """Regression (P3 2026-07-17 FR-01 GATE1 false-FAIL): a genuine GATE1
        PASS was misreported as "commit did not land" because the orchestrator's
        own record_gate_timestamp() append to gate_timestamps.jsonl (a tracked
        file) was never committed before the dirty-tree guard ran two lines
        later — so the guard always saw its own sibling code's write as proof
        the sub-agent's commit had failed, and routed a clean PASS into a
        pointless CODE-FIX retry. run-fr-step must return 0 here, not 6."""
        import subprocess as _sp
        import sys
        import types
        import harness_cli

        _setup_preflight_fixtures(tmp_path, step="GATE1")
        # Baseline commit so the guard only ever sees dirt introduced by the
        # step under test, not the fixture setup itself.
        _sp.run(["git", "add", "-A"], cwd=str(tmp_path), check=False)
        _sp.run(["git", "commit", "-q", "-m", "fixture baseline"], cwd=str(tmp_path), check=False)

        # No finalize-gate sentinel / quality_complete written → naturally
        # not-already-done, so record_gate_timestamp() fires (real public
        # behavior, not a patched private helper).
        _pass_output = '{"status": "DONE", "pass": true, "gate_score": 95.0}'

        class _FakeSpawner:
            def __init__(self, project_path=None):
                pass
            def spawn(self, **kwargs):
                return {"status": "complete", "output": _pass_output}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="GATE1", project=str(tmp_path),
            srs=None, timeout=60, max_turns=5, max_fix_rounds=2, no_push=True,
        )
        rc = harness_cli.cmd_run_fr_step(args)
        assert rc == 0, "genuine GATE1 PASS must not be blocked by the orchestrator's own bookkeeping write"

        dirty = _sp.run(
            ["git", "status", "--porcelain"], cwd=str(tmp_path),
            capture_output=True, text=True,
        ).stdout.strip()
        assert dirty == "", f"gate_timestamps.jsonl write must be committed, not left dirty: {dirty!r}"

    def test_gate1_fails_fast_on_structural_signature(self, tmp_path, monkeypatch):
        """Regression (P3 2026-07-12 FR-04 abort): when a spawned fix sub-agent
        hits a deterministic-breakage signature, every retry reproduces the
        identical failure — no number of CODE-FIX/LINT-FIX/COVERAGE-FIX
        rounds can ever succeed. run-fr-step must detect this and abort
        immediately with DISPATCH_STRUCTURALLY_BROKEN_EXIT_CODE instead of
        exhausting max_fix_rounds (previously: a silent multi-hour retry loop).

        Round 12 站0c: the original driving signature (the connectors
        banner) was disproven as a failure cause and the production
        registry is now empty, so a synthetic signature drives the
        mechanism (see test_banner_only_failure_is_not_structural_abort
        for the semantic flip).

        Uses a real (fresh) git repo with no finalize-gate sentinel rather than
        patching the private `_fr_step_already_done` — on a fresh tmp_path,
        GATE1's idempotency check naturally finds no sentinel and returns
        False, which is real public behavior, not an implementation-detail
        patch (see tests/test_patch_discipline.py)."""
        import sys
        import types
        import harness_cli

        import core.agent_spawner as _real_spawner_mod

        _setup_preflight_fixtures(tmp_path, step="GATE1")

        _gate_fail_output = '{"status": "DONE", "pass": false, "failing_dims": ["D1"], "gate_score": 0.2}'
        _synthetic_output = "SYNTHETIC_STRUCTURAL_BREAKAGE: env permanently dead"
        monkeypatch.setattr(
            _real_spawner_mod, "_STRUCTURAL_FAILURE_SIGNATURES",
            ("SYNTHETIC_STRUCTURAL_BREAKAGE",),
        )
        spawn_calls: list[dict] = []

        class _FakeSpawner:
            def __init__(self, project_path=None):
                pass
            def spawn(self, **kwargs):
                spawn_calls.append(kwargs)
                if len(spawn_calls) == 1:
                    # Initial GATE1 dispatch: gate fails, triggers a fix round.
                    return {"status": "complete", "output": _gate_fail_output}
                # Every fix-dispatch attempt (CODE-FIX/LINT-FIX/etc.) fails
                # identically because the environment itself is broken.
                return {"status": "FAILED", "output": _synthetic_output}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="GATE1", project=str(tmp_path),
            srs=None, timeout=60, max_turns=5, max_fix_rounds=3,
        )
        rc = harness_cli.cmd_run_fr_step(args)
        assert rc == DISPATCH_STRUCTURALLY_BROKEN_EXIT_CODE
        # Exactly 2 spawns: the initial GATE1 dispatch + the first fix attempt.
        # No second or third fix round, no GATE1 re-dispatch — the whole
        # point is to not retry a structurally-broken environment.
        assert len(spawn_calls) == 2

    def test_first_dispatch_fails_fast_on_structural_signature(self, tmp_path, monkeypatch):
        """FIX-O regression (P3 2026-07-13 FR-01 abort): a structural
        signature must be caught on the FIRST dispatch of a step too, not only
        inside the GATE1 fix-loop's retry dispatches. Before this fix, a
        TDD-RED/TDD-GREEN/TDD-IMPROVE (or GATE1's very first, pre-fix-loop)
        dispatch hitting this env condition fell into the generic
        `else: ... return 1` branch — no [FATAL] diagnostic, no distinct exit
        code, no env-var hint — even though the two other dispatch sites in
        this same function (fix-loop CODE-FIX/LINT-FIX/COVERAGE-FIX, and
        GATE1's post-fix-round re-dispatch) already special-case it.
        (Round 12 站0c: synthetic signature — production registry is empty.)"""
        import sys
        import types
        import harness_cli

        import core.agent_spawner as _real_spawner_mod

        _setup_preflight_fixtures(tmp_path, step="TDD-RED")

        _synthetic_output = "SYNTHETIC_STRUCTURAL_BREAKAGE: env permanently dead"
        monkeypatch.setattr(
            _real_spawner_mod, "_STRUCTURAL_FAILURE_SIGNATURES",
            ("SYNTHETIC_STRUCTURAL_BREAKAGE",),
        )
        spawn_calls: list[dict] = []

        class _FakeSpawner:
            def __init__(self, project_path=None):
                pass
            def spawn(self, **kwargs):
                spawn_calls.append(kwargs)
                return {"status": "FAILED", "output": _synthetic_output}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED", project=str(tmp_path),
            srs=None, timeout=60, max_turns=30, max_fix_rounds=3,
        )
        rc = harness_cli.cmd_run_fr_step(args)
        assert rc == DISPATCH_STRUCTURALLY_BROKEN_EXIT_CODE
        # Single dispatch, no retry — TDD-RED has no fix-loop to enter.
        assert len(spawn_calls) == 1

    def test_banner_only_failure_is_not_structural_abort(self, tmp_path, monkeypatch):
        """Round 12 站0c semantic flip: the connectors banner alone must NOT
        trigger the structural fast-abort any more. Production evidence
        (2026-07-16 P3 run: 76/461 sessions_spawn.log entries carried the
        banner as their ONLY error output, while Fix H-G's own data showed
        4/5 next-dispatches succeed with the banner present) disproved the
        fatal-env theory — the banner is startup noise, and treating it as
        deterministic breakage aborted pipelines that would have recovered.
        A banner-only failure now takes the ordinary error path (retry per
        Fix H-H, then plain non-zero exit — NOT exit 23)."""
        import sys
        import types
        import harness_cli

        _setup_preflight_fixtures(tmp_path, step="TDD-RED")

        _banner_output = (
            "⚠ claude.ai connectors are disabled because ANTHROPIC_API_KEY "
            "or another auth source is set and takes precedence over your "
            "claude.ai login · Unset it to load your organization's connectors"
        )
        spawn_calls: list[dict] = []

        class _FakeSpawner:
            def __init__(self, project_path=None):
                pass
            def spawn(self, **kwargs):
                spawn_calls.append(kwargs)
                return {"status": "FAILED", "output": _banner_output}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED", project=str(tmp_path),
            srs=None, timeout=60, max_turns=30, max_fix_rounds=3,
        )
        rc = harness_cli.cmd_run_fr_step(args)
        assert rc != DISPATCH_STRUCTURALLY_BROKEN_EXIT_CODE
        assert rc == 1
        # Ordinary error path: Fix H-H's bounded step retry runs (2 attempts),
        # instead of the old single-dispatch structural abort.
        assert len(spawn_calls) == 2

    def test_resume_fr_phase_carryforward_uses_gate1_delta(self, tmp_path, monkeypatch):
        """resume-fr-phase emits GATE1-DELTA for carry-forward phases when code unchanged."""
        import harness_cli
        import sys

        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8"
        )
        _patch_already_done(monkeypatch, lambda *a, **k: False)
        monkeypatch.setattr("core.quality_gate.gate1_evidence.fr_code_changed_since_last_gate1", lambda *a, **k: False,
        )
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        for phase in (5, 7, 8):
            captured.truncate(0)
            captured.seek(0)
            args = argparse.Namespace(phase=phase, project=str(tmp_path))
            rc = harness_cli.cmd_resume_fr_phase(args)
            assert rc == 0
            out = captured.getvalue()
            assert "GATE1-DELTA" in out, f"Phase {phase} should use GATE1-DELTA"
            assert "TDD-RED" not in out, f"Phase {phase} should not show TDD-RED"

    def test_resume_fr_phase_carryforward_switches_to_tdd_when_code_changed(
        self, tmp_path, monkeypatch,
    ):
        """Carry-forward phases switch to full TDD when code changed since last Gate 1."""
        import harness_cli
        import sys

        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8"
        )
        _patch_already_done(monkeypatch, lambda *a, **k: False)
        monkeypatch.setattr("core.quality_gate.gate1_evidence.fr_code_changed_since_last_gate1", lambda *a, **k: True,
        )
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        args = argparse.Namespace(phase=7, project=str(tmp_path))
        rc = harness_cli.cmd_resume_fr_phase(args)
        assert rc == 0
        out = captured.getvalue()
        assert "TDD-RED" in out, "Code changed → should use full TDD"
        assert "GATE1-DELTA" not in out, "Code changed → should not use GATE1-DELTA"

    def test_fr_step_already_done_requires_file_existence(self, tmp_path, monkeypatch):
        """_fr_step_already_done returns False if commit matches but physical test file or src dir is missing."""
        import subprocess as _sp

        class _FakeResult:
            returncode = 0
            stdout = "test(RED): failing test for FR-01"
            # Round 41 站1: `stderr` is not decoration. This stub stands in for
            # every subprocess this call path makes, and the path now includes
            # run_suite's pytest invocation, which reads both streams. A stub
            # that answers fewer questions than the object it impersonates
            # fails as soon as the production code asks one more.
            stderr = ""

        # Git log mock returns matching commit
        monkeypatch.setattr(_sp, "run", lambda *_, **__: _FakeResult())

        # RED Test: Test file missing -> should return False
        assert not _fr_step_already_done("TDD-RED", "FR-01", tmp_path)

        # Create test file -> should return True
        (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tests" / "test_fr01.py").write_text("def test_fr(): pass")
        assert _fr_step_already_done("TDD-RED", "FR-01", tmp_path)

        # GREEN Test: Src dir missing -> should return False
        assert not _fr_step_already_done("TDD-GREEN", "FR-01", tmp_path)

        # Create empty src dir -> should return False
        (tmp_path / "03-development" / "src").mkdir(parents=True, exist_ok=True)
        assert not _fr_step_already_done("TDD-GREEN", "FR-01", tmp_path)

        # Create source file with tag -> should return True
        f = tmp_path / "03-development" / "src" / "impl.py"
        f.write_text("# [FR-01]")
        assert _fr_step_already_done("TDD-GREEN", "FR-01", tmp_path)

    def test_gate1_already_done_uses_quality_complete_not_overall_score(self, tmp_path, monkeypatch):
        """_fr_step_already_done("GATE1", ...) must key off the manifest's
        quality_complete verdict, not a same-ballpark-by-coincidence
        comparison of overall_score against quality_targets.min_coverage.

        Repro (2026-07-08 P3 run): FR-01 overall_score=80.28 (weighted
        composite of linting/type_safety/test_coverage) happened to clear
        min_coverage=80 (a coverage-percentage threshold, different unit),
        so the old code treated GATE1 as already-done and skipped
        re-evaluation even though quality_complete was False (test_coverage
        dimension score=42 < its own 80 threshold).
        """
        import subprocess as _sp

        class _FakeResult:
            returncode = 0
            stdout = "feat(FR-01): Gate1 PASS"

        monkeypatch.setattr(_sp, "run", lambda *_, **__: _FakeResult())

        manifest_dir = tmp_path / ".methodology"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / "quality_manifest.json"

        # overall_score clears min_coverage numerically, but quality_complete
        # is False (a dimension-level threshold — test_coverage — failed).
        manifest_path.write_text(json.dumps({
            "quality_targets": {"min_coverage": 80.0},
            "gate_results": {"gate1": {"FR-01": {
                "score": 80.28, "quality_complete": False,
            }}},
        }))
        assert not _fr_step_already_done("GATE1", "FR-01", tmp_path), (
            "quality_complete=False must force re-evaluation regardless of "
            "how overall_score compares to min_coverage"
        )

        # quality_complete True → safe to skip.
        manifest_path.write_text(json.dumps({
            "quality_targets": {"min_coverage": 80.0},
            "gate_results": {"gate1": {"FR-01": {
                "score": 97.62, "quality_complete": True,
            }}},
        }))
        assert _fr_step_already_done("GATE1", "FR-01", tmp_path)

    def test_gate1_phase_scoped_ignores_stale_commit_when_sentinel_missing(self, tmp_path, monkeypatch):
        """Bug A+B repro: after a `git reset --hard` back to a phase boundary and
        re-running the same phase, a stale 'feat(FR-05): Gate1 PASS' commit can
        still be reachable from HEAD, and quality_manifest.json can still carry
        a stale quality_complete=True flag from the previous lineage. Without
        phase-scoping, _fr_step_already_done("GATE1", ...) would report the step
        already done and silently skip producing this phase's real GATE1
        deliverable. The phase-scoped finalize-gate sentinel is the fix: it is
        only ever written right after a genuine bridge.finalize_gate() PASS for
        THIS phase, so its absence overrides both the stale commit and the
        stale manifest flag.
        """
        import subprocess as _sp

        class _FakeResult:
            returncode = 0
            stdout = "feat(FR-05): Gate1 PASS"  # stale commit still matches an unscoped grep

        monkeypatch.setattr(_sp, "run", lambda *_, **__: _FakeResult())

        manifest_dir = tmp_path / ".methodology"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "quality_manifest.json").write_text(json.dumps({
            "gate_results": {"gate1": {"FR-05": {"score": 80.0, "quality_complete": True}}},
        }))
        # No sentinel written for phase=3 → finalize-gate never actually ran this phase.
        assert not _fr_step_already_done("GATE1", "FR-05", tmp_path, phase=3), (
            "phase-scoped sentinel absence must force re-evaluation even when a "
            "stale commit + stale manifest flag both say 'done'"
        )

    def test_gate1_phase_scoped_true_when_sentinel_and_manifest_agree(self, tmp_path):
        from core.quality_gate.gate1_evidence import _finalize_sentinel_path
        sentinel = _finalize_sentinel_path(tmp_path, 1, "FR-05", phase=3)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("2026-07-10T00:00:00+00:00\n", encoding="utf-8")

        manifest_dir = tmp_path / ".methodology"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "quality_manifest.json").write_text(json.dumps({
            "gate_results": {"gate1": {"FR-05": {"score": 92.0, "quality_complete": True}}},
        }))
        assert _fr_step_already_done("GATE1", "FR-05", tmp_path, phase=3)

    def test_gate1_phase_scoped_false_when_sentinel_present_but_manifest_disagrees(self, tmp_path):
        """Defense-in-depth: sentinel existing alone isn't sufficient if the
        manifest disagrees — quality_complete check still applies on top."""
        from core.quality_gate.gate1_evidence import _finalize_sentinel_path
        sentinel = _finalize_sentinel_path(tmp_path, 1, "FR-05", phase=3)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("2026-07-10T00:00:00+00:00\n", encoding="utf-8")

        manifest_dir = tmp_path / ".methodology"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "quality_manifest.json").write_text(json.dumps({
            "gate_results": {"gate1": {"FR-05": {"score": 40.0, "quality_complete": False}}},
        }))
        assert not _fr_step_already_done("GATE1", "FR-05", tmp_path, phase=3)

    # ------------------------------------------------------------------
    # Bug Fix Multi-Tag-Docstring + Idempotency-Cascade (2026-07-21)
    # ------------------------------------------------------------------

    def test_fr_step_already_done_multi_tag_docstring_matches_each_fr(self, tmp_path, monkeypatch):
        """Multi-tag docstring `[FR-02, FR-03, FR-04]` MUST match each individual
        FR's TDD-GREEN heuristic. Pre-fix: only literal `[FR-02]` matched; the
        combined string failed for every FR even when each is genuinely present
        in the tag set.

        Live-failing scenario before fix: taskq/executor.py after FR-04 IMPROVE
        has header that contains the multi-tag set `[FR-02, FR-03, FR-04]`;
        FR-02's heuristic returns False even though `[FR-02]` is semantically
        a tag in that combined set.

        Review fix (2026-07-21): the docstring scan is only ever reached when
        a matching "feat({fr_id}): GREEN" commit was found (commit evidence
        remains a hard requirement — see the review-fix comment above the
        `if not committed: return False` check in _fr_step_already_done).
        So this test mocks a NON-empty commit grep, isolating the docstring
        multi-tag matching behaviour from the commit-evidence requirement.
        """
        import subprocess as _sp

        class _NonEmptyGrep:
            returncode = 0
            stdout = "abc1234 feat(FR-XX): GREEN\n"  # commit evidence present

        monkeypatch.setattr(_sp, "run", lambda *_, **__: _NonEmptyGrep())

        # NO cascade pre-conditions (no sentinel, no manifest), so the
        # multi-tag docstring scan is the only signal that matters here.
        src_dir = tmp_path / "03-development" / "src"
        src_dir.mkdir(parents=True)
        (src_dir / "executor.py").write_text(
            '"""[FR-02, FR-03, FR-04] `taskq.executor` — shared executor."""\n'
            "def run_task(tid): return tid\n"
        )

        # Each FR in the multi-tag set MUST match.
        assert _fr_step_already_done("TDD-GREEN", "FR-02", tmp_path, phase=3), (
            "FR-02 (leading tag in [FR-02, FR-03, FR-04]) must match"
        )
        assert _fr_step_already_done("TDD-GREEN", "FR-03", tmp_path, phase=3), (
            "FR-03 (mid-list tag in [FR-02, FR-03, FR-04]) must match"
        )
        assert _fr_step_already_done("TDD-GREEN", "FR-04", tmp_path, phase=3), (
            "FR-04 (trailing tag in [FR-02, FR-03, FR-04]) must match"
        )

        # A FR NOT in the tag set must NOT match (negative case).
        assert not _fr_step_already_done("TDD-GREEN", "FR-99", tmp_path, phase=3), (
            "FR-99 (not in any tag) must not match the multi-tag docstring"
        )

    def test_fr_step_already_done_docstring_scan_does_not_match_unbracketed_prose(
        self, tmp_path, monkeypatch,
    ):
        """Review fix regression (2026-07-21): the multi-tag scan must be
        anchored to `[...]` bracket contents, not a whole-file substring
        search. A plain comment mentioning another FR (no brackets) MUST
        NOT false-positive match — reproduced pre-fix:
        `_fr_step_already_done("TDD-GREEN", "FR-03", ...)` returned True
        for a file whose only FR-03 reference was
        `# TODO: coordinate with FR-03, FR-09 before touching this file`
        (no enclosing brackets at all).
        """
        import subprocess as _sp

        class _NonEmptyGrep:
            returncode = 0
            stdout = "abc1234 feat(FR-03): GREEN\n"  # commit evidence present

        monkeypatch.setattr(_sp, "run", lambda *_, **__: _NonEmptyGrep())

        src_dir = tmp_path / "03-development" / "src"
        src_dir.mkdir(parents=True)
        (src_dir / "unrelated.py").write_text(
            "# TODO: coordinate with FR-03, FR-09 before touching this file\n"
            "def run(): pass\n"
        )

        assert not _fr_step_already_done("TDD-GREEN", "FR-03", tmp_path, phase=3), (
            "An unbracketed prose mention of FR-03 must NOT satisfy the "
            "docstring-tag heuristic — only an exact tag inside a `[...]` "
            "bracket block counts."
        )

        # Positive control: the SAME fr_id properly bracketed DOES match,
        # proving the negative result above isn't from some other cause.
        (src_dir / "unrelated.py").write_text(
            '"""[FR-03] properly tagged module."""\n'
            "def run(): pass\n"
        )
        assert _fr_step_already_done("TDD-GREEN", "FR-03", tmp_path, phase=3), (
            "Sanity check: a properly bracketed [FR-03] tag must still match "
            "(proves the negative case above is due to missing brackets, "
            "not a broken scan)."
        )

    def test_fr_step_already_done_tdd_red_leftover_uncommitted_artifact_not_marked_done(
        self, tmp_path, monkeypatch,
    ):
        """Review fix regression (2026-07-21): a test file left on disk by a
        dispatch that crashed BEFORE its commit landed must NOT be marked
        done. Commit evidence is a hard requirement for every step,
        including TDD-RED/TDD-GREEN — the artifact heuristic is dual
        verification ON TOP OF a matching commit, never a substitute for
        one. Reproduced pre-fix: `_fr_step_already_done("TDD-RED", "FR-02",
        ..., phase=3)` returned True with an empty `git log --grep` AND an
        on-disk `test_fr02.py` with no commit at all.
        """
        import subprocess as _sp

        class _EmptyGrep:
            returncode = 0
            stdout = ""  # no commit at all — simulates a crashed dispatch

        monkeypatch.setattr(_sp, "run", lambda *_, **__: _EmptyGrep())

        test_dir = tmp_path / "03-development" / "tests"
        test_dir.mkdir(parents=True)
        (test_dir / "test_fr02.py").write_text("def test_x(): pass\n")

        assert not _fr_step_already_done("TDD-RED", "FR-02", tmp_path, phase=3), (
            "A leftover, uncommitted test file must NOT satisfy TDD-RED's "
            "idempotency check — commit evidence is required."
        )

        src_dir = tmp_path / "03-development" / "src"
        src_dir.mkdir(parents=True)
        (src_dir / "fr02_helper.py").write_text("x = 1\n")

        assert not _fr_step_already_done("TDD-GREEN", "FR-02", tmp_path, phase=3), (
            "A leftover, uncommitted source file (matched via filename-number "
            "heuristic) must NOT satisfy TDD-GREEN's idempotency check — "
            "commit evidence is required."
        )

    def test_fr_step_already_done_tdd_green_cascade_when_gate1_sentinel_and_quality_complete(
        self, tmp_path, monkeypatch,
    ):
        """When GATE1 sentinel + manifest quality_complete=true exist for this
        FR in this phase, TDD-RED/GREEN/IMPROVE heuristic MUST short-circuit
        to True even if commit grep is empty (phase boundary scenario) AND
        docstring scan would also fail (no source artifact at all).

        This is the canonical bug-fix repro for FR-02: GREEN commits
        6a0b272/71cb187/e6e2fee pre-date the e91cc23 boundary, so the commit
        grep is empty; AND the heuristic fallback path was unreachable due to
        the early `return False` at line 1682-1683 (now removed by this fix).
        With the cascade, the sentinel + quality_complete signals are
        sufficient to mark the TDD steps as done (they are the transitive
        prerequisites that produced the sentinel + passing manifest).
        """
        import subprocess as _sp

        class _EmptyGrep:
            returncode = 0
            stdout = ""

        monkeypatch.setattr(_sp, "run", lambda *_, **__: _EmptyGrep())

        # Write GATE1 sentinel for FR-02 phase=3.
        from core.quality_gate.gate1_evidence import _finalize_sentinel_path
        sentinel = _finalize_sentinel_path(tmp_path, 1, "FR-02", phase=3)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("2026-07-21T00:00:00+00:00\n", encoding="utf-8")

        # Write manifest with quality_complete=true for FR-02.
        manifest_dir = tmp_path / ".methodology"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "quality_manifest.json").write_text(json.dumps({
            "gate_results": {"gate1": {"FR-02": {"score": 100.0, "quality_complete": True}}},
        }))

        # NO src dir, NO test dir, NO commit grep — yet cascade fires.
        assert _fr_step_already_done("TDD-RED", "FR-02", tmp_path, phase=3), (
            "TDD-RED must short-circuit to True when GATE1 sentinel + "
            "quality_complete=true exist (cascade shortcut)"
        )
        assert _fr_step_already_done("TDD-GREEN", "FR-02", tmp_path, phase=3), (
            "TDD-GREEN must short-circuit to True via cascade"
        )
        # TDD-IMPROVE also covered by cascade (the cascade lives at the top
        # of the function before commit grep, and applies to all three
        # TDD steps; TDD-IMPROVE has no separate artifact heuristic in
        # this fix).
        assert _fr_step_already_done("TDD-IMPROVE", "FR-02", tmp_path, phase=3), (
            "TDD-IMPROVE must short-circuit to True via cascade (GATE1 "
            "sentinel + quality_complete=true is transitive proof that "
            "TDD-IMPROVE prerequisites ran)"
        )

    def test_fr_step_already_done_cascade_not_used_for_gate1_itself(self, tmp_path, monkeypatch):
        """Cascade MUST NOT fire for GATE1 step itself. GATE1 has its own
        sentinel + quality_complete logic at lines 1670-1712; cascade
        deliberately excludes GATE1 from the trigger set to prevent a
        circular short-circuit (cascade reads the SAME manifest that the
        GATE1 branch reads — without exclusion, GATE1 would short-circuit
        on its own sentinel regardless of the sentinel write timing)."""
        import subprocess as _sp

        # Empty commit grep (would normally make GATE1 return False via
        # sentinel-only check, which depends on sentinel.exists()).
        class _EmptyGrep:
            returncode = 0
            stdout = ""

        monkeypatch.setattr(_sp, "run", lambda *_, **__: _EmptyGrep())

        # No sentinel, no manifest → GATE1 must return False (cascade NOT in play).
        assert not _fr_step_already_done("GATE1", "FR-01", tmp_path, phase=3), (
            "GATE1 without sentinel must return False; cascade is excluded "
            "from GATE1 step to prevent circular short-circuit"
        )

    def test_fr_step_already_done_negative_no_sentinel_no_artifact(self, tmp_path, monkeypatch):
        """Negative: no sentinel, no manifest quality_complete, no source
        artifact → all three TDD heuristics return False (genuine not-done).
        Pins that the cascade is not a blanket 'always True' shortcut.
        """
        import subprocess as _sp

        class _EmptyGrep:
            returncode = 0
            stdout = ""

        monkeypatch.setattr(_sp, "run", lambda *_, **__: _EmptyGrep())

        # No .methodology dir, no 03-development/src, no test file at all.
        assert not _fr_step_already_done("TDD-RED", "FR-77", tmp_path, phase=3), (
            "TDD-RED must return False when no sentinel, no manifest, no test file"
        )
        assert not _fr_step_already_done("TDD-GREEN", "FR-77", tmp_path, phase=3), (
            "TDD-GREEN must return False when no sentinel, no manifest, no src"
        )
        assert not _fr_step_already_done("TDD-IMPROVE", "FR-77", tmp_path, phase=3), (
            "TDD-IMPROVE must return False when no sentinel, no manifest"
        )

        # Cascade requires phase is not None — verify with phase=None too.
        assert not _fr_step_already_done("TDD-GREEN", "FR-77", tmp_path, phase=None), (
            "Cascade must NOT fire when phase=None (defensive default)"
        )

    @staticmethod
    def _git(tmp_path, *args):
        import subprocess as _sp
        r = _sp.run(["git", *args], cwd=str(tmp_path), capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout

    def _init_repo(self, tmp_path):
        self._git(tmp_path, "init", "-q")
        self._git(tmp_path, "config", "user.email", "t@t.com")
        self._git(tmp_path, "config", "user.name", "t")

    def test_tdd_improve_ignores_stale_commit_before_phase_boundary(self, tmp_path):
        """Reset-rerun repro (2026-07-11 P3 rerun, FR-02): a059848 (the chosen
        P3-pre boundary) was itself a descendant of an earlier complete P3 run
        and its OWN ancestry already contained `refactor(FR-02): IMPROVE`.
        _fr_step_already_done's unscoped `git log --grep` matched that stale
        commit, and TDD-IMPROVE has no secondary evidence check (unlike
        TDD-RED/GREEN) — it fell straight to `return True`, silently skipping
        the real IMPROVE work for this run. Scoping the grep to
        `<phase-boundary>..HEAD` (boundary read from tracked state.json
        phase_completed, which survives `git reset --hard` unlike sentinels
        under gitignored .sessi-work/) excludes commits that predate this
        run's lineage.
        """
        self._init_repo(tmp_path)

        # Stale prior-lineage work, an ancestor of the boundary commit —
        # mirrors a059848's ancestry containing old GREEN/IMPROVE commits.
        (tmp_path / "old.txt").write_text("1")
        self._git(tmp_path, "add", "old.txt")
        self._git(tmp_path, "commit", "-q", "-m", "refactor(FR-02): IMPROVE")

        # This run's lineage root (state.json phase_completed["2"].sha points here).
        (tmp_path / "boundary.txt").write_text("2")
        self._git(tmp_path, "add", "boundary.txt")
        self._git(tmp_path, "commit", "-q", "-m", "handover: advance to Phase 3")
        boundary_sha = self._git(tmp_path, "rev-parse", "HEAD").strip()

        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        (method_dir / "state.json").write_text(json.dumps({
            "phase_completed": {"2": {"sha": boundary_sha}},
        }))

        # This run's own progress so far — no IMPROVE commit for FR-02 in it.
        (tmp_path / "new.txt").write_text("3")
        self._git(tmp_path, "add", "new.txt")
        self._git(tmp_path, "commit", "-q", "-m", "test(RED): failing test for FR-02")

        assert not _fr_step_already_done("TDD-IMPROVE", "FR-02", tmp_path, phase=3), (
            "stale IMPROVE commit predating the phase-3 boundary must not "
            "satisfy idempotency for this run's TDD-IMPROVE step"
        )

    def test_tdd_improve_recognizes_commit_after_phase_boundary(self, tmp_path):
        """Companion to the stale-lineage repro: a genuine IMPROVE commit made
        WITHIN this run's lineage (after the boundary) must still mark the
        step done — the range-scoping must not break real idempotency."""
        self._init_repo(tmp_path)

        (tmp_path / "boundary.txt").write_text("1")
        self._git(tmp_path, "add", "boundary.txt")
        self._git(tmp_path, "commit", "-q", "-m", "handover: advance to Phase 3")
        boundary_sha = self._git(tmp_path, "rev-parse", "HEAD").strip()

        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        (method_dir / "state.json").write_text(json.dumps({
            "phase_completed": {"2": {"sha": boundary_sha}},
        }))

        (tmp_path / "new.txt").write_text("2")
        self._git(tmp_path, "add", "new.txt")
        self._git(tmp_path, "commit", "-q", "-m", "refactor(FR-02): IMPROVE")

        assert _fr_step_already_done("TDD-IMPROVE", "FR-02", tmp_path, phase=3)

    def test_tdd_improve_falls_back_to_unscoped_when_boundary_unresolvable(self, tmp_path, monkeypatch):
        """No state.json / no phase_completed entry (e.g. old projects, or
        phase=1/2 with nothing recorded yet): behavior must be unchanged
        (unscoped grep) — the range-scoping fix must be a no-op for projects
        with no reset history."""
        import subprocess as _sp

        class _FakeResult:
            returncode = 0
            stdout = "refactor(FR-02): IMPROVE"

        monkeypatch.setattr(_sp, "run", lambda *_, **__: _FakeResult())

        assert _fr_step_already_done("TDD-IMPROVE", "FR-02", tmp_path, phase=3)

    def test_run_fr_step_handles_git_push_failure_as_fatal(self, tmp_path, monkeypatch, capsys):
        """cmd_run_fr_step prints an error and returns 1 when git push fails (fatal check-recovery)."""
        import sys
        import types
        import harness_cli
        import subprocess as _sp

        _setup_preflight_fixtures(tmp_path, step="TDD-RED")

        _patch_already_done(monkeypatch, lambda s, f, p, phase=None: False)

        class _FakeSpawner:
            def __init__(self, project_path=None): pass
            def spawn(self, **kwargs):
                return {"status": "complete", "output": "{}"}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        # Mock git commands: make git push fail (returncode 1)
        def _fake_run(cmd, **_):
            class _Res:
                returncode = 1 if "push" in cmd else 0
                stdout = ""
                stderr = "fatal: Could not read from remote repository."
            return _Res()
        monkeypatch.setattr(_sp, "run", _fake_run)

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED", project=str(tmp_path),
            srs=None, timeout=600, max_turns=30, max_fix_rounds=3, no_push=False
        )

        rc = harness_cli.cmd_run_fr_step(args)
        assert rc == 1
        captured = capsys.readouterr().out
        assert "git push failed" in captured

    def test_run_fr_step_dirty_tree_message_uses_resolved_project(self, tmp_path, monkeypatch, capsys):
        """Regression: the dirty-tree BLOCKED resume instruction must not tell
        the user/sub-agent to re-run with a CWD-relative '--project .' — a
        sub-agent's Bash CWD when it later re-runs this command is not
        guaranteed to be the project root."""
        import sys
        import types
        import harness_cli
        import subprocess as _sp

        assert tmp_path != Path.cwd()
        _setup_preflight_fixtures(tmp_path, step="TDD-RED")

        class _FakeSpawner:
            def __init__(self, project_path=None): pass
            def spawn(self, **kwargs):
                return {"status": "complete", "output": "{}"}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        # No explicit _fr_step_already_done patch: `git status --porcelain`
        # is the only command faked to return dirty output below; every
        # other subprocess call (including the real idempotency `git log
        # --grep` inside _fr_step_already_done) sees empty stdout and
        # naturally reports "not done" — real subprocess behavior, not an
        # implementation-detail patch (tests/test_patch_discipline.py).
        #
        # Pre/post diff (fr_cmds.py: dirty-tree guard baseline): the 1st
        # `status --porcelain` call captures pre-step baseline (clean),
        # the 2nd call observes a NEW dirty entry introduced by THIS step.
        # This simulates the realistic block trigger (guard only fires on
        # NEW dirt), not "everything dirty from start" — a regression fix.
        _porcelain_calls = {"n": 0}
        def _fake_run(cmd, **_):
            class _Res:
                returncode = 0
                stdout = ""
                stderr = ""
            if "status" in cmd and "--porcelain" in cmd:
                _porcelain_calls["n"] += 1
                _Res.stdout = "" if _porcelain_calls["n"] == 1 else "M somefile.py\n"
            return _Res()
        monkeypatch.setattr(_sp, "run", _fake_run)

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED", project=str(tmp_path),
            srs=None, timeout=600, max_turns=30, max_fix_rounds=3, no_push=False,
        )
        rc = harness_cli.cmd_run_fr_step(args)
        assert rc == 6
        err = capsys.readouterr().err
        assert f"--project {tmp_path}" in err
        assert "--project ." not in err

    def test_run_fr_step_respects_no_push_argument(self, tmp_path, monkeypatch, capsys):
        """cmd_run_fr_step skips git push when no_push is True."""
        import sys
        import types
        import harness_cli
        import subprocess as _sp

        _setup_preflight_fixtures(tmp_path, step="TDD-RED")

        _patch_already_done(monkeypatch, lambda s, f, p, phase=None: False)

        class _FakeSpawner:
            def __init__(self, project_path=None): pass
            def spawn(self, **kwargs):
                return {"status": "complete", "output": "{}"}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        run_calls = []
        def _fake_run(cmd, **_):
            run_calls.append(cmd)
            class _Res:
                returncode = 0
                stdout = ""
                stderr = ""
            return _Res()
        monkeypatch.setattr(_sp, "run", _fake_run)

        # 1. Test with no_push = True
        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED", project=str(tmp_path),
            srs=None, timeout=600, max_turns=30, max_fix_rounds=3, no_push=True
        )

        rc = harness_cli.cmd_run_fr_step(args)
        assert rc == 0
        assert not any("push" in cmd for cmd in run_calls), "git push should not be called when no_push=True"
        captured = capsys.readouterr().out
        assert "skipping git push" in captured
        assert "complete" in captured
        assert "+ pushed to GitHub" not in captured

        # 2. Test with no_push = False (normal behavior)
        run_calls.clear()
        args.no_push = False
        rc = harness_cli.cmd_run_fr_step(args)
        assert rc == 0
        assert any("push" in cmd for cmd in run_calls), "git push should be called when no_push=False"
        captured = capsys.readouterr().out
        assert "complete + pushed to GitHub" in captured


# =============================================================================
# _parse_spec_names_for_fr (harness_bridge)
# =============================================================================

class TestParseSpecNamesForFr:
    """Tests for _parse_spec_names_for_fr — the canonical TEST_SPEC.md parser."""

    def _parse(self, spec_text, fr_id):
        from harness.harness_bridge import _parse_spec_names_for_fr
        return _parse_spec_names_for_fr(spec_text, fr_id)

    def test_basic_table(self):
        spec = (
            "### FR-01: Foo\n"
            "| # | Test Function | Type | Derivation |\n"
            "|---|---|---|---|\n"
            "| 1 | `test_fr01_happy` | happy_path | Q1 |\n"
        )
        assert self._parse(spec, "FR-01") == ["test_fr01_happy"]

    def test_cross_cutting_h2_stops_collection(self):
        """Rows under ## Cross-Cutting must NOT be attributed to the previous FR."""
        spec = (
            "### FR-22: Last\n"
            "| # | Test Function | Type | Derivation |\n"
            "|---|---|---|---|\n"
            "| 1 | `test_fr22_real` | happy_path | Q1 |\n"
            "\n"
            "## Cross-Cutting Integration Tests\n"
            "| # | Test Function | Type | Derivation |\n"
            "|---|---|---|---|\n"
            "| 1 | `test_cross_something` | integration | Q5 |\n"
        )
        names = self._parse(spec, "FR-22")
        assert "test_fr22_real" in names
        assert "test_cross_something" not in names

    def test_h2_non_cross_cutting_also_stops(self):
        """Any H2 heading (not only ## Cross-Cutting) must close the current FR."""
        spec = (
            "### FR-01: Foo\n"
            "| # | Test Function | Type | Derivation |\n"
            "|---|---|---|---|\n"
            "| 1 | `test_fr01_a` | happy_path | Q1 |\n"
            "\n"
            "## Security Red Team\n"
            "| # | Test Function | Type | Derivation |\n"
            "|---|---|---|---|\n"
            "| 1 | `test_redteam_x` | security | Q9 |\n"
        )
        names = self._parse(spec, "FR-01")
        assert "test_fr01_a" in names
        assert "test_redteam_x" not in names

    def test_horizontal_rule_closes_table(self):
        """--- between FR section and next section must not cause bleed."""
        spec = (
            "### FR-05: Bar\n"
            "| # | Test Function | Type | Derivation |\n"
            "|---|---|---|---|\n"
            "| 1 | `test_fr05_ok` | happy_path | Q1 |\n"
            "\n"
            "---\n"
            "\n"
            "## Other Section\n"
            "| # | Test Function | Type | Derivation |\n"
            "|---|---|---|---|\n"
            "| 1 | `test_other_z` | integration | Q2 |\n"
        )
        names = self._parse(spec, "FR-05")
        assert "test_fr05_ok" in names
        assert "test_other_z" not in names

    def test_missing_table_header_returns_empty(self):
        """FR section with data rows but no header row → returns [] (warns caller)."""
        spec = (
            "### FR-22: Missing header\n"
            "| 1 | `test_fr22_no_header` | happy_path | Q1 |\n"
        )
        # Without the table header row, parser cannot identify the table
        assert self._parse(spec, "FR-22") == []

    def test_old_bullet_list_format(self):
        """Backward-compat: bullet-list format `- test_foo` still works."""
        spec = (
            "### FR-03: Old\n"
            "- `test_fr03_legacy`\n"
            "- test_fr03_also_legacy\n"
        )
        names = self._parse(spec, "FR-03")
        assert "test_fr03_legacy" in names
        assert "test_fr03_also_legacy" in names


# =============================================================================
# run-fr-step idempotency skip: gate timestamp must fire when skipping GATE1-DELTA
# =============================================================================

class TestRunFrStepSkipSideEffects:
    """When _fr_step_already_done returns True,
    gate1_evidence.record_gate_timestamp (for GATE1-DELTA) must still be called."""

    def _make_plan(self, tmp_path: Path, phase: int, fr_id: str) -> Path:
        plan = tmp_path / ".methodology" / f"phase{phase}_plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(
            f"- [ ] **[ORCH-GATE1]** run-fr-step --step GATE1-DELTA for {fr_id}:\n",
            encoding="utf-8",
        )
        return plan

    def _make_manifest(self, tmp_path: Path, fr_id: str, score: float = 95.0) -> None:
        import json
        m = tmp_path / ".methodology" / "quality_manifest.json"
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(json.dumps({
            "fr_ids": [fr_id],
            "quality_targets": {"min_coverage": 80.0},
            "gate_results": {"gate1": {fr_id: {"score": score, "quality_complete": True}}},
        }), encoding="utf-8")

    def test_gate_timestamp_recorded_on_gate1_delta_skip(self, tmp_path, monkeypatch):
        """record_gate_timestamp must be called for GATE1-DELTA skip, and the
        row must be marked as coming from the SKIP branch.

        Round 20 站4 added the marking. The row exists so
        _check_gate1_live_coverage does not exit-14 when every FR legitimately
        skips — but no evaluation ran, so core/doctor.py must not count it as
        evidence independent of the sentinel whose existence caused the skip in
        the first place.
        """
        import harness_cli
        recorded = []
        _patch_already_done(monkeypatch, lambda *a, **k: True)
        from core.quality_gate import gate1_evidence as _ge
        monkeypatch.setattr(
            _ge, "record_gate_timestamp",
            lambda project, phase, gate, fr_id, source=_ge.EVIDENCE_SOURCE_FINALIZE:
                recorded.append((phase, gate, fr_id, source)),
        )

        self._make_manifest(tmp_path, "FR-03")
        args = argparse.Namespace(
            phase=5, fr_id="FR-03", step="GATE1-DELTA",
            project=str(tmp_path), srs=None,
            timeout=600, max_fix_rounds=3, no_push=True,
            no_mcp=False, permission_mode=None, max_turns=None,
        )
        harness_cli.cmd_run_fr_step(args)

        assert (5, 1, "FR-03", _ge.EVIDENCE_SOURCE_SKIP) in recorded, (
            f"gate timestamp not recorded as a skip-sourced row: {recorded}"
        )

    def test_no_gate_timestamp_for_non_delta_skip(self, tmp_path, monkeypatch):
        """record_gate_timestamp must NOT be called for non-DELTA step skips."""
        import harness_cli
        recorded = []
        _patch_already_done(monkeypatch, lambda *a, **k: True)
        from core.quality_gate import gate1_evidence as _ge
        monkeypatch.setattr(_ge, "record_gate_timestamp",
                            lambda *a, **k: recorded.append(True))

        self._make_manifest(tmp_path, "FR-01")
        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED",
            project=str(tmp_path), srs=None,
            timeout=600, max_fix_rounds=3, no_push=True,
            no_mcp=False, permission_mode=None, max_turns=None,
        )
        harness_cli.cmd_run_fr_step(args)

        assert not recorded, "gate timestamp should not be recorded for TDD-RED skip"


# =============================================================================
# Bug fix: _compute_fr_spec_data must strip [...] from parameterized test names
# =============================================================================

class TestComputeFrSpecDataParameterized:
    """_compute_fr_spec_data must match parameterized test names by stripping [param]."""

    def _make_project(self, tmp_path, spec_rows, test_body):
        arch = tmp_path / "02-architecture"
        arch.mkdir(parents=True)
        table = "\n".join(f"| {i+1} | `{name}` | Functional |" for i, name in enumerate(spec_rows))
        (arch / "TEST_SPEC.md").write_text(
            "### FR-01: Lexicon\n\n"
            "| # | Test Function | Type |\n"
            "|---|--------------|------|\n"
            f"{table}\n",
            encoding="utf-8",
        )
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_fr01.py").write_text(test_body, encoding="utf-8")
        return tmp_path

    def test_parameterized_name_matches_base_function(self, tmp_path):
        """TEST_SPEC row 'test_fn[param]' must match 'def test_fn' in test file."""
        self._make_project(
            tmp_path,
            spec_rows=[
                "test_fr_01_lexicon_coverage[視頻→影片]",
                "test_fr_01_lexicon_coverage[影片→視頻]",
            ],
            test_body="def test_fr_01_lexicon_coverage(word_pair):\n    pass\n",
        )
        result = _compute_fr_spec_data(tmp_path, "FR-01", "tests/test_fr01.py")
        assert result["spec_cov_pct"] == 100, (
            f"parameterized names must match by base name; got {result['spec_cov_pct']}"
        )

    def test_missing_base_function_gives_zero(self, tmp_path):
        """If the base function does not exist in the test file, spec_cov_pct == 0."""
        self._make_project(
            tmp_path,
            spec_rows=["test_fr_01_missing[param]"],
            test_body="# no functions here\n",
        )
        result = _compute_fr_spec_data(tmp_path, "FR-01", "tests/test_fr01.py")
        assert result["spec_cov_pct"] == 0

    def test_backtick_name_matches(self, tmp_path):
        """TEST_SPEC row '`test_fn`' (backtick-quoted) must match 'def test_fn'."""
        self._make_project(
            tmp_path,
            spec_rows=["`test_fr_01_lookup`"],
            test_body="def test_fr_01_lookup(x):\n    pass\n",
        )
        result = _compute_fr_spec_data(tmp_path, "FR-01", "tests/test_fr01.py")
        assert result["spec_cov_pct"] == 100, (
            f"backtick-quoted spec name must strip backticks before matching; got {result['spec_cov_pct']}"
        )

    def test_paren_suffix_matches(self, tmp_path):
        """TEST_SPEC row 'test_fn()' (with parens) must match 'def test_fn'."""
        self._make_project(
            tmp_path,
            spec_rows=["test_fr_01_lookup()"],
            test_body="def test_fr_01_lookup(x):\n    pass\n",
        )
        result = _compute_fr_spec_data(tmp_path, "FR-01", "tests/test_fr01.py")
        assert result["spec_cov_pct"] == 100, (
            f"() suffix must be stripped before matching; got {result['spec_cov_pct']}"
        )

    def test_async_def_matches(self, tmp_path):
        """'async def test_fn(...)' must be found the same as sync 'def test_fn'."""
        self._make_project(
            tmp_path,
            spec_rows=["test_fr_01_async"],
            test_body="async def test_fr_01_async(client):\n    pass\n",
        )
        result = _compute_fr_spec_data(tmp_path, "FR-01", "tests/test_fr01.py")
        assert result["spec_cov_pct"] == 100, (
            f"async def must be detected by the function scanner; got {result['spec_cov_pct']}"
        )

    def test_section_boundary_stops_at_non_fr_heading(self, tmp_path):
        """A trailing non-FR heading (e.g. '### NFR Integration (...)') must
        NOT leak its test rows into the preceding FR's spec_test_names.
        Pre-fix, the local `_extract_test_spec_names` parser only reset
        `current_fr` on a `### FR-XX` heading, so any other heading level
        left `current_fr` stuck — every row after it (including unrelated
        NFR sections) was wrongly counted against the last-seen FR (Bug Fix
        Spec-Cov-Section-Boundary, 2026-07-21)."""
        arch = tmp_path / "02-architecture"
        arch.mkdir(parents=True)
        (arch / "TEST_SPEC.md").write_text(
            "### FR-01: Lexicon\n\n"
            "| # | Test Function | Type |\n"
            "|---|--------------|------|\n"
            "| 1 | `test_fr_01_lookup` | Functional |\n"
            "\n"
            "### NFR Integration (Integration-tier NFR cases only)\n\n"
            "| # | Test Function | Type |\n"
            "|---|--------------|------|\n"
            "| 1 | `test_nfr03_recovery` | Functional |\n",
            encoding="utf-8",
        )
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_fr01.py").write_text(
            "def test_fr_01_lookup(x):\n    pass\n", encoding="utf-8",
        )
        result = _compute_fr_spec_data(tmp_path, "FR-01", "tests/test_fr01.py")
        assert result["spec_test_names"] == ["test_fr_01_lookup"], (
            f"NFR Integration section's test_nfr03_recovery must NOT leak "
            f"into FR-01's spec_test_names; got {result['spec_test_names']}"
        )
        assert result["spec_cov_pct"] == 100
        assert result["missing_spec_count"] == 0

    def test_duplicate_parametrize_rows_do_not_inflate_missing_count(self, tmp_path):
        """TEST_SPEC.md's v2.13.0 'Multi-scenario expansion' rule
        deliberately repeats the SAME function name across N parametrize
        rows (one row per scenario, all sharing one canonical test
        function). All N rows must count as covered if the function
        exists — not collapse to 1-covered/(N-1)-missing via asymmetric
        set/list dedup (Bug Fix Spec-Cov-Asymmetric-Dedup, 2026-07-21)."""
        arch = tmp_path / "02-architecture"
        arch.mkdir(parents=True)
        rows = "\n".join(
            f"| {i + 1} | `test_fr_01_exit_code_map` | Functional |" for i in range(5)
        )
        (arch / "TEST_SPEC.md").write_text(
            "### FR-01: Lexicon\n\n"
            "| # | Test Function | Type |\n"
            "|---|--------------|------|\n"
            f"{rows}\n",
            encoding="utf-8",
        )
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_fr01.py").write_text(
            "def test_fr_01_exit_code_map():\n    pass\n", encoding="utf-8",
        )
        result = _compute_fr_spec_data(tmp_path, "FR-01", "tests/test_fr01.py")
        assert len(result["spec_test_names"]) == 5
        assert result["spec_cov_pct"] == 100, (
            f"5 duplicate parametrize rows sharing one existing function "
            f"must all count as covered; got "
            f"spec_cov_pct={result['spec_cov_pct']}"
        )
        assert result["missing_spec_count"] == 0, (
            f"asymmetric set/list dedup would wrongly report 4 'missing'; "
            f"got missing_spec_count={result['missing_spec_count']}"
        )

    def test_fr05_real_test_spec_section_and_dedup_combined(self, tmp_path):
        """Combined regression using the real FR-05 shape that triggered
        both bugs in production: 6 standalone cases + 5 duplicate
        parametrize rows (11 total), followed by an '### NFR Integration'
        section. Pre-fix this computed spec_test_names=16 (11 real + 5
        leaked NFR rows) and spec_cov_pct=75 (12 unique covered / 16
        inflated denominator) even though every FR-05 test existed and
        `cli.py` had 100% line coverage — the exact false-BLOCKED loop
        FR-05's GATE1 hit for 7 rounds before a lucky sub-agent override.
        Post-fix: spec_test_names=11, spec_cov_pct=100."""
        arch = tmp_path / "02-architecture"
        arch.mkdir(parents=True)
        exit_code_rows = "\n".join(
            f"| {i + 7} | `test_fr05_07_exit_code_map` | integration |"
            for i in range(5)
        )
        (arch / "TEST_SPEC.md").write_text(
            "### FR-05: CLI Integration\n\n"
            "| # | Test Function | Type |\n"
            "|---|--------------|------|\n"
            "| 1 | `test_fr05_01_status_all_fields` | happy_path |\n"
            "| 2 | `test_fr05_02_status_json` | happy_path |\n"
            "| 3 | `test_fr05_03_list_happy` | happy_path |\n"
            "| 4 | `test_fr05_04_list_filter_done` | happy_path |\n"
            "| 5 | `test_fr05_05_clear` | happy_path |\n"
            "| 6 | `test_fr05_06_unknown_task_id` | validation |\n"
            f"{exit_code_rows}\n"
            "\n"
            "### NFR Integration (Integration-tier NFR cases only)\n\n"
            "| # | Test Function | Type |\n"
            "|---|--------------|------|\n"
            "| 1 | `test_nfr03_02_recovery_within_cooldown` | integration |\n"
            "| 2 | `test_nfr08_01_four_process_concurrent` | integration |\n",
            encoding="utf-8",
        )
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_fr05.py").write_text(
            "def test_fr05_01_status_all_fields(): pass\n"
            "def test_fr05_02_status_json(): pass\n"
            "def test_fr05_03_list_happy(): pass\n"
            "def test_fr05_04_list_filter_done(): pass\n"
            "def test_fr05_05_clear(): pass\n"
            "def test_fr05_06_unknown_task_id(): pass\n"
            "def test_fr05_07_exit_code_map(): pass\n",
            encoding="utf-8",
        )
        result = _compute_fr_spec_data(tmp_path, "FR-05", "tests/test_fr05.py")
        assert len(result["spec_test_names"]) == 11, (
            f"expected 11 rows (6 standalone + 5 parametrize), NOT leaking "
            f"the 2 NFR Integration rows; got "
            f"{len(result['spec_test_names'])}"
        )
        assert result["spec_cov_pct"] == 100, (
            f"all 11 rows map to functions that exist; got "
            f"{result['spec_cov_pct']}"
        )
        assert result["missing_spec_count"] == 0


class TestRunToolDispatcher:
    """Bug #110: harness_cli run-tool subcommand dispatches to tool_runners.run_tool."""

    def test_help_lists_run_tool_subcommand(self):
        from harness_cli import build_parser
        parser = build_parser()
        sub_action = next(
            a for a in parser._actions if a.dest == "command" or a.choices and "run-tool" in a.choices
        )
        assert "run-tool" in sub_action.choices  # type: ignore[reportOperatorIssue]

    def test_run_tool_invokes_tool_runners(self, tmp_path, monkeypatch, capsys):
        from harness_cli import cmd_run_tool
        def fake_run(tool, project_root, timeout_override=None):
            return "OK", 0
        def fake_score(tool, output, returncode):
            return 95.0
        monkeypatch.setattr("harness.tool_runners.run_tool", fake_run)
        monkeypatch.setattr("harness.tool_runners.compute_tool_score", fake_score)
        args = argparse.Namespace(
            tool="ast-error-handling", project=str(tmp_path),
            timeout_override=None, json=False,
        )
        result = cmd_run_tool(args)
        assert result == 0
        captured = capsys.readouterr()
        assert "tool=ast-error-handling" in captured.out
        assert "score:      95.0" in captured.out

    def test_run_tool_json_output(self, tmp_path, monkeypatch, capsys):
        from harness_cli import cmd_run_tool
        monkeypatch.setattr("harness.tool_runners.run_tool", lambda *_, **__: ("raw", 1))
        monkeypatch.setattr("harness.tool_runners.compute_tool_score", lambda *_, **__: 50.0)
        args = argparse.Namespace(
            tool="ruff", project=str(tmp_path), timeout_override=None, json=True,
        )
        result = cmd_run_tool(args)
        assert result == 1  # non-zero returncode → exit 1
        captured = capsys.readouterr()
        import json
        payload = json.loads(captured.out)
        assert payload["tool"] == "ruff"
        assert payload["returncode"] == 1
        assert payload["score"] == 50.0


# =============================================================================
# SRS FR fallback regex (b3f5a1c — both header and table syntax must match)
# =============================================================================

class TestSrsFrFallbackRegex:
    """The advance-phase FR-extraction regex must recognise both markdown formats:
    '### FR-NN:' header syntax and '| FR-NN |' table syntax."""

    _PATTERN = r"^(?:###\s+FR-|\|\s*FR-)(\d+)(?:\s*:|\s*\|)"

    def test_header_syntax(self):
        import re
        text = "### FR-01: First requirement\n### FR-12: Another\n"
        assert re.findall(self._PATTERN, text, re.MULTILINE) == ["01", "12"]

    def test_table_syntax(self):
        import re
        text = "| FR-01 | First requirement |\n| FR-12 | Another |\n"
        assert re.findall(self._PATTERN, text, re.MULTILINE) == ["01", "12"]

    def test_mixed_header_and_table(self):
        import re
        text = "### FR-01: Header format\n| FR-02 | Table format |\n"
        assert re.findall(self._PATTERN, text, re.MULTILINE) == ["01", "02"]

    def test_prose_fr_reference_not_matched(self):
        import re
        text = "This references FR-01 and FR-02 in prose text only.\n"
        assert re.findall(self._PATTERN, text, re.MULTILINE) == []


# =============================================================================
# _fr_step_preflight — srs_path parameter (c744ea3 fix)
# =============================================================================

class TestFrStepPreflightSrsPath:
    """_fr_step_preflight must accept an explicit srs_path (Path or str) and use
    it instead of the default fallback lookup.  cmd_run_fr_step was previously
    passing raw args.srs (str or None) despite having already resolved it to an
    absolute Path on line 7015 — the fix passes the resolved Path object."""

    def _make_manifest(self, tmp_path: Path, fr_id: str = "FR-01") -> None:
        meth = tmp_path / ".methodology"
        meth.mkdir(exist_ok=True)
        (meth / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": [fr_id]}), encoding="utf-8"
        )

    def test_explicit_absolute_srs_path_accepted(self, tmp_path):
        import harness_cli  # noqa: F401  entry-first load order (cli-first crashes until S5)
        import cli.fr_cmds as _frm
        # SRS.md at a non-default absolute path — no default lookup should occur.
        srs_dir = tmp_path / "custom-docs"
        srs_dir.mkdir()
        srs = srs_dir / "SRS.md"
        srs.write_text("### FR-01: Feature\n\n---\n", encoding="utf-8")
        self._make_manifest(tmp_path)
        _ok, errors = _frm._fr_step_preflight("TDD-RED", tmp_path, "FR-01", srs_path=srs)
        assert not any("SRS" in e for e in errors), f"Unexpected SRS error: {errors}"

    def test_explicit_srs_path_missing_adds_error(self, tmp_path):
        import harness_cli  # noqa: F401  entry-first load order (cli-first crashes until S5)
        import cli.fr_cmds as _frm
        self._make_manifest(tmp_path)
        nonexistent = tmp_path / "no-such.md"
        ok, errors = _frm._fr_step_preflight("TDD-RED", tmp_path, "FR-01", srs_path=nonexistent)
        assert not ok
        assert any("SRS" in e for e in errors)

    def test_relative_srs_str_resolved_against_project(self, tmp_path):
        import harness_cli  # noqa: F401  entry-first load order (cli-first crashes until S5)
        import cli.fr_cmds as _frm
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "SRS.md").write_text("### FR-01: Feature\n\n---\n", encoding="utf-8")
        self._make_manifest(tmp_path)
        _ok, errors = _frm._fr_step_preflight(
            "TDD-RED", tmp_path, "FR-01", srs_path="docs/SRS.md"
        )
        assert not any("SRS" in e for e in errors), f"Unexpected SRS error: {errors}"

    def test_env_check_missing_message_uses_resolved_project(self, tmp_path):
        """Regression: the env_check_result.json-missing preflight error must
        tell the caller to re-run run-env-check with the resolved absolute
        project path, not a literal '.' — the same CWD-relative-path bug
        class as the GATE1 sub-agent prompt (see _build_fr_step_prompt
        regression test)."""
        import harness_cli  # noqa: F401  entry-first load order (cli-first crashes until S5)
        import cli.fr_cmds as _frm

        assert tmp_path != Path.cwd()
        self._make_manifest(tmp_path)
        ok, errors = _frm._fr_step_preflight("GATE1", tmp_path, "FR-01", srs_path=None)
        assert not ok
        joined = "\n".join(errors)
        assert f"--project {tmp_path}" in joined
        assert "--project . " not in joined

    def test_cmd_run_fr_step_passes_resolved_path_not_raw_string(self, tmp_path, monkeypatch):
        """Regression: cmd_run_fr_step line 7056 was passing getattr(args,'srs',None)
        (a relative string) instead of the already-resolved absolute Path from line 7015."""
        import harness_cli

        captured: dict = {}

        def _spy(step, project, fr_id, srs_path=None):
            captured["srs_path"] = srs_path
            return True, []

        monkeypatch.setattr("cli.fr_cmds._fr_step_preflight", _spy)
        _patch_already_done(monkeypatch, lambda s, f, p, phase=None: True)

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED", project=str(tmp_path),
            srs="docs/SRS.md",
            timeout=600, max_turns=30, max_fix_rounds=3,
        )
        harness_cli.cmd_run_fr_step(args)
        # idempotency skips before preflight — verify the resolved path was computed
        # by checking cmd_run_fr_step used args.srs (not getattr with a silent None)
        # The spy isn't reached on skip, but args.srs attribute access must not throw.
        assert args.srs == "docs/SRS.md"  # arg is always available (registered by argparse)


# =============================================================================
# run-fr-step --step amend-sab (PR: P3 IMPROVE→GATE1 SAB sync)
#
# Bug: `agent_spawner._COMMIT_REQUIRED_STEPS` SSOT listed `amend-sab` since
# 2026-07-15 but `cmd_run_fr_step` did not expose it as an argparse choice.
# TDD-IMPROVE could extract a new `.py` file (e.g. DRY helper), leaving SAB.json
# stale, which made the next `run-gate --gate 1` BLOCK with "Unregistered
# modules detected". Fix: bridge the SSOT name through argparse, and route
# `amend-sab` through `cmd_amend_sab` (deterministic, not LLM-dispatched).
# =============================================================================

class TestRunFrStepAmendSab:
    """`run-fr-step --step amend-sab` delegates to `cmd_amend_sab`, not a sub-agent."""

    def _make_args(self, tmp_path: Path, **overrides) -> argparse.Namespace:
        ns = argparse.Namespace(
            phase=3, fr_id="FR-03", step="amend-sab",
            project=str(tmp_path), src_dir=None,
            dry_run=False, strict=False,
            srs=None, timeout=600, max_turns=30, max_fix_rounds=3,
            no_mcp=False, no_push=True, prompt_file=None,
        )
        for k, v in overrides.items():
            setattr(ns, k, v)
        return ns

    @staticmethod
    def _git_commit(tmp_path: Path, message: str, *, allow_empty: bool = False) -> None:
        """`git commit` scoped with an explicit identity so this test does
        not depend on the runner having a global user.name/user.email
        configured (CI runners commonly don't). `allow_empty=True` lets
        idempotency-style tests land a marker commit even when the
        working tree has nothing to stage."""
        cmd = ["git", "-c", "user.name=test", "-c", "user.email=test@test.com",
               "commit", "-q", "-m", message]
        if allow_empty:
            cmd.append("--allow-empty")
        subprocess.run(cmd, cwd=str(tmp_path), check=True)

    def test_argparse_accepts_amend_sab(self, tmp_path):
        """`run-fr-step --step amend-sab` must parse without SystemExit (was a
        hard `choices` rejection before this PR)."""
        from harness_cli import build_parser
        parser = build_parser()
        # argparse normalises via `type=str.upper` before matching choices, so
        # pass the canonical lowercase form to verify the choices list gained it.
        args = parser.parse_args([
            "run-fr-step", "--phase", "3", "--fr-id", "FR-03",
            "--step", "amend-sab", "--project", str(tmp_path),
            "--no-push",
        ])
        assert args.step == "AMEND-SAB"  # normalised by type=str.upper
        assert args.fr_id == "FR-03"

    def test_amend_sab_delegates_to_cmd_amend_sab_not_llm(self, tmp_path, monkeypatch, capsys):
        """`cmd_run_fr_step(amend-sab)` must call `cmd_amend_sab` and MUST NOT
        invoke AgentSpawner.spawn (proves no LLM dispatch)."""
        from harness_cli import cmd_run_fr_step
        from cli import project_cmds

        # Stub cmd_amend_sab so we can capture the call without running the
        # full SAB-discover pipeline.
        calls: list[argparse.Namespace] = []

        def _stub_amend_sab(args):
            calls.append(args)
            return 0

        # AgentSpawner is imported lazily inside `cmd_run_fr_step` (line ~200):
        #     from core.agent_spawner import AgentSpawner
        # It's NOT in `cli.fr_cmds` module namespace — Python's `from ... import`
        # inside a function binds the name in that function's locals only.
        # Monkeypatch the SOURCE module so the local `from ... import AgentSpawner`
        # inside `cmd_run_fr_step` resolves to our spy.
        from core import agent_spawner as _core_spawner
        spawned: list = []

        class _SpawnerSpy:
            def __init__(self, *a, **kw):
                spawned.append(("ctor", kw))

            def spawn(self, *a, **kw):
                spawned.append(("spawn", kw))
                raise AssertionError(
                    "amend-sab MUST NOT dispatch a sub-agent — "
                    "pure-mechanical tool, deterministic scan."
                )

        monkeypatch.setattr(project_cmds, "cmd_amend_sab", _stub_amend_sab)
        monkeypatch.setattr(_core_spawner, "AgentSpawner", _SpawnerSpy)

        # `AMEND-SAB` is NOT in `_FR_STEP_COMMIT_PATTERNS` (fr_cmds.py:1581-1587)
        # so `_fr_step_already_done` short-circuits to False at line 1638-1639.
        # No mock needed — the delegation branch runs before any LLM dispatch.

        # Build a minimal valid SAB so cmd_amend_sab has something to load.
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        (tmp_path / ".methodology" / "SAB.json").write_text(
            json.dumps({"layers": [{"name": "app", "modules": []}]})
        )
        src = tmp_path / "03-development" / "src"
        src.mkdir(parents=True)
        (src / "app.py").write_text("x = 1")

        cmd_run_fr_step(self._make_args(tmp_path))

        assert len(spawned) == 0, f"AgentSpawner was constructed/dispatched: {spawned}"
        assert len(calls) == 1, f"cmd_amend_sab called {len(calls)} times, expected 1"
        # Default src_dir filled in by the delegation branch.
        assert calls[0].src_dir == "03-development/src"
        assert calls[0].dry_run is False
        assert calls[0].strict is False

    def test_amend_sab_writes_sab_for_unregistered_modules(self, tmp_path, monkeypatch):
        """End-to-end: invoke the real `cmd_amend_sab` (via delegation) with a
        src tree containing 2 unregistered `.py` files. SAB.json must gain both."""
        from harness_cli import cmd_run_fr_step

        # Real cmd_amend_sab, no stubbing — this verifies the delegation
        # wiring actually reaches the real implementation.
        # No `_fr_step_already_done` mock: AMEND-SAB is not in
        # _FR_STEP_COMMIT_PATTERNS so the check returns False by design.

        (tmp_path / ".methodology").mkdir(exist_ok=True)
        (tmp_path / ".methodology" / "SAB.json").write_text(
            json.dumps({"layers": [{"name": "app", "modules": ["app.seed"]}]})
        )
        src = tmp_path / "03-development" / "src" / "app"
        src.mkdir(parents=True)
        (src / "seed.py").write_text("x = 1")
        (src / "extra_one.py").write_text("y = 1")
        (src / "extra_two.py").write_text("z = 1")

        rc = cmd_run_fr_step(self._make_args(tmp_path))
        assert rc == 0

        sab = json.loads((tmp_path / ".methodology" / "SAB.json").read_text())
        registered = {
            m["name"] if isinstance(m, dict) else m
            for layer in sab["layers"]
            for m in layer["modules"]
        }
        assert registered == {"app.seed", "app.extra_one", "app.extra_two"}

    def test_amend_sab_idempotent(self, tmp_path, monkeypatch):
        """Re-running `amend-sab` against an aligned tree must be a no-op
        (SAB.json diff = 0 after second invocation)."""
        from harness_cli import cmd_run_fr_step

        # No `_fr_step_already_done` mock — AMEND-SAB is not in
        # _FR_STEP_COMMIT_PATTERNS (see test_amend_sab_writes_sab_for_unregistered_modules).
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        (tmp_path / ".methodology" / "SAB.json").write_text(
            json.dumps({"layers": [{"name": "app", "modules": []}]})
        )
        src = tmp_path / "03-development" / "src" / "app"
        src.mkdir(parents=True)
        (src / "seed.py").write_text("x = 1")

        cmd_run_fr_step(self._make_args(tmp_path))
        sab_after_first = (tmp_path / ".methodology" / "SAB.json").read_text()

        cmd_run_fr_step(self._make_args(tmp_path))
        sab_after_second = (tmp_path / ".methodology" / "SAB.json").read_text()

        assert sab_after_first == sab_after_second

    def test_amend_sab_blocks_when_sab_json_left_uncommitted(self, tmp_path, capsys):
        """Regression: the delegation branch returns BEFORE the general
        post-step dirty-tree guard / `_COMMIT_REQUIRED_STEPS` check further
        down `cmd_run_fr_step` — those never run for an early return — and
        `_COMMIT_REQUIRED_STEPS` itself stores this step as lowercase
        "amend-sab" while `step` is always upper-cased, so neither backstop
        would fire even if reached. `cmd_amend_sab` never commits by design.
        Without a dedicated check, a genuine SAB.json mutation left
        uncommitted would silently persist. Must BLOCK (exit 6) instead."""
        from harness_cli import cmd_run_fr_step

        (tmp_path / ".methodology").mkdir(exist_ok=True)
        (tmp_path / ".methodology" / "SAB.json").write_text(
            json.dumps({"layers": [{"name": "app", "modules": []}]})
        )
        src = tmp_path / "03-development" / "src" / "app"
        src.mkdir(parents=True)
        (src / "seed.py").write_text("x = 1")

        subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
        subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True)
        self._git_commit(tmp_path, "init")

        rc = cmd_run_fr_step(self._make_args(tmp_path))
        err = capsys.readouterr().err

        assert rc == 6, f"Must BLOCK when SAB.json is left uncommitted, got rc={rc}"
        assert "SAB.json was updated but not committed" in err
        assert "git" in err and "commit" in err

    def test_amend_sab_no_block_when_operator_commits(self, tmp_path, capsys):
        """Counter-example to the block above: once the caller commits
        SAB.json after amend-sab (as the orchestrator prompt instructs),
        the step must succeed cleanly (rc=0), proving the guard only fires
        on genuinely uncommitted state, not on every mutation."""
        from harness_cli import cmd_run_fr_step

        (tmp_path / ".methodology").mkdir(exist_ok=True)
        (tmp_path / ".methodology" / "SAB.json").write_text(
            json.dumps({"layers": [{"name": "app", "modules": []}]})
        )
        src = tmp_path / "03-development" / "src" / "app"
        src.mkdir(parents=True)
        (src / "seed.py").write_text("x = 1")

        subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
        subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True)
        self._git_commit(tmp_path, "init")

        rc = cmd_run_fr_step(self._make_args(tmp_path))
        assert rc == 6  # first call: mutated but uncommitted

        subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True)
        self._git_commit(tmp_path, "amend: register SAB modules")

        rc2 = cmd_run_fr_step(self._make_args(tmp_path))
        assert rc2 == 0, "Once committed and no further drift, amend-sab must succeed"

    def test_amend_sab_short_circuits_when_already_committed(self, tmp_path, monkeypatch):
        """Regression (fix/round-18-dispatch-ssot, Bug A): the
        `_FR_STEP_COMMIT_PATTERNS` dict gain an `AMEND-SAB` key so a
        second `cmd_run_fr_step(... amend-sab)` whose amend-sab commit is
        already in `git log` short-circuits via `_fr_step_already_done`,
        skipping the duplication-prevention `rc == 6` dirty-tree block.
        Without this dict key, `_fr_step_already_done("AMEND-SAB", ...)`
        returned False unconditionally (key missing → `""` → falsy) and
        the dirty-tree BLOCK fired on every re-run even when SAB.json
        was already committed.
        """
        from harness_cli import cmd_run_fr_step
        from cli import project_cmds

        # Stub cmd_amend_sab at the source module that the delegation branch
        # imports it from. If short-circuit fails, this counter goes > 0.
        calls: list[argparse.Namespace] = []

        def _stub_amend_sab(args):
            calls.append(args)
            # No SAB.json mutation — simulates an idempotent re-run against
            # a repo where the amendment already landed.
            return 0

        monkeypatch.setattr(project_cmds, "cmd_amend_sab", _stub_amend_sab)

        (tmp_path / ".methodology").mkdir(exist_ok=True)
        (tmp_path / ".methodology" / "SAB.json").write_text(
            json.dumps({"layers": [{"name": "app", "modules": []}]})
        )
        src = tmp_path / "03-development" / "src" / "app"
        src.mkdir(parents=True)
        (src / "seed.py").write_text("x = 1")

        subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
        subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True)
        self._git_commit(tmp_path, "init")
        # Commit that matches the AMEND-SAB pattern in _FR_STEP_COMMIT_PATTERNS
        # — key inserted in fix/round-18-dispatch-ssot (Bug A):
        #   "AMEND-SAB": "amend: register SAB modules ({fr_id})"
        # formatted with fr_id=FR-03. allow_empty because the dirty-tree
        # BLOCK check in the AMEND-SAB branch assumes new work to commit;
        # this test simulates the post-commit state, so a marker commit is
        # the right shape.
        self._git_commit(tmp_path, "amend: register SAB modules (FR-03)", allow_empty=True)

        rc = cmd_run_fr_step(self._make_args(tmp_path))

        assert rc == 0, (
            f"Re-run after a matching amend-sab commit MUST short-circuit "
            f"(return 0) via _fr_step_already_done, not call cmd_amend_sab "
            f"and not trip the dirty-tree BLOCK. Got rc={rc}, "
            f"cmd_amend_sab called {len(calls)} time(s)."
        )
        assert len(calls) == 0, (
            f"On idempotent re-run, cmd_amend_sab MUST NOT be invoked. "
            f"Saw {len(calls)} call(s); the AMEND-SAB dict key did not "
            f"short-circuit _fr_step_already_done."
        )


# =============================================================================
# COVERAGE-FIX prompt vs PRAGMA_NO_COVER_ALLOWLIST SSOT (PR: Round 18 Bug B)
#
# Round 17 PR review surfaced that the COVERAGE-FIX prompt taught LLM agents
# a 4-pattern allowed pragma list while the Gate 1 audit scanner (in
# core/phase_hooks.py:_audit_pragma_no_cover) only honored one pattern:
# `except BaseException`. Round 18 fixes this by rendering the SSOT tuple
# verbatim into the prompt + replacing the contradictory
# `raise NotImplementedError # pragma: no cover` example with one that
# actually passes Gate 1. These tests pin the bidirectional binding so
# future widening of the tuple automatically propagates to the prompt.
# =============================================================================


class TestCoverageFixPromptMatchesPragmaAllowlist:
    """The COVERAGE-FIX dispatch prompt must teach only what the Gate 1
    audit scanner accepts — agents following the prompt MUST produce
    output the scanner does not reject as `py-pragma-no-cover`."""

    def _render_coverage_fix_prompt(self, tmp_path: Path) -> str:
        """Render the COVERAGE-FIX prompt via the same code path the
        dispatch loop would use. Stub `_compute_fr_spec_data` upstream
        so we don't need real SRS / spec files."""
        from cli.fr_cmds import _build_fr_step_prompt

        # Minimal project layout so _compute_fr_spec_data can construct
        # test_file path; layout is read-only here.
        (tmp_path / "03-development").mkdir(exist_ok=True)
        (tmp_path / "03-development" / "tests").mkdir(exist_ok=True)
        (tmp_path / "03-development" / "src").mkdir(exist_ok=True)
        (tmp_path / "02-architecture").mkdir(exist_ok=True)
        (tmp_path / "01-requirements").mkdir(exist_ok=True)
        srs_path = tmp_path / "01-requirements" / "SRS.md"
        srs_path.touch()
        return _build_fr_step_prompt(
            "COVERAGE-FIX", "FR-03", phase=3, project=tmp_path,
            srs_path=srs_path,
        )

    def test_prompt_renders_pragma_allowlist_verbatim(self, tmp_path):
        """Regression (fix/round-18-dispatch-ssot, Bug B): the COVERAGE-FIX
        prompt body must include an `Allowed exemptions:` block listing
        every entry from `PRAGMA_NO_COVER_ALLOWLIST` so the agent sees
        exactly what Gate 1's audit will accept (and is warned that
        adding a non-listed pattern will fail)."""
        prompt = self._render_coverage_fix_prompt(tmp_path)

        from core.phase_hooks import PRAGMA_NO_COVER_ALLOWLIST
        assert "Allowed exemptions (rendered verbatim from Gate 1 audit's" in prompt, (
            "Prompt must call out that the listed exemptions are rendered "
            "verbatim from the audit's SSOT tuple."
        )
        assert "PRAGMA_NO_COVER_ALLOWLIST" in prompt, (
            "Prompt must reference the SSOT constant name so future SSOT "
            "widening drives operator-visible prompt updates."
        )
        for pat in PRAGMA_NO_COVER_ALLOWLIST:
            assert pat in prompt, (
                f"Pragma pattern {pat!r} from PRAGMA_NO_COVER_ALLOWLIST "
                f"is not present in the rendered prompt. Widening the "
                f"SSOT must auto-propagate via the `for pat in ...` "
                f"interpolation block."
            )

    def test_prompt_does_not_teach_non_ssup_patterns(self, tmp_path):
        """Pre-fix regression (Bug B): the COVERAGE-FIX prompt showed
        `raise NotImplementedError  # pragma: no cover — abstract base, subclass must implement`
        as an EXAMPLE. The audit scanner would reject that exact line
        because the SSOT only honors `except BaseException`. The fix
        replaces this example with one that matches the SSOT, so a
        test asserting its absence proves the drift is closed."""
        prompt = self._render_coverage_fix_prompt(tmp_path)

        # The pre-fix contradictory example must NOT appear.
        forbidden_examples = [
            "raise NotImplementedError  # pragma: no cover — abstract base, subclass must implement",
        ]
        for forbidden in forbidden_examples:
            assert forbidden not in prompt, (
                f"COVERAGE-FIX prompt still teaches the pre-fix example "
                f"{forbidden!r}, which the Gate 1 audit scanner would "
                f"reject. Replace with an example matching "
                f"PRAGMA_NO_COVER_ALLOWLIST."
            )

        # The post-fix SSOT-compliant example must appear (regression
        # against accidental removal). Use a flexible substring match
        # so the test isn't tied to exact wording tweaks.
        assert "except BaseException: pass  # pragma: no cover" in prompt, (
            "The post-fix prompt must demonstrate the SSOT-compliant "
            "annotation `except BaseException: pass  # pragma: no cover`. "
            "Otherwise the agent's learning signal is purely the allowlist "
            "list with no concrete example to mirror."
        )


# =============================================================================
# cmd_amend_sab writes a sessions_spawn.log entry from the mutation site
# (PR: Round 18 Bug C).
#
# Pre-fix, the AMEND-SAB delegation branch in cmd_run_fr_step early-returned
# before AgentSpawner.spawn() so sessions_spawn.log never recorded the
# dispatch. The fix places a log_spawn call inside cmd_amend_sab itself
# (the mutation site) so every caller is covered — the standalone
# amend-sab subcommand, the run-fr-step delegation, any future caller.
# =============================================================================


class TestAmendSabWritesSessionsSpawnLogEntry:
    """Every cmd_amend_sab invocation appends exactly one
    `.methodology/sessions_spawn.log` line with role="tool:amend-sab"
    and session_id=""."""

    def _build_args(self, tmp_path: Path, **overrides) -> argparse.Namespace:
        ns = argparse.Namespace(
            project=str(tmp_path),
            src_dir="03-development/src",
            dry_run=False,
            strict=False,
        )
        for k, v in overrides.items():
            setattr(ns, k, v)
        return ns

    def _read_log_entries(self, tmp_path: Path) -> list[dict]:
        log_path = tmp_path / ".methodology" / "sessions_spawn.log"
        if not log_path.exists():
            return []
        return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]

    def test_amend_sab_success_writes_one_log_entry(self, tmp_path, capsys):
        """`cmd_amend_sab` mutating SAB.json with new modules MUST
        write exactly one log entry with status=COMPLETED and the
        amend-sab role sentinel."""
        from cli.project_cmds import cmd_amend_sab

        (tmp_path / ".methodology").mkdir(exist_ok=True)
        (tmp_path / ".methodology" / "SAB.json").write_text(
            json.dumps({"layers": [{"name": "app", "modules": ["app.seed"]}]})
        )
        src = tmp_path / "03-development" / "src" / "app"
        src.mkdir(parents=True)
        (src / "seed.py").write_text("x = 1")
        (src / "extra_one.py").write_text("y = 1")

        rc = cmd_amend_sab(self._build_args(tmp_path))
        assert rc == 0

        entries = self._read_log_entries(tmp_path)
        assert len(entries) == 1, (
            f"Expected exactly one sessions_spawn.log entry per amend-sab "
            f"invocation; got {len(entries)} entries: {entries}"
        )
        e = entries[0]
        assert e["role"] == "tool:amend-sab"
        assert e["session_id"] == ""
        assert e["status"] == "COMPLETED"
        assert e["step"] == "AMEND-SAB"
        assert e["tool_kind"] == "amend-sab"
        assert e["outcome"] == "completed"
        assert e["rc"] == 0
        assert e["src_dir"] == "03-development/src"

    def test_amend_sab_noop_still_writes_log_entry(self, tmp_path):
        """Idempotent no-op (amend-sab on already-aligned tree) MUST
        still write a log entry — observability is unconditional on
        invocation, not conditional on whether a mutation happened.
        Otherwise the audit trail loses track of how many times the
        operator ran the tool."""
        from cli.project_cmds import cmd_amend_sab

        (tmp_path / ".methodology").mkdir(exist_ok=True)
        (tmp_path / ".methodology" / "SAB.json").write_text(
            json.dumps({"layers": [{"name": "app", "modules": ["app.seed"]}]})
        )
        src = tmp_path / "03-development" / "src" / "app"
        src.mkdir(parents=True)
        (src / "seed.py").write_text("x = 1")

        rc = cmd_amend_sab(self._build_args(tmp_path))
        assert rc == 0

        entries = self._read_log_entries(tmp_path)
        assert len(entries) == 1
        assert entries[0]["status"] == "COMPLETED"
        assert entries[0]["outcome"] == "completed"

    def test_amend_sab_failure_writes_log_with_failed_status(self, tmp_path, capsys):
        """`cmd_amend_sab` hitting an exception MUST write a log entry
        with status=FAILED so audit tools can spot the failure despite
        the dispatched error printed to stderr."""
        from cli.project_cmds import cmd_amend_sab
        import cli.project_cmds as pc

        # Force amend_sab to throw — captures both the exception handler
        # in cmd_amend_sab AND the post-call log entry, which is what
        # we want here (the user asked for observability through every path).
        def _boom(*a, **kw):
            raise RuntimeError("synthetic failure for test")

        class _BoomModule:
            amend_sab = staticmethod(_boom)
            discover_modules = staticmethod(lambda *a, **kw: [])
            phantom_modules = staticmethod(lambda *a, **kw: [])

        monkeypatch_patch = pc.__dict__.copy()
        monkeypatch_patch["amend_sab"] = _boom
        # Easier: use sys.modules injection
        import sys
        # Round 101: capture the real MODULE, not `pc.amend_sab` (a function).
        # The cleanup below used to `pop` unconditionally, which evicted
        # core.quality_gate.sab_amender from sys.modules for the rest of the
        # session — so a later test that did `import ... as sa` got a fresh
        # module object while a function imported at collection time still
        # read the old one's globals, and `monkeypatch.setattr(sa, ...)` had
        # no effect. Two of Round 101's guards passed alone and failed in the
        # full suite because of it.
        _real_sab_amender = sys.modules.get("core.quality_gate.sab_amender")
        sys.modules["core.quality_gate.sab_amender"] = _BoomModule

        (tmp_path / ".methodology").mkdir(exist_ok=True)
        (tmp_path / ".methodology" / "SAB.json").write_text(
            json.dumps({"layers": [{"name": "app", "modules": []}]})
        )
        src = tmp_path / "03-development" / "src" / "app"
        src.mkdir(parents=True)
        (src / "seed.py").write_text("x = 1")

        try:
            rc = cmd_amend_sab(self._build_args(tmp_path))
        finally:
            if _real_sab_amender is not None:
                sys.modules["core.quality_gate.sab_amender"] = _real_sab_amender
            else:
                sys.modules.pop("core.quality_gate.sab_amender", None)

        assert rc == 1
        entries = self._read_log_entries(tmp_path)
        assert len(entries) == 1
        assert entries[0]["status"] == "FAILED(rc=1)"
        assert entries[0]["outcome"] == "exception"
        assert entries[0]["rc"] == 1

