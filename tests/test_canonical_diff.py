"""Unit tests for scripts/canonical_diff.py — word-level AC scoring, verdict
classification, and Elicitation-mode handling.

Bug D (improvement D of plan): Canonical Interpretation Rule +
No-Prescription Rule + DERIVED tag were prompt-level only. Framework had no
diff tool to detect over-specification. These tests cover the regression
targets — the two ambiguous phrases that caused HR-12 deadlock during P1
('excluding subprocess execution', 'retry on failed/timeout').

Commonality: phase-agnostic. Same engine scores SRS↔SPEC, TESTSPEC↔SRS,
VERIFICATION↔SRS via --mode flag.
"""

import json
from pathlib import Path

import pytest

from scripts.canonical_diff import (
    _best_match_ratio,
    _split_ac_clauses,
    _split_sentences,
    build_diff_report,
    compute_over_spec_score,
    write_report,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def spec_with_ambiguous_phrases(tmp_path: Path) -> Path:
    """SPEC.md fixture containing the two HR-12 deadlock phrases."""
    spec = tmp_path / "SPEC.md"
    spec.write_text(
        "# Task Queue Spec\n\n"
        "The system shall execute Python modules with timing instrumentation "
        "excluding subprocess execution. The system shall retry on failed or "
        "timeout responses. Output is the last 1000 characters of stdout.\n"
    )
    return spec


@pytest.fixture
def srs_verbatim_ac(tmp_path: Path) -> Path:
    """SRS.md that transcribes the canonical phrase verbatim → score ~0."""
    srs = tmp_path / "SRS.md"
    srs.write_text(
        "# SRS\n\n"
        "### FR-01\n"
        "Execute Python modules with timing instrumentation excluding "
        "subprocess execution.\n\n"
        "### FR-02\n"
        "Retry on failed or timeout responses.\n"
    )
    return srs


@pytest.fixture
def srs_over_specified(tmp_path: Path) -> Path:
    """SRS.md that interprets ambiguous canonical without DERIVED tag → high score."""
    srs = tmp_path / "SRS.md"
    srs.write_text(
        "# SRS\n\n"
        "### FR-01\n"
        "Execute Python modules. Measurement MUST include full python -m taskq "
        "wall-clock including fork/exec time. Subprocess execution must be "
        "fully excluded via process isolation. The only valid interpretation "
        "is that wall-clock includes ALL child process time.\n\n"
        "### FR-02\n"
        "Retry on failed or timeout responses — but only network failures "
        "qualify; timeout excludes user-initiated cancels.\n"
    )
    return srs


@pytest.fixture
def srs_with_derived_tag(tmp_path: Path) -> Path:
    """SRS.md that adds interpretation WITH DERIVED tag → score penalized but verdict='interpreted'."""
    srs = tmp_path / "SRS.md"
    srs.write_text(
        "# SRS\n\n"
        "### FR-01\n"
        "DERIVED: SPEC.md:1 — chose to interpret 'subprocess execution' as "
        "all child process time, not just direct subprocess.Popen calls.\n"
        "Execute Python modules. Measurement includes full python -m taskq "
        "wall-clock including fork/exec time.\n"
    )
    return srs


# ---------------------------------------------------------------------------
# _split_sentences / _split_ac_clauses
# ---------------------------------------------------------------------------


class TestSplitSentences:
    def test_splits_on_period_space_capital(self):
        s = ("First sentence here is long enough to survive. "
             "Second sentence there is also long enough. "
             "Third one also must be longer than fifteen characters.")
        out = _split_sentences(s)
        assert len(out) == 3

    def test_strips_code_fences(self):
        s = "Some prose. ```python\nprint('hi')\n``` More prose here."
        out = _split_sentences(s)
        # Should drop code fence content
        assert all("print" not in o for o in out)

    def test_ignores_very_short_fragments(self):
        s = "A. B. Real sentence here that should be kept."
        out = _split_sentences(s)
        # Only the long one survives
        assert len(out) == 1
        assert "kept" in out[0]


class TestSplitAcClauses:
    def test_extracts_fr_headers(self):
        text = "# SRS\n\n### FR-01\nFirst AC.\n\n### FR-02\nSecond AC.\n"
        clauses = _split_ac_clauses(text)
        labels = [c["label"] for c in clauses]
        assert "FR-01" in labels
        assert "FR-02" in labels

    def test_detects_derived_tag(self):
        text = "### FR-01\nDERIVED: SPEC.md:1 — chose X.\nThe AC body."
        clauses = _split_ac_clauses(text)
        assert clauses[0]["derived_present"] is True

    def test_no_derived_tag(self):
        text = "### FR-01\nJust a normal AC."
        clauses = _split_ac_clauses(text)
        assert clauses[0]["derived_present"] is False


# ---------------------------------------------------------------------------
# _best_match_ratio
# ---------------------------------------------------------------------------


class TestBestMatchRatio:
    def test_identical_text_returns_one(self):
        s = "Exclude subprocess execution during measurement."
        canonical = ["Exclude subprocess execution during measurement."]
        assert _best_match_ratio(s, canonical) > 0.95

    def test_completely_unrelated_returns_near_zero(self):
        s = "The quick brown fox jumps over the lazy dog."
        canonical = ["Apple banana cherry date elderberry fig grape."]
        ratio = _best_match_ratio(s, canonical)
        assert ratio < 0.4

    def test_empty_canonical_returns_zero(self):
        assert _best_match_ratio("anything", []) == 0.0


# ---------------------------------------------------------------------------
# compute_over_spec_score
# ---------------------------------------------------------------------------


class TestComputeOverSpecScore:
    def test_verbatim_transcription_low_score(self, spec_with_ambiguous_phrases):
        from scripts.canonical_diff import _split_sentences
        canonical = _split_sentences(spec_with_ambiguous_phrases.read_text())
        # Verbatim transcription → high match ratio, low over-spec score
        ac = ("Execute Python modules with timing instrumentation "
              "excluding subprocess execution.")
        s = compute_over_spec_score(ac, canonical, derived_present=False)
        assert s["verdict"] == "verbatim"
        assert s["over_spec_score"] < 0.3

    def test_interpretive_choices_no_derived_high_score(self, spec_with_ambiguous_phrases):
        from scripts.canonical_diff import _split_sentences
        canonical = _split_sentences(spec_with_ambiguous_phrases.read_text())
        # Interpretation: "MUST include full python -m taskq wall-clock
        # including fork/exec" + "the only valid interpretation is..."
        ac = ("Execute Python modules. Measurement MUST include full python "
              "-m taskq wall-clock including fork/exec time. The only valid "
              "interpretation is that wall-clock includes ALL child process "
              "time.")
        s = compute_over_spec_score(ac, canonical, derived_present=False)
        # No DERIVED tag + interpretive markers (MUST, only) → penalty applied
        assert s["over_spec_score"] > 0.5
        assert s["verdict"] in ("interpreted", "invention")

    def test_interpretive_with_derived_tag_caps_score(self, spec_with_ambiguous_phrases):
        from scripts.canonical_diff import _split_sentences
        canonical = _split_sentences(spec_with_ambiguous_phrases.read_text())
        # Same interpretive text but with DERIVED tag → no penalty
        ac = ("DERIVED: SPEC.md:1 — chose X. Execute Python modules. "
              "Measurement MUST include full python -m taskq wall-clock "
              "including fork/exec time.")
        s = compute_over_spec_score(ac, canonical, derived_present=True)
        # verdict stays 'interpreted' (not 'invention') because derived_present=True
        assert s["verdict"] != "invention"
        assert s["derived_present"] is True

    def test_pure_invention_high_score(self, spec_with_ambiguous_phrases):
        from scripts.canonical_diff import _split_sentences
        canonical = _split_sentences(spec_with_ambiguous_phrases.read_text())
        ac = ("Implement distributed consensus with Raft protocol across "
              "5-node cluster with leader election timeouts.")
        s = compute_over_spec_score(ac, canonical, derived_present=False)
        # No relation to SPEC → invention verdict
        assert s["verdict"] == "invention"
        assert s["over_spec_score"] > 0.7


# ---------------------------------------------------------------------------
# build_diff_report (end-to-end)
# ---------------------------------------------------------------------------


class TestBuildDiffReport:
    def test_verbatim_report(
        self, srs_verbatim_ac, spec_with_ambiguous_phrases
    ):
        r = build_diff_report(srs_verbatim_ac, spec_with_ambiguous_phrases)
        assert r["spec_present"] is True
        assert r["summary"]["invention_count"] == 0
        assert r["summary"]["verbatim_count"] >= 1

    def test_over_specified_report(
        self, srs_over_specified, spec_with_ambiguous_phrases
    ):
        r = build_diff_report(srs_over_specified, spec_with_ambiguous_phrases)
        s = r["summary"]
        # At least one AC should be flagged as over-spec
        assert s["high_score_count"] >= 1 or s["invention_count"] >= 1

    def test_derived_tag_lowers_verdict(
        self, srs_with_derived_tag, spec_with_ambiguous_phrases
    ):
        r = build_diff_report(srs_with_derived_tag, spec_with_ambiguous_phrases)
        # Even with interpretive content, no AC should be flagged as 'invention'
        for rec in r["per_ac"]:
            assert rec["score"]["verdict"] != "invention"

    def test_elicitation_mode_no_canonical(self, srs_verbatim_ac):
        """SPEC.md missing → spec_present=False, all ACs score as invention
        (informational; generate_full_plan.py §B-2 wraps in try/except so this
        never blocks P1 — Elicitation mode simply has nothing to diff against)."""
        r = build_diff_report(srs_verbatim_ac, None)
        assert r["spec_present"] is False
        assert r["canonical"] is None
        # Summary still produced (just with high scores)
        assert r["summary"]["total_ac"] >= 1

    def test_missing_canonical_path_treated_as_elicitation(
        self, srs_verbatim_ac, tmp_path
    ):
        nonexistent = tmp_path / "does_not_exist.md"
        r = build_diff_report(srs_verbatim_ac, nonexistent)
        assert r["spec_present"] is False


# ---------------------------------------------------------------------------
# write_report
# ---------------------------------------------------------------------------


class TestWriteReport:
    def test_writes_valid_json(self, tmp_path, srs_verbatim_ac, spec_with_ambiguous_phrases):
        report = build_diff_report(srs_verbatim_ac, spec_with_ambiguous_phrases)
        out = tmp_path / "report.json"
        write_report(report, out)
        assert out.exists()
        loaded = json.loads(out.read_text())
        assert loaded["deliverable"] == str(srs_verbatim_ac)

    def test_creates_parent_dirs(self, tmp_path, srs_verbatim_ac, spec_with_ambiguous_phrases):
        report = build_diff_report(srs_verbatim_ac, spec_with_ambiguous_phrases)
        out = tmp_path / "nested" / "deep" / "report.json"
        write_report(report, out)
        assert out.exists()


# ---------------------------------------------------------------------------
# HR-12 regression — the two original deadlock phrases
# ---------------------------------------------------------------------------


class TestHR12Regression:
    """Regression tests for the two ambiguous canonical phrases that triggered
    5-round HR-12 deadlock during P1 (2026-06-28). Each must score as
    'verbatim' (low score) when AC is a faithful transcription, and
    'interpreted' (high score) when AC adds prescriptive detail."""

    def test_excluding_subprocess_execution_verbatim(
        self, spec_with_ambiguous_phrases
    ):
        from scripts.canonical_diff import _split_sentences
        canonical = _split_sentences(spec_with_ambiguous_phrases.read_text())
        ac = ("Execute Python modules with timing instrumentation excluding "
              "subprocess execution.")
        s = compute_over_spec_score(ac, canonical)
        assert s["verdict"] == "verbatim"
        assert s["over_spec_score"] < 0.3

    def test_excluding_subprocess_execution_over_specified(
        self, spec_with_ambiguous_phrases
    ):
        from scripts.canonical_diff import _split_sentences
        canonical = _split_sentences(spec_with_ambiguous_phrases.read_text())
        # Original deadlock pattern: prescriptive clause + 'only valid interpretation'
        ac = ("Execute Python modules. The only valid interpretation is that "
              "'excluding subprocess execution' means excluding ALL child "
              "process time including fork/exec overhead, measured via "
              "python -m taskq wall-clock.")
        s = compute_over_spec_score(ac, canonical, derived_present=False)
        assert s["over_spec_score"] > 0.3
        # No DERIVED → at least 'interpreted' or worse
        assert s["verdict"] in ("interpreted", "invention")

    def test_retry_on_failed_or_timeout_verbatim(
        self, spec_with_ambiguous_phrases
    ):
        from scripts.canonical_diff import _split_sentences
        canonical = _split_sentences(spec_with_ambiguous_phrases.read_text())
        ac = "Retry on failed or timeout responses."
        s = compute_over_spec_score(ac, canonical)
        assert s["verdict"] == "verbatim"

    def test_retry_on_failed_or_timeout_over_specified(
        self, spec_with_ambiguous_phrases
    ):
        from scripts.canonical_diff import _split_sentences
        canonical = _split_sentences(spec_with_ambiguous_phrases.read_text())
        # Prescriptive: "only network failures qualify"
        ac = ("Retry on failed or timeout responses — only network failures "
              "qualify; user-initiated cancels MUST NOT trigger retry.")
        s = compute_over_spec_score(ac, canonical, derived_present=False)
        assert s["over_spec_score"] > 0.3


class TestFrCoverage:
    """Round 86 站3 — the omission axis, beside the invention axis.

    `per_ac` scores what Agent A wrote against the canonical text; nothing in
    this report said which canonical requirements never arrived. Agent B's
    first checklist question is exactly that ("did A transcribe ALL features
    from the canonical spec"), and for a spec too large to relay whole it can
    no longer be answered by reading the DOC.
    """

    def _pair(self, tmp_path, spec_frs, srs_frs):
        spec = tmp_path / "SPEC.md"
        spec.write_text(
            "# Spec\n\n" + "".join(f"### {f}: thing\n\nbody\n\n" for f in spec_frs),
            encoding="utf-8")
        srs = tmp_path / "SRS.md"
        srs.write_text(
            "# Software Requirements Specification\n\n"
            + "".join(f"### {f}: thing\n\n**Acceptance criteria**\n"
                      f"- AC-1.1 body\n\n" for f in srs_frs),
            encoding="utf-8")
        return srs, spec

    def test_a_requirement_the_srs_never_transcribed_is_named(self, tmp_path):
        srs, spec = self._pair(tmp_path, ["FR-01", "FR-02", "FR-03"], ["FR-01", "FR-03"])
        cov = build_diff_report(srs, spec)["fr_coverage"]
        assert cov["in_spec_only"] == ["FR-02"]
        assert cov["in_both"] == ["FR-01", "FR-03"]
        assert cov["in_srs_only"] == []

    def test_a_requirement_the_srs_invented_is_named_too(self, tmp_path):
        srs, spec = self._pair(tmp_path, ["FR-01"], ["FR-01", "FR-09"])
        assert build_diff_report(srs, spec)["fr_coverage"]["in_srs_only"] == ["FR-09"]

    def test_coverage_precedes_per_ac_so_a_head_excerpt_still_carries_it(self, tmp_path):
        """This report is itself relayed, and above the ceiling only its head is.

        taskq-new's srs_vs_spec_diff.json is 27,762 bytes at 124 ACs — over the
        24,576-byte relay ceiling — and `per_ac` is the part that grows without
        bound. A coverage table written after it would be the first thing lost.
        """
        srs, spec = self._pair(tmp_path, ["FR-01"], ["FR-01"])
        keys = list(build_diff_report(srs, spec))
        assert keys.index("fr_coverage") < keys.index("per_ac")

    def test_elicitation_mode_reports_no_coverage_rather_than_a_false_empty(
        self, srs_verbatim_ac,
    ):
        # No canonical spec means the question has no answer, not the answer
        # "nothing is missing" — a distinction Round 46 is named after.
        assert build_diff_report(srs_verbatim_ac, None)["fr_coverage"] == {}

    def test_the_fr_id_set_has_one_definition(self, tmp_path):
        """Reused from check_spec_alignment rather than re-derived here.

        A second FR regex is how one document comes to have two answers to
        "which requirements are in it" — the shape this repo keeps finding.
        """
        from core.quality_gate.spec_alignment import structural_fr_ids

        srs, spec = self._pair(tmp_path, ["FR-01", "FR-02"], ["FR-01"])
        cov = build_diff_report(srs, spec)["fr_coverage"]
        spec_ids = structural_fr_ids(spec.read_text(encoding="utf-8"))
        assert sorted(spec_ids) == sorted(cov["in_both"] + cov["in_spec_only"])
