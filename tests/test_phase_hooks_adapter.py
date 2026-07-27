"""Tests for core.adapters.phase_hooks_adapter."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.adapters.phase_hooks_adapter import PhaseHooksAdapter


class TestPhaseHooksAdapter:
    """Tests for the PhaseHooksAdapter thin adapter class."""

    def _make_mock_hooks(self):
        hooks = MagicMock()
        hooks.preflight_all.return_value = {"all_passed": True, "details": {}}
        hooks.preflight_fsm_check.return_value = {"passed": True}
        hooks.preflight_constitution.return_value = {"passed": True}
        hooks.monitoring_before_dev.return_value = None
        hooks.monitoring_after_dev.return_value = None
        hooks.monitoring_before_rev.return_value = None
        hooks.monitoring_after_rev.return_value = None
        hooks.monitoring_hr12_check.return_value = True
        hooks.postflight_all.return_value = {"passed": True}
        hooks.postflight_summary.return_value = {"summary": "ok"}
        hooks.monitoring_events = []
        hooks.fr_results = []
        return hooks

    @pytest.fixture
    def adapter(self):
        a = PhaseHooksAdapter("/tmp/test_project", phase=3)
        a._hooks = self._make_mock_hooks()
        return a

    def test_init_defaults(self):
        a = PhaseHooksAdapter("/tmp/test")
        assert a.project_path == "/tmp/test"
        assert a.phase is None
        assert a._hooks is None

    def test_init_with_phase(self):
        a = PhaseHooksAdapter("/tmp/test", phase=3)
        assert a.phase == 3

    def test_get_hooks_lazy_init(self):
        a = PhaseHooksAdapter("/tmp/t", phase=2)
        assert a._hooks is None
        a._hooks = self._make_mock_hooks()
        hooks = a._get_hooks()
        assert hooks is not None

    def test_get_hooks_cached(self, adapter):
        hooks1 = adapter._get_hooks()
        hooks2 = adapter._get_hooks()
        assert hooks1 is hooks2

    def test_preflight_delegates(self, adapter):
        result = adapter.preflight()
        assert result["all_passed"] is True

    def test_preflight_fsm(self, adapter):
        result = adapter.preflight_fsm()
        assert result["passed"] is True

    def test_preflight_constitution(self, adapter):
        result = adapter.preflight_constitution()
        assert result["passed"] is True

    def test_before_dev_delegates(self, adapter):
        adapter.before_dev("FR-01")
        adapter._get_hooks().monitoring_before_dev.assert_called_with("FR-01")

    def test_after_dev_delegates(self, adapter):
        adapter.after_dev("FR-01", {"status": "done"})
        adapter._get_hooks().monitoring_after_dev.assert_called_once()

    def test_after_dev_none_result(self, adapter):
        adapter.after_dev("FR-01")  # no result
        adapter._get_hooks().monitoring_after_dev.assert_called_once()

    def test_before_rev_delegates(self, adapter):
        adapter.before_rev("FR-01")
        adapter._get_hooks().monitoring_before_rev.assert_called_with("FR-01")

    def test_after_rev_delegates(self, adapter):
        adapter.after_rev("FR-01", {"review_status": "APPROVE"})
        adapter._get_hooks().monitoring_after_rev.assert_called_once()

    def test_hr12_check(self, adapter):
        result = adapter.hr12_check("FR-01", 1, 5)
        assert result is True

    def test_postflight(self, adapter):
        result = adapter.postflight()
        assert result["passed"] is True

    def test_postflight_summary(self, adapter):
        result = adapter.postflight_summary()
        assert result["summary"] == "ok"

    def test_get_current_phase_no_state(self, adapter, tmp_path):
        adapter.project_path = str(tmp_path)
        assert adapter.get_current_phase() is None

    def test_get_monitoring_events(self, adapter):
        assert adapter.get_monitoring_events() == []

    def test_get_fr_results(self, adapter):
        assert adapter.get_fr_results() == []

    def test_phase_property_passed_through(self):
        a = PhaseHooksAdapter("/tmp/x", phase=5)
        assert a.phase == 5

    def test_adapts_pathlib_path(self):
        p = Path("/tmp/pp")
        a = PhaseHooksAdapter(str(p))
        assert a.project_path == str(p)

    def test_get_current_phase_exists(self, adapter, tmp_path):
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        (method_dir / "state.json").write_text('{"current_phase": 3}')
        adapter.project_path = str(tmp_path)
        phase = adapter.get_current_phase()
        assert phase == 3

    def test_get_current_phase_no_state_file(self, adapter, tmp_path):
        adapter.project_path = str(tmp_path)
        assert adapter.get_current_phase() is None

    def test_get_current_phase_corrupt_json(self, adapter, tmp_path):
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        (method_dir / "state.json").write_text("{bad json")
        adapter.project_path = str(tmp_path)
        assert adapter.get_current_phase() is None

    def test_run_phase_lifecycle_preflight_fails(self, adapter):
        adapter._hooks.preflight_all.return_value = {"all_passed": False}
        result = adapter.run_phase_lifecycle([])
        assert result["preflight"]["all_passed"] is False
        assert result["postflight"]["success"] is False

    def test_run_phase_lifecycle_success_path(self, adapter):
        result = adapter.run_phase_lifecycle([])
        assert "preflight" in result
        assert "fr_outcomes" in result
        assert "postflight" in result


class TestSabConstitutionCheck:
    """Tests for SAB constitution validation in PhaseHooks."""

    @pytest.fixture
    def phase_hooks_cls(self):
        from core.phase_hooks import PhaseHooks
        return PhaseHooks

    def test_p1_skips_sab_check(self, tmp_path, phase_hooks_cls):
        """P1 has no SAB requirement — passes with skipped=True."""
        hooks = phase_hooks_cls(str(tmp_path), phase=1)
        result = hooks.preflight_sab_check()
        assert result["passed"] is True
        assert result.get("skipped") is True

    def test_p2_skips_sab_check(self, tmp_path, phase_hooks_cls):
        """P2 may not have SAB.json yet — passes with skipped=True."""
        hooks = phase_hooks_cls(str(tmp_path), phase=2)
        result = hooks.preflight_sab_check()
        assert result["passed"] is True
        assert result.get("skipped") is True

    def test_p3_fails_without_sab_json(self, tmp_path, phase_hooks_cls):
        """P3+ requires SAB.json — fails if missing."""
        hooks = phase_hooks_cls(str(tmp_path), phase=3)
        result = hooks.preflight_sab_check()
        assert result["passed"] is False
        assert "SAB.json not found" in result.get("message", "")

    def test_p3_passes_with_valid_sab(self, tmp_path, phase_hooks_cls):
        """P3 with valid SAB.json and all modules present passes."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {
            "layers": [
                {"name": "L1", "modules": ["mod.py"], "allowed_dependencies": []},
            ],
            "dependencies": {"L1": []},
        }
        (method_dir / "SAB.json").write_text(
            __import__("json").dumps(sab_json)
        )
        (tmp_path / "mod.py").write_text("# mod")

        hooks = phase_hooks_cls(str(tmp_path), phase=3)
        result = hooks.preflight_sab_check()
        assert result["passed"] is True
        assert result["layers"] == 1

    def test_sab_check_extra_deps_flagged(self, tmp_path, phase_hooks_cls):
        """Dependencies not in allowed list cause violations."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {
            "layers": [
                {"name": "L1", "modules": ["mod.py"], "allowed_dependencies": []},
            ],
            "dependencies": {"L1": ["L2"]},
        }
        (method_dir / "SAB.json").write_text(
            __import__("json").dumps(sab_json)
        )
        (tmp_path / "mod.py").write_text("# mod")

        hooks = phase_hooks_cls(str(tmp_path), phase=3)
        result = hooks.preflight_sab_check()
        assert result["passed"] is False
        assert len(result["violations"]) >= 1
        assert any("L2" in v for v in result["violations"])

    def test_sab_check_missing_modules_p4_blocks(self, tmp_path, phase_hooks_cls):
        """Modules declared in SAB but missing on disk cause violations at P4+."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {
            "layers": [
                {"name": "L1", "modules": ["nonexistent.py"], "allowed_dependencies": []},
            ],
            "dependencies": {"L1": []},
        }
        (method_dir / "SAB.json").write_text(
            __import__("json").dumps(sab_json)
        )

        hooks = phase_hooks_cls(str(tmp_path), phase=4)
        result = hooks.preflight_sab_check()
        assert result["passed"] is False
        assert any("missing" in v for v in result["violations"])

    def test_sab_check_dict_module_blank_implemented_in_falls_back_to_name(
        self, tmp_path, phase_hooks_cls
    ):
        """Dict-shaped module entry with a *blank* (present-but-empty)
        ``implemented_in`` must fall back to ``name`` for the existence
        check, exactly like a missing key does.

        Round 6 station 1: the pre-fix inline unwrap was
        ``m.get("implemented_in", m.get("name", ""))`` — ``.get()`` only
        falls back to its default when the key is *absent*, not when it is
        present-but-blank, so ``implemented_in: ""`` resolved to the literal
        empty string. ``Path(x) / ""  == Path(x)``, and the project root
        always exists, so the existence check silently passed for ANY
        module carrying this shape — a false negative that would hide a
        genuinely missing module forever. Delegating to
        ``sab_amender.sab_module_candidate()`` (which explicitly checks
        ``isinstance(candidate, str) and candidate.strip()`` before using
        ``implemented_in``) closes this.
        """
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {
            "layers": [
                {
                    "name": "L1",
                    "modules": [
                        {"name": "nonexistent_module", "implemented_in": ""},
                    ],
                    "allowed_dependencies": [],
                },
            ],
            "dependencies": {"L1": []},
        }
        (method_dir / "SAB.json").write_text(
            __import__("json").dumps(sab_json)
        )
        # Deliberately do NOT create nonexistent_module.py anywhere.

        hooks = phase_hooks_cls(str(tmp_path), phase=4)
        result = hooks.preflight_sab_check()
        assert result["passed"] is False, (
            "blank implemented_in must not silently mask a missing module "
            f"(got: {result})"
        )
        assert any("missing" in v for v in result["violations"])

    def test_sab_check_missing_modules_p3_allowed(self, tmp_path, phase_hooks_cls):
        """At P3 entry, module-existence check is skipped — implementation dirs not created yet."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {
            "layers": [
                {"name": "L1", "modules": ["nonexistent.py"], "allowed_dependencies": []},
            ],
            "dependencies": {"L1": []},
        }
        (method_dir / "SAB.json").write_text(
            __import__("json").dumps(sab_json)
        )

        hooks = phase_hooks_cls(str(tmp_path), phase=3)
        result = hooks.preflight_sab_check()
        # P3 skips module existence — structural validation only
        assert result["passed"] is True

    def test_preflight_all_includes_sab(self, tmp_path, phase_hooks_cls):
        """preflight_all() result dict includes 'sab' key."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        # Minimal setup: state.json so FSM doesn't fail
        (method_dir / "state.json").write_text('{"state": "ACTIVE", "current_phase": 3}')

        hooks = phase_hooks_cls(str(tmp_path), phase=3, enable_kill_switch=False)
        result = hooks.preflight_all()
        assert "sab" in result["details"]
        # P3 without SAB.json → sab check fails but preflight_all may still pass
        # (SAB is not a BLOCK-level check yet)
        sab_result = result["details"]["sab"]
        assert "passed" in sab_result


