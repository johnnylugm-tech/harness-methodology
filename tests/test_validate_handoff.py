"""Regression: validate-handoff CLI (v2.9.1 B.1) catches cross-deliverable
dependency breaks that the e2e run surfaced.

E2E framework finding (integration-test run, 2026-06-12): P1 orchestrator
failed to produce TEST_INVENTORY.yaml, P2 orchestrator produced a wrong-
shape TEST_SPEC.md (prose strategy doc instead of derive_test_cases.md
table). The framework had no programmatic check that P1's inventory fed
P2's spec — Agent B peer review is per-deliverable, not cross-deliverable.

`validate-handoff --from-phase N` fills that gap:

  --from-phase 1: P1→P2: TEST_INVENTORY.yaml exists, non-empty, covers all SRS FRs
  --from-phase 2: P2→P3: TEST_SPEC.md has parseable named test cases
  --from-phase 3: P3→P4: every FR has per-FR Gate 1 sentinel
  --from-phase 4: P4→P5: VERIFICATION_REPORT.md exists, non-trivial content
  --from-phase 5: P5→P6: BASELINE.md exists

Workflow JS now calls this as a pre-launch precondition.
"""

from __future__ import annotations

import json
from pathlib import Path


from harness_cli import _validate_handoff, build_parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_srs(project: Path, frs: list[str]) -> None:
    (project / "01-requirements").mkdir(parents=True, exist_ok=True)
    (project / "01-requirements" / "SRS.md").write_text(
        "# SRS\n\n## FRs\n\n" + "\n".join(f"### {fr}: description" for fr in frs) + "\n",
        encoding="utf-8",
    )


def _seed_inventory(project: Path, content: str) -> None:
    (project / "01-requirements").mkdir(parents=True, exist_ok=True)
    (project / "01-requirements" / "TEST_INVENTORY.yaml").write_text(
        content, encoding="utf-8"
    )


def _seed_test_spec(project: Path, content: str) -> None:
    (project / "02-architecture").mkdir(parents=True, exist_ok=True)
    (project / "02-architecture" / "TEST_SPEC.md").write_text(content, encoding="utf-8")


def _seed_manifest_frs(project: Path, frs: list[str]) -> None:
    (project / ".methodology").mkdir(parents=True, exist_ok=True)
    (project / ".methodology" / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": frs}), encoding="utf-8"
    )


def _seed_fr_sentinel(project: Path, fr_id: str) -> None:
    (project / ".sessi-work" / "sentinels").mkdir(parents=True, exist_ok=True)
    (project / ".sessi-work" / "sentinels" / f"g1_{fr_id.lower()}.flag").write_text(
        "sentinel\n", encoding="utf-8"
    )


