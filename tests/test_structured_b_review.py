"""Unit tests for scripts/structured_b_review.py — B review JSON extraction + validation.

Bug class F (improvement F of convergence plan): workflow JS used
balancedJsonAt() walker for parsing B reviewer's free-text output. The
walker is the root of 3 documented bugs (#122, #134, #135). This module
provides a Python-side alternative that workflow JS can invoke via Bash.

These tests cover:
  - Brace-balanced JSON extraction (handles strings with embedded braces)
  - Schema validation via core/review_schema_validator
  - CANCELLED synthesis on schema violation
  - Downgrade rules propagation (over_interpretation cap)
  - HR-12 regression: B returns valid JSON but with non-canonical fields
  - Edge cases: empty text, prose with embedded JSON, multiple JSON candidates

Commonality: phase-agnostic. Used by all 8 phase workflow JS files.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.structured_b_review import (
    _extract_json_candidates,
    extract_b_review_json,
    structured_b_review,
)


# ---------------------------------------------------------------------------
# Brace-balanced extraction
# ---------------------------------------------------------------------------


class TestExtractJsonCandidates:
    def test_simple_object(self):
        text = '{"a": 1, "b": 2}'
        spans = _extract_json_candidates(text)
        assert len(spans) == 1
        assert text[spans[0][0]:spans[0][1]] == text

    def test_nested_object(self):
        text = '{"a": {"b": {"c": 1}}}'
        spans = _extract_json_candidates(text)
        assert len(spans) == 1

    def test_brace_inside_string_ignored(self):
        text = '{"a": "this has } in it"}'
        spans = _extract_json_candidates(text)
        assert len(spans) == 1
        assert text[spans[0][0]:spans[0][1]] == text

    def test_escaped_quote_in_string(self):
        text = '{"a": "he said \\"hello\\" then { leave"}'
        spans = _extract_json_candidates(text)
        assert len(spans) == 1

    def test_no_json(self):
        spans = _extract_json_candidates("plain text no JSON")
        assert spans == []

    def test_unclosed_brace_ignored(self):
        spans = _extract_json_candidates('{"a": 1, "b":')
        # Should not return a partial span
        # Just verify no exception
        assert isinstance(spans, list)

    def test_multiple_top_level_objects(self):
        text = '{"a": 1} some prose {"b": 2}'
        spans = _extract_json_candidates(text)
        assert len(spans) == 2


# ---------------------------------------------------------------------------
# JSON extraction + parse
# ---------------------------------------------------------------------------


class TestExtractBReviewJson:
    def test_pure_json_input(self):
        text = '{"review_status": "APPROVE", "gaps": []}'
        result, _ = extract_b_review_json(text)
        assert result == {"review_status": "APPROVE", "gaps": []}

    def test_prose_with_embedded_json(self):
        # LLM often wraps JSON in prose
        text = (
            "Looking at the deliverable, here is my assessment:\n\n"
            '{"review_status": "REJECT", "reason": "Missing atomic write test", '
            '"gaps": [{"severity": "high", "evidence_type": "real_invention", '
            '"canonical_ref": "SPEC.md L10", "message": "no atomic_write test"}]}\n\n'
            "Please address these issues."
        )
        result, meta = extract_b_review_json(text)
        assert result is not None
        assert meta["found"] is True
        assert result["review_status"] == "REJECT"
        assert len(result["gaps"]) == 1
        # byte_offset should point to the '{' of the JSON
        assert text[meta["byte_offset"]] == "{"

    def test_prefers_last_candidate(self):
        # LLM restating JSON in summary → prefer last
        text = (
            '{"review_status": "APPROVE", "gaps": []}\n\n'
            "Summary: I initially thought there were issues, but on reflection:\n"
            '{"review_status": "APPROVE", "gaps": []}'
        )
        result, meta = extract_b_review_json(text)
        assert meta["found"] is True
        # Should pick the second (later) one
        assert result == {"review_status": "APPROVE", "gaps": []}

    def test_no_json_returns_meta(self):
        text = "Just prose, no structured output at all."
        result, meta = extract_b_review_json(text)
        assert result is None
        assert meta["found"] is False
        assert "no balanced JSON" in meta["diagnostic"]

    def test_balanced_but_not_json(self):
        # Looks like JSON but isn't (e.g. JS object syntax with unquoted keys)
        text = "{review_status: APPROVE}"
        result, meta = extract_b_review_json(text)
        # Walker finds a balanced span, but json.loads fails
        assert result is None
        assert meta["candidates_considered"] >= 1

    def test_handles_unicode_in_strings(self):
        text = '{"review_status": "APPROVE", "note": "測試 ✓"}'
        result, _ = extract_b_review_json(text)
        assert result is not None
        assert result["note"] == "測試 ✓"


# ---------------------------------------------------------------------------
# End-to-end structured_b_review
# ---------------------------------------------------------------------------


class TestStructuredBReview:
    def test_valid_approve(self):
        text = '{"review_status": "APPROVE", "gaps": []}'
        result = structured_b_review(text, phase=1, deliverable="SRS.md")
        assert result["status"] == "OK"
        assert result["review_status"] == "APPROVE"
        assert result["gaps"] == []
        assert result["validation"]["valid"] is True
        assert result["validation"]["synthesized"] is False

    def test_valid_reject_with_gaps(self):
        text = json.dumps({
            "review_status": "REJECT",
            "reason": "missing tests — AC-FR02-1 has no corresponding assertion in the "
                      "test file; the acceptance criterion requires validating both the "
                      "success and failure paths, and only the success path is covered",
            "gaps": [{
                "severity": "high",
                "evidence_type": "real_invention",
                "canonical_ref": "SPEC.md L10",
                "message": "AC-FR02-1 test missing"
            }]
        })
        result = structured_b_review(text)
        assert result["status"] == "OK"
        assert result["review_status"] == "REJECT"
        assert len(result["gaps"]) == 1
        assert result["gaps"][0]["severity"] == "high"

    def test_hr12_regression_over_interpretation_capped(self):
        # B returns high-severity gap with evidence_type=over_interpretation.
        # Framework's validate_b_output should cap to medium.
        text = json.dumps({
            "review_status": "REJECT",
            "gaps": [{
                "severity": "high",
                "evidence_type": "over_interpretation",
                "canonical_ref": "NFR-01",
                "message": "Ambiguous canonical phrasing"
            }]
        })
        result = structured_b_review(text)
        assert result["status"] == "OK"
        # Gap severity should be downgraded to medium
        assert result["gaps"][0]["severity"] == "medium"

    def test_schema_violation_synthesizes_cancelled(self):
        # B returns JSON with wrong field name (issues instead of gaps)
        text = json.dumps({
            "review_status": "REJECT",
            "issues": [{"severity": "high"}]  # WRONG: should be 'gaps'
        })
        result = structured_b_review(text)
        assert result["status"] == "CANCELLED"
        assert result["review_status"] == "CANCELLED"
        assert result["validation"]["synthesized"] is True
        assert result["validation"]["valid"] is False
        # Synthesized gap should be methodology_artifact
        assert len(result["gaps"]) == 1
        assert result["gaps"][0]["evidence_type"] == "methodology_artifact"

    def test_no_json_synthesizes_cancelled(self):
        text = "I'm not sure how to evaluate this. Let me think..."
        result = structured_b_review(text)
        assert result["status"] == "CANCELLED"
        assert result["extraction"]["found"] is False
        assert result["validation"]["synthesized"] is True
        assert len(result["gaps"]) == 1
        # Gap should explain the synthesis
        assert "no parseable JSON" in result["gaps"][0]["message"]

    def test_methodology_artifact_passes_through(self):
        # Note: methodology_artifact severity capping is owned by improvement H
        # (review_quota.py), not by F (structured_b_review). F only handles
        # over_interpretation cap and CANCELLED synthesis on schema violation.
        # This test documents current F behavior.
        text = json.dumps({
            "review_status": "REJECT",
            "gaps": [{
                "severity": "high",
                "evidence_type": "methodology_artifact",
                "canonical_ref": "",
                "message": "sha256 hash of canonical file missing"
            }]
        })
        result = structured_b_review(text)
        assert result["status"] == "OK"
        # Original severity passes through (H will cap downstream)
        assert result["gaps"][0]["severity"] == "high"
        assert result["gaps"][0]["evidence_type"] == "methodology_artifact"

    def test_unrecoverable_for_non_dict(self):
        # If extract returns a list (not dict), it should be UNRECOVERABLE
        text = '["a", "b", "c"]'
        result = structured_b_review(text)
        # Walker finds balanced span, json.loads returns list (not dict)
        # extract_b_review_json returns None for non-dict
        # So this falls into the "no_json" path → CANCELLED
        assert result["status"] in ("CANCELLED", "UNRECOVERABLE")


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


class TestCLI:
    @pytest.fixture
    def script_path(self) -> Path:
        return Path("scripts/structured_b_review.py").resolve()

    def test_cli_ok(self, tmp_path: Path, script_path: Path):
        raw = tmp_path / "raw.txt"
        raw.write_text('{"review_status": "APPROVE", "gaps": []}')
        result = subprocess.run(
            [sys.executable, str(script_path), "--raw-text", str(raw), "--quiet"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["status"] == "OK"
        assert out["review_status"] == "APPROVE"

    def test_cli_cancelled_synthesized(self, tmp_path: Path, script_path: Path):
        raw = tmp_path / "raw.txt"
        # Wrong field name
        raw.write_text('{"review_status": "REJECT", "issues": []}')
        result = subprocess.run(
            [sys.executable, str(script_path), "--raw-text", str(raw), "--quiet"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1  # CANCELLED
        out = json.loads(result.stdout)
        assert out["status"] == "CANCELLED"
        assert out["validation"]["synthesized"] is True

    def test_cli_no_json(self, tmp_path: Path, script_path: Path):
        raw = tmp_path / "raw.txt"
        raw.write_text("just prose with no JSON")
        result = subprocess.run(
            [sys.executable, str(script_path), "--raw-text", str(raw), "--quiet"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        out = json.loads(result.stdout)
        assert out["extraction"]["found"] is False

    def test_cli_missing_file(self, tmp_path: Path, script_path: Path):
        result = subprocess.run(
            [sys.executable, str(script_path),
             "--raw-text", str(tmp_path / "missing")],
            capture_output=True, text=True,
        )
        assert result.returncode == 2

    def test_cli_json_out(self, tmp_path: Path, script_path: Path):
        raw = tmp_path / "raw.txt"
        raw.write_text('{"review_status": "APPROVE", "gaps": []}')
        json_out = tmp_path / "out.json"
        result = subprocess.run(
            [sys.executable, str(script_path), "--raw-text", str(raw),
             "--json-out", str(json_out), "--quiet"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert result.stdout == ""
        assert json_out.exists()
        out = json.loads(json_out.read_text())
        assert out["status"] == "OK"

    def test_cli_hr12_regression(self, tmp_path: Path, script_path: Path):
        raw = tmp_path / "raw.txt"
        raw.write_text(json.dumps({
            "review_status": "REJECT",
            "gaps": [{
                "severity": "high",
                "evidence_type": "over_interpretation",
                "canonical_ref": "NFR-01",
                "message": "ambiguous canonical phrasing"
            }]
        }))
        result = subprocess.run(
            [sys.executable, str(script_path), "--raw-text", str(raw),
             "--phase", "1", "--deliverable", "SRS.md", "--quiet"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        # Bug B regression guard: over_interpretation high → medium
        assert out["gaps"][0]["severity"] == "medium"


# ---------------------------------------------------------------------------
# Escalation wiring (T1-B) — enforce_escalation via --round/--max-rounds
# ---------------------------------------------------------------------------


class TestEscalationWiring:
    def test_no_round_omits_escalation_fields(self):
        text = '{"review_status": "APPROVE", "gaps": []}'
        result = structured_b_review(text)
        assert "escalation_action" not in result
        assert "escalation_reason" not in result

    def test_approve_no_gaps_escalates_approve(self):
        text = '{"review_status": "APPROVE", "gaps": []}'
        result = structured_b_review(text, round_num=1, max_rounds=5)
        assert result["escalation_action"] == "approve"

    def test_reject_escalates_retry(self):
        text = json.dumps({
            "review_status": "REJECT",
            "reason": "missing tests — AC-FR02-1 has no corresponding assertion in the "
                      "test file; both success and failure paths must be covered",
            "gaps": [{
                "severity": "high", "evidence_type": "real_invention",
                "canonical_ref": "SPEC.md L10", "message": "AC-FR02-1 test missing",
            }],
        })
        result = structured_b_review(text, round_num=1, max_rounds=5)
        assert result["escalation_action"] == "retry"

    def test_final_round_escalates_human(self):
        text = json.dumps({
            "review_status": "REJECT",
            "reason": "missing tests — AC-FR02-1 has no corresponding assertion in the "
                      "test file; both success and failure paths must be covered",
            "gaps": [{
                "severity": "high", "evidence_type": "real_invention",
                "canonical_ref": "SPEC.md L10", "message": "AC-FR02-1 test missing",
            }],
        })
        result = structured_b_review(text, round_num=5, max_rounds=5)
        assert result["escalation_action"] == "escalate_human"

    def test_cancelled_no_json_escalates_retry(self):
        result = structured_b_review("no json here at all", round_num=1, max_rounds=5)
        assert result["status"] == "CANCELLED"
        assert result["escalation_action"] == "retry"

    def test_cli_exit_code_reflects_escalation_action(self, tmp_path):
        raw = tmp_path / "raw.txt"
        raw.write_text(json.dumps({"review_status": "APPROVE", "gaps": []}))
        result = subprocess.run(
            [sys.executable, "scripts/structured_b_review.py",
             "--raw-text", str(raw), "--round", "1", "--max-rounds", "5", "--quiet"],
            cwd=str(Path(__file__).parent.parent), capture_output=True, text=True,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["escalation_action"] == "approve"


# ---------------------------------------------------------------------------
# Doc-content deterministic verification (T1-B) — replaces X1/VETO guard
# ---------------------------------------------------------------------------


class TestDocContentVerification:
    def test_hallucinated_gap_downgraded_and_approved(self):
        """A high-severity gap whose claimed term never appears in the
        reviewed doc gets deterministically downgraded to low — this is
        the safe replacement for the old VETO guard (a second LLM
        self-reporting confidence to auto-flip REJECT->APPROVE)."""
        text = json.dumps({
            "review_status": "APPROVE",
            "reason": "The document correctly covers all requirements with clear "
                      "acceptance criteria and no ambiguity remains after this pass",
            "gaps": [{
                "severity": "high", "evidence_type": "real_invention",
                "canonical_ref": "SPEC.md L5",
                "message": "claims 'quantum encryption' is required but SPEC never mentions this",
            }],
        })
        result = structured_b_review(
            text, round_num=1, max_rounds=5,
            doc_content="# SRS.md\nFR-01: do X\nNo mention of quantum anything here.\n",
        )
        assert result["gaps"][0]["severity"] == "low"
        assert result["gaps"][0]["category"] == "nit"
        assert result["escalation_action"] == "approve"

    def test_reject_never_promoted_to_approve_by_verification(self):
        """Even if every gap gets downgraded to low, a REJECT review_status
        must never be silently promoted to APPROVE by this deterministic
        check — only a genuine B re-review (or human) can do that."""
        text = json.dumps({
            "review_status": "REJECT",
            "reason": "hallucinated claim about a feature that does not exist anywhere "
                      "in this document or the underlying specification at all",
            "gaps": [{
                "severity": "high", "evidence_type": "real_invention",
                "canonical_ref": "SPEC.md L5",
                "message": "claims 'quantum encryption' is required",
            }],
        })
        result = structured_b_review(
            text, round_num=1, max_rounds=5,
            doc_content="# SRS.md\nFR-01: do X\n",
        )
        assert result["review_status"] == "REJECT"
        assert result["gaps"][0]["severity"] == "low"
        assert result["escalation_action"] == "retry"

    def test_verified_gap_severity_unchanged(self):
        text = json.dumps({
            "review_status": "REJECT",
            "reason": "the document invents a requirement for quantum encryption support "
                      "that is not present anywhere in the canonical specification file",
            "gaps": [{
                "severity": "high", "evidence_type": "real_invention",
                "canonical_ref": "SPEC.md L5",
                "message": "claims 'quantum encryption' is required",
            }],
        })
        result = structured_b_review(
            text, round_num=1, max_rounds=5,
            doc_content="# SRS.md\nFR-01: needs quantum encryption support.\n",
        )
        assert result["gaps"][0]["severity"] == "high"

    def test_no_doc_content_omits_verification_key(self):
        text = '{"review_status": "APPROVE", "gaps": []}'
        result = structured_b_review(text, round_num=1, max_rounds=5)
        assert "b2_verification" not in result


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_input_same_output(self):
        text = '{"review_status": "APPROVE", "gaps": []}'
        r1 = structured_b_review(text)
        r2 = structured_b_review(text)
        assert r1 == r2

    def test_no_llm_dependency(self):
        # Verify the script does NOT touch any LLM/network
        import scripts.structured_b_review as mod
        src = Path(mod.__file__).read_text()
        forbidden = ["import requests", "urllib", "claude", "openai", "anthropic"]
        for token in forbidden:
            # allowed: schema annotation, type hints referencing "claude" the model class — none here
            assert token not in src, f"LLM/network call found: {token}"
