"""Adversarial self-E2E probes: every fabrication/bypass attempt must BLOCK.

Two manual E2E audit rounds each found CRITICAL gate-bypass bugs (fake
push-milestone pushes C-1/C-2, advance-phase passing with missing
deliverables, check-constitution scoring an empty directory 100%) that
4,700+ green unit tests missed — unit tests mock; these probes drive the
real CLI against sandbox projects, no LLM involved.

Contract: each probe asserts a NON-ZERO exit AND the specific block
reason, so a probe can't pass vacuously by failing on some unrelated
earlier check. Convention: every future E2E-found bypass bug adds a
probe here in its fix commit.
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
CLI = REPO / "harness_cli.py"

pytestmark = pytest.mark.integration


def _run(sandbox: Path, *cli_args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), *cli_args, "--project", str(sandbox)],
        capture_output=True, text=True, timeout=180, cwd=REPO,
    )


def _sandbox(tmp_path: Path, phase: int) -> Path:
    """Minimal initialised project at *phase* (git repo, state.json)."""
    meth = tmp_path / ".methodology"
    meth.mkdir()
    (meth / "state.json").write_text(json.dumps({
        "state": "RUNNING", "current_phase": phase,
    }), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True,
                   capture_output=True)
    return tmp_path


class TestAdvancePhaseProbes:
    def test_p1_fabricated_stage_pass_without_gate_records_blocks(self, tmp_path):
        """A hand-written STAGE_PASS.md with no real gate evidence in the
        manifest must not let advance-phase through."""
        sandbox = _sandbox(tmp_path, 3)
        summary = sandbox / "00-summary"
        summary.mkdir()
        (summary / "Phase3_STAGE_PASS.md").write_text(
            "# Phase 3 STAGE_PASS\n\nGate 1 Composite Score: **99.9**\n"
            "quality_complete: **True**\n",
            encoding="utf-8",
        )
        result = _run(sandbox, "advance-phase", "--completed", "3")
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "BLOCK" in combined.upper() or "FAIL" in combined.upper(), combined[-2000:]

    def test_p2_missing_deliverables_blocks(self, tmp_path):
        """advance-phase --completed 4 without TEST_RESULTS.md must block
        (E2E round 2 found this PASSING once)."""
        sandbox = _sandbox(tmp_path, 4)
        result = _run(sandbox, "advance-phase", "--completed", "4")
        assert result.returncode != 0
        combined = (result.stdout + result.stderr).upper()
        assert "BLOCK" in combined or "MISSING" in combined or "FAIL" in combined, \
            (result.stdout + result.stderr)[-2000:]


class TestPushMilestoneProbes:
    def test_p3_push_milestone_without_entry_gate_blocks(self, tmp_path):
        """C-1/C-2 class: p5-baseline milestone without Gate 3 PASS must be
        rejected before anything is committed or pushed."""
        sandbox = _sandbox(tmp_path, 5)
        result = _run(sandbox, "push-milestone", "--type", "p5-baseline")
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "gate" in combined.lower() or "BLOCK" in combined.upper(), combined[-2000:]
        # Nothing may have been committed by the failed attempt.
        log = subprocess.run(["git", "log", "--oneline"], cwd=sandbox,
                             capture_output=True, text=True)
        assert log.stdout.strip() == "", "failed push-milestone must not create commits"


class TestConstitutionProbes:
    def test_p4_empty_project_does_not_score_100(self, tmp_path):
        """E2E round 2: check-constitution returned 100% for an empty
        directory. An empty project has no deliverable to grade — it must
        not pass with a perfect score."""
        sandbox = _sandbox(tmp_path, 1)
        result = _run(sandbox, "check-constitution", "--phase", "1")
        combined = result.stdout + result.stderr
        assert result.returncode != 0 or "100" not in combined.split("Score")[-1][:20], \
            combined[-2000:]


class TestMutationProbes:
    def test_p5_corrupt_mutmut_cache_is_error_not_zero_score(self, tmp_path):
        """The re-fixed sqlite-swallow bug, end to end: a corrupt cache must
        surface as an error, never as a clean 0/0 mutant count."""
        from core.quality_gate.mutation_enforcer import _count_mutmut_results

        cache = tmp_path / ".mutmut-cache"
        cache.write_text("this is not a sqlite database", encoding="utf-8")
        with pytest.raises((sqlite3.Error, OSError)):
            _count_mutmut_results(cache)


class TestSpecAlignmentProbes:
    """Direction A: canonical_spec (PRD) → SRS front-edge gate. A dropped or
    invented requirement is decidable (no LLM), so an agent cannot make it pass
    without actually reconciling SRS.md with the canonical source."""

    @staticmethod
    def _ingestion(tmp_path: Path, srs_body: str) -> Path:
        (tmp_path / "PROJECT_BRIEF.md").write_text(
            "canonical_spec: SPEC.md\n", encoding="utf-8")
        (tmp_path / "SPEC.md").write_text(
            "### FR-01: login\n### FR-02: logout\n", encoding="utf-8")
        req = tmp_path / "01-requirements"
        req.mkdir()
        (req / "SRS.md").write_text(srs_body, encoding="utf-8")
        return tmp_path

    def test_dropped_requirement_blocks_cli(self, tmp_path):
        """SRS omits canonical FR-02 → check-spec-alignment must exit non-zero
        naming the dropped requirement (build target no longer matches PRD)."""
        sandbox = self._ingestion(tmp_path, "### FR-01: login\n")
        result = _run(sandbox, "check-spec-alignment")
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "FR-02" in combined and "dropped" in combined.lower(), combined[-2000:]

    def test_invented_requirement_blocks_cli(self, tmp_path):
        """SRS adds FR-09 with no canonical counterpart → must block."""
        sandbox = self._ingestion(
            tmp_path, "### FR-01: login\n### FR-02: logout\n### FR-09: telepathy\n")
        result = _run(sandbox, "check-spec-alignment")
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "FR-09" in combined and "invent" in combined.lower(), combined[-2000:]


class TestPropertyProbes:
    """Direction B: opt-in property gate. Declaring an invariant obliges an
    executing property test and a self-consistent predicate — both decidable,
    so an agent cannot claim a property it never tested or that contradicts its
    own example."""

    @staticmethod
    def _spec(tmp_path: Path, invariant: str) -> Path:
        arch = tmp_path / "02-architecture"
        arch.mkdir()
        (arch / "TEST_SPEC.md").write_text(
            "## Functional Requirement Test Cases\n\n"
            "### FR-01: roundtrip\n\n"
            "| # | Test Function | Inputs | Type | Derivation |\n"
            "|---|---|---|---|---|\n"
            '| 1 | `test_fr01_x` | source="abc" | happy_path | Q1 |\n\n'
            "**Properties**:\n"
            "| property_id | invariant | applies_to |\n"
            "|---|---|---|\n"
            f"| P1 | `{invariant}` | 1 |\n",
            encoding="utf-8",
        )
        return tmp_path

    def test_declared_but_unexecuted_property_blocks_cli(self, tmp_path):
        """A property invariant with no hypothesis/fast-check test executing it
        must block — declaring an invariant proves nothing on its own."""
        sandbox = self._spec(tmp_path, "len(source) == 3")  # holds, but no test
        result = _run(sandbox, "check-property-spec")
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "property_not_executed" in combined, combined[-2000:]

    def test_contradictory_invariant_blocks_cli(self, tmp_path):
        """An invariant false for its own declared case is a spec contradiction
        (reused red_assertion engine) — blocks regardless of any test."""
        sandbox = self._spec(tmp_path, "len(source) == 5")  # false for "abc"
        result = _run(sandbox, "check-property-spec", "--no-require-execution")
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "FR-01" in combined and "BLOCK" in combined.upper(), combined[-2000:]


class TestArtifactConsistencyProbes:
    """Audit issues 2 & 3: both are decidable, so an agent cannot ship an
    invented forward-reference filename or an NFR dropped from ADR's table and
    have the real CLI pass."""

    def test_illegal_forward_ref_blocks_cli(self, tmp_path):
        """A P1 artifact referencing 02-architecture/ARCHITECTURE.md (real
        deliverable: SAD.md) must block — it 404s downstream automation."""
        req = tmp_path / "01-requirements"
        req.mkdir()
        (req / "TRACEABILITY_MATRIX.md").write_text(
            "Architecture doc: `./02-architecture/ARCHITECTURE.md`\n", encoding="utf-8")
        result = _run(tmp_path, "check-artifact-consistency")
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "ARCHITECTURE.md" in combined, combined[-2000:]

    def test_nfr_dropped_from_adr_table_blocks_cli(self, tmp_path):
        """An SRS NFR absent from ADR.md's traceability table must block, even
        if it is mentioned in prose elsewhere."""
        req = tmp_path / "01-requirements"
        req.mkdir()
        (req / "SRS.md").write_text("### NFR-01\n### NFR-06\n", encoding="utf-8")
        adr = tmp_path / "02-architecture" / "adr"
        adr.mkdir(parents=True)
        (adr / "ADR.md").write_text(
            "| ADR | FR / NFR served |\n|-----|------|\n| ADR-1 | NFR-01 |\n\n"
            "prose: NFR-06 is covered by ADR-1\n", encoding="utf-8")
        result = _run(tmp_path, "check-artifact-consistency")
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "NFR-06" in combined, combined[-2000:]


class TestManifestProbes:
    def test_p6_truncated_manifest_with_fsm_evidence_blocks(self, tmp_path):
        """Pattern B: a manifest whose gate1 results were emptied while FSM
        evidence says gates ran must block the FULL preflight (run-phase —
        the pre-push hook path; pre-commit-check is the deliberately
        lightweight FSM/kill-switch subset and does not carry this check)."""
        sandbox = _sandbox(tmp_path, 3)
        meth = sandbox / ".methodology"
        (meth / "quality_manifest.json").write_text(json.dumps({
            "schema_version": "1.0",
            "generated_at_phase": 3,
            "fr_ids": ["FR-01"],
            "gate_results": {"gate1": {}},
        }), encoding="utf-8")
        # FSM evidence that gate 1 already ran for FR-01:
        (meth / "state.json").write_text(json.dumps({
            "state": "RUNNING", "current_phase": 3,
            "last_gate": 1, "last_fr": "FR-01",
        }), encoding="utf-8")
        # Satisfy the earlier human-approve entry gate so the probe reaches
        # the manifest-integrity check it targets.
        subprocess.run(
            ["git", "-c", "user.name=probe", "-c", "user.email=probe@test",
             "commit", "--allow-empty", "-q", "-m", "phase2(review-complete): approve"],
            cwd=sandbox, check=True, capture_output=True,
        )
        result = _run(sandbox, "run-phase", "--phase", "3")
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "gate_results.gate1 is empty but Gate 1 has run" in combined, \
            combined[-2000:]