class TestReVerifyOverlay:
    """Tests for PR 13: re-verify must re-apply TRACEABILITY_MATRIX.overlay.yaml."""

    def test_rverify_applies_overlay_so_manually_verified_fr_not_in_still_untested(
        self, tmp_path, monkeypatch,
    ):
        """Auto-fix re-verify skips overlay → manually-VERIFIED FRs reappear as untested.

        Scenario:
          - FR-07: has code but no test (scanner: IN_PROGRESS). User manually
            reviewed the code and marks it VERIFIED in overlay using the
            "test_files: ['Manual: ...']" pattern.
          - FR-08: has code but no test → genuinely untested. Auto-fix will fix it.

        Initial pass: FR-07 is filtered out by overlay (test_files contains "Manual").
                      FR-08 remains → auto-fix dispatched for FR-08.
        After auto-fix creates a test for FR-08, re-verify runs check_traceability().
        BUG (without fix): FR-07 would reappear in still_untested because the
                           overlay is not re-applied to the re-verify report2.
        FIX: re-apply overlay to report2 so FR-07 stays filtered → passed=True.
        """
        import yaml

        # --- Minimal project structure ---
        arch = tmp_path / "02-architecture"
        arch.mkdir(parents=True)
        (arch / "SAD.md").write_text(
            "FR-07: manually-verified requirement\n"
            "FR-08: will be auto-fixed\n"
        )

        src = tmp_path / "core"
        src.mkdir()
        # FR-07: has code, no test → IN_PROGRESS by scanner (not in untested list
        # unless overlay filter uses "Manual" in test_files to suppress it)
        (src / "feat.py").write_text('"""[FR-07]""" def feat(): pass\n')
        # FR-08: has code, no test → genuinely untested
        (src / "pending.py").write_text('"""[FR-08]""" def pending(): pass\n')

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        # No test files for either FR → both would be untested without overlay

        # Overlay marks FR-07 as manually verified via test_files: ["Manual"].
        # The overlay filter in preflight_traceability uses:
        #   "Manual" in str(row.get("test_files", []))
        overlay_path = tmp_path / "TRACEABILITY_MATRIX.overlay.yaml"
        overlay_data = {
            "schema": "harness/traceability/overlay/v1",
            "overrides": [
                {
                    "fr_id": "FR-07",
                    "status": "VERIFIED",
                    "code_files": ["core/feat.py"],
                    "test_files": ["Manual: reviewed by QA team"],
                    "justification": "Verified manually per ASPICE exception",
                },
            ],
        }
        overlay_path.write_text(yaml.safe_dump(overlay_data), encoding="utf-8")

        # --- Mock _dispatch_trace_auto_fix: returns True AND creates test for FR-08
        dispatched_calls = []

        def mock_dispatch(project_path, untested, uncoded, phase=None):
            dispatched_calls.append((list(untested), list(uncoded)))
            # Simulate auto-fix: create test file for FR-08 on disk
            (Path(project_path) / "tests" / "test_pending.py").write_text(
                '"""[FR-08]"""\n'
            )
            return True

        from core import phase_hooks as ph_module
        monkeypatch.setattr(ph_module, "_dispatch_trace_auto_fix", mock_dispatch)

        # --- Run preflight_traceability at P5 (blocking)
        hooks = ph_module.PhaseHooks(str(tmp_path), phase=5)
        result = hooks.preflight_traceability()

        # --- Verify auto-fix was dispatched for FR-08 (FR-07 was overlay-filtered)
        assert len(dispatched_calls) == 1, "auto-fix should have been dispatched"
        dispatched_untested = dispatched_calls[0][0]
        assert "FR-07" not in dispatched_untested, (
            "FR-07 should have been filtered by overlay before dispatch"
        )
        assert "FR-08" in dispatched_untested, "FR-08 should have been dispatched"

        # --- Key assertion: FR-07 must NOT appear in still_untested after re-verify.
        # Without the PR 13 fix: FR-07 reappears in still_untested (overlay not
        #   re-applied to report2) → passed=False.
        # With the fix: re-apply overlay filters FR-07 out → still_untested=[],
        #   still_uncoded=[] → passed=True.
        assert result["passed"] is True, (
            "FR-07 is manually VERIFIED in overlay (test_files=['Manual: ...']); "
            "re-verify must respect the overlay and not re-report FR-07 as "
            f"still_untested. Dispatched untested was: {dispatched_untested}"
        )

    def test_preflight_traceability_recognizes_emoji_prefixed_overlay_status(
        self, tmp_path,
    ):
        """Round 27: overlay status "✅ verified" (the convention real projects
        actually use — see enforcement/framework_enforcer.py's Round 26 fix
        for why) must exempt an FR the same way a plain "VERIFIED" literal
        does. Before the fix, `status == "VERIFIED"` never matched this and
        the FR was silently NOT exempted."""
        import yaml
        from core import phase_hooks as ph_module

        arch = tmp_path / "02-architecture"
        arch.mkdir(parents=True)
        (arch / "SAD.md").write_text("FR-09: manually-verified via emoji overlay\n")

        src = tmp_path / "core"
        src.mkdir()
        (src / "feat.py").write_text('"""[FR-09]""" def feat(): pass\n')

        (tmp_path / "tests").mkdir()  # no test file → would be untested

        overlay_path = tmp_path / "TRACEABILITY_MATRIX.overlay.yaml"
        overlay_data = {
            "schema": "harness/traceability/overlay/v1",
            "overrides": [
                {"fr_id": "FR-09", "status": "✅ verified",
                 "justification": "Verified manually per ASPICE exception"},
            ],
        }
        overlay_path.write_text(yaml.safe_dump(overlay_data), encoding="utf-8")

        hooks = ph_module.PhaseHooks(str(tmp_path), phase=5)
        result = hooks.preflight_traceability()

        # Scoped to the PR 13 exemption itself — attestation status is a
        # separate blocking condition this fixture doesn't set up, so
        # `passed` is not asserted here.
        assert "FR-09" not in result["untested"], (
            "FR-09 carries an emoji-prefixed '✅ verified' overlay status; "
            "the PR 13 exemption must recognize it, not just the bare "
            "'VERIFIED' literal."
        )


