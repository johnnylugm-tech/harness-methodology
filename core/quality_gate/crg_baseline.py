"""Whether a CRG architecture score may be recorded as a baseline (Round 37).

`cmd_finalize_gate` snapshots `.sessi-work/crg_metrics.json` into
`.methodology/crg_baseline_p{N}.json` at each exit gate, and later phases
measure structural drift against that file. Until Round 37 the copy was
unconditional.

Measured on taskq-renew: the P6 baseline was written with
architecture_score=77.8 while gate4_p6_full.yaml states the architecture
threshold as 80 and CI's standalone `crg-arch-check` enforces it as an
absolute floor. A reference point that cannot itself pass is not a
reference point — every later "no regression vs baseline" answer was
measured against a failing number.

The floor is read from the gate config, never restated here: Round 18 站2
made harness/gate_configs/*.yaml the only authority on a dimension's
threshold, and 80.0 is already restated in three workflow generators
(`crg_threshold=80.0` in spec_phase{3,4,6}.py) and in the CI template
(`--threshold 80`). This module adds no fourth copy.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from core.degradation_ledger import record_degradation
from core.quality_gate.gate_thresholds import load_gate_thresholds

__all__ = ["architecture_floor", "should_write_baseline", "snapshot_baseline"]

# Gate 4 is the full-quality gate; its architecture threshold is the floor
# CI's crg-arch-check enforces on every push from Phase 3 onward.
_FLOOR_GATE = 4
_DIMENSION = "architecture"


def architecture_floor() -> float:
    """The architecture threshold, from the YAML that scores against it."""
    return load_gate_thresholds(_FLOOR_GATE)[_DIMENSION]


def should_write_baseline(metrics: dict) -> tuple[bool, str]:
    """(may_write, reason_when_not) for a crg_metrics dict.

    A missing score is refused rather than defaulted: Round 35's rule is that
    a run which could not measure has no score, and no score is not a passing
    score.
    """
    score = metrics.get("architecture_score")
    if not isinstance(score, (int, float)):
        return False, (
            "crg_metrics has no numeric architecture_score — a run that could "
            "not measure has no baseline to offer"
        )
    floor = architecture_floor()
    if score < floor:
        return False, (
            f"architecture_score {score:.1f} is below the floor {floor:.0f} "
            f"stated in gate{_FLOOR_GATE}'s config — a score that cannot pass "
            f"cannot be the reference later phases are compared against"
        )
    return True, ""


def snapshot_baseline(project: Path, phase: int) -> bool:
    """Copy this run's crg_metrics.json to the phase's baseline, if it may be.

    Returns True when a baseline was written. Refusal is recorded in the
    degradation ledger and printed — a missing baseline degrades the next
    phase's drift check to "no reference", and that has to be visible.
    """
    metrics_path = project / ".sessi-work" / "crg_metrics.json"
    if not metrics_path.is_file():
        return False
    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        may_write, why_not = False, f"crg_metrics.json unreadable: {exc}"
    else:
        may_write, why_not = should_write_baseline(metrics)

    if not may_write:
        record_degradation(
            project, "crg:baseline",
            f"no baseline written for phase {phase}", why_not,
        )
        print(f"  [CRG] Baseline NOT saved — {why_not}")
        return False

    baseline_path = project / ".methodology" / f"crg_baseline_p{phase}.json"
    try:
        shutil.copy2(metrics_path, baseline_path)
        # Stamp with git SHA for traceability
        sha_r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=str(project),
        )
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
        data["_baseline_sha"] = sha_r.stdout.strip()
        data["_baseline_phase"] = phase
        baseline_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except (OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"  [WARN] CRG baseline save failed: {exc}")
        return False
    print(f"  [CRG] Baseline saved: .methodology/crg_baseline_p{phase}.json")
    return True
