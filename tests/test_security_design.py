"""Tests for core/quality_gate/security_design.py — threat-model-as-code.

Round 10 gap-analysis response: replaces keyword-density security scoring
(proven to false-positive-fail honest tool-type projects — Bug #35) with a
decidable structural check of SAD.md §6's <!-- SEC:START/END --> block. An
honest `applicability: none` + justification always passes — none of R1-R8
ever scores prose content.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import pytest
import yaml

from core.quality_gate.security_design import (
    STRIDE_CATEGORIES,
    check_security_design,
    extract_security_block,
    render_canonical_security_template,
)
from core.utils.project_layout import ProjectLayout


def _w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _sec_block(yaml_body: str) -> str:
    return "<!-- SEC:START -->\n```yaml\n" + yaml_body + "```\n<!-- SEC:END -->\n"


def _sab_block(yaml_body: str) -> str:
    return "<!-- SAB:START -->\n```yaml\n" + yaml_body + "```\n<!-- SAB:END -->\n"


_BASE_SEC: dict = {
    "security_design": {
        "version": "1.0",
        "applicability": "full",
        "trust_boundaries": [
            {"id": "TB-01", "name": "external HTTP input", "description": "desc"},
        ],
        "threats": [
            {
                "id": "T-01",
                "boundary": "TB-01",
                "category": "tampering",
                "description": "malformed payload mutates state",
                "mitigation": "schema validation",
                "owner_module": "pkg.mod",
                "verified_by": "test_sec_t01_ok",
            },
        ],
    }
}


def _sec_yaml(sec_overrides=None, threats=None, boundaries=None) -> str:
    data = copy.deepcopy(_BASE_SEC)
    if boundaries is not None:
        data["security_design"]["trust_boundaries"] = boundaries
    if threats is not None:
        data["security_design"]["threats"] = threats
    if sec_overrides:
        data["security_design"].update(sec_overrides)
    return yaml.dump(data, sort_keys=False)


def _sab_yaml(modules=("pkg.mod",), nfr_traceability=None) -> str:
    data = {
        "sab": {
            "version": "1.0",
            "created_at": "2026-01-01",
            "phase": 2,
            "project": "test",
            "layers": [{"name": "core", "modules": list(modules)}],
            "allowed_dependencies": [],
            "quality_targets": {},
            "nfr_traceability": nfr_traceability or {},
            "fr_module_traceability": {},
            "architecture_constraints": [],
            "high_risk_modules": [],
        }
    }
    return yaml.dump(data, sort_keys=False)


def _disable_feature(project: Path) -> None:
    _w(project / ".methodology" / "harness_config.json",
       json.dumps({"version": 1, "features": {"security_design": False}}))


def _errors(vs):
    return [v for v in vs if v.severity == "error"]


# ── extract_security_block ───────────────────────────────────────────────────


def test_extract_returns_none_when_no_marker(tmp_path):
    sad = tmp_path / "SAD.md"
    _w(sad, "# SAD\n\nno security block here.\n")
    assert extract_security_block(sad) is None


def test_extract_returns_none_when_sad_missing(tmp_path):
    assert extract_security_block(tmp_path / "nonexistent" / "SAD.md") is None


def test_extract_raises_on_malformed_yaml(tmp_path):
    sad = tmp_path / "SAD.md"
    _w(sad, "# SAD\n\n" + _sec_block("security_design: [1, 2\n"))
    with pytest.raises(RuntimeError, match="Failed to parse SEC"):
        extract_security_block(sad)


def test_extract_returns_dict_with_root_key(tmp_path):
    sad = tmp_path / "SAD.md"
    _w(sad, "# SAD\n\n" + _sec_block(_sec_yaml()))
    data = extract_security_block(sad)
    assert isinstance(data, dict)
    assert data["security_design"]["applicability"] == "full"


# ── render_canonical_security_template ──────────────────────────────────────


def test_render_template_is_parseable_yaml():
    parsed = yaml.safe_load(render_canonical_security_template())
    assert parsed["security_design"]["applicability"] == "full"


def test_render_template_default_ids_present():
    text = render_canonical_security_template()
    assert "TB-01" in text and "T-01" in text


# ── gating: feature flag / SAD existence / phase ────────────────────────────


def test_feature_disabled_returns_empty(tmp_path):
    _disable_feature(tmp_path)
    _w(ProjectLayout(tmp_path).sad_path, "# SAD\n\nno block.\n")
    assert check_security_design(tmp_path) == []


def test_sad_missing_returns_empty(tmp_path):
    assert check_security_design(tmp_path) == []


def test_phase_below_3_returns_empty_even_when_block_missing(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path, "# SAD\n\nno block.\n")
    assert check_security_design(tmp_path, phase=2) == []
    assert check_security_design(tmp_path, phase=1) == []


# ── R1: missing block ────────────────────────────────────────────────────────


def test_missing_sec_block_blocks_at_phase_3(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path, "# SAD\n\nno block.\n")
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert len(errs) == 1 and errs[0].rule_id == "SEC-R1"


def test_missing_sec_block_blocks_at_phase_none(tmp_path):
    """phase=None means 'no phase context' — structural checks (R1-R7) run
    in full, same convention as check_forward_refs/check_module_fr_coverage."""
    _w(ProjectLayout(tmp_path).sad_path, "# SAD\n\nno block.\n")
    errs = _errors(check_security_design(tmp_path, phase=None))
    assert len(errs) == 1 and errs[0].rule_id == "SEC-R1"


# ── R2: parse errors ─────────────────────────────────────────────────────────


def test_malformed_yaml_blocks(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path,
       "# SAD\n\n" + _sec_block("security_design: [1, 2\n"))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert len(errs) == 1 and errs[0].rule_id == "SEC-R2"


def test_wrong_root_key_blocks(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path,
       "# SAD\n\n" + _sec_block(yaml.dump({"not_security_design": {}})))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert len(errs) == 1 and errs[0].rule_id == "SEC-R2"


# ── R3: applicability ────────────────────────────────────────────────────────


def test_invalid_applicability_value_blocks(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path,
       "# SAD\n\n" + _sec_block(_sec_yaml(sec_overrides={"applicability": "partial"})))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert len(errs) == 1 and errs[0].rule_id == "SEC-R3"


def test_none_applicability_without_justification_blocks(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path,
       "# SAD\n\n" + _sec_block(_sec_yaml(sec_overrides={"applicability": "none"})))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert len(errs) == 1 and errs[0].rule_id == "SEC-R3"


def test_none_applicability_with_short_justification_blocks(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path,
       "# SAD\n\n" + _sec_block(_sec_yaml(
           sec_overrides={"applicability": "none", "justification": "no api"})))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert len(errs) == 1 and errs[0].rule_id == "SEC-R3"


def test_none_applicability_with_long_justification_passes(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path,
       "# SAD\n\n" + _sec_block(_sec_yaml(sec_overrides={
           "applicability": "none",
           "justification": "CLI-only formatting tool, no network, no auth, no PII.",
       })))
    assert check_security_design(tmp_path, phase=3) == []


# ── R4: trust boundaries ─────────────────────────────────────────────────────


def test_full_without_trust_boundaries_blocks(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path,
       "# SAD\n\n" + _sec_block(_sec_yaml(boundaries=[], threats=[])))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert any(e.rule_id == "SEC-R4" for e in errs)


def test_trust_boundary_bad_id_format_blocks(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path,
       "# SAD\n\n" + _sec_block(_sec_yaml(boundaries=[
           {"id": "boundary-1", "name": "x"},
       ])))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert any(e.rule_id == "SEC-R4" and "TB-NN" in e.message for e in errs)


def test_trust_boundary_duplicate_id_blocks(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path,
       "# SAD\n\n" + _sec_block(_sec_yaml(boundaries=[
           {"id": "TB-01", "name": "a"},
           {"id": "TB-01", "name": "b"},
       ], threats=[
           {"id": "T-01", "boundary": "TB-01", "category": "tampering",
            "description": "d", "mitigation": "m", "owner_module": "pkg.mod",
            "verified_by": "test_x"},
       ])))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert any(e.rule_id == "SEC-R4" and "duplicated" in e.message for e in errs)


def test_trust_boundary_missing_name_blocks(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path,
       "# SAD\n\n" + _sec_block(_sec_yaml(boundaries=[{"id": "TB-01", "name": ""}])))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert any(e.rule_id == "SEC-R4" and "name" in e.message for e in errs)


# ── R5: threats ──────────────────────────────────────────────────────────────


def test_full_without_threats_blocks(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path,
       "# SAD\n\n" + _sec_block(_sec_yaml(threats=[])))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert any(e.rule_id == "SEC-R5" and "threats" in e.message for e in errs)


def test_threat_unknown_boundary_blocks(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path,
       "# SAD\n\n" + _sec_block(_sec_yaml(threats=[
           {"id": "T-01", "boundary": "TB-99", "category": "tampering",
            "description": "d", "mitigation": "m", "owner_module": "pkg.mod",
            "verified_by": "test_x"},
       ])))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert any(e.rule_id == "SEC-R5" and "unknown boundary" in e.message for e in errs)


def test_threat_bad_category_blocks(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path,
       "# SAD\n\n" + _sec_block(_sec_yaml(threats=[
           {"id": "T-01", "boundary": "TB-01", "category": "hacking",
            "description": "d", "mitigation": "m", "owner_module": "pkg.mod",
            "verified_by": "test_x"},
       ])))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert any(e.rule_id == "SEC-R5" and "STRIDE" in e.message for e in errs)


def test_threat_missing_description_blocks(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path,
       "# SAD\n\n" + _sec_block(_sec_yaml(threats=[
           {"id": "T-01", "boundary": "TB-01", "category": "tampering",
            "description": "", "mitigation": "m", "owner_module": "pkg.mod",
            "verified_by": "test_x"},
       ])))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert any(e.rule_id == "SEC-R5" and "description" in e.message for e in errs)


def test_threat_missing_mitigation_blocks(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path,
       "# SAD\n\n" + _sec_block(_sec_yaml(threats=[
           {"id": "T-01", "boundary": "TB-01", "category": "tampering",
            "description": "d", "mitigation": "", "owner_module": "pkg.mod",
            "verified_by": "test_x"},
       ])))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert any(e.rule_id == "SEC-R5" and "mitigation" in e.message for e in errs)


def test_threat_bad_verified_by_format_blocks(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path,
       "# SAD\n\n" + _sec_block(_sec_yaml(threats=[
           {"id": "T-01", "boundary": "TB-01", "category": "tampering",
            "description": "d", "mitigation": "m", "owner_module": "pkg.mod",
            "verified_by": "TestFooBar"},
       ])))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert any(e.rule_id == "SEC-R5" and "test name" in e.message for e in errs)


def test_comma_separated_verified_by_blocks(tmp_path):
    """Real incident (P2 2026-07-14): an agent wrote verified_by as a
    comma-separated list of test names instead of a single name — pins
    that exact shape so the regression can't silently reopen."""
    _w(ProjectLayout(tmp_path).sad_path,
       "# SAD\n\n" + _sec_block(_sec_yaml(threats=[
           {"id": "T-01", "boundary": "TB-01", "category": "tampering",
            "description": "d", "mitigation": "m", "owner_module": "pkg.mod",
            "verified_by": "test_x, test_y"},
       ])))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert any(e.rule_id == "SEC-R5" and "test name" in e.message for e in errs)