# ── Round 14 A: preview_next_phase_blocking tests ──────────────────────────


def test_preview_next_phase_blocking_property_spec_obligation(tmp_path) -> None:
    """When in P3 with a property declared but no hypothesis test, previewing
    P4 must surface a 'property_spec' obligation (the root cause of the
    2026-07-26 P3→P4 push block on taskq)."""
    from core.phase_hooks import PhaseHooks
    from core.utils.project_layout import ProjectLayout

    spec_path = ProjectLayout(tmp_path).test_spec_path
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        "# TEST_SPEC\n\n"
        "## Functional Requirement Test Cases\n\n"
        "### FR-01: round-trip\n\n"
        "| # | Test Function | Inputs | Type | Derivation |\n"
        "|---|---|---|---|---|\n"
        "| 1 | `test_fr01_x` | source=\"abc\" | happy_path | Q1 |\n\n"
        "**Properties**\n"
        "| property_id | invariant | applies_to |\n"
        "|---|---|---|\n"
        "| P1 | `len(source) == 3` | 1 |\n",
        encoding="utf-8",
    )
    # No hypothesis test in tests/ → property_spec must block at P4
    hooks = PhaseHooks(str(tmp_path), phase=3, enable_kill_switch=False)
    obls = hooks.preview_next_phase_blocking(next_phase=4)
    property_obls = [o for o in obls if o.check_id == "property_spec"]
    assert property_obls, f"expected property_spec obligation, got {obls}"
    assert any(o.rule_id == "FR-01" for o in property_obls)
    for o in property_obls:
        assert o.target_phase == 4


