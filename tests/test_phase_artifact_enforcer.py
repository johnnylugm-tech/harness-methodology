"""Tests for core/quality_gate/phase_artifact_enforcer.py — ASPICE traceability chain."""

from unittest.mock import patch

from core.quality_gate.phase_artifact_enforcer import (  # pyright: ignore[reportMissingImports]
    Phase,
    PhaseArtifactRegistry,
    PhaseLinkResult,
)


class TestPhase:
    def test_all_phases_exist(self):
        assert len(list(Phase)) == 9
        assert Phase.CONSTITUTION.value == 0
        assert Phase.SPECIFY.value == 1
        assert Phase.PLAN.value == 2
        assert Phase.IMPLEMENT.value == 3
        assert Phase.VERIFY.value == 4
        assert Phase.SYSTEM_TEST.value == 5
        assert Phase.QUALITY.value == 6
        assert Phase.RISK.value == 7
        assert Phase.CONFIG.value == 8

    def test_phase_values_are_sequential(self):
        values = [p.value for p in Phase]
        assert values == list(range(9))


class TestPhaseArtifactRegistry:
    def test_all_phases_in_registry(self):
        """PHASE_ARTIFACTS covers SPECIFY through CONFIG (8 phases)."""
        registry = PhaseArtifactRegistry("/tmp").PHASE_ARTIFACTS
        for p in list(Phase)[1:]:  # Skip CONSTITUTION (pre-phase)
            assert p in registry, f"Phase {p.name} missing from PHASE_ARTIFACTS"

    def test_depends_on_chain_no_cycles(self):
        """Verify the ASPICE dependency chain has no cycles."""
        registry = PhaseArtifactRegistry("/tmp").PHASE_ARTIFACTS
        for phase, info in registry.items():
            for dep in info.get("depends_on", []):
                # Each dependency must point to a phase that exists
                assert dep in registry, f"{phase.name} depends on {dep.name} which is not in registry"
                # The dependency's own depends_on should NOT include the dependent
                dep_info = registry.get(dep, {})
                assert phase not in dep_info.get("depends_on", []), \
                    f"Cycle detected: {phase.name} <-> {dep.name}"

    def test_depends_on_chain_is_linear(self):
        """Each phase (except P1) should depend on the previous phase."""
        phases = [Phase.SPECIFY, Phase.PLAN, Phase.IMPLEMENT, Phase.VERIFY,
                  Phase.SYSTEM_TEST, Phase.QUALITY, Phase.RISK, Phase.CONFIG]
        for i in range(1, len(phases)):
            current = phases[i]
            prev = phases[i - 1]
            deps = PhaseArtifactRegistry("/tmp").PHASE_ARTIFACTS.get(current, {}).get("depends_on", [])
            assert prev in deps, f"{current.name} should depend on {prev.name}, deps={[d.name for d in deps]}"

    def test_specify_has_no_dependencies(self):
        deps = PhaseArtifactRegistry("/tmp").PHASE_ARTIFACTS[Phase.SPECIFY]["depends_on"]
        assert deps == []

    def test_implement_depends_on_specify_and_plan(self):
        deps = PhaseArtifactRegistry("/tmp").PHASE_ARTIFACTS[Phase.IMPLEMENT]["depends_on"]
        assert Phase.SPECIFY in deps
        assert Phase.PLAN in deps