def _seed_gate2_pass(project: Path) -> None:
    (project / ".methodology").mkdir(parents=True, exist_ok=True)
    (project / ".methodology" / "gate2_result.json").write_text(
        json.dumps({"gate": 2, "composite_score": 92.25}), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# B.1 — P1→P2 handoff
# ---------------------------------------------------------------------------


class TestHandoffP1ToP2:
    def test_missing_inventory_blocks(self, tmp_path: Path):
        """E2E finding core case: P1 never produced TEST_INVENTORY.yaml → block."""
        _seed_srs(tmp_path, ["FR-01"])
        errs = _validate_handoff(tmp_path, from_phase=1)
        assert any("TEST_INVENTORY.yaml missing" in e for e in errs)

    def test_empty_inventory_blocks(self, tmp_path: Path):
        """Empty inventory (P1 produced a stub) → block."""
        _seed_srs(tmp_path, ["FR-01"])
        _seed_inventory(tmp_path, "")
        errs = _validate_handoff(tmp_path, from_phase=1)
        assert any("empty" in e.lower() for e in errs)

    def test_inventory_missing_frs_blocks(self, tmp_path: Path):
        """Inventory exists but doesn't cover all FRs from SRS → block."""
        _seed_srs(tmp_path, ["FR-01", "FR-02", "FR-03"])
        _seed_inventory(
            tmp_path,
            "fr_tests:\n  FR-01:\n    unit:\n      - test_fr01_happy\n",
        )
        errs = _validate_handoff(tmp_path, from_phase=1)
        assert any("FR-02" in e and "FR-03" in e for e in errs)

    def test_inventory_with_no_fr_or_crosscutting_sections_blocks(self, tmp_path: Path):
        """Inventory exists but is structurally wrong (no fr_tests or cross_cutting) → block."""
        _seed_srs(tmp_path, ["FR-01"])
        _seed_inventory(tmp_path, "some_other_key: value\n")
        errs = _validate_handoff(tmp_path, from_phase=1)
        assert any("fr_tests" in e and "cross_cutting" in e for e in errs)

    def test_complete_inventory_passes(self, tmp_path: Path):
        """Happy path: inventory covers all FRs → no errors."""
        _seed_srs(tmp_path, ["FR-01", "FR-02"])
        _seed_inventory(
            tmp_path,
            "fr_tests:\n"
            "  FR-01:\n    unit:\n      - test_fr01_happy\n"
            "  FR-02:\n    unit:\n      - test_fr02_happy\n",
        )
        errs = _validate_handoff(tmp_path, from_phase=1)
        assert errs == []

    def test_inventory_with_cross_cutting_passes(self, tmp_path: Path):
        """Inventory with cross_cutting but no fr_tests is OK (e.g. shared lib)."""
        _seed_srs(tmp_path, [])
        _seed_inventory(
            tmp_path,
            "cross_cutting:\n  logging:\n    - test_log_redaction\n",
        )
        errs = _validate_handoff(tmp_path, from_phase=1)
        assert errs == []


# ---------------------------------------------------------------------------
# B.1 — P2→P3 handoff
# ---------------------------------------------------------------------------


class TestHandoffP2ToP3:
    def test_missing_test_spec_blocks(self, tmp_path: Path):
        """P2 never produced TEST_SPEC.md → block."""
        # Need a SAD.md with FRs for the inner check to engage
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        (tmp_path / "02-architecture" / "SAD.md").write_text(
            "# SAD\n### FR-01: a\n", encoding="utf-8"
        )
        errs = _validate_handoff(tmp_path, from_phase=2)
        assert any("TEST_SPEC.md missing" in e for e in errs)

    def test_prose_test_spec_with_frs_blocks(self, tmp_path: Path):
        """E2E finding core case: prose TEST_SPEC.md with no table → block."""
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        (tmp_path / "02-architecture" / "SAD.md").write_text(
            "# SAD\n### FR-01: a\n", encoding="utf-8"
        )
        _seed_test_spec(
            tmp_path,
            "# TEST_SPEC\n\n## §1 Strategy Overview\nProse, no table.\n",
        )
        errs = _validate_handoff(tmp_path, from_phase=2)
        # Should reference wrong shape / derive_test_cases
        assert any("prose" in e or "wrong shape" in e or "0 parseable" in e for e in errs)

    def test_well_formed_test_spec_passes(self, tmp_path: Path):
        """Happy path: TEST_SPEC.md has derive_test_cases.md table rows → OK."""
        _seed_test_spec(
            tmp_path,
            "# TEST_SPEC\n\n"
            "### FR-01: example\n"
            "| # | Test Function | Type | Derivation |\n"
            "|---|---|---|---|\n"
            "| 1 | `test_fr01_happy` | happy_path | Q1 |\n",
        )
        errs = _validate_handoff(tmp_path, from_phase=2)
        assert errs == []


# ---------------------------------------------------------------------------
# B.1 — P3→P4 handoff (delegates to B.2 precondition)
# ---------------------------------------------------------------------------


class TestHandoffP3ToP4:
    def test_no_manifest_blocks(self, tmp_path: Path):
        """No quality_manifest.json + no --fr-ids → cannot resolve FRs → block."""
        # No _seed_manifest_frs
        errs = _validate_handoff(tmp_path, from_phase=3)
        assert any("Could not resolve FR IDs" in e for e in errs)

    def test_all_sentinels_present_passes(self, tmp_path: Path):
        """All FRs have Gate 1 sentinels + Gate 2 PASS → OK."""
        _seed_manifest_frs(tmp_path, ["FR-01", "FR-02"])
        _seed_gate2_pass(tmp_path)
        _seed_fr_sentinel(tmp_path, "FR-01")
        _seed_fr_sentinel(tmp_path, "FR-02")
        errs = _validate_handoff(tmp_path, from_phase=3)
        assert errs == []

    def test_missing_sentinel_blocks(self, tmp_path: Path):
        """One FR missing sentinel → block."""
        _seed_manifest_frs(tmp_path, ["FR-01", "FR-02"])
        _seed_gate2_pass(tmp_path)
        _seed_fr_sentinel(tmp_path, "FR-01")
        # FR-02 missing
        errs = _validate_handoff(tmp_path, from_phase=3)
        assert any("FR-02" in e for e in errs)


# ---------------------------------------------------------------------------
# B.1 — P4→P5 + P5→P6 handoff
# ---------------------------------------------------------------------------


class TestHandoffP4ToP5:
    def test_missing_report_blocks(self, tmp_path: Path):
        errs = _validate_handoff(tmp_path, from_phase=4)
        assert any("VERIFICATION_REPORT.md missing" in e for e in errs)

    def test_short_report_blocks(self, tmp_path: Path):
        (tmp_path / "04-verification").mkdir(parents=True, exist_ok=True)
        (tmp_path / "04-verification" / "VERIFICATION_REPORT.md").write_text(
            "tiny", encoding="utf-8"
        )
        errs = _validate_handoff(tmp_path, from_phase=4)
        assert any("suspiciously short" in e for e in errs)

    def test_full_report_passes(self, tmp_path: Path):
        (tmp_path / "04-verification").mkdir(parents=True, exist_ok=True)
        (tmp_path / "04-verification" / "VERIFICATION_REPORT.md").write_text(
            "x" * 2000, encoding="utf-8"
        )
        errs = _validate_handoff(tmp_path, from_phase=4)
        assert errs == []


class TestHandoffP5ToP6:
    def test_missing_baseline_blocks(self, tmp_path: Path):
        errs = _validate_handoff(tmp_path, from_phase=5)
        assert any("BASELINE.md missing" in e for e in errs)

    def test_baseline_at_05_dir_passes(self, tmp_path: Path):
        (tmp_path / "05-verification").mkdir(parents=True, exist_ok=True)
        (tmp_path / "05-verification" / "BASELINE.md").write_text("# BASELINE\n", encoding="utf-8")
        errs = _validate_handoff(tmp_path, from_phase=5)
        assert errs == []

    def test_baseline_at_root_passes(self, tmp_path: Path):
        (tmp_path / "BASELINE.md").write_text("# BASELINE\n", encoding="utf-8")
        errs = _validate_handoff(tmp_path, from_phase=5)
        assert errs == []


# ---------------------------------------------------------------------------
# Bug #115 — P6→P7 and P7→P8 validators (extension to phases 6 and 7)
# ---------------------------------------------------------------------------


class TestHandoffP6ToP7:
    """Bug #115: P6→P7 (QUALITY_REPORT.md / RELEASE_NOTES.md / FINAL_SIGN_OFF.md /
    gate4_result.json PASS) and P7→P8 (risk register deliverables) validators."""

    def test_missing_quality_artifacts_blocks(self, tmp_path: Path):
        errs = _validate_handoff(tmp_path, from_phase=6)
        # All three P6 deliverables missing → at least 3 errors
        assert len(errs) >= 3
        assert any("QUALITY_REPORT.md missing" in e for e in errs)
        assert any("RELEASE_NOTES.md missing" in e for e in errs)
        assert any("FINAL_SIGN_OFF.md missing" in e for e in errs)

    def test_quality_artifacts_present_but_gate4_missing_blocks(self, tmp_path: Path):
        (tmp_path / "06-quality").mkdir(parents=True, exist_ok=True)
        for name in ("QUALITY_REPORT.md", "RELEASE_NOTES.md", "FINAL_SIGN_OFF.md"):
            (tmp_path / "06-quality" / name).write_text(f"# {name}\n", encoding="utf-8")
        errs = _validate_handoff(tmp_path, from_phase=6)
        # gate4_result.json still missing → still blocked
        assert any("gate4_result.json missing" in e for e in errs)

    def test_quality_artifacts_with_gate4_pass_passes(self, tmp_path: Path):
        (tmp_path / "06-quality").mkdir(parents=True, exist_ok=True)
        for name in ("QUALITY_REPORT.md", "RELEASE_NOTES.md", "FINAL_SIGN_OFF.md"):
            (tmp_path / "06-quality" / name).write_text(f"# {name}\n", encoding="utf-8")
        (tmp_path / ".sessi-work").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".sessi-work" / "gate4_result.json").write_text(
            json.dumps({"verdict": "PASS"}), encoding="utf-8"
        )
        errs = _validate_handoff(tmp_path, from_phase=6)
        assert errs == []

    def test_gate4_fail_blocks_handoff(self, tmp_path: Path):
        """Even with all deliverables present, a FAIL gate4 verdict must block P7 entry."""
        (tmp_path / "06-quality").mkdir(parents=True, exist_ok=True)
        for name in ("QUALITY_REPORT.md", "RELEASE_NOTES.md", "FINAL_SIGN_OFF.md"):
            (tmp_path / "06-quality" / name).write_text(f"# {name}\n", encoding="utf-8")
        (tmp_path / ".sessi-work").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".sessi-work" / "gate4_result.json").write_text(
            json.dumps({"verdict": "FAIL"}), encoding="utf-8"
        )
        errs = _validate_handoff(tmp_path, from_phase=6)
        assert any("verdict" in e and "FAIL" in e for e in errs)


