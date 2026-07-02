"""Unit tests for scripts/check_methodology_consistency.py — rule drift (A)
and deliverable H1 schema (C).

Bug A+C of 5-point plan: methodology rule text and deliverable H1 schema
were duplicated across plan source / workflow JS prompts / templates /
live deliverables, with no framework-side consistency check. These tests
verify:
  - Mini YAML parser handles our constrained manifest format (escape sequences
    in regex patterns, literal block scalars, nested mappings).
  - Multi-token fingerprint matching tolerates markdown formatting drift
    (e.g. **bold**) without false positives on real drift.
  - Three-way deliverable consistency check catches the regression targets
    for bug #137 (diskPrefix cfg string mismatch) and bug #138 (YAML
    diskPrefix non-first-line).

Commonality: phase-agnostic. Reads the registry + schema once and validates
every rule / deliverable across all surfaces.
"""

from pathlib import Path

import pytest

from scripts.check_methodology_consistency import (
    _extract_fingerprint_tokens,
    _parse_simple_yaml,
    _PLAN_RULE_MARKER_RE,
    _JS_RULE_MARKER_RE,
    check_deliverables,
    check_rules,
    load_deliverables_schema,
    load_rules_manifest,
)


# ---------------------------------------------------------------------------
# _parse_simple_yaml — fixture tests for the manifest format
# ---------------------------------------------------------------------------


class TestParseSimpleYaml:
    def test_parses_simple_key_value(self):
        out = _parse_simple_yaml("foo: bar\nbaz: qux\n")
        assert out == {"foo": "bar", "baz": "qux"}

    def test_parses_nested_mapping(self):
        out = _parse_simple_yaml("outer:\n  inner: value\n")
        assert out == {"outer": {"inner": "value"}}

    def test_parses_list_of_scalars(self):
        out = _parse_simple_yaml("items:\n  - a\n  - b\n  - c\n")
        assert out == {"items": ["a", "b", "c"]}

    def test_parses_list_of_mappings(self):
        out = _parse_simple_yaml(
            "rules:\n"
            "  - id: R-1\n"
            "    text: hello\n"
        )
        assert out == {"rules": [{"id": "R-1", "text": "hello"}]}

    def test_parses_literal_block_scalar(self):
        out = _parse_simple_yaml(
            "key: |\n"
            "  line one\n"
            "  line two\n"
        )
        assert out["key"] == "line one\nline two"

    def test_coerces_int_and_bool(self):
        out = _parse_simple_yaml("n: 42\nb: true\ns: hello\n")
        assert out == {"n": 42, "b": True, "s": "hello"}

    def test_strips_comments(self):
        out = _parse_simple_yaml("foo: bar  # trailing comment\n# full comment\nbaz: qux\n")
        assert out == {"foo": "bar", "baz": "qux"}


# ---------------------------------------------------------------------------
# _extract_fingerprint_tokens — robust to markdown formatting
# ---------------------------------------------------------------------------


class TestExtractFingerprintTokens:
    def test_returns_4_tokens(self):
        text = (
            "CANONICAL INTERPRETATION RULE (anti-over-specification): "
            "verbatim canonical phrase transcription is required for ambiguous terms."
        )
        tokens = _extract_fingerprint_tokens(text)
        assert len(tokens) == 4

    def test_skips_stopwords(self):
        text = "the and or of in on at to for with from by as is are be been being has have had"
        tokens = _extract_fingerprint_tokens(text)
        # Should fall back to 5-char fallback → still empty since no 5+ char tokens
        assert len(tokens) == 0

    def test_tokens_are_unique(self):
        text = "canonical canonical canonical verbatim verbatim phrase phrase text text"
        tokens = _extract_fingerprint_tokens(text)
        assert len(set(t.lower() for t in tokens)) == len(tokens)


# ---------------------------------------------------------------------------
# Regex marker patterns
# ---------------------------------------------------------------------------


