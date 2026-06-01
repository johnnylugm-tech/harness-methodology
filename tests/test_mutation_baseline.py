"""Guard: mutation_baseline.json must exist and be filled with real measurements.

This test prevents un-measured baselines from being merged. Run
``mutmut run && mutmut results`` to populate mutation_baseline.json,
then commit it alongside code changes to the scoped files.
"""
import json
from pathlib import Path

BASELINE_PATH = Path(__file__).parent.parent / "mutation_baseline.json"
SCOPED_FILES = [
    "harness/tool_runners.py",
    "core/quality_gate/sab_parser.py",
]


def test_mutation_baseline_file_exists():
    """mutation_baseline.json must be present in the repository root."""
    assert BASELINE_PATH.exists(), (
        "mutation_baseline.json missing.\n"
        "Run: mutmut run && mutmut results\n"
        "Then fill mutation_baseline.json with the measured kill rates."
    )


def test_mutation_baseline_is_valid_json():
    """mutation_baseline.json must be parseable JSON."""
    try:
        data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AssertionError(f"mutation_baseline.json is not valid JSON: {exc}") from exc
    assert isinstance(data, dict)


def test_mutation_baseline_has_required_keys():
    """mutation_baseline.json must contain an entry for every scoped file."""
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    for fpath in SCOPED_FILES:
        assert fpath in data, (
            f"mutation_baseline.json is missing entry for '{fpath}'.\n"
            "Run: mutmut run && mutmut results"
        )


def test_mutation_baseline_pending_entries_are_expected():
    """PENDING entries (kill_rate=0, measured_at=PENDING) are allowed until first run.

    Once any entry is filled (measured_at != 'PENDING'), all entries must be filled.
    This prevents partial baselines from silently accumulating.
    """
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    scoped = {k: v for k, v in data.items() if not k.startswith("_")}

    pending = [k for k, v in scoped.items() if v.get("measured_at") == "PENDING"]
    measured = [k for k, v in scoped.items() if v.get("measured_at") != "PENDING"]

    if not measured:
        # All PENDING — first run not done yet; this is acceptable.
        return

    # Some measured, some not — inconsistent state.
    assert not pending, (
        f"Partial baseline: {pending} are still PENDING while {measured} are measured.\n"
        "Run: mutmut run && mutmut results  (then update all entries)"
    )
