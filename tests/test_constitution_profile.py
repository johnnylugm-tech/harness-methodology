"""Tests for core/quality_gate/constitution/profile.py — ConstitutionProfile."""

import json
import os
import pytest
import tempfile
from pathlib import Path

from core.quality_gate.constitution.profile import (
    ConstitutionProfile,
    defaults,
    load_profile,
    get_profile,
    reset_profile,
)



class TestDefaults:
    def test_active_dimensions_p1(self):
        p = defaults()
        assert p.active_dimensions(1) == ["correctness", "security"]

    def test_active_dimensions_p3(self):
        p = defaults()
        assert p.active_dimensions(3) == ["correctness", "security", "maintainability"]

    def test_active_dimensions_p4(self):
        p = defaults()
        assert p.active_dimensions(4) == ["correctness", "security", "maintainability", "coverage"]

    def test_composite_threshold_p1(self):
        # P1 lowered from 100 to 75 — requirements docs use a reduced keyword set.
        assert defaults().composite_threshold(1) == 75.0

    def test_composite_threshold_p3(self):
        # P3-P4 lowered from 90 to 80 — not every project uses all security primitives.
        assert defaults().composite_threshold(3) == 80.0

    def test_composite_threshold_p5(self):
        # P5-P8 document phases use 65.0 (matching P7 pattern).
        assert defaults().composite_threshold(5) == 65.0

    def test_composite_threshold_p6(self):
        assert defaults().composite_threshold(6) == 65.0

    def test_composite_threshold_p7(self):
        assert defaults().composite_threshold(7) == 65.0

    def test_composite_threshold_p8(self):
        assert defaults().composite_threshold(8) == 65.0

    def test_dimension_threshold_correctness(self):
        assert defaults().dimension_threshold("correctness") == 100.0

    def test_dimension_threshold_coverage(self):
        assert defaults().dimension_threshold("coverage") == 90.0

    def test_dimension_threshold_unknown(self):
        assert defaults().dimension_threshold("bogus") == 80.0

    def test_keywords_security(self):
        p = defaults()
        kw = p.dimension_keywords("security")
        assert "auth" in kw
        assert "encrypt" in kw
        assert len(kw) == 20

    def test_p1_security_keywords_are_requirements_appropriate(self):
        p = defaults()
        kw = p.dimension_keywords_for_phase("security", 1)
        # Implementation-level terms must be absent from P1 vocabulary.
        for impl_kw in ("hmac", "sanitize", "whitelist", "compare_digest", "input sanitizer", "mask", "signature", "rate limit"):
            assert impl_kw not in kw, f"implementation keyword '{impl_kw}' must not be in P1 security vocab"
        # Requirements-level terms must be present.
        for req_kw in ("auth", "validation", "permission", "security", "vulnerability", "encrypt", "tls"):
            assert req_kw in kw, f"requirements keyword '{req_kw}' must be in P1 security vocab"

    def test_p1_correctness_keywords_exclude_sad(self):
        p = defaults()
        kw = p.dimension_keywords_for_phase("correctness", 1)
        assert "sad" not in kw, "'sad' is a P2 artifact and must not be in P1 correctness vocab"
        assert "fr-" in kw
        assert "requirement" in kw
        assert "traceability" in kw

    def test_global_security_keywords_unchanged(self):
        # Global (non-phase-specific) security keywords still have 20 entries.
        assert len(defaults().dimension_keywords("security")) == 20

    def test_file_filter_srs(self):
        k = defaults().file_filter_keywords("srs")
        assert "srs" in k
        assert "fr-" in k

    def test_file_filter_all(self):
        assert defaults().file_filter_keywords("all") == []

    def test_file_filter_unknown(self):
        assert defaults().file_filter_keywords("bogus") is None

    def test_phase_directory(self):
        p = defaults()
        assert p.phase_directory(1) == "01-requirements"
        assert p.phase_directory(5) == "05-verification"
        assert p.phase_directory(99) == "docs"

    def test_dimension_rule(self):
        p = defaults()
        assert p.dimension_rule("correctness") == "TH-03"
        assert p.dimension_rule("security") == "TH-04"
        assert p.dimension_rule("bogus") == "TH-02"

    def test_to_dict_roundtrip(self):
        p = defaults()
        d = p.to_dict()
        p2 = ConstitutionProfile.from_dict(d)
        assert p2.composite_threshold(1) == 75.0
        assert p2.dimension_keywords("security") == p.dimension_keywords("security")

    def test_to_json_roundtrip(self):
        p = defaults()
        js = p.to_json()
        p2 = ConstitutionProfile.from_dict(json.loads(js))
        assert p2.composite_threshold(3) == 80.0