class TestMarkerRegexes:
    def test_plan_marker_extracts_id(self):
        text = "<!-- @rule R-CANONICAL-INTERP-001 -->\nSome content"
        ids = _PLAN_RULE_MARKER_RE.findall(text)
        assert ids == ["R-CANONICAL-INTERP-001"]

    def test_js_marker_extracts_id(self):
        text = "// @rule R-SEVERITY-RUBRIC-001\nSome prompt"
        ids = _JS_RULE_MARKER_RE.findall(text)
        assert ids == ["R-SEVERITY-RUBRIC-001"]

    def test_multiple_markers_in_text(self):
        text = (
            "<!-- @rule R-1 -->\ntext\n"
            "<!-- @rule R-2 -->\nmore\n"
            "<!-- @rule R-3 -->\nfinal\n"
        )
        ids = _PLAN_RULE_MARKER_RE.findall(text)
        assert ids == ["R-1", "R-2", "R-3"]


# ---------------------------------------------------------------------------
# Real registry + schema loaders
# ---------------------------------------------------------------------------


class TestLoadManifest:
    def test_loads_three_rules(self):
        # Use the actual manifest shipped with the harness
        rules = load_rules_manifest()
        assert "R-CANONICAL-INTERP-001" in rules
        assert "R-SEVERITY-RUBRIC-001" in rules
        assert "R-NO-PRESCRIPTION-001" in rules

    def test_each_rule_has_text_and_surfaces(self):
        rules = load_rules_manifest()
        for rule_id, rule in rules.items():
            assert "text" in rule, f"{rule_id} missing text"
            assert "surfaces" in rule, f"{rule_id} missing surfaces"
            assert len(rule["text"]) > 50, f"{rule_id} text too short"

    def test_returns_empty_for_missing_path(self, tmp_path):
        rules = load_rules_manifest(tmp_path / "missing.yaml")
        assert rules == {}


class TestLoadDeliverablesSchema:
    def test_loads_four_p1_deliverables(self):
        schema = load_deliverables_schema()
        assert "SRS" in schema
        assert "SPEC_TRACKING" in schema
        assert "TRACEABILITY_MATRIX" in schema
        assert "TEST_INVENTORY" in schema

    def test_each_deliverable_has_required_fields(self):
        schema = load_deliverables_schema()
        for name, d in schema.items():
            assert "disk_prefix" in d, f"{name} missing disk_prefix"
            assert "template_h1_pattern" in d, f"{name} missing template_h1_pattern"
            assert "disk_path_segment" in d, f"{name} missing disk_path_segment"