def test_threat_duplicate_id_blocks(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path,
       "# SAD\n\n" + _sec_block(_sec_yaml(threats=[
           {"id": "T-01", "boundary": "TB-01", "category": "tampering",
            "description": "d1", "mitigation": "m1", "owner_module": "pkg.mod",
            "verified_by": "test_a"},
           {"id": "T-01", "boundary": "TB-01", "category": "spoofing",
            "description": "d2", "mitigation": "m2", "owner_module": "pkg.mod",
            "verified_by": "test_b"},
       ])))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert any(e.rule_id == "SEC-R5" and "duplicated" in e.message for e in errs)


def test_boundary_with_zero_threats_blocks(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path,
       "# SAD\n\n" + _sec_block(_sec_yaml(boundaries=[
           {"id": "TB-01", "name": "a"},
           {"id": "TB-02", "name": "b"},
       ], threats=[
           {"id": "T-01", "boundary": "TB-01", "category": "tampering",
            "description": "d", "mitigation": "m", "owner_module": "pkg.mod",
            "verified_by": "test_x"},
       ])))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert any(e.rule_id == "SEC-R5" and "TB-02" in e.message and "zero threats" in e.message
               for e in errs)


def test_all_stride_categories_are_six():
    assert STRIDE_CATEGORIES == {
        "spoofing", "tampering", "repudiation", "information_disclosure",
        "denial_of_service", "elevation_of_privilege",
    }