class TestVerifyPhaseLink:
    def test_missing_both_phases_fails(self, tmp_path):
        registry = PhaseArtifactRegistry(str(tmp_path))
        result = registry.verify_phase_link(Phase.SPECIFY, Phase.PLAN)
        assert result.passed is False
        assert "missing" in result.reason.lower()

    def test_existing_artifacts_with_reference_passes(self, tmp_path):
        # Create SPECIFY artifacts
        req_dir = tmp_path / "01-requirements"
        req_dir.mkdir()
        (req_dir / "SRS.md").write_text("# SRS\n\nRequirements specification for the project.\n")
        (req_dir / "SPEC_TRACKING.md").write_text("# Spec Tracking\n\nTracking FR items.\n")
        (req_dir / "TRACEABILITY_MATRIX.md").write_text("# Traceability\n\nMatrix.\n")

        # Create PLAN artifact that references SRS
        arch_dir = tmp_path / "02-architecture"
        arch_dir.mkdir()
        (arch_dir / "SAD.md").write_text(
            "# Architecture\n\nBased on the SRS requirements, this document defines...\n"
            "References SPEC_TRACKING for traceability.\n"
        )

        registry = PhaseArtifactRegistry(str(tmp_path))
        result = registry.verify_phase_link(Phase.SPECIFY, Phase.PLAN)
        assert result.passed is True, f"Expected pass, got: {result.reason}"
        assert "verified" in result.reason.lower()

    def test_existing_artifacts_without_reference_now_passes(self, tmp_path):
        """The substring 'reference' check was removed — existence is enough.

        Previously an artifact that didn't mention the predecessor's filename
        failed; now traceability means both artifacts exist (the substring scan
        was pure theatre an agent passes by pasting a keyword).
        """
        req_dir = tmp_path / "01-requirements"
        req_dir.mkdir()
        (req_dir / "SRS.md").write_text("# SRS\n\nRequirements.\n")

        arch_dir = tmp_path / "02-architecture"
        arch_dir.mkdir()
        (arch_dir / "SAD.md").write_text(
            "# Architecture\n\nDesign document with no references to requirements.\n"
        )

        registry = PhaseArtifactRegistry(str(tmp_path))
        result = registry.verify_phase_link(Phase.SPECIFY, Phase.PLAN)
        assert result.passed is True
        assert "verified" in result.reason.lower()

    def test_specify_to_nonexistent_specify_fails_missing_artifacts(self, tmp_path):
        """SPECIFY->SPECIFY self-link fails when P1 artifacts don't exist."""
        registry = PhaseArtifactRegistry(str(tmp_path))
        result = registry.verify_phase_link(Phase.SPECIFY, Phase.SPECIFY)
        assert result.passed is False
        assert "missing artifacts" in result.reason.lower()

    def test_specify_with_artifacts_self_link_passes(self, tmp_path):
        """SPECIFY->SPECIFY self-link passes when artifacts exist with references."""
        req_dir = tmp_path / "01-requirements"
        req_dir.mkdir()
        (req_dir / "SRS.md").write_text("# SRS\n\nRequirements with SRS references.\n")
        (req_dir / "SPEC_TRACKING.md").write_text("# Tracking\n\nTracks SRS items.\n")
        (req_dir / "TRACEABILITY_MATRIX.md").write_text("# Matrix\n\nMaps SRS.\n")

        registry = PhaseArtifactRegistry(str(tmp_path))
        result = registry.verify_phase_link(Phase.SPECIFY, Phase.SPECIFY)
        assert result.passed is True

    def test_skip_to_side_skips_to_artifact_check(self, tmp_path):
        """P1 artifacts exist, P2 not yet — flag skips P2 artifact existence."""
        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "01-requirements" / "SRS.md").write_text("# SRS\n\nRequirements.\n")
        (tmp_path / "01-requirements" / "SPEC_TRACKING.md").write_text("# Tracking\n")
        (tmp_path / "01-requirements" / "TRACEABILITY_MATRIX.md").write_text("# Matrix\n")
        # 02-architecture/SAD.md NOT created

        registry = PhaseArtifactRegistry(str(tmp_path))

        # Without flag → fails (requires P2 artifacts)
        strict = registry.verify_phase_link(Phase.SPECIFY, Phase.PLAN)
        assert strict.passed is False

        # With flag → passes (P2 artifacts are the current phase's output)
        relaxed = registry.verify_phase_link(
            Phase.SPECIFY, Phase.PLAN, skip_to_side=True,
        )
        assert relaxed.passed is True, (
            f"skip_to_side should skip P2 check: {relaxed.reason}"
        )