def test_preview_next_phase_blocking_clean_returns_empty(tmp_path) -> None:
    """When no property is declared and no reliability findings exist,
    previewing P4 must return an empty obligation list (no false positives)."""
    from core.phase_hooks import PhaseHooks

    hooks = PhaseHooks(str(tmp_path), phase=3, enable_kill_switch=False)
    obls = hooks.preview_next_phase_blocking(next_phase=4)
    # Sanity: with no project artifacts, the only simulation-blocking
    # findings should be originating from PREFLIGHT_CHECKS that match the
    # delayed-blocking allowlist. For an empty /tmp project, no obligations
    # are expected (no property declared, no source files, no manifest).
    prop_obls = [o for o in obls if o.check_id == "property_spec"]
    assert prop_obls == [], (
        f"empty project should not surface property_spec obligation, got {obls}"
    )


def test_preview_next_phase_blocking_rejects_invalid_phase(tmp_path) -> None:
    """Out-of-range next_phase must raise ValueError (PARAMETER-GUARD contract)."""
    from core.phase_hooks import PhaseHooks

    hooks = PhaseHooks(str(tmp_path), phase=3, enable_kill_switch=False)
    import pytest
    with pytest.raises(ValueError):
        hooks.preview_next_phase_blocking(next_phase=99)
    with pytest.raises(ValueError):
        hooks.preview_next_phase_blocking(next_phase=0)