# ── R6: owner_module vs SAB modules ─────────────────────────────────────────


def test_owner_module_not_in_sab_blocks(tmp_path):
    layout = ProjectLayout(tmp_path)
    _w(layout.sad_path,
       _sab_block(_sab_yaml(modules=["other.module"])) + "\n"
       + _sec_block(_sec_yaml()))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert any(e.rule_id == "SEC-R6" and "pkg.mod" in e.message for e in errs)


def test_owner_module_in_sab_passes_r6(tmp_path):
    layout = ProjectLayout(tmp_path)
    _w(layout.sad_path,
       _sab_block(_sab_yaml(modules=["pkg.mod"])) + "\n"
       + _sec_block(_sec_yaml()))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert not any(e.rule_id == "SEC-R6" for e in errs)


def test_owner_module_matches_dict_shaped_sab_entry(tmp_path):
    """SAB module entries may be dict-shaped {name, implemented_in} — must
    unwrap via the shared sab_amender.sab_module_candidate SSOT helper."""
    layout = ProjectLayout(tmp_path)
    sab_data = {
        "sab": {
            "version": "1.0", "created_at": "2026-01-01", "phase": 2,
            "project": "test",
            "layers": [{"name": "core", "modules": [
                {"name": "logical.name", "implemented_in": "pkg.mod"},
            ]}],
            "allowed_dependencies": [], "quality_targets": {},
            "nfr_traceability": {}, "fr_module_traceability": {},
            "architecture_constraints": [], "high_risk_modules": [],
        }
    }
    _w(layout.sad_path,
       _sab_block(yaml.dump(sab_data, sort_keys=False)) + "\n"
       + _sec_block(_sec_yaml()))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert not any(e.rule_id == "SEC-R6" for e in errs)