# ---------------------------------------------------------------------------
# check_deliverables — three-way consistency
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_template_and_js(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a tmp_path layout:
        tmp/templates/SRS.md  (matching template_h1)
        tmp/.claude/workflows/phase1.js  (matching diskPrefix literal)
    """
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "SRS.md").write_text("# SRS - {Project Name}\n")

    js_dir = tmp_path / ".claude" / "workflows"
    js_dir.mkdir(parents=True)
    (js_dir / "phase1-requirements.js").write_text(
        "const srsCfg = { diskPrefix: 'Software Requirements Specification' }\n"
    )

    return tmp_path, templates, js_dir


class TestCheckDeliverables:
    def test_three_way_consistent(self, fake_template_and_js):
        project_root, templates, js_dir = fake_template_and_js
        schema = load_deliverables_schema()
        errors = check_deliverables(
            schema,
            plan_dir=project_root / ".methodology",
            workflow_dir=js_dir,
            templates_dir=templates,
        )
        # Empty (or only warns for missing live deliverable)
        # The real schema has 4 deliverables; other 3 templates don't exist in this
        # fixture, so we expect 3 'template missing' warnings, but NO drift errors.
        for e in errors:
            assert "drift" not in e.lower(), f"unexpected drift: {e}"
            assert "does not match" not in e.lower(), f"unexpected mismatch: {e}"

    def test_detects_diskprefix_drift_in_workflow_js(
        self, fake_template_and_js
    ):
        """Regression for bug #137: workflow JS diskPrefix cfg drifted from
        what A actually writes as H1."""
        project_root, templates, js_dir = fake_template_and_js
        # Overwrite JS with a wrong diskPrefix literal
        (js_dir / "phase1-requirements.js").write_text(
            "const srsCfg = { diskPrefix: 'Software Req Spec' }\n"  # wrong!
        )
        schema = load_deliverables_schema()
        errors = check_deliverables(
            schema,
            plan_dir=project_root / ".methodology",
            workflow_dir=js_dir,
            templates_dir=templates,
        )
        # Should detect drift
        assert any(
            "Software Requirements Specification" in e and "not found" in e
            for e in errors
        ), f"missing drift error in: {errors}"

    def test_detects_template_h1_pattern_mismatch(self, tmp_path: Path):
        templates = tmp_path / "templates"
        templates.mkdir()
        # Template H1 doesn't match the schema's expected pattern
        (templates / "SRS.md").write_text("# Requirements\n")
        js_dir = tmp_path / ".claude" / "workflows"
        js_dir.mkdir(parents=True)
        (js_dir / "phase1-requirements.js").write_text(
            "const srsCfg = { diskPrefix: 'Software Requirements Specification' }\n"
        )
        schema = load_deliverables_schema()
        errors = check_deliverables(
            schema,
            plan_dir=tmp_path / ".methodology",
            workflow_dir=js_dir,
            templates_dir=templates,
        )
        # Should detect template H1 mismatch
        assert any("SRS" in e and "does not match" in e for e in errors), \
            f"missing template mismatch error: {errors}"

    def test_yaml_disk_prefix_must_match_first_line(self, tmp_path: Path):
        """Regression for bug #138: YAML diskPrefix must be on line 1."""
        templates = tmp_path / "templates"
        templates.mkdir()
        # Template has TEST_INVENTORY.yaml but first line doesn't match diskPrefix
        (templates / "TEST_INVENTORY.yaml").write_text(
            "# Different header\n# second line\nkey: value\n"
        )
        js_dir = tmp_path / ".claude" / "workflows"
        js_dir.mkdir(parents=True)
        (js_dir / "phase1-requirements.js").write_text(
            "const testInvCfg = { diskPrefix: '# TEST_INVENTORY.yaml' }\n"
        )
        schema = load_deliverables_schema()
        errors = check_deliverables(
            schema,
            plan_dir=tmp_path / ".methodology",
            workflow_dir=js_dir,
            templates_dir=templates,
        )
        # Should detect YAML first-line mismatch
        assert any("TEST_INVENTORY" in e for e in errors), \
            f"missing YAML first-line error: {errors}"


# ---------------------------------------------------------------------------
# check_rules — fingerprint-based drift detection
# ---------------------------------------------------------------------------


class TestCheckRules:
    def test_finds_rule_in_plan_source(self, tmp_path: Path):
        """Regression for original P1 HR-12 deadlock: rule present in plan
        source means check passes for that surface."""
        plan_dir = tmp_path / ".methodology"
        plan_dir.mkdir()
        (plan_dir / "phase1_plan.md").write_text(
            "<!-- @rule R-CANONICAL-INTERP-001 -->"
            "CANONICAL INTERPRETATION RULE (anti-over-specification): "
            "verbatim canonical phrase transcription. ambiguity.\n"
        )
        rules = {
            "R-CANONICAL-INTERP-001": {
                "text": "CANONICAL INTERPRETATION RULE (anti-over-specification): "
                        "verbatim canonical phrase transcription. ambiguity.",
                "surfaces": ["plan_task_hint"],
            }
        }
        errors = check_rules(rules, plan_dir=plan_dir, workflow_dir=tmp_path / "workflows")
        # No errors for the plan surface
        assert not any("plan_task_hint" in e for e in errors), f"unexpected: {errors}"

    def test_detects_missing_rule_in_plan_source(self, tmp_path: Path):
        plan_dir = tmp_path / ".methodology"
        plan_dir.mkdir()
        # Plan source does NOT contain the rule text
        (plan_dir / "phase1_plan.md").write_text("# Some other plan\n")
        rules = {
            "R-TEST-001": {
                "text": "UNIQUE_FINGERPRINT_TOKEN_XYZ123: this is a test rule.",
                "surfaces": ["plan_task_hint"],
            }
        }
        errors = check_rules(rules, plan_dir=plan_dir, workflow_dir=tmp_path / "workflows")
        assert any("plan_task_hint" in e and "R-TEST-001" in e for e in errors), \
            f"missing plan drift error: {errors}"

    def test_absent_plan_source_is_not_drift(self, tmp_path: Path, capsys):
        """Absence-vs-drift: with no phase*_plan.md generated yet (fresh init,
        or between plan-all runs — plans are per-run artifacts), there is no
        duplicated rule text that can drift. Plan surfaces must be skipped with
        an INFO note, not reported as fingerprint-token drift (mirrors the
        Bug M05 distinction for constitution_doc)."""
        plan_dir = tmp_path / ".methodology"
        plan_dir.mkdir()  # exists but holds no phase*_plan.md
        rules = {
            "R-TEST-001": {
                "text": "UNIQUE_FINGERPRINT_TOKEN_XYZ123: this is a test rule.",
                "surfaces": ["plan_task_hint", "plan_checks"],
            }
        }
        errors = check_rules(rules, plan_dir=plan_dir, workflow_dir=tmp_path / "workflows")
        assert not any("plan" in e for e in errors), f"false drift on absent plans: {errors}"
        assert "no phase*_plan.md" in capsys.readouterr().out

    def test_absent_workflow_js_is_not_drift(self, tmp_path: Path, capsys):
        """Same absence-vs-drift rule for the workflow JS surfaces."""
        rules = {
            "R-TEST-001": {
                "text": "UNIQUE_FINGERPRINT_TOKEN_XYZ123: this is a test rule.",
                "surfaces": ["workflow_a_prompt"],
            }
        }
        errors = check_rules(
            rules, plan_dir=tmp_path / ".methodology", workflow_dir=tmp_path / "workflows"
        )
        assert not any("workflow JS" in e for e in errors), \
            f"false drift on absent workflows: {errors}"
        assert "no phase*.js" in capsys.readouterr().out

    def test_tolerates_markdown_formatting_drift_in_workflow_js(
        self, tmp_path: Path
    ):
        """Bug A fix: workflow JS prompt uses **bold** formatting that differs
        from plan source. Fingerprint matching should NOT flag this."""
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        # Workflow JS uses markdown bold formatting around the rule text
        (wf_dir / "phase1.js").write_text(
            "// @rule R-CANONICAL-INTERP-001\n"
            "**CANONICAL INTERPRETATION RULE** (anti-over-specification): "
            "verbatim canonical phrase transcription. ambiguity.\n"
        )
        rules = {
            "R-CANONICAL-INTERP-001": {
                "text": "CANONICAL INTERPRETATION RULE (anti-over-specification): "
                        "verbatim canonical phrase transcription. ambiguity.",
                "surfaces": ["workflow_a_prompt"],
            }
        }
        errors = check_rules(rules, plan_dir=tmp_path / ".methodology", workflow_dir=wf_dir)
        # Should pass — fingerprint tokens match despite markdown formatting
        assert not errors, f"unexpected drift errors: {errors}"

    def test_detects_substantive_drift_in_workflow_js(self, tmp_path: Path):
        """When the rule text is rewritten (not just markdown formatting),
        fingerprint matching should flag it."""
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        # Workflow JS has substantively different text
        (wf_dir / "phase1.js").write_text(
            "**REWRITTEN RULE** (totally different): unrelated content here.\n"
        )
        rules = {
            "R-TEST-002": {
                "text": "UNIQUE_FINGERPRINT_ABC987 (canonical): verbatim phrase transcription. ambiguity.",
                "surfaces": ["workflow_a_prompt"],
            }
        }
        errors = check_rules(rules, plan_dir=tmp_path / ".methodology", workflow_dir=wf_dir)
        assert any("workflow_a_prompt" in e for e in errors), \
            f"missing workflow drift error: {errors}"


# ---------------------------------------------------------------------------
# End-to-end CLI smoke test (uses actual shipped manifest + schema)
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_actual_shipped_manifest_and_schema_pass(self):
        """The shipped rules/manifest.yaml + schemas/deliverables.schema.yaml
        must pass consistency checks against the actual plan + workflow JS.
        This is the regression gate for future drift."""
        import subprocess
        from pathlib import Path

        # Tests run with harness/ as cwd; the script is at scripts/<name>.
        script = Path("scripts/check_methodology_consistency.py").resolve()
        assert script.exists(), f"script missing: {script}"

        # If we are running in a standalone clone (e.g. CI) rather than as a submodule
        # inside a host project, the workflow JS dir won't exist.
        if not (script.parent.parent.parent / ".claude" / "workflows").exists():
            pytest.skip("Standalone repo — no host project workflows to check against")

        result = subprocess.run(
            ["python3", str(script)],
            capture_output=True, text=True,
        )
        # On the live project after A+C wiring, expect 0 errors
        assert result.returncode == 0, \
            f"CLI failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        assert "OK" in result.stdout
