"""Regression tests for check_methodology_consistency.py — Bugs M04/M05/M06.

M04 (line 220): _parse_simple_yaml raises ValueError on malformed YAML;
   callers don't catch, so the whole CLI aborts on syntax errors instead
   of degrading to a warning.
M05 (line 428): constitution_doc surface check is `pass` with no diagnostic.
   If CONSTITUTION.md is missing when the rule registry declares it, drift
   goes undetected.
M06 (line 449): _extract_fingerprint_tokens regex requires 5+ chars; the
   fallback (5-char branch) reuses the same regex with the same minimum
   length, so it never recovers shorter tokens. Effectively dead.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


def _load_module():
    scripts_dir = str(Path(__file__).resolve().parents[1] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    return importlib.import_module("check_methodology_consistency")


@pytest.fixture
def module():
    return _load_module()


# ---------------------------------------------------------------------------
# Bug M04: malformed YAML must degrade to warning, not abort
# ---------------------------------------------------------------------------

class TestM04MalformedYamlGraceful:
    def test_malformed_yaml_returns_empty_not_raises(self, module, tmp_path):
        """Bug M04 regression: when the rules manifest is broken YAML,
        load_rules_manifest must not raise — it should return {} and the
        caller can continue. Currently the whole CLI aborts."""
        manifest = tmp_path / "rules.yaml"
        # `- item` at top level with no list parent triggers ValueError at
        # _parse_simple_yaml line 171 ("list item in non-list parent").
        manifest.write_text("- foo\n- bar\n", encoding="utf-8")
        result = module.load_rules_manifest(manifest)
        assert result == {}, f"M04: malformed YAML must return {{}}, got {result!r}"


# ---------------------------------------------------------------------------
# Bug M05: constitution_doc surface check must emit diagnostic
# ---------------------------------------------------------------------------

class TestM05ConstitutionSurfaceDiagnostic:
    def test_constitution_surface_emits_warning_when_missing(
        self, module, tmp_path
    ):
        """Bug M05 regression: when a rule declares surface=constitution_doc
        but CONSTITUTION.md is absent, the check should produce a diagnostic,
        not silently pass."""
        # No CONSTITUTION.md created
        assert not (tmp_path / "CONSTITUTION.md").exists()

        rules = {
            "R-1": {
                "text": "Some canonical rule text with enough words to fingerprint properly.",
                "surfaces": ["constitution_doc"],
            }
        }
        # check_rules signature: rules, plan_dir, workflow_dir
        errors = module.check_rules(rules, plan_dir=tmp_path, workflow_dir=tmp_path)
        # At least one error must mention CONSTITUTION or the rule
        joined = " ".join(errors)
        assert "R-1" in joined or "CONSTITUTION" in joined.upper() or "constitution" in joined.lower(), (
            f"M05: constitution_doc check with missing file must produce diagnostic, "
            f"got errors={errors}"
        )

    def test_constitution_surface_passes_when_present(self, module, tmp_path):
        """Sanity: when CONSTITUTION.md exists, check does not error on this surface."""
        (tmp_path / "CONSTITUTION.md").write_text(
            "Some canonical rule text with enough words to fingerprint properly.\n",
            encoding="utf-8",
        )
        rules = {
            "R-1": {
                "text": "Some canonical rule text with enough words to fingerprint properly.",
                "surfaces": ["constitution_doc"],
            }
        }
        errors = module.check_rules(rules, plan_dir=tmp_path, workflow_dir=tmp_path, project_root=tmp_path)
        # The check should not add a "missing" error
        assert not any("R-1" in e and "missing" in e.lower() for e in errors), (
            f"M05: present CONSTITUTION.md should not raise missing error, got {errors}"
        )


# ---------------------------------------------------------------------------
# Bug M06: _extract_fingerprint_tokens must accept shorter tokens in fallback
# ---------------------------------------------------------------------------

class TestM06FingerprintFallback:
    def test_short_words_are_picked_in_fallback(self, module):
        """Bug M06 regression: text with only 5-7 char words must yield 4 tokens
        in fallback mode. The current regex requires {4,} chars after the first
        letter, so 'alpha beta gamma delta' (5 chars each) is rejected."""
        # words: alpha, beta, gamma, delta, epsilon, zeta (5 chars each)
        text = "alpha beta gamma delta epsilon zeta"
        tokens = module._extract_fingerprint_tokens(text, n=4)
        assert len(tokens) >= 4, (
            f"M06: fallback should pick 5-char words, got tokens={tokens}"
        )

    def test_mixed_length_yields_at_least_4(self, module):
        """Realistic short canonical text still produces 4 tokens via fallback."""
        # 'token' 5, 'match' 5, 'surface' 7, 'align' 5, 'drift' 5 — all 5+ char,
        # not in STOP. 'rule' is in STOP, so use other content words.
        text = "token match surface align drift"
        tokens = module._extract_fingerprint_tokens(text, n=4)
        assert len(tokens) >= 4, (
            f"M06: 5-7 char words should be picked in fallback, got tokens={tokens}"
        )