def test_missing_sab_gives_info_not_error(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path, "# SAD\n\n" + _sec_block(_sec_yaml()))
    vs = check_security_design(tmp_path, phase=3)
    r6 = [v for v in vs if v.rule_id == "SEC-R6"]
    assert len(r6) == 1 and r6[0].severity == "info"
    assert _errors(vs) == []  # owner_module cross-check must not block


# ── R7: SAB security-NFR ↔ threat.nfr cross-reference ───────────────────────


def test_threat_nfr_not_in_srs_blocks(tmp_path):
    layout = ProjectLayout(tmp_path)
    _w(layout.srs_path, "### NFR-01\n")
    _w(layout.sad_path,
       _sab_block(_sab_yaml(modules=["pkg.mod"])) + "\n"
       + _sec_block(_sec_yaml(threats=[
           {"id": "T-01", "boundary": "TB-01", "category": "tampering",
            "description": "d", "mitigation": "m", "owner_module": "pkg.mod",
            "verified_by": "test_x", "nfr": "NFR-99"},
       ])))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert any(e.rule_id == "SEC-R7" and "NFR-99" in e.message for e in errs)


def test_sab_security_nfr_not_referenced_blocks(tmp_path):
    layout = ProjectLayout(tmp_path)
    _w(layout.srs_path, "### NFR-02\n")
    _w(layout.sad_path,
       _sab_block(_sab_yaml(modules=["pkg.mod"], nfr_traceability={
           "NFR-02": {"type": "security", "target": "x", "module": "pkg.mod"},
       })) + "\n" + _sec_block(_sec_yaml()))  # base threat has no `nfr` field
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert any(e.rule_id == "SEC-R7" and "NFR-02" in e.message for e in errs)


