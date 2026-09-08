"""Unit tests for scripts/b_gap_validator.py — deterministic B gap verification.

Bug class F (improvement F of convergence plan): workflow JS v7 added
validateBGaps() to downgrade gaps grounded in hallucinated content. The
implementation was workflow-JS LLM-coupled and duplicated across 5 sites.
This Python module is the framework-side replacement.

These tests cover:
  - Term extraction from quoted / backtick / identifier / vocab sources
  - Verification against doc_content (matched vs unverified)
  - Severity recommendation rules (the workflow JS override logic)
  - Edge cases (empty message, no extractable terms, evidence_type caps)
  - CLI smoke tests
  - The HR-12 regression scenario: B claims "Node.js library" for a Python
    taskq — validator must downgrade to low

Commonality: phase-agnostic. Used by all 8 phase workflow JS files.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.b_gap_validator import (
    DEFAULT_TECHNICAL_VOCAB,
    _build_vocab_regex,
    doc_is_template_stub,
    extract_terms,
    load_vocabulary,
    recommend_severity,
    validate_gaps,
    verify_gap_against_doc,
)


# ---------------------------------------------------------------------------
# Term extraction
# ---------------------------------------------------------------------------


class TestExtractTerms:
    def test_quoted_double(self):
        terms = extract_terms('The AC says "python 3.11" verbatim')
        assert "python 3.11" in terms

    def test_quoted_single(self):
        terms = extract_terms("The AC says 'taskq' is required")
        assert "taskq" in terms

    def test_backtick_code_span(self):
        terms = extract_terms("Use `ac_fr02_3` as identifier")
        assert "ac_fr02_3" in terms

    def test_identifier_with_digit(self):
        terms = extract_terms("AC ID is FR01 not FR-01")
        assert "FR01" in terms

    def test_vocabulary_match(self):
        re_ = _build_vocab_regex(DEFAULT_TECHNICAL_VOCAB)
        terms = extract_terms("Implementation uses Python", vocabulary_regex=re_)
        assert any("python" in t.lower() for t in terms)

    def test_no_terms_returns_empty(self):
        assert extract_terms("plain text no terms") == []

    def test_dedup_preserves_first_seen(self):
        terms = extract_terms('"python" then "python" again')
        # 'python' appears once (dedup), and 'again' would also appear
        assert "python" in terms
        # Should not be duplicated
        assert terms.count("python") == 1

    def test_skips_short_terms(self):
        # Single-char tokens are noise
        terms = extract_terms('Use "a" as var')
        # 'a' is 1 char, should be skipped (min length 2)
        assert "a" not in terms

    def test_combined_sources(self):
        terms = extract_terms('The "ac_fr02_3" entry in `python` module FR-01')
        # Should contain at least the quoted, backtick, and identifier hits
        assert "ac_fr02_3" in terms or "FR-01" in terms


# ---------------------------------------------------------------------------
# Vocabulary regex
# ---------------------------------------------------------------------------


class TestBuildVocabRegex:
    def test_longer_term_wins(self):
        re_ = _build_vocab_regex({"x": ["shell", "shell=true"]})
        # Both should match, but longer first
        s = "the script runs shell=true with shell mode"
        m = re_.findall(s)
        assert any("shell=true" in hit for hit in m)

    def test_case_insensitive(self):
        re_ = _build_vocab_regex({"x": ["python"]})
        assert re_.search("PYTHON") is not None

    def test_special_chars_escaped(self):
        re_ = _build_vocab_regex({"x": ["shell=true"]})
        assert re_.search("set shell=true here") is not None
        # Should NOT match 'shellXtrue' (the = is special)
        assert re_.search("shellXtrue") is None


# ---------------------------------------------------------------------------
# Vocabulary loading
# ---------------------------------------------------------------------------


class TestLoadVocabulary:
    def test_returns_defaults_when_no_path(self):
        v = load_vocabulary(None)
        assert v is DEFAULT_TECHNICAL_VOCAB

    def test_loads_custom_file(self, tmp_path: Path):
        p = tmp_path / "vocab.json"
        p.write_text(json.dumps({"custom": ["term1", "term2"]}))
        v = load_vocabulary(p)
        assert "custom" in v
        assert v["custom"] == ["term1", "term2"]

    def test_missing_file_falls_back_to_defaults(self, tmp_path: Path):
        v = load_vocabulary(tmp_path / "missing.json")
        assert v is DEFAULT_TECHNICAL_VOCAB

    def test_invalid_json_falls_back_to_defaults(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("not json")
        v = load_vocabulary(p)
        assert v is DEFAULT_TECHNICAL_VOCAB


# ---------------------------------------------------------------------------
# Gap verification
# ---------------------------------------------------------------------------


class TestVerifyGapAgainstDoc:
    def test_matched_quote_in_doc(self):
        matched, _ = verify_gap_against_doc(
            'AC says "python 3.11" is required',
            "We use Python 3.11 throughout.",
        )
        assert "python 3.11" in matched

    def test_unverified_quote_not_in_doc(self):
        _, unverified = verify_gap_against_doc(
            'AC says "Node.js library" is required',
            "We use Python with taskq.",
        )
        assert "Node.js library" in unverified

    def test_mixed_matched_and_unverified(self):
        matched, unverified = verify_gap_against_doc(
            'Mix of "Python" and "Redis" mentioned',
            "Implementation uses Python only.",
        )
        assert "Python" in matched
        assert any("Redis" in u for u in unverified)


# ---------------------------------------------------------------------------
# Severity recommendation
# ---------------------------------------------------------------------------


class TestRecommendSeverity:
    def test_low_unchanged(self):
        gap = {"severity": "low", "message": "anything"}
        assert recommend_severity(gap, [], []) == "low"

    def test_medium_with_match_kept(self):
        gap = {"severity": "medium", "message": "x"}
        assert recommend_severity(gap, ["python"], []) == "medium"

    def test_high_with_match_kept(self):
        gap = {"severity": "high", "message": "x"}
        assert recommend_severity(gap, ["python"], []) == "high"

    def test_high_all_unverified_downgraded(self):
        # HR-12 regression scenario
        gap = {"severity": "high", "message": "x"}
        assert recommend_severity(gap, [], ["Node.js library"]) == "low"

    def test_medium_all_unverified_downgraded(self):
        gap = {"severity": "medium", "message": "x"}
        assert recommend_severity(gap, [], ["nonexistent"]) == "low"

    def test_no_extractable_terms_keeps_original(self):
        # If gap has no terms at all, can't verify; don't penalize
        gap = {"severity": "high", "message": "x"}
        assert recommend_severity(gap, [], []) == "high"

    def test_methodology_artifact_always_low(self):
        gap = {"severity": "high", "evidence_type": "methodology_artifact",
               "message": "x"}
        assert recommend_severity(gap, ["python"], []) == "low"

    def test_over_interpretation_capped_at_medium(self):
        gap = {"severity": "high", "evidence_type": "over_interpretation",
               "message": "x"}
        assert recommend_severity(gap, ["python"], []) == "medium"

    def test_over_interpretation_with_match_capped_at_medium(self):
        gap = {"severity": "high", "evidence_type": "over_interpretation",
               "message": "x"}
        assert recommend_severity(gap, ["python"], []) == "medium"

    def test_methodology_artifact_high_kept_when_doc_is_stub(self):
        """On a stub document the methodology_artifact hard cap is disabled:
        terms absent because A never transcribed are not methodology noise.
        The review layer synthesizes its own high gap; B-stated severities
        about the missing content must survive to drive the fix."""
        gap = {"severity": "high", "evidence_type": "methodology_artifact",
               "message": "x"}
        assert recommend_severity(gap, ["python"], [], doc_is_stub=True) == "high"

    def test_unverified_claims_not_downgraded_when_doc_is_stub(self):
        """The hallucination downgrade assumes claim terms should be findable;
        a stub document contains no real content, so absence is expected
        state, not hallucination evidence."""
        gap = {"severity": "high", "evidence_type": "real_invention",
               "message": "x"}
        assert recommend_severity(gap, [], ["FR-09"], doc_is_stub=True) == "high"

    def test_stub_flag_keeps_non_stub_behaviour_when_false(self):
        """doc_is_stub=False (the default) must reproduce the pre-fix rules —
        the methodology cap and hallucination downgrade stay active."""
        gap = {"severity": "high", "evidence_type": "methodology_artifact",
               "message": "x"}
        assert recommend_severity(gap, ["python"], [], doc_is_stub=False) == "low"


# ---------------------------------------------------------------------------
# validate_gaps — end-to-end
# ---------------------------------------------------------------------------


class TestValidateGaps:
    def test_hr12_regression_node_js_in_python_taskq(self):
        """The classic HR-12 trigger: B reviewer hallucinated Node.js claim
        for a Python taskq project. Validator must downgrade to low."""
        gaps = [{
            "severity": "high",
            "evidence_type": "real_invention",
            "canonical_ref": "SPEC.md L42",
            "message": 'SRS claims "Node.js library" with TypeScript background '
                       'job queue and DLQ workers',
        }]
        # Doc is about Python taskq — does NOT mention Node.js
        doc = "taskq is a Python 3.11 CLI with task submit/status/clear commands. " \
              "Atomic write via os.replace. No shell=True anywhere."
        report = validate_gaps(gaps, doc)
        assert report["summary"]["total"] == 1
        assert report["summary"]["verified_count"] == 0
        assert report["summary"]["downgraded_count"] == 1
        row = report["gaps"][0]
        assert row["verified"] is False
        assert any("Node.js" in u for u in row["unverified_claims"])
        assert row["severity_recommendation"] == "low"

    def test_real_high_kept_when_verified(self):
        gaps = [{
            "severity": "high",
            "evidence_type": "real_invention",
            "canonical_ref": "SPEC.md L10",
            "message": 'Missing `atomic_write` requirement; SPEC.md L10 says '
                       '"tasks.json atomic write (進程中斷後仍為合法 JSON)"',
        }]
        doc = "taskq uses atomic_write via os.replace for tasks.json. " \
              "Even if interrupted, the file remains valid JSON."
        report = validate_gaps(gaps, doc)
        row = report["gaps"][0]
        assert row["verified"] is True
        assert row["severity_recommendation"] == "high"

    def test_over_interpretation_high_capped_at_medium(self):
        gaps = [{
            "severity": "high",
            "evidence_type": "over_interpretation",
            "canonical_ref": "NFR-01",
            "message": 'NFR-01 p95 < 50ms including subprocess execution',
        }]
        doc = "taskq submit + status combined p95 < 50ms. " \
              "Subprocess execution time is excluded per canonical phrasing."
        report = validate_gaps(gaps, doc)
        row = report["gaps"][0]
        assert row["severity_recommendation"] == "medium"

    def test_empty_message_downgrades(self):
        gaps = [{"severity": "high", "message": "", "evidence_type": "real_invention"}]
        report = validate_gaps(gaps, "any content")
        assert report["gaps"][0]["severity_recommendation"] == "low"

    def test_empty_gaps_list(self):
        report = validate_gaps([], "any doc")
        assert report["summary"]["total"] == 0
        assert report["gaps"] == []

    def test_summary_counts(self):
        gaps = [
            {"severity": "high", "message": '"python" must be there', "evidence_type": "real_invention"},
            {"severity": "high", "message": '"nonexistent" claim', "evidence_type": "real_invention"},
            {"severity": "high", "message": "methodology noise", "evidence_type": "methodology_artifact"},
        ]
        doc = "python is here"
        report = validate_gaps(gaps, doc)
        s = report["summary"]
        assert s["total"] == 3
        assert s["verified_count"] == 1
        # gap 1: kept high (verified), gap 2: downgraded (unverified),
        # gap 3: methodology_artifact cap (downgraded from high → low)
        assert s["downgraded_count"] == 2
        assert s["by_original_severity"]["high"] == 3
        assert s["by_recommended_severity"]["high"] == 1
        assert s["by_recommended_severity"]["low"] == 2

    def test_stub_doc_keeps_stated_high_severity_end_to_end(self):
        """validate_gaps on a sentinel-carrying stub doc must keep a
        high methodology_artifact gap high (terms are absent because the
        deliverable was never authored, not because B hallucinated)."""
        gaps = [{
            "severity": "high",
            "evidence_type": "methodology_artifact",
            "canonical_ref": "",
            "fr_id": None,
            "message": "SRS remains the unfilled harness template: FR-01 through "
                       "FR-10 and NFR-01 through NFR-12 are not transcribed.",
        }]
        doc = "# Software Requirements Specification (SRS) — {Project Name}\n\n" \
              "<!-- harness:template-stub -->\n\n## 1. Requirements Overview\n" \
              "{Brief description of project goals}\n"
        report = validate_gaps(gaps, doc)
        assert report["gaps"][0]["severity_recommendation"] == "high"

    def test_non_stub_doc_still_caps_methodology_to_low_end_to_end(self):
        """Control: the same gap against a real document is still capped low."""
        gaps = [{
            "severity": "high",
            "evidence_type": "methodology_artifact",
            "canonical_ref": "",
            "fr_id": None,
            "message": "SRS remains the unfilled harness template.",
        }]
        doc = "# Software Requirements Specification (SRS) — taskq-api\n\n" \
              "## FR-01: Create tasks\nAcceptance: works.\n"
        report = validate_gaps(gaps, doc)
        assert report["gaps"][0]["severity_recommendation"] == "low"


# ---------------------------------------------------------------------------
# doc_is_template_stub — deterministic stub detection
# ---------------------------------------------------------------------------


class TestDocIsTemplateStub:
    def test_sentinel_literal_is_stub(self):
        assert doc_is_template_stub(
            "# SRS — {Project Name}\n\n<!-- harness:template-stub -->\n"
        ) is True

    def test_eight_plus_placeholders_is_stub(self):
        placeholders = "\n".join(f"| FR-{i:02d} | {{requirement {i}}} |" for i in range(8))
        assert doc_is_template_stub(f"# Doc\n{placeholders}\n") is True

    def test_yaml_comment_wrapped_sentinel_is_stub(self):
        assert doc_is_template_stub(
            "# TEST_INVENTORY.yaml — P1 Naming Authority\n"
            "# <!-- harness:template-stub --> — remove once filled.\n"
        ) is True

    def test_seven_placeholders_is_not_stub(self):
        content = "\n".join(f"{{field {i}}}" for i in range(7))
        assert doc_is_template_stub(f"# Doc\n{content}\n") is False

    def test_clean_content_is_not_stub(self):
        assert doc_is_template_stub(
            "# Software Requirements Specification (SRS) — taskq-api\n\n"
            "## FR-01: Create tasks\nAcceptance: works.\n"
        ) is False


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


class TestCLI:
    @pytest.fixture
    def script_path(self) -> Path:
        return Path("scripts/b_gap_validator.py").resolve()

    @pytest.fixture
    def python_taskq_doc(self, tmp_path: Path) -> Path:
        doc = tmp_path / "doc.md"
        doc.write_text("taskq is a Python 3.11 CLI. Atomic write via os.replace.\n")
        return doc

    def test_cli_ok(self, tmp_path: Path, script_path: Path, python_taskq_doc: Path):
        gaps = tmp_path / "gaps.json"
        gaps.write_text(json.dumps([
            {"severity": "high", "message": '"Node.js library" claim',
             "evidence_type": "real_invention"}
        ]))
        result = subprocess.run(
            [sys.executable, str(script_path), "--gaps", str(gaps),
             "--doc-content", str(python_taskq_doc), "--quiet"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert out["summary"]["total"] == 1
        assert out["gaps"][0]["severity_recommendation"] == "low"

    def test_cli_missing_gaps_file(self, tmp_path: Path, script_path: Path,
                                   python_taskq_doc: Path):
        result = subprocess.run(
            [sys.executable, str(script_path),
             "--gaps", str(tmp_path / "missing"),
             "--doc-content", str(python_taskq_doc)],
            capture_output=True, text=True,
        )
        assert result.returncode == 2

    def test_cli_invalid_gaps_json(self, tmp_path: Path, script_path: Path,
                                   python_taskq_doc: Path):
        gaps = tmp_path / "bad.json"
        gaps.write_text("not a list")
        result = subprocess.run(
            [sys.executable, str(script_path), "--gaps", str(gaps),
             "--doc-content", str(python_taskq_doc)],
            capture_output=True, text=True,
        )
        assert result.returncode == 2

    def test_cli_gaps_must_be_list(self, tmp_path: Path, script_path: Path,
                                   python_taskq_doc: Path):
        gaps = tmp_path / "dict.json"
        gaps.write_text('{"not": "a list"}')
        result = subprocess.run(
            [sys.executable, str(script_path), "--gaps", str(gaps),
             "--doc-content", str(python_taskq_doc)],
            capture_output=True, text=True,
        )
        assert result.returncode == 2

    def test_cli_json_out(self, tmp_path: Path, script_path: Path,
                          python_taskq_doc: Path):
        gaps = tmp_path / "g.json"
        gaps.write_text("[]")
        json_out = tmp_path / "out.json"
        result = subprocess.run(
            [sys.executable, str(script_path), "--gaps", str(gaps),
             "--doc-content", str(python_taskq_doc),
             "--json-out", str(json_out), "--quiet"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert result.stdout == ""
        assert json_out.exists()
        out = json.loads(json_out.read_text())
        assert out["summary"]["total"] == 0


# ---------------------------------------------------------------------------
# validate_b2_response (T1-B) — gaps + reason + citations, replaces X1 LLM re-check
# ---------------------------------------------------------------------------


class TestValidateB2Response:
    def test_verified_gap_reason_citation(self):
        from scripts.b_gap_validator import validate_b2_response

        b2 = {
            "review_status": "REJECT",
            "reason": "the module uses python subprocess calls without a timeout",
            "citations": ["app/worker.py:42"],
            "gaps": [{"severity": "high", "evidence_type": "real_invention",
                      "message": "subprocess call has no timeout"}],
        }
        doc = "app/worker.py:42 calls subprocess.run without timeout="
        result = validate_b2_response(b2, doc)
        assert result["gaps"]["summary"]["total"] == 1
        assert result["reason_verification"]["verified"] is True
        assert result["citations_verification"][0]["citation"] == "app/worker.py:42"
        assert result["citations_verification"][0]["verified"] is True

    def test_unverified_reason_and_citation(self):
        from scripts.b_gap_validator import validate_b2_response

        b2 = {
            "review_status": "REJECT",
            "reason": "the document mentions 'quantum encryption' requirements",
            "citations": ["SPEC.md:999"],
            "gaps": [],
        }
        doc = "# SRS.md\nFR-01: do X\n"
        result = validate_b2_response(b2, doc)
        assert result["reason_verification"]["verified"] is False
        assert "quantum encryption" in result["reason_verification"]["unverified_claims"]
        assert result["citations_verification"][0]["verified"] is False

    def test_empty_reason_counts_as_verified(self):
        from scripts.b_gap_validator import validate_b2_response

        result = validate_b2_response({"review_status": "APPROVE", "gaps": []}, "doc")
        assert result["reason_verification"]["verified"] is True
        assert result["reason_verification"]["matched_terms"] == []

    def test_no_citations_returns_empty_list(self):
        from scripts.b_gap_validator import validate_b2_response

        result = validate_b2_response({"review_status": "APPROVE", "gaps": []}, "doc")
        assert result["citations_verification"] == []


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_input_same_output(self):
        gaps = [{"severity": "high", "message": '"python" claim',
                 "evidence_type": "real_invention"}]
        doc = "python is here"
        r1 = validate_gaps(gaps, doc)
        r2 = validate_gaps(gaps, doc)
        assert r1 == r2

    def test_no_llm_dependency(self):
        # Validate that validate_gaps does NOT touch any LLM/network
        import scripts.b_gap_validator as mod
        # No agent/import/claude/openai references in the module
        src = Path(mod.__file__).read_text()
        forbidden = ["claude", "openai", "anthropic", "import requests", "urllib"]
        for token in forbidden:
            assert token not in src, f"LLM/network call found: {token}"