# ── Round 15 §3: _obligations_from_preflight per-check extractors ──────────
#
# Round 14 A shipped only property_spec + reliability_lint extractors; the
# other 8 _DELAYED_BLOCKING_PREFLIGHTS members fell through to a generic
# fallback that produced no actionable detail (e.g. "drift_detection would
# block at phase 4"). These tests fabricate each preflight's real
# blocking-path return shape (confirmed by reading the corresponding
# preflight_* method) and assert the extractor produces a useful obligation.


def test_obligations_drift_detection_extracts_per_item() -> None:
    from core.phase_hooks import _obligations_from_preflight

    res = {
        "passed": False, "drifts": 1, "score": 88.9, "threshold": 95.0,
        "details": {
            "sab": {"drift_type": "sab", "has_drift": True, "items": [
                {"type": "sab", "severity": "LOW",
                 "location": "03-development/conftest.py",
                 "description": "New file not registered in any SAB layer"},
            ]},
        },
    }
    obls = _obligations_from_preflight("drift_detection", res, target_phase=4)
    assert len(obls) == 1
    assert obls[0].rule_id == "sab"
    assert obls[0].file == "03-development/conftest.py"
    assert "not registered" in obls[0].message


def test_obligations_sab_check_extracts_per_violation() -> None:
    from core.phase_hooks import _obligations_from_preflight

    res = {"passed": False, "violations": [
        "Layer L1: deps ['L9'] reference unknown layers",
    ], "layers": 2}
    obls = _obligations_from_preflight("sab_check", res, target_phase=3)
    assert len(obls) == 1
    assert "unknown layers" in obls[0].message