def test_sab_security_nfr_referenced_passes(tmp_path):
    layout = ProjectLayout(tmp_path)
    _w(layout.srs_path, "### NFR-02\n")
    _w(layout.sad_path,
       _sab_block(_sab_yaml(modules=["pkg.mod"], nfr_traceability={
           "NFR-02": {"type": "security", "target": "x", "module": "pkg.mod"},
       })) + "\n" + _sec_block(_sec_yaml(threats=[
           {"id": "T-01", "boundary": "TB-01", "category": "tampering",
            "description": "d", "mitigation": "m", "owner_module": "pkg.mod",
            "verified_by": "test_x", "nfr": "NFR-02"},
       ])))
    errs = _errors(check_security_design(tmp_path, phase=3))
    assert not any(e.rule_id == "SEC-R7" for e in errs)


# ── R8: verified_by test existence (phase >= 5 only) ────────────────────────


def test_verified_by_test_missing_at_phase_5_blocks(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path, "# SAD\n\n" + _sec_block(_sec_yaml()))
    vs = check_security_design(tmp_path, phase=5)
    hits = [v for v in vs if v.check_type == "security_test_missing"]
    assert len(hits) == 1 and hits[0].rule_id == "SEC-R8"


def test_verified_by_test_missing_at_phase_3_passes(tmp_path):
    """No SAB block here either, hence the R6 info; R8 must stay silent —
    filtering to security_test_missing isolates R8's own verdict."""
    _w(ProjectLayout(tmp_path).sad_path, "# SAD\n\n" + _sec_block(_sec_yaml()))
    vs = check_security_design(tmp_path, phase=3)
    assert not any(v.check_type == "security_test_missing" for v in vs)


def test_verified_by_test_missing_at_phase_none_passes(tmp_path):
    """A bare tooling call with no phase context must not demand tests a
    P2/P3 threat model legitimately hasn't written yet."""
    _w(ProjectLayout(tmp_path).sad_path, "# SAD\n\n" + _sec_block(_sec_yaml()))
    vs = check_security_design(tmp_path, phase=None)
    assert not any(v.check_type == "security_test_missing" for v in vs)


def test_verified_by_test_present_at_phase_5_passes(tmp_path):
    layout = ProjectLayout(tmp_path)
    _w(layout.sad_path, "# SAD\n\n" + _sec_block(_sec_yaml()))
    _w(tmp_path / "tests" / "test_foo.py",
       "def test_sec_t01_ok():\n    assert True\n")
    vs = check_security_design(tmp_path, phase=5)
    assert not any(v.check_type == "security_test_missing" for v in vs)


def test_verified_by_test_present_js_language_passes(tmp_path):
    _w(tmp_path / ".methodology" / "state.json",
       json.dumps({"current_phase": 5, "language": "javascript"}))
    layout = ProjectLayout(tmp_path)
    _w(layout.sad_path, "# SAD\n\n" + _sec_block(_sec_yaml()))
    _w(tmp_path / "tests" / "test_foo.test.js",
       "it('test_sec_t01_ok', () => { expect(true).toBe(true); });\n")
    vs = check_security_design(tmp_path, phase=5)
    assert not any(v.check_type == "security_test_missing" for v in vs)


# ── end-to-end: fully well-formed block ─────────────────────────────────────


def test_well_formed_full_block_with_sab_passes_at_phase_3(tmp_path):
    layout = ProjectLayout(tmp_path)
    _w(layout.sad_path,
       _sab_block(_sab_yaml(modules=["pkg.mod"])) + "\n" + _sec_block(_sec_yaml()))
    assert check_security_design(tmp_path, phase=3) == []


# ── template ↔ factory snapshot contract ────────────────────────────────────


