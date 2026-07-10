"""Production-file line-count ratchet — god-file growth must be deliberate.

Round 3 claims 2/4 residue: the repo's largest files (harness_bridge,
gate_cmds, phase_cmds, fr_cmds, ...) are safety-critical surfaces
deliberately NOT decomposed this round (the M2-M4 plangen split handled the
one with a proven drift wound). This ratchet does for file growth what
test_patch_discipline does for private patches, with one deliberate
difference spelled out here: line counts legitimately grow, so ceilings MAY
be raised — but only in the same commit as the growth, with the reason in
the commit message. The product is diff-visibility of growth, not an
absolute cap. A file not listed in _LINE_CEILING must stay below
_GOD_FILE_THRESHOLD entirely; lowering a ceiling after shrinking a file is
manual, same as the patch ratchet.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_SCAN_DIRS = ("cli", "core", "harness", "scripts", "detection")
_GOD_FILE_THRESHOLD = 900

# Snapshot 2026-07-11 (Round 3 Station L, after the M2-M4 plangen split —
# generate_full_plan.py itself is down to a ~250-line facade and off this
# list; the split's two large products are honestly listed).
_LINE_CEILING: dict[str, int] = {
    "harness/harness_bridge.py": 2929,
    "cli/gate_cmds.py": 2567,
    "cli/phase_cmds.py": 2515,
    "cli/fr_cmds.py": 2147,
    "cli/project_cmds.py": 1860,
    "scripts/phase_auditor.py": 1846,
    "scripts/plangen/blocks.py": 1650,
    "core/phase_hooks.py": 1579,
    "cli/check_cmds.py": 1356,
    "harness/git_strategy.py": 1290,
    "scripts/plangen/phase_tasks.py": 1106,
    "core/quality_gate/mutation_enforcer.py": 967,
}


def _production_line_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in _SCAN_DIRS:
        for path in sorted((REPO / d).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(REPO).as_posix()
            counts[rel] = len(
                path.read_text(encoding="utf-8", errors="replace").splitlines()
            )
    return counts


def _violations(counts: dict[str, int]) -> list[str]:
    out = []
    for rel, count in sorted(counts.items()):
        ceiling = _LINE_CEILING.get(rel)
        if ceiling is not None:
            if count > ceiling:
                out.append(
                    f"{rel}: {count} lines > ceiling {ceiling} — split the "
                    f"file, or if the growth is deliberate raise the ceiling "
                    f"in THIS commit and justify it in the commit message"
                )
        elif count >= _GOD_FILE_THRESHOLD:
            out.append(
                f"{rel}: {count} lines — new god file (unlisted, threshold "
                f"{_GOD_FILE_THRESHOLD}); split it or add a justified "
                f"ceiling entry"
            )
    return out


def test_production_file_line_ratchet():
    over = _violations(_production_line_counts())
    assert not over, (
        "god-file growth must be a reviewed decision, not a silent drift:\n  "
        + "\n  ".join(over)
    )


def test_comparator_fires_on_listed_growth():
    """Negative: one line over a listed ceiling must trip the ratchet."""
    rel = "cli/gate_cmds.py"
    assert _violations({rel: _LINE_CEILING[rel] + 1})


def test_comparator_fires_on_new_god_file():
    """Negative: an unlisted file at the threshold must trip the ratchet."""
    assert _violations({"cli/newly_huge.py": _GOD_FILE_THRESHOLD})


def test_comparator_quiet_at_or_under_limits():
    rel = "cli/gate_cmds.py"
    assert _violations({
        rel: _LINE_CEILING[rel],
        "cli/small.py": _GOD_FILE_THRESHOLD - 1,
    }) == []