class TestVerifyPhaseChain:
    def test_phase1_chain_all_verified(self, tmp_path):
        """P1 has no dependencies, so chain should be trivially verified."""
        # Create P1 artifacts
        req_dir = tmp_path / "01-requirements"
        req_dir.mkdir()
        (req_dir / "SRS.md").write_text("# SRS\n\nRequirements.\n")

        registry = PhaseArtifactRegistry(str(tmp_path))
        result = registry.verify_phase_chain(current_phase=1)
        assert result["all_verified"] is True

    def test_phase2_chain_with_missing_p1_fails(self, tmp_path):
        """P2 chain should fail if P1 artifacts are missing."""
        registry = PhaseArtifactRegistry(str(tmp_path))
        result = registry.verify_phase_chain(current_phase=2)
        assert result["all_verified"] is False
        assert len(result["missing_links"]) > 0

    def test_phase2_entry_p1_exists_p2_not_yet_created(self, tmp_path):
        """P2 entry gate: P1 artifacts exist but P2 (SAD.md) not yet created.

        SAD.md is the OUTPUT of P2, so it legitimately does not exist when
        entering P2. The check should only verify predecessor (P1) artifacts.
        """
        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "01-requirements" / "SRS.md").write_text("# SRS\n\nRequirements.\n")
        (tmp_path / "01-requirements" / "SPEC_TRACKING.md").write_text("# Tracking\n")
        (tmp_path / "01-requirements" / "TRACEABILITY_MATRIX.md").write_text("# Matrix\n")
        # NB: 02-architecture/SAD.md does NOT exist — we're entering P2

        registry = PhaseArtifactRegistry(str(tmp_path))
        result = registry.verify_phase_chain(current_phase=2)
        assert result["all_verified"] is True, (
            f"P2 entry should pass with only P1 artifacts; missing: {result['missing_links']}"
        )

    def test_phase3_entry_with_p1_p2_artifacts_no_p3(self, tmp_path):
        """P3 entry: P1+P2 artifacts exist, but P3 not yet created.

        P3 (IMPLEMENT) has multi-dependency on both SPECIFY and PLAN.
        Both SPECIFY→IMPLEMENT and PLAN→IMPLEMENT should tolerate the
        missing P3 output simultaneously via skip_to_side.

        The SPECIFY→PLAN link is also checked (not skip_to_side, since P2
        is a completed predecessor), so P2 must exist and reference P1.
        """
        # P1: SPECIFY
        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "01-requirements" / "SRS.md").write_text("# SRS\n\nRequirements.\n")
        (tmp_path / "01-requirements" / "SPEC_TRACKING.md").write_text("# Tracking\n")
        (tmp_path / "01-requirements" / "TRACEABILITY_MATRIX.md").write_text("# Matrix\n")

        # P2: PLAN (references P1)
        (tmp_path / "02-architecture").mkdir()
        (tmp_path / "02-architecture" / "SAD.md").write_text(
            "# Architecture\n\nBased on SRS and SPEC_TRACKING.\n"
        )

        # P3: NOT created — we're entering P3
        # 03-development/ does NOT exist

        registry = PhaseArtifactRegistry(str(tmp_path))
        result = registry.verify_phase_chain(current_phase=3)
        assert result["all_verified"] is True, (
            f"P3 entry should pass with only P1+P2 artifacts; missing: {result['missing_links']}"
        )

    def test_phase3_entry_no_longer_fails_on_missing_reference(self, tmp_path):
        """P3 entry: the predecessor-reference substring check was removed.

        P2's SAD.md no longer needs to mention P1 artifacts by name. As long as
        both phases' artifacts exist, the SPECIFY→PLAN link verifies — no link
        should fail with a 'traceability reference' reason any more.
        """
        # P1: SPECIFY
        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "01-requirements" / "SRS.md").write_text("# SRS\n\nRequirements.\n")
        (tmp_path / "01-requirements" / "SPEC_TRACKING.md").write_text("# Tracking\n")
        (tmp_path / "01-requirements" / "TRACEABILITY_MATRIX.md").write_text("# Matrix\n")

        # P2: PLAN — no textual reference to P1 artifacts (now acceptable)
        (tmp_path / "02-architecture").mkdir()
        (tmp_path / "02-architecture" / "SAD.md").write_text(
            "# Architecture\n\nA standalone architecture document.\n"
        )

        registry = PhaseArtifactRegistry(str(tmp_path))
        result = registry.verify_phase_chain(current_phase=3)
        assert not any(
            "traceability reference" in m.lower() for m in result["missing_links"]
        ), f"reference-substring failures should be gone; got: {result['missing_links']}"

    def test_phase4_chain_with_all_artifacts_passes(self, tmp_path):
        """Full P1-P4 chain should pass when all artifacts exist with references."""
        # P1: SPECIFY
        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "01-requirements" / "SRS.md").write_text("# SRS\n\nRequirements spec.\n")
        (tmp_path / "01-requirements" / "SPEC_TRACKING.md").write_text("# Tracking\n")
        (tmp_path / "01-requirements" / "TRACEABILITY_MATRIX.md").write_text("# Matrix\n")

        # P2: PLAN (references SRS)
        (tmp_path / "02-architecture").mkdir()
        (tmp_path / "02-architecture" / "SAD.md").write_text(
            "# Architecture\n\nBased on SRS and SPEC_TRACKING.\n"
        )

        # P3: IMPLEMENT — src and tests dirs are the required artifacts
        (tmp_path / "03-development").mkdir()
        (tmp_path / "03-development" / "src").mkdir()
        (tmp_path / "03-development" / "tests").mkdir()

        # P4: VERIFY (references IMPLEMENT)
        (tmp_path / "04-testing").mkdir()
        (tmp_path / "04-testing" / "TEST_PLAN.md").write_text(
            "# Test Plan\n\nVerification plan for Phase 4.\n"
        )
        (tmp_path / "04-testing" / "TEST_RESULTS.md").write_text("# Results\n\nTests passed.\n")

        registry = PhaseArtifactRegistry(str(tmp_path))
        result = registry.verify_phase_chain(current_phase=4)
        assert result["all_verified"] is True, f"Missing: {result['missing_links']}"
        assert result["stats"]["missing"] == 0

    def test_verify_phase_chain_crash_reported_as_missing(self, tmp_path):
        """verify_phase_chain exception → all_verified=False, missing contains CRASH:."""
        registry = PhaseArtifactRegistry(str(tmp_path))
        with patch.object(registry, "verify_phase_link",
                          side_effect=RuntimeError("simulated crash")):
            result = registry.verify_phase_chain(current_phase=4)
            assert result["all_verified"] is False
            assert any("CRASH:" in m for m in result["missing_links"])


class TestPhaseLinkResult:
    def test_dataclass_fields(self):
        r = PhaseLinkResult(
            from_phase=Phase.SPECIFY,
            to_phase=Phase.PLAN,
            passed=True,
            reason="verified",
        )
        assert r.from_phase == Phase.SPECIFY
        assert r.to_phase == Phase.PLAN
        assert r.passed is True
        assert r.reason == "verified"
        assert r.expected_artifacts == []
        assert r.found_artifacts == []

    def test_dataclass_with_artifacts(self):
        r = PhaseLinkResult(
            from_phase=Phase.SPECIFY,
            to_phase=Phase.PLAN,
            passed=True,
            reason="ok",
            expected_artifacts=["SRS.md", "SAD.md"],
            found_artifacts=["SRS.md"],
            missing_artifacts=["SAD.md"],
        )
        assert r.expected_artifacts == ["SRS.md", "SAD.md"]
        assert r.missing_artifacts == ["SAD.md"]
