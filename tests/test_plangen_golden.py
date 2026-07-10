"""Golden byte-equal pinning of the 9-phase plan generator output.

Round 3 Station M1 — the safety net under the generate_full_plan.py split
(M2-M4 move ~3,300 lines verbatim into scripts/plangen/; these goldens are
the proof the move changed zero output bytes). They stay after the split:
any deliberate prose change to a phase plan must regenerate the goldens IN
THE SAME COMMIT, which is the point — plan-prose changes become visible in
diff review instead of hiding inside f-string seas (the bea1bb1 drift class).

Regenerate after a deliberate prose change:

    REGEN_GOLDEN=1 python3 -m pytest tests/test_plangen_golden.py -q

Normalizations (the ONLY tolerated nondeterminism — do not grow this list
casually; prefer fixing the generator):
  - the `> **Date**: YYYY-MM-DD` header line (datetime.now at generation)
  - the literal harness version token ``v{_HARNESS_VERSION}`` (read from
    pyproject.toml, bumps with releases)
The fixture project lives in a fixed-name subdir so ``repo_path.name`` in
the plan header is deterministic without normalization.
"""

import json
import os
import re
from pathlib import Path

import pytest

from scripts.generate_full_plan import _HARNESS_VERSION, generate_full_plan

GOLDEN_DIR = Path(__file__).parent / "golden" / "plangen"

_DATE_RE = re.compile(r"^> \*\*Date\*\*: \d{4}-\d{2}-\d{2}$", re.MULTILINE)

# Static mode for all 9 phases (fixture provides the SRS phases 2-4 require)
# plus one dynamic-mode case: its plan takes distinct placeholder branches.
CASES = [(p, False) for p in range(1, 10)] + [(3, True)]


def _fixture_project(tmp_path: Path) -> Path:
    """Deterministic minimal project (mirror of test_generate_full_plan.py's
    `project` fixture, plus a fixed directory name for a stable plan header)."""
    proj = tmp_path / "golden-fixture-project"
    m_dir = proj / ".methodology"
    m_dir.mkdir(parents=True)
    (m_dir / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": ["FR-01", "FR-02"], "gate_results": {}}),
        encoding="utf-8",
    )
    (proj / "01-requirements").mkdir()
    (proj / "01-requirements" / "SRS.md").write_text("# SRS\n", encoding="utf-8")
    return proj


def _normalize(text: str) -> str:
    text = _DATE_RE.sub("> **Date**: <DATE>", text)
    return text.replace(f"v{_HARNESS_VERSION}", "v<VERSION>")


def _golden_name(phase: int, dynamic: bool) -> str:
    return f"phase{phase}{'_dynamic' if dynamic else ''}.md"


@pytest.mark.parametrize("phase,dynamic", CASES)
def test_plan_output_matches_golden(phase, dynamic, tmp_path):
    proj = _fixture_project(tmp_path)
    text = generate_full_plan(phase, proj, None, dynamic=dynamic)
    assert text is not None, f"phase {phase} generation returned None"
    normalized = _normalize(text)

    golden_path = GOLDEN_DIR / _golden_name(phase, dynamic)
    if os.environ.get("REGEN_GOLDEN") == "1":
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(normalized, encoding="utf-8")

    golden = golden_path.read_text(encoding="utf-8")
    assert normalized == golden, (
        f"phase {phase}{' dynamic' if dynamic else ''} plan output drifted "
        f"from its golden. If the prose change is deliberate, regenerate in "
        f"THIS commit: REGEN_GOLDEN=1 python3 -m pytest tests/test_plangen_golden.py -q"
    )


def test_harness_version_reads_pyproject():
    """The version probe must not silently fall back to its "2.4.0" default.

    The golden normalization is self-referential on _HARNESS_VERSION (it
    replaces whatever the probe returned), so a broken probe is invisible to
    the goldens. M2 moved the probe one directory deeper (plangen/) and its
    __file__-anchored pyproject path needed a depth fix — this pins that
    anchor class directly against the real pyproject.toml.
    """
    py = Path(__file__).resolve().parent.parent / "pyproject.toml"
    m = re.search(
        r'\[project\]\n.*?\nversion\s*=\s*"([^"]+)"', py.read_text(), re.DOTALL
    )
    assert m, "pyproject.toml [project] version not found"
    assert _HARNESS_VERSION == m.group(1)


def test_generation_is_deterministic(tmp_path):
    """Two runs over the same fixture must agree after normalization —
    catches any nondeterminism source beyond the two documented ones."""
    proj = _fixture_project(tmp_path)
    first = generate_full_plan(3, proj)
    second = generate_full_plan(3, proj)
    assert first is not None and second is not None
    assert _normalize(first) == _normalize(second)
