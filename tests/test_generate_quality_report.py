import json
from scripts.generate_quality_report import (
    _find_latest_gate_result, _build_dimension_table, _build_fr_summary, generate_quality_report,
)
from cli._shared import gate_result_paths

def test_l1_find_latest_gate_result_methodology_fallback(tmp_path):
    """Test L1: .methodology/ directory fallback in _find_latest_gate_result."""
    (tmp_path / ".methodology").mkdir()
    
    # Write to .methodology instead of .sessi-work
    res_path = tmp_path / ".methodology" / "gate3_result.json"
    res_path.write_text(json.dumps({"passed": True, "score": 95}))
    
    gate_num, data = _find_latest_gate_result(tmp_path)
    assert gate_num == 3
    assert data["score"] == 95

def test_l1_build_dimension_table_breakdown_schema():
    """Test L1: breakdown schema parsing in _build_dimension_table."""
    gate_result = {
        "breakdown": {
            "code_coverage": {"score": 80, "detail": "foo"},
            "type_safety": {"score": 90, "detail": "bar"}
        }
    }
    
    lines = _build_dimension_table(gate_result)
    text = "\n".join(lines)
    # Checks if auto-generated title label works correctly
    assert "Code Coverage" in text
    assert "Type Safety" in text
    assert "| 80 |" in text or "| 80" in text
    assert "| 90 |" in text or "| 90" in text


def test_methodology_wins_over_sessi_work_same_gate(tmp_path):
    """When both dirs hold the same gate result, the committable .methodology copy
    (composite patched by finalize-gate) must win over the ephemeral, unpatched
    .sessi-work copy. Root cause of the 0/100 placeholder QUALITY_REPORT: the old
    search order read .sessi-work first (agent self-assessed score, never patched).
    """
    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".sessi-work").mkdir()
    (tmp_path / ".sessi-work" / "gate4_result.json").write_text(
        json.dumps({"composite_score": 0, "passed": False}))
    (tmp_path / ".methodology" / "gate4_result.json").write_text(
        json.dumps({"composite_score": 96.41, "passed": True}))
    gate_num, data = _find_latest_gate_result(tmp_path)
    assert gate_num == 4
    assert data["composite_score"] == 96.41


def test_a_low_score_renders_fail_with_no_escape():
    """Round 39 站1: there is no PASS (DA-waiver) row, because there is no
    waiver. Round 38 站3 removed the mechanism; this report used to read
    `da_waiver_applied` from the manifest, a field that stopped being written
    in the same commit — a reader whose writer was gone.
    """
    gate_result = {
        "breakdown": {"architecture": {"score": 0, "detail": "cohesion 0.228"}},
    }
    text = "\n".join(_build_dimension_table(gate_result))
    assert "Architecture" in text
    assert "✗ FAIL" in text
    assert "DA-waiver" not in text


def test_an_agent_written_waiver_field_changes_nothing():
    """Anti-fabrication, unchanged in intent: gate{N}_result.json is written by
    the agent and only composite_score/quality_complete/verdict/passed get
    harness-recomputed. A `da_waiver` key the agent invents was never honoured
    here and still is not — it is simply no longer honoured anywhere.
    """
    gate_result = {
        "breakdown": {"security": {"score": 0, "detail": "no auth checks found"}},
        "da_waiver": {"security": True},  # agent's own self-assessment
    }
    text = "\n".join(_build_dimension_table(gate_result))
    assert "✗ FAIL" in text
    assert "DA-waiver" not in text


def test_a_stale_manifest_waiver_does_not_resurrect_the_pass(tmp_path):
    """The case that matters for projects that ran before Round 38.

    Their `quality_manifest.json` may still carry `da_waiver_applied` from an
    older run. The report must not read it — a field left behind by a removed
    mechanism is history, not a verdict.
    """
    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".methodology" / "gate4_result.json").write_text(json.dumps({
        "composite_score": 40,
        "breakdown": {
            "security": {"score": 0, "detail": "unvalidated agent claim"},
            "architecture": {"score": 0, "detail": "was waived before R38"},
        },
        "da_waiver": {"security": True},
    }))
    (tmp_path / ".methodology" / "quality_manifest.json").write_text(json.dumps({
        "gate_results": {"gate4": {"da_waiver_applied": ["architecture"]}},
    }))
    generate_quality_report(str(tmp_path))
    report = (tmp_path / "06-quality" / "QUALITY_REPORT.md").read_text(encoding="utf-8")
    assert "| Security | 0/100 | ✗ FAIL |" in report
    assert "| Architecture | 0/100 | ✗ FAIL |" in report
    assert "DA-waiver" not in report


