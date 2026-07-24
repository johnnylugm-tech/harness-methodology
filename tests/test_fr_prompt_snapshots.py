"""Golden byte-equal pinning of `_build_fr_step_prompt` per-step output.

Round 17 站0 — the safety net UNDER the two subsequent structural changes
to the 719-line prompt builder (cli/fr_cmds.py:_build_fr_step_prompt):

  - 站1 (finding A) single-sources the threshold / spec-cap / weight
    formulas the GATE1 prompt currently hand-copies from finalize_gate;
  - 站4 (finding D) extracts the 9 `if step == "…"` branches into a
    per-step façade under cli/fr_prompts/.

Both claim to change ZERO output bytes. These goldens are the proof: any
byte drift in a rendered prompt fails here, so a prose change becomes
visible in diff review instead of hiding inside an f-string sea (the same
`bea1bb1` drift class the plangen goldens guard — see test_plangen_golden).

Regenerate after a DELIBERATE prompt change (and eyeball the diff):

    REGEN_GOLDEN=1 python3 -m pytest tests/test_fr_prompt_snapshots.py -q

Determinism sources controlled here (the ONLY tolerated nondeterminism —
do not grow this list casually; prefer making the builder deterministic):
  - the fixture project's absolute path, interpolated into the GATE1
    prompt's `--project {project}` lines → normalized to `<PROJECT>`.
  - TDD-RED's CRG semantic-search call (harness.crg_bridge) → stubbed to
    return zero hits so `_related_ctx` is empty and stable regardless of
    whether a live CRG graph exists in the test environment.
The fixture lives in a fixed-name subdir so only the tmp prefix varies,
and that prefix is the single string the normalizer rewrites.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import sys
import types
from collections.abc import Callable
from pathlib import Path

import pytest

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports
from cli.fr_cmds import _build_fr_step_prompt  # noqa: E402
from core.utils.project_layout import ProjectLayout  # noqa: E402

GOLDEN_DIR = Path(__file__).parent / "golden" / "fr_prompts"

# Deterministic tool-snapshot injected into the fix-step prompts that accept
# one (TEST-FIX / COVERAGE-FIX / INFRA-FIX / LINT-FIX / CODE-FIX-with-dims).
_TOOL_SNAPSHOT = "RUFF: E501 line too long (3 occurrences)\nPYTEST: 2 failed, 8 passed"

# Every step that `_build_fr_step_prompt` renders a prompt for. The
# completeness meta-test below re-derives this set from the function source
# and asserts equality, so a new `step == "…"` branch added without a golden
# fails loudly (and a removed branch flags a stale golden).
_COVERED_STEPS = {
    "TDD-RED", "TDD-GREEN", "TDD-IMPROVE",
    "GATE1", "GATE1-DELTA",
    "TEST-FIX", "COVERAGE-FIX", "INFRA-FIX", "LINT-FIX", "CODE-FIX",
}

# key → builder(proj, srs) → prompt. One entry per return point (CODE-FIX has
# two independent returns: diagnostic vs classified dims). Explicit positional
# calls keep the case table fully typed (a `**dict` expansion of a
# heterogeneous str/int/Path table cannot map onto the builder's params).
_BUILDERS: dict[str, Callable[[Path, Path], str]] = {
    "tdd_red": lambda p, s: _build_fr_step_prompt("TDD-RED", "FR-01", 3, p, s),
    "tdd_green": lambda p, s: _build_fr_step_prompt("TDD-GREEN", "FR-01", 3, p, s),
    "tdd_improve": lambda p, s: _build_fr_step_prompt("TDD-IMPROVE", "FR-01", 3, p, s),
    "gate1": lambda p, s: _build_fr_step_prompt("GATE1", "FR-01", 3, p, s),
    "test_fix": lambda p, s: _build_fr_step_prompt(
        "TEST-FIX", "FR-01", 3, p, s, tool_snapshot=_TOOL_SNAPSHOT),
    "coverage_fix": lambda p, s: _build_fr_step_prompt(
        "COVERAGE-FIX", "FR-01", 3, p, s, tool_snapshot=_TOOL_SNAPSHOT),
    "infra_fix": lambda p, s: _build_fr_step_prompt(
        "INFRA-FIX", "FR-01", 3, p, s, tool_snapshot=_TOOL_SNAPSHOT),
    "lint_fix": lambda p, s: _build_fr_step_prompt(
        "LINT-FIX", "FR-01", 3, p, s, tool_snapshot=_TOOL_SNAPSHOT),
    "code_fix_diag": lambda p, s: _build_fr_step_prompt(
        "CODE-FIX", "FR-01", 3, p, s, failing_dims=None),
    "code_fix_dims": lambda p, s: _build_fr_step_prompt(
        "CODE-FIX", "FR-01", 3, p, s,
        failing_dims=["linting", "test_coverage"], tool_snapshot=_TOOL_SNAPSHOT),
}

_CASE_KEYS = sorted(_BUILDERS.keys())


@pytest.fixture(autouse=True)
def _stub_crg(monkeypatch):
    """Make TDD-RED's `from harness.crg_bridge import CRGBridge` deterministic.

    A live CRG graph would return environment-dependent semantic-search
    hits; a missing module would take the except branch (WARN to stderr).
    Inject a stub module returning zero hits so `_related_ctx` is empty and
    identical in every environment, with no stderr noise.
    """
    stub_mod = types.ModuleType("harness.crg_bridge")

    class _StubCRGBridge:
        def __init__(self, *a, **k):
            pass

        def semantic_search(self, *a, **k):
            return {"results": []}

    setattr(stub_mod, "CRGBridge", _StubCRGBridge)
    monkeypatch.setitem(sys.modules, "harness.crg_bridge", stub_mod)


def _fixture_project(tmp_path: Path) -> tuple[Path, Path]:
    """Deterministic minimal project driving every prompt branch.

    Files are placed at the paths `ProjectLayout` resolves (not hard-coded)
    so the fixture tracks the layout SSOT. TEST_SPEC gives 3 rows, the test
    file backs 2 of them → spec_cov 2/3 = 67% with 1 missing, exercising the
    GATE1 spec-cap interpolation (the numbers 站1 single-sources).
    """
    proj = tmp_path / "fr-prompt-fixture"
    layout = ProjectLayout(proj)

    m_dir = proj / ".methodology"
    m_dir.mkdir(parents=True)
    (m_dir / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": ["FR-01"], "gate_score_overrides": {}}),
        encoding="utf-8",
    )

    srs = layout.srs_path
    srs.parent.mkdir(parents=True, exist_ok=True)
    srs.write_text(
        "### FR-01: Widget submission\n\n"
        "The system MUST accept a widget submission command and return an\n"
        "8-character hex id.\n\n"
        "**Acceptance Criteria:**\n"
        "- AC1: a valid command returns an 8-hex id on stdout, exit 0.\n"
        "- AC2: an empty command returns a validation error, exit 1.\n"
        "\n---\n",
        encoding="utf-8",
    )

    spec = layout.test_spec_path
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        "### FR-01: Widget submission\n\n"
        "| # | Test Function | Type |\n"
        "|---|--------------|------|\n"
        "| 1 | test_fr01_01_happy | Functional |\n"
        "| 2 | test_fr01_02_empty | Functional |\n"
        "| 3 | test_fr01_03_toolong | Functional |\n",
        encoding="utf-8",
    )

    test_dir = layout.active_test_dir
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / "test_fr01.py").write_text(
        "def test_fr01_01_happy():\n    assert True\n\n"
        "def test_fr01_02_empty():\n    assert True\n",
        encoding="utf-8",
    )

    (proj / "03-development" / "src").mkdir(parents=True, exist_ok=True)
    return proj, srs


def _normalize(text: str, proj: Path) -> str:
    """Rewrite the one tolerated nondeterminism source: the fixture's
    absolute path (GATE1 interpolates it into `--project {project}`)."""
    return text.replace(str(proj), "<PROJECT>")


@pytest.mark.parametrize("key", _CASE_KEYS)
def test_prompt_matches_golden(key, tmp_path):
    proj, srs = _fixture_project(tmp_path)
    out = _normalize(_BUILDERS[key](proj, srs), proj)

    golden_path = GOLDEN_DIR / f"{key}.txt"
    if os.environ.get("REGEN_GOLDEN") == "1":
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(out, encoding="utf-8")

    golden = golden_path.read_text(encoding="utf-8")
    assert out == golden, (
        f"{key} prompt drifted from its golden. If the prompt change is "
        f"deliberate, regenerate IN THIS COMMIT and eyeball the diff:\n"
        f"  REGEN_GOLDEN=1 python3 -m pytest tests/test_fr_prompt_snapshots.py -q"
    )


def test_generation_is_deterministic(tmp_path):
    """Two renders over the same fixture must agree after normalization —
    catches any nondeterminism beyond the documented project-path source."""
    proj, srs = _fixture_project(tmp_path)
    for key, builder in _BUILDERS.items():
        first = _normalize(builder(proj, srs), proj)
        second = _normalize(builder(proj, srs), proj)
        assert first == second, f"{key} prompt is nondeterministic"


def test_gate1_delta_is_byte_identical_to_gate1(tmp_path):
    """GATE1 and GATE1-DELTA share one `step in (…)` branch; the step string
    never enters the prompt body, so their output must be byte-identical.
    Pins the shared branch so 站4's split cannot silently fork them."""
    proj, srs = _fixture_project(tmp_path)
    g1 = _build_fr_step_prompt("GATE1", "FR-01", 3, proj, srs)
    gd = _build_fr_step_prompt("GATE1-DELTA", "FR-01", 3, proj, srs)
    assert g1 == gd


def test_every_prompt_step_has_a_snapshot():
    """Completeness meta-test: the set of step literals the builder branches
    on MUST equal `_COVERED_STEPS` (which the parametrized golden test and
    the case table are keyed to). A new `step == "X"` branch added without a
    golden — or a removed branch leaving a stale golden — fails here."""
    src = inspect.getsource(_build_fr_step_prompt)
    literals = set(re.findall(r'step == "([A-Z0-9-]+)"', src))
    for group in re.findall(r"step in \(([^)]+)\)", src):
        literals |= set(re.findall(r'"([A-Z0-9-]+)"', group))
    assert literals == _COVERED_STEPS, (
        f"prompt step branches {sorted(literals)} != covered/snapshotted "
        f"steps {sorted(_COVERED_STEPS)}. Add a golden + case-table entry "
        f"for any new step, or drop the stale golden for a removed one."
    )