class TestMerge:
    def test_merge_overrides_threshold(self):
        base = defaults()
        override = ConstitutionProfile.from_dict({
            "phases": {"3": {"composite_threshold": 85}}
        })
        merged = base.merge(override)
        assert merged.composite_threshold(3) == 85.0
        # other phases unchanged
        assert merged.composite_threshold(1) == 75.0

    def test_merge_overrides_keywords(self):
        base = defaults()
        override = ConstitutionProfile.from_dict({
            "dimensions": {
                "security": {"keywords": ["custom-auth", "custom-encrypt"]}
            }
        })
        merged = base.merge(override)
        kw = merged.dimension_keywords("security")
        assert kw == ["custom-auth", "custom-encrypt"]

    def test_merge_empty_keywords_keeps_defaults(self):
        base = defaults()
        override = ConstitutionProfile.from_dict({
            "dimensions": {"security": {"keywords": []}}
        })
        merged = base.merge(override)
        # empty list means "keep existing"
        assert len(merged.dimension_keywords("security")) == 20

    def test_merge_adds_new_dimension(self):
        base = defaults()
        override = ConstitutionProfile.from_dict({
            "dimensions": {
                "performance": {"threshold": 80, "keywords": ["latency", "throughput"]}
            }
        })
        merged = base.merge(override)
        assert merged.dimension_threshold("performance") == 80.0
        assert "latency" in merged.dimension_keywords("performance")

    def test_merge_file_filters(self):
        base = defaults()
        override = ConstitutionProfile.from_dict({
            "file_filters": {"srs": ["custom-srs-filter"]}
        })
        merged = base.merge(override)
        assert merged.file_filter_keywords("srs") == ["custom-srs-filter"]

    def test_merge_phase_dir_map(self):
        base = defaults()
        override = ConstitutionProfile.from_dict({
            "phase_dir_map": {"1": "custom-phase-1"}
        })
        merged = base.merge(override)
        assert merged.phase_directory(1) == "custom-phase-1"

    def test_merge_only_active_dims_preserves_threshold(self):
        """Partial phase override (active_dims only) must not reset composite_threshold."""
        base = defaults()
        override = ConstitutionProfile.from_dict({
            "phases": {"1": {"active_dimensions": ["correctness", "security", "maintainability"]}}
        })
        merged = base.merge(override)
        assert merged.composite_threshold(1) == 75.0
        assert merged.active_dimensions(1) == ["correctness", "security", "maintainability"]

    def test_merge_only_keywords_preserves_dimension_threshold(self):
        """Partial dimension override (keywords only) must not reset threshold."""
        base = defaults()
        override = ConstitutionProfile.from_dict({
            "dimensions": {"correctness": {"keywords": ["my-fr"]}}
        })
        merged = base.merge(override)
        assert merged.dimension_threshold("correctness") == 100.0
        assert merged.dimension_keywords("correctness") == ["my-fr"]


class TestLoadProfile:
    def setup_method(self):
        reset_profile()

    def teardown_method(self):
        reset_profile()
        if "METHODOLOGY_CONSTITUTION_PROFILE" in os.environ:
            del os.environ["METHODOLOGY_CONSTITUTION_PROFILE"]

    def test_load_defaults_when_no_file(self):
        p = load_profile(path="/nonexistent/profile.json")
        assert p.composite_threshold(1) == 75.0

    def test_load_from_file(self, tmp_path: Path):
        profile_path = tmp_path / "profile.json"
        profile_path.write_text(json.dumps({
            "phases": {"3": {"composite_threshold": 85}}
        }))
        p = load_profile(path=str(profile_path))
        assert p.composite_threshold(3) == 85.0

    def test_load_from_env_var(self):
        os.environ["METHODOLOGY_CONSTITUTION_PROFILE"] = json.dumps({
            "phases": {"3": {"composite_threshold": 75}}
        })
        p = load_profile(path="/nonexistent/profile.json")
        assert p.composite_threshold(3) == 75.0

    def test_load_from_overrides_param(self):
        p = load_profile(
            path="/nonexistent/profile.json",
            overrides={"phases": {"2": {"composite_threshold": 95}}}
        )
        assert p.composite_threshold(2) == 95.0

    def test_file_overrides_env(self):
        """Env var should merge on top of file."""
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "profile.json"
            f.write_text(json.dumps({"phases": {"3": {"composite_threshold": 85}}}))
            os.environ["METHODOLOGY_CONSTITUTION_PROFILE"] = json.dumps({
                "phases": {"4": {"composite_threshold": 70}}
            })
            p = load_profile(path=str(f))
            # file sets P3=85, env sets P4=70
            assert p.composite_threshold(3) == 85.0
            assert p.composite_threshold(4) == 70.0
            # P1 unchanged
            assert p.composite_threshold(1) == 75.0

    def test_get_profile_singleton(self):
        reset_profile()
        p1 = get_profile()
        p2 = get_profile()
        assert p1 is p2  # singleton

    def test_invalid_file_warns(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.json"
            bad.write_text("not json")
            p = load_profile(path=str(bad))
            assert p.composite_threshold(1) == 75.0  # falls back to built-in defaults

pytestmark = pytest.mark.constitution