def test_obligations_traceability_extracts_untested_uncoded_and_attestation() -> None:
    from core.phase_hooks import _obligations_from_preflight

    res = {
        "passed": False, "untested": ["FR-02"], "uncoded": ["FR-05"],
        "attestation": "mismatch", "attestation_message": "hash drift",
    }
    obls = _obligations_from_preflight("traceability", res, target_phase=5)
    rule_ids = {o.rule_id for o in obls}
    assert rule_ids == {"FR-02", "FR-05", "attestation"}
    att = next(o for o in obls if o.rule_id == "attestation")
    assert "hash drift" in att.message


def test_obligations_traceability_clean_attestation_produces_no_extra_obligation() -> None:
    from core.phase_hooks import _obligations_from_preflight

    res = {"passed": True, "untested": [], "uncoded": [], "attestation": "clean"}
    obls = _obligations_from_preflight("traceability", res, target_phase=5)
    assert obls == []


def test_obligations_fr_spec_consistency_extracts_orphans_both_directions() -> None:
    from core.phase_hooks import _obligations_from_preflight

    res = {"passed": False, "sad_only": ["FR-07"], "spec_only": ["FR-08"]}
    obls = _obligations_from_preflight("fr_spec_consistency", res, target_phase=5)
    assert {o.rule_id for o in obls} == {"FR-07", "FR-08"}
    fr07 = next(o for o in obls if o.rule_id == "FR-07")
    assert "TEST_SPEC.md" in fr07.message


def test_obligations_artifact_consistency_reads_error_details() -> None:
    from core.phase_hooks import _obligations_from_preflight

    res = {"passed": False, "errors": 1, "needs_review": 0, "error_details": [
        {"rule_id": "illegal_forward_ref", "message": "invented ARCHITECTURE.md"},
    ]}
    obls = _obligations_from_preflight("artifact_consistency", res, target_phase=2)
    assert len(obls) == 1
    assert obls[0].rule_id == "illegal_forward_ref"
    assert "invented" in obls[0].message


def test_obligations_artifact_consistency_missing_error_details_degrades_empty() -> None:
    """Defensive: a preflight_artifact_consistency return dict from before
    Round 15 §3 (no error_details key) must not crash the extractor — it
    degrades to an empty list rather than KeyError."""
    from core.phase_hooks import _obligations_from_preflight

    res = {"passed": False, "errors": 1, "needs_review": 0}
    obls = _obligations_from_preflight("artifact_consistency", res, target_phase=2)
    assert obls == []


def test_obligations_config_liveness_extracts_orphan_keys_with_location() -> None:
    from core.phase_hooks import _obligations_from_preflight

    res = {"passed": False, "orphans": {"KOKORO_URL": "src/config.py:42"},
           "used_count": 3, "declaration_files": [".env.example"]}
    obls = _obligations_from_preflight("config_liveness", res, target_phase=4)
    assert len(obls) == 1
    assert obls[0].rule_id == "KOKORO_URL"
    assert obls[0].file == "src/config.py"
    assert obls[0].line == 42


def test_obligations_previous_phase_artifacts_extracts_missing_links() -> None:
    from core.phase_hooks import _obligations_from_preflight

    res = {"passed": False, "missing": ["PLAN->IMPLEMENT: link broken"],
           "verified": [], "stats": {"total": 4, "verified": 3, "missing": 1}}
    obls = _obligations_from_preflight(
        "previous_phase_artifacts", res, target_phase=4)
    assert len(obls) == 1
    assert "PLAN->IMPLEMENT" in obls[0].message


def test_obligations_bvs_phase_order_extracts_violations() -> None:
    from core.phase_hooks import _obligations_from_preflight

    res = {"passed": False, "violations": [
        {"rule": "HR-03", "message": "Phase 5 entered before Phase 4 exit gate"},
    ]}
    obls = _obligations_from_preflight("bvs_phase_order", res, target_phase=5)
    assert len(obls) == 1
    assert obls[0].rule_id == "HR-03"
    assert "before Phase 4" in obls[0].message