# ── Fix H-E (2026-07-15): per-FR canonical gate{N}_result.json paths ─────────

def test_gate_result_paths_without_fr_id(tmp_path):
    """Without fr_id, candidates list omits the per-FR path."""
    paths = gate_result_paths(tmp_path, 1)
    assert paths == [
        tmp_path / ".sessi-work" / "gate1_result.json",
        tmp_path / ".methodology" / "gate1_result.json",
        tmp_path / "gate1_result.json",
    ]


def test_gate_result_paths_with_fr_id(tmp_path):
    """With fr_id, candidates list inserts the per-FR canonical path."""
    paths = gate_result_paths(tmp_path, 1, fr_id="FR-03")
    per_fr = tmp_path / ".methodology" / "gate_results" / "gate1" / "FR-03.json"
    assert per_fr in paths
    # Order: sessi-work -> methodology latest -> per-FR canonical -> project root
    assert paths.index(per_fr) == 2


def test_gate_result_paths_per_fr_returned_first_when_only_present(tmp_path):
    """When only the per-FR file exists (latest alias deleted), the helper
    still locates it via the candidate chain so readers can pick it up."""
    (tmp_path / ".methodology" / "gate_results" / "gate1").mkdir(parents=True)
    per_fr = tmp_path / ".methodology" / "gate_results" / "gate1" / "FR-03.json"
    per_fr.write_text(json.dumps({"composite_score": 80, "passed": True}))
    paths = gate_result_paths(tmp_path, 1, fr_id="FR-03")
    resolved = next((p for p in paths if p.exists()), None)
    assert resolved == per_fr


def test_dimension_table_excluded_by_feature_flag_score_null():
    """A dimension disabled via harness_config.json feature flag has
    score: null + excluded_by_feature_flag: true (schema-documented state,
    see harness/ssi/schemas/harness_gate_result.schema.json). Must render
    N/A + EXCLUDED, not crash on `None >= 70` and not fabricate 0/100 FAIL."""
    gate_result = {
        "breakdown": {
            "mutation_testing": {
                "score": None,
                "excluded_by_feature_flag": True,
                "detail": "Disabled via harness_config.json",
            },
        },
    }
    text = "\n".join(_build_dimension_table(gate_result))
    assert "N/A" in text
    assert "EXCLUDED" in text
    assert "✗ FAIL" not in text


def test_dimension_table_framework_owned_score_null():
    """A framework-owned dimension (e.g. architecture, CRG-scored) or one where
    no measurement applies (e.g. pytest-benchmark with no benchmarks) can also
    be score: null without the exclusion flag. Must render N/A + FRAMEWORK-OWNED,
    not crash and not fabricate 0/100 FAIL."""
    gate_result = {
        "breakdown": {
            "architecture": {"score": None, "detail": "CRG not yet run"},
        },
    }
    text = "\n".join(_build_dimension_table(gate_result))
    assert "N/A" in text
    assert "FRAMEWORK-OWNED" in text
    assert "✗ FAIL" not in text


def test_fr_summary_null_score_renders_na_not_crash():
    """Same null-vs-absent gap as _build_dimension_table, in the per-FR table:
    result.get("score", "N/A") does not substitute for a present null. Must
    render N/A, not crash and not leave a bare `None` in the table."""
    quality_manifest = {
        "gate_results": {
            "gate1": {
                "FR-01": {"score": None, "quality_complete": False},
            },
        },
    }
    text = "\n".join(_build_fr_summary(quality_manifest))
    assert "N/A" in text
    assert "None" not in text
