"""Evidence a verdict cites must outlive the verdict (Round 50 站0).

Round 45 站1 established the rule; this is the case it did not reach.

S4 cross-validation writes the raw tool output it judged to
`.sessi-work/harness_verification/<dim>_harness.txt`, and its block message
tells the operator to go read that file. `cli/phase_cmds.py` deletes
`.sessi-work/` wholesale at every phase transition — deliberately, because
stale artifacts there caused the next phase's gate to skip re-computation.

Both behaviours are individually correct. Together they mean the audit trail
for a gate verdict is gone one advance later.

Measured 2026-08-13: a Gate 4 recorded a cross-validation gap for the
performance dimension at 06:19 UTC and published PASS at 06:29. Asked ten
days later which S4 branch that second run took, the answer is unavailable —
`.sessi-work/harness_verification/` no longer exists on that project, so the
question cannot be settled from the record. The investigation into this
defect was itself blocked by it.

The rule these tests encode: a directory that advance clears is not a place
a verdict may cite.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path
from unittest.mock import patch

import yaml

from core.evidence_retention import (
    ADVANCE_CLEARED_DIRS,
    cited_evidence_dir,
)

REPO = Path(__file__).resolve().parents[1]


def test_cited_evidence_is_not_in_a_directory_advance_clears(tmp_path):
    """The one invariant. Everything else here defends it."""
    rel = cited_evidence_dir(tmp_path).relative_to(tmp_path).as_posix()
    for cleared in ADVANCE_CLEARED_DIRS:
        assert not (rel == cleared or rel.startswith(cleared + "/")), (
            f"a verdict cites {rel!r}, and advance-phase deletes {cleared!r} "
            f"at every phase transition"
        )


def test_the_cleared_list_is_not_empty():
    """A guard whose input set is empty passes by vacuum (Round 46)."""
    assert ADVANCE_CLEARED_DIRS
    assert ".sessi-work" in ADVANCE_CLEARED_DIRS


def test_advance_clears_exactly_what_the_list_says():
    """phase_cmds must delete via the constant, not via its own string.

    Otherwise the list above becomes documentation of a behaviour rather than
    the behaviour itself, and the two drift the first time someone adds a
    second scratch directory.
    """
    src = (REPO / "cli" / "phase_cmds.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert ".sessi-work" not in literals, (
        "cli/phase_cmds.py still names the scratch directory as a bare "
        "string literal; the cleanup and the retention rule must read the "
        "same constant or they will disagree"
    )


def _run_s4_once(root: Path, monkeypatch, tool_output: str) -> None:
    """Drive one S4 cross-validation over a single tool dimension.

    Same seams as tests/test_anti_fabrication.py's TestHarnessCrossValidation:
    a gate yaml with one dimension, and `run_tool` patched so no real tool
    runs. Nothing private is patched inside the function under test.
    """
    import core.quality_gate.gate_thresholds as _gt
    from harness.harness_bridge import GateContext, _run_harness_cross_validation

    cfg_path = root / "gate4_p6_full.yaml"
    cfg_path.write_text(yaml.dump({"gate": 4, "dimensions": [
        {"name": "linting", "requires_tool_execution": True, "tool": "ruff",
         "threshold": 90},
    ]}), encoding="utf-8")
    monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg_path)
    ctx = GateContext(
        gate_num=4, config={}, project_root=str(root), phase=6, fr_id=None,
        ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
        work_dir=str(root / ".sessi-work"), sab_data={},
    )
    raw = {"breakdown": {"linting": {"score": 95}}}  # >= threshold, so the tool runs
    with patch("harness.tool_runners.run_tool", return_value=(tool_output, 0)):
        _run_harness_cross_validation(ctx, raw)  # type: ignore[arg-type]


def test_the_audit_file_survives_the_cleanup_advance_performs(tmp_path, monkeypatch):
    """The behaviour, end to end. Written after the fix, on purpose.

    Station 0's tests read source, which is enough to fail on the defect but
    not enough to show the property holds. This one writes the audit file the
    way a real gate does, then performs exactly the deletion advance-phase
    performs, and asks whether the file a verdict cites is still there.
    """
    _run_s4_once(tmp_path, monkeypatch, "[]")

    audit = cited_evidence_dir(tmp_path) / "linting_harness.txt"
    assert audit.is_file(), "S4 wrote no audit file at all"

    for cleared in ADVANCE_CLEARED_DIRS:
        shutil.rmtree(tmp_path / cleared, ignore_errors=True)

    assert audit.is_file(), (
        "the audit file a gate verdict points the operator at did not survive "
        "the phase transition — which is the whole defect"
    )


def test_a_committed_audit_file_is_bounded(tmp_path, monkeypatch):
    """It lives under .methodology/ now, so it is committed — and bounded.

    Round 45 站1 already set a ceiling for evidence that gets copied into
    `.methodology/`; this file is written there directly and reads the same
    knob rather than a second one beside it.
    """
    from core.harness_config import get_value

    max_bytes = int(get_value(tmp_path, "gate_evidence_max_bytes"))
    _run_s4_once(tmp_path, monkeypatch, "x" * (max_bytes + 50_000))

    text = (cited_evidence_dir(tmp_path) / "linting_harness.txt").read_text(encoding="utf-8")
    assert len(text) < max_bytes + 1000, (
        f"the audit file is {len(text)} characters against a "
        f"{max_bytes}-character ceiling, and it is committed"
    )
    assert "truncated" in text, "a shortened audit file must say that it is one"


def test_s4_writes_where_it_says_it_writes():
    """The block message and the write must name one directory.

    Round 24's rule: a [BLOCKED] carries the remediation, not a pointer to a
    place the remediation might be.
    """
    src = (REPO / "harness" / "harness_bridge.py").read_text(encoding="utf-8")
    assert ".sessi-work/harness_verification" not in src, (
        "harness_bridge still points operators at a path inside a directory "
        "advance-phase deletes"
    )