def test_sad_template_sec_block_is_factory_snapshot():
    """templates/SAD.md §6 SEC block MUST be a verbatim snapshot of
    render_canonical_security_template(). The static markdown cannot call
    the factory at runtime, so this test is the only guard against the two
    drifting apart (same pattern as test_sab_parser.py's SAB snapshot
    test — the exact failure this design set out to prevent)."""
    import re

    sad_path = Path(__file__).resolve().parent.parent / "templates" / "SAD.md"
    text = sad_path.read_text(encoding="utf-8")
    m = re.search(
        r"<!-- SEC:START -->\n```yaml\n(.*?)```\n<!-- SEC:END -->",
        text, re.DOTALL,
    )
    assert m, "fenced ```yaml SEC block not found in templates/SAD.md"
    assert m.group(1).strip("\n") == render_canonical_security_template().strip("\n"), (
        "templates/SAD.md §6 SEC block has drifted from "
        "render_canonical_security_template() — re-paste the factory output."
    )


# ── preflight / CLI wiring (Round 10 station 3) ─────────────────────────────


def _hooks(project: Path, phase: int):
    from core.phase_hooks import PhaseHooks

    return PhaseHooks(str(project), phase=phase, enable_kill_switch=False)


def test_preflight_artifact_consistency_blocks_on_missing_sec_block(tmp_path):
    _w(ProjectLayout(tmp_path).sad_path, "# SAD\n\nno block.\n")
    r2 = _hooks(tmp_path, 2).preflight_artifact_consistency()
    assert r2["passed"] is True  # P2 — security_design structural rules start at P3
    r3 = _hooks(tmp_path, 3).preflight_artifact_consistency()
    assert r3["passed"] is False and r3["errors"] >= 1


def test_preflight_artifact_consistency_passes_well_formed_sec_block(tmp_path):
    layout = ProjectLayout(tmp_path)
    _w(layout.sad_path,
       _sab_block(_sab_yaml(modules=["pkg.mod"])) + "\n" + _sec_block(_sec_yaml()))
    r3 = _hooks(tmp_path, 3).preflight_artifact_consistency()
    assert r3["passed"] is True


def test_preflight_artifact_consistency_ignores_sec_block_when_feature_off(tmp_path):
    _disable_feature(tmp_path)
    _w(ProjectLayout(tmp_path).sad_path, "# SAD\n\nno block.\n")
    r3 = _hooks(tmp_path, 3).preflight_artifact_consistency()
    assert r3["passed"] is True


def test_cli_check_artifact_consistency_reports_missing_sec_block(tmp_path, capsys):
    from cli.check_cmds import cmd_check_artifact_consistency

    _w(tmp_path / ".methodology" / "state.json",
       json.dumps({"current_phase": 3}))
    _w(ProjectLayout(tmp_path).sad_path, "# SAD\n\nno block.\n")
    rc = cmd_check_artifact_consistency(
        argparse.Namespace(project=str(tmp_path), forward_refs_only=False)
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "SEC-R1" in out


def test_cli_check_artifact_consistency_passes_well_formed_sec_block(tmp_path):
    from cli.check_cmds import cmd_check_artifact_consistency

    _w(tmp_path / ".methodology" / "state.json",
       json.dumps({"current_phase": 3}))
    layout = ProjectLayout(tmp_path)
    _w(layout.sad_path,
       _sab_block(_sab_yaml(modules=["pkg.mod"])) + "\n" + _sec_block(_sec_yaml()))
    rc = cmd_check_artifact_consistency(
        argparse.Namespace(project=str(tmp_path), forward_refs_only=False)
    )
    assert rc == 0


def test_cli_check_artifact_consistency_no_state_json_skips_r8_only(tmp_path, capsys):
    """No readable current_phase -> phase=None -> R1-R7 fully checked (no
    phase context = check everything structural) but R8 stays silent."""
    from cli.check_cmds import cmd_check_artifact_consistency

    _w(ProjectLayout(tmp_path).sad_path, "# SAD\n\nno block.\n")
    rc = cmd_check_artifact_consistency(
        argparse.Namespace(project=str(tmp_path), forward_refs_only=False)
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "SEC-R1" in out
