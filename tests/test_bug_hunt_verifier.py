"""Unit tests for core/quality_gate/bug_hunt_verifier.py — framework-owned
adversarial_review verdict (Gate 3 blocking rules).

Coverage:
  - report missing / unreadable / structurally invalid → block
  - confirmed critical/high OPEN → block; medium/low OPEN → no block
  - resolved: needs fix_commit or existing repro_test → else block
  - refuted: needs refute_evidence → else block
  - unconfirmed findings → never block (adversarial verify already rejected)
  - stale git_sha → warning only (passes, stale=True)
  - schema ↔ verifier field contract alignment
  - Gate 3 bridge override (_override_adversarial_review_dim_score)
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from core.quality_gate.bug_hunt_verifier import (
    BugHuntVerdict,
    verify_bug_hunt_report,
    _current_git_sha,
    REPORT_RELPATH,
    _REQUIRED_FINDING_FIELDS,
    _REQUIRED_TOP_FIELDS,
)
from harness.harness_bridge import DimResult, _override_adversarial_review_dim_score

REPO_ROOT = Path(__file__).parent.parent


# ── Helpers ────────────────────────────────────────────────────────────────

def _write_report(project_root: Path, data: dict) -> Path:
    """Write a bug_hunt_report.json and return its path."""
    p = project_root / REPORT_RELPATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return p


def _minimal_report(**overrides) -> dict:
    """Return a valid, minimal report that should pass verification."""
    return {
        "generated_at": "2026-06-01T00:00:00Z",
        "git_sha": "abc1234",
        "lenses": ["correctness"],
        "findings": [],
        **overrides,
    }


def _finding(**overrides) -> dict:
    """Return a valid, confirmed critical finding that would normally block."""
    return {
        "id": "mod#1",
        "module": "core.foo",
        "lens": "correctness",
        "severity": "critical",
        "title": "null deref",
        "file": "core/foo.py",
        "line_start": 42,
        "reasoning": "x can be None at this point",
        "confidence": "high",
        "confirmed": True,
        "resolution": {"status": "open"},
        **overrides,
    }


# ── Report missing / unreadable ────────────────────────────────────────────

class TestReportPresence:
    def test_report_not_found_blocks(self, tmp_path: Path):
        """No bug_hunt_report.json → block with report_found=False."""
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        assert v.score == 0.0
        assert v.report_found is False
        assert any("not found" in r for r in v.reasons)

    def test_report_unreadable_blocks(self, tmp_path: Path):
        """Corrupt JSON → block with report_found=True."""
        p = tmp_path / REPORT_RELPATH
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("not valid json {{{", encoding="utf-8")
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        assert v.score == 0.0
        assert v.report_found is True
        assert any("unreadable" in r for r in v.reasons)

    def test_report_unreadable_os_error(self, tmp_path: Path, monkeypatch):
        """OSError during read (permission denied) → block."""
        p = tmp_path / REPORT_RELPATH
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")
        def _fail(_self, *_a, **_kw):
            raise OSError("permission denied")
        monkeypatch.setattr(Path, "read_text", _fail)
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        assert v.report_found is True


# ── Structural validity ────────────────────────────────────────────────────

class TestStructuralValidity:
    def test_missing_top_level_field_blocks(self, tmp_path: Path):
        """Report missing 'lenses' (required top-level field) → block."""
        _write_report(tmp_path, _minimal_report())
        p = tmp_path / REPORT_RELPATH
        data = json.loads(p.read_text())
        del data["lenses"]
        p.write_text(json.dumps(data))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        assert any("lenses" in r for r in v.reasons)

    def test_findings_not_a_list_blocks(self, tmp_path: Path):
        """'findings' is a string instead of a list → block."""
        _write_report(tmp_path, _minimal_report(findings="not_a_list"))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        assert any("must be a list" in r for r in v.reasons)

    def test_finding_not_a_dict(self, tmp_path: Path):
        """A finding entry that is not a dict → structural block."""
        _write_report(tmp_path, _minimal_report(findings=["not_a_dict"]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        assert any("not an object" in r for r in v.reasons)

    def test_empty_findings_passes(self, tmp_path: Path):
        """Empty findings list, all fields present → pass 100."""
        _write_report(tmp_path, _minimal_report())
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is True
        assert v.score == 100.0
        assert v.report_found is True


# ── Confirmed / unconfirmed ─────────────────────────────────────────────────

class TestUnconfirmedNeverBlocks:
    def test_unconfirmed_critical_does_not_block(self, tmp_path: Path):
        """Unconfirmed critical → the adversarial verify already rejected it."""
        f = _finding(severity="critical", confirmed=False,
                     resolution={"status": "open"})
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is True
        assert v.score == 100.0

    def test_confirmed_false_explicit(self, tmp_path: Path):
        """confirmed=False (explicit) → skipped."""
        f = _finding(confirmed=False)
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is True

    def test_missing_confirmed_field(self, tmp_path: Path):
        """Finding missing the 'confirmed' field → structural block."""
        f = _finding()
        del f["confirmed"]
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        assert any("confirmed" in r for r in v.reasons)


# ── Open severity blocking ──────────────────────────────────────────────────

class TestOpenSeverityBlocking:
    def test_open_critical_blocks(self, tmp_path: Path):
        """Confirmed critical, status=open → block."""
        f = _finding(severity="critical", resolution={"status": "open"})
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        assert v.open_blocking == 1
        assert any("critical" in r and "OPEN" in r for r in v.reasons)

    def test_open_high_blocks(self, tmp_path: Path):
        """Confirmed high, status=open → block."""
        f = _finding(severity="high", resolution={"status": "open"})
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        assert v.open_blocking == 1

    def test_open_medium_does_not_block(self, tmp_path: Path):
        """Confirmed medium, status=open → informational, no block."""
        f = _finding(severity="medium", resolution={"status": "open"})
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is True

    def test_open_low_does_not_block(self, tmp_path: Path):
        """Confirmed low, status=open → informational, no block."""
        f = _finding(severity="low", resolution={"status": "open"})
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is True

    def test_severity_case_insensitive(self, tmp_path: Path):
        """Severity 'Critical' (mixed case) should still block."""
        f = _finding(severity="Critical", resolution={"status": "open"})
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False


# ── Resolved evidence ───────────────────────────────────────────────────────

class TestResolvedEvidence:
    def test_resolved_with_fix_commit_passes(self, tmp_path: Path):
        """Resolved with fix_commit → gate passes."""
        f = _finding(
            severity="critical",
            resolution={"status": "resolved",
                        "fix_commit": "abc123def456"},
        )
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is True

    def test_resolved_with_existing_repro_test_passes(self, tmp_path: Path):
        """Resolved with repro_test that exists on disk → gate passes."""
        repro = tmp_path / "tests" / "test_repro.py"
        repro.parent.mkdir(parents=True, exist_ok=True)
        repro.write_text("def test_repro(): pass", encoding="utf-8")
        f = _finding(
            severity="critical",
            resolution={"status": "resolved",
                        "repro_test": "tests/test_repro.py"},
        )
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is True

    def test_resolved_with_nonexistent_repro_test_blocks(self, tmp_path: Path):
        """Resolved with repro_test path that does NOT exist → block."""
        f = _finding(
            severity="critical",
            resolution={"status": "resolved",
                        "repro_test": "tests/ghost_file.py"},
        )
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        assert any("does not exist" in r for r in v.reasons)

    def test_resolved_without_evidence_blocks(self, tmp_path: Path):
        """Resolved without fix_commit or repro_test → anti-fabrication block."""
        f = _finding(
            severity="critical",
            resolution={"status": "resolved"},
        )
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        assert any("anti-fabrication" in r for r in v.reasons)

    def test_resolved_with_both_evidence_types_passes(self, tmp_path: Path):
        """Resolved with BOTH fix_commit AND repro_test → passes."""
        repro = tmp_path / "tests" / "test_both.py"
        repro.parent.mkdir(parents=True, exist_ok=True)
        repro.write_text("def test(): pass", encoding="utf-8")
        f = _finding(
            severity="high",
            resolution={"status": "resolved",
                        "fix_commit": "abc123",
                        "repro_test": "tests/test_both.py"},
        )
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is True


# ── Refuted evidence ────────────────────────────────────────────────────────

class TestRefutedEvidence:
    def test_refuted_with_evidence_passes(self, tmp_path: Path):
        """Refuted with refute_evidence → gate passes."""
        f = _finding(
            severity="critical",
            resolution={
                "status": "refuted",
                "refute_evidence": "The guard at line 50 already checks for None",
            },
        )
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is True

    def test_refuted_without_evidence_blocks(self, tmp_path: Path):
        """Refuted without refute_evidence → block (claim without proof)."""
        f = _finding(
            severity="critical",
            resolution={"status": "refuted"},
        )
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        assert any("refute_evidence" in r for r in v.reasons)

    def test_refuted_with_empty_evidence_blocks(self, tmp_path: Path):
        """Refuted with empty string refute_evidence → block (strip check)."""
        f = _finding(
            severity="critical",
            resolution={"status": "refuted", "refute_evidence": "   "},
        )
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        assert any("refute_evidence" in r for r in v.reasons)


# ── Invalid resolution status ───────────────────────────────────────────────

class TestInvalidResolutionStatus:
    def test_unknown_resolution_status_blocks(self, tmp_path: Path):
        """Resolution status 'fixed' (not in {open, resolved, refuted}) → block."""
        f = _finding(
            severity="critical",
            resolution={"status": "fixed"},
        )
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        assert any("invalid resolution.status" in r for r in v.reasons)

    def test_missing_resolution_status_blocks(self, tmp_path: Path):
        """Resolution dict without 'status' key → invalid status → block."""
        f = _finding(
            severity="critical",
            resolution={"fix_commit": "abc123"},
        )
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        assert any("invalid resolution.status" in r for r in v.reasons)


# ── Stale git_sha ───────────────────────────────────────────────────────────

class TestStaleGitSha:
    def test_stale_report_passes_with_warning(self, tmp_path: Path):
        """git_sha differs from HEAD → still passes, but stale=True."""
        # Init a real git repo inside tmp_path so _current_git_sha
        # returns an actual sha. Then write a report with a different
        # sha → stale must be True.
        env = {**os.environ, "GIT_AUTHOR_NAME": "t",
               "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t"}
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                       cwd=tmp_path, capture_output=True, env=env)
        _write_report(tmp_path, _minimal_report(
            git_sha="0000000000000000000000000000000000000000",
        ))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.stale is True
        assert v.ok is True
        assert v.score == 100.0

    def test_no_git_repo_no_stale(self, tmp_path: Path):
        """When git is not available (no .git), stale stays False."""
        _write_report(tmp_path, _minimal_report(git_sha="abc1234"))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.stale is False


# ── Missing required finding fields ─────────────────────────────────────────

class TestMissingFindingFields:
    def test_missing_module_field_blocks(self, tmp_path: Path):
        """Required field 'module' missing from finding → structural block."""
        f = _finding()
        del f["module"]
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        assert any("module" in r for r in v.reasons)

    def test_multiple_missing_fields_reported(self, tmp_path: Path):
        """All missing required fields listed in reasons."""
        f = _finding()
        del f["module"]
        del f["file"]
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        combined = " ".join(v.reasons)
        assert "module" in combined
        assert "file" in combined


# ── Mixed scenarios ─────────────────────────────────────────────────────────

class TestMixedScenarios:
    def test_open_medium_and_resolved_critical_passes(self, tmp_path: Path):
        """One open medium (non-blocking) + one resolved critical (with evidence)
        → gate passes (medium doesn't block, critical is resolved)."""
        repro = tmp_path / "tests" / "test_fix.py"
        repro.parent.mkdir(parents=True, exist_ok=True)
        repro.write_text("def test(): pass", encoding="utf-8")
        findings = [
            _finding(id="mod#1", severity="medium",
                     resolution={"status": "open"}),
            _finding(id="mod#2", severity="critical",
                     resolution={"status": "resolved",
                                 "repro_test": "tests/test_fix.py"}),
        ]
        _write_report(tmp_path, _minimal_report(findings=findings))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is True
        assert v.score == 100.0

    def test_one_open_critical_among_resolved_blocks(self, tmp_path: Path):
        """One open critical blocks even if all others are resolved."""
        repro = tmp_path / "tests" / "test_ok.py"
        repro.parent.mkdir(parents=True, exist_ok=True)
        repro.write_text("def test(): pass", encoding="utf-8")
        findings = [
            _finding(id="mod#1", severity="high",
                     resolution={"status": "resolved",
                                 "fix_commit": "abc"}),
            _finding(id="mod#2", severity="critical",
                     resolution={"status": "open"}),
        ]
        _write_report(tmp_path, _minimal_report(findings=findings))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        assert v.open_blocking == 1

    def test_multiple_open_blocking_counted(self, tmp_path: Path):
        """Two open critical findings → open_blocking=2."""
        findings = [
            _finding(id="mod#1", severity="critical",
                     resolution={"status": "open"}),
            _finding(id="mod#2", severity="high",
                     resolution={"status": "open"}),
        ]
        _write_report(tmp_path, _minimal_report(findings=findings))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        assert v.open_blocking == 2

    def test_all_non_blocking_passes(self, tmp_path: Path):
        """All findings are medium/low → informational only, gate passes."""
        findings = [
            _finding(id="a", severity="medium",
                     resolution={"status": "open"}),
            _finding(id="b", severity="low",
                     resolution={"status": "open"}),
            _finding(id="c", severity="medium",
                     resolution={"status": "resolved",
                                 "fix_commit": "abc"}),
        ]
        _write_report(tmp_path, _minimal_report(findings=findings))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is True
        assert v.open_blocking == 0

    def test_repro_test_rejects_path_traversal(self, tmp_path: Path):
        """repro_test='../../etc/passwd' → does NOT exist under project → block.
        Path.is_file() is relative to project_root, so traversal has no effect."""
        f = _finding(
            severity="high",
            resolution={"status": "resolved",
                        "repro_test": "../../etc/passwd"},
        )
        _write_report(tmp_path, _minimal_report(findings=[f]))
        v = verify_bug_hunt_report(str(tmp_path))
        assert v.ok is False
        assert any("does not exist" in r for r in v.reasons)


# ── BugHuntVerdict dataclass ────────────────────────────────────────────────

class TestBugHuntVerdict:
    def test_defaults(self):
        """Default fields match the dataclass definition."""
        v = BugHuntVerdict(ok=True, score=100.0)
        assert v.report_found is False
        assert v.stale is False
        assert v.open_blocking == 0
        assert v.reasons == []

    def test_equality(self):
        """Two verdicts with same fields are equal."""
        a = BugHuntVerdict(ok=False, score=0.0, report_found=True,
                           reasons=["bad"])
        b = BugHuntVerdict(ok=False, score=0.0, report_found=True,
                           reasons=["bad"])
        assert a == b

    def test_inequality(self):
        """Different reasons → not equal."""
        a = BugHuntVerdict(ok=False, score=0.0, reasons=["a"])
        b = BugHuntVerdict(ok=False, score=0.0, reasons=["b"])
        assert a != b


# ── _current_git_sha ────────────────────────────────────────────────────────

class TestCurrentGitSha:
    def test_returns_none_outside_git_repo(self, tmp_path: Path):
        """No .git directory → returns None."""
        assert _current_git_sha(tmp_path) is None

    def test_returns_sha_inside_git_repo(self, tmp_path: Path):
        """Inside a git repo → returns a 40-char hex sha."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=tmp_path, capture_output=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "t",
                 "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@t"},
        )
        sha = _current_git_sha(tmp_path)
        assert sha is not None
        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha)


# ── Schema ↔ verifier contract alignment ────────────────────────────────────

class TestSchemaAlignment:
    def test_required_finding_fields_match_schema(self):
        """_REQUIRED_FINDING_FIELDS must be a subset of the JSON schema's
        findings.items.required — guards against silent drift."""
        schema = json.loads(
            (REPO_ROOT / "schemas" / "bug_hunt_report.schema.json").read_text()
        )
        schema_finding_required = set(
            schema["properties"]["findings"]["items"]["required"]
        )
        assert set(_REQUIRED_FINDING_FIELDS) <= schema_finding_required

    def test_required_top_fields_match_schema(self):
        """_REQUIRED_TOP_FIELDS must be a subset of the JSON schema's
        top-level required list."""
        schema = json.loads(
            (REPO_ROOT / "schemas" / "bug_hunt_report.schema.json").read_text()
        )
        assert set(_REQUIRED_TOP_FIELDS) <= set(schema["required"])


# ── Gate 3 bridge override (_override_adversarial_review_dim_score) ──────────

class TestGateOverride:
    CONFIG_DIMS = [{"name": "adversarial_review", "threshold": 100,
                    "requires_tool_execution": False}]

    def test_appended_when_agent_omits_dimension(self, tmp_path: Path):
        """When agent breakdown omits adversarial_review, the framework
        must APPEND it — blocking dims can't depend on agent cooperation."""
        _write_report(tmp_path, _minimal_report(findings=[
            _finding(severity="critical", resolution={"status": "open"}),
        ]))
        dims = [DimResult(name="linting", score=95.0, threshold=90.0)]
        new_dims, changed = _override_adversarial_review_dim_score(
            dims, str(tmp_path), self.CONFIG_DIMS)
        assert changed is True
        ar = next(d for d in new_dims if d.name == "adversarial_review")
        assert ar.score == 0.0
        assert ar.threshold == 100.0
        assert ar.issues  # failure reasons must be surfaced

    def test_agent_self_score_overridden(self, tmp_path: Path):
        """Agent records adversarial_review=100 but report has open critical
        → framework must override to 0.0."""
        _write_report(tmp_path, _minimal_report(findings=[
            _finding(severity="critical", resolution={"status": "open"}),
        ]))
        dims = [DimResult(name="adversarial_review", score=100.0, threshold=100.0)]
        new_dims, changed = _override_adversarial_review_dim_score(
            dims, str(tmp_path), self.CONFIG_DIMS)
        assert changed is True
        assert new_dims[0].score == 0.0

    def test_clean_report_scores_100(self, tmp_path: Path):
        """Report with no open critical/high finding → framework scores 100."""
        _write_report(tmp_path, _minimal_report(findings=[
            _finding(severity="critical",
                     resolution={"status": "resolved", "fix_commit": "abc123"}),
        ]))
        new_dims, _ = _override_adversarial_review_dim_score(
            [], str(tmp_path), self.CONFIG_DIMS)
        assert new_dims[0].score == 100.0

    def test_noop_when_gate_config_lacks_dimension(self, tmp_path: Path):
        """When gate config doesn't declare adversarial_review, override must
        be a no-op — other gates must not be affected."""
        dims = [DimResult(name="linting", score=95.0, threshold=90.0)]
        new_dims, changed = _override_adversarial_review_dim_score(
            dims, str(tmp_path), [{"name": "linting"}])
        assert changed is False
        assert new_dims == dims

    def test_gate3_yaml_declares_dimension(self):
        """gate3_p4_exit.yaml must declare adversarial_review with threshold=100,
        weight=0.00, requires_tool_execution=False."""
        import yaml
        cfg = yaml.safe_load(
            (REPO_ROOT / "harness" / "gate_configs" / "gate3_p4_exit.yaml")
            .read_text(encoding="utf-8")
        )
        ar = next(d for d in cfg["dimensions"]
                  if d["name"] == "adversarial_review")
        assert ar["threshold"] == 100
        assert ar["weight"] == 0.00
        assert ar["requires_tool_execution"] is False