class TestHandoffP7ToP8:
    """Bug #115: P7→P8 (07-risk/RISK_REGISTER.md etc.) validator."""

    def test_missing_risk_artifacts_blocks(self, tmp_path: Path):
        errs = _validate_handoff(tmp_path, from_phase=7)
        assert len(errs) == 3
        assert any("RISK_REGISTER.md missing" in e for e in errs)
        assert any("RISK_MITIGATION_PLANS.md missing" in e for e in errs)
        assert any("RISK_STATUS_REPORT.md missing" in e for e in errs)

    def test_risk_artifacts_present_passes(self, tmp_path: Path):
        (tmp_path / "07-risk").mkdir(parents=True, exist_ok=True)
        for name in ("RISK_REGISTER.md", "RISK_MITIGATION_PLANS.md", "RISK_STATUS_REPORT.md"):
            (tmp_path / "07-risk" / name).write_text(f"# {name}\n", encoding="utf-8")
        errs = _validate_handoff(tmp_path, from_phase=7)
        assert errs == []


# ---------------------------------------------------------------------------
# B.1 — Dispatch + argparser
# ---------------------------------------------------------------------------


class TestHandoffDispatch:
    def test_unsupported_from_phase_rejected(self, tmp_path: Path):
        """from-phase=0 and 8+ are not in the validator map; 6 and 7 are now
        supported as of Bug #115."""
        for n in (0, 8, 9):
            errs = _validate_handoff(tmp_path, from_phase=n)
            assert any(f"from-phase={n}" in e for e in errs)

    def test_from_phase_6_passes_with_all_artifacts(self, tmp_path: Path):
        """Bug #115: from-phase=6 is now in the validator map."""
        (tmp_path / "06-quality").mkdir(parents=True, exist_ok=True)
        for name in ("QUALITY_REPORT.md", "RELEASE_NOTES.md", "FINAL_SIGN_OFF.md"):
            (tmp_path / "06-quality" / name).write_text(f"# {name}\n", encoding="utf-8")
        (tmp_path / ".sessi-work").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".sessi-work" / "gate4_result.json").write_text(
            json.dumps({"verdict": "PASS"}), encoding="utf-8"
        )
        assert _validate_handoff(tmp_path, from_phase=6) == []

    def test_from_phase_7_passes_with_all_artifacts(self, tmp_path: Path):
        """Bug #115: from-phase=7 is now in the validator map."""
        (tmp_path / "07-risk").mkdir(parents=True, exist_ok=True)
        for name in ("RISK_REGISTER.md", "RISK_MITIGATION_PLANS.md", "RISK_STATUS_REPORT.md"):
            (tmp_path / "07-risk" / name).write_text(f"# {name}\n", encoding="utf-8")
        assert _validate_handoff(tmp_path, from_phase=7) == []

    def test_cli_appears_in_argparser(self):
        """Smoke-test: validate-handoff is registered in the argparser."""
        parser = build_parser()
        args = parser.parse_args(
            ["validate-handoff", "--from-phase", "2", "--project", "/tmp/x"]
        )
        assert args.from_phase == 2

    def test_cli_accepts_from_phase_6_and_7(self):
        """Bug #115: argparse choices must include 6 and 7."""
        parser = build_parser()
        for n in (6, 7):
            args = parser.parse_args(
                ["validate-handoff", "--from-phase", str(n), "--project", "/tmp/x"]
            )
            assert args.from_phase == n
        # args.func should be the cmd
        assert callable(args.func)
