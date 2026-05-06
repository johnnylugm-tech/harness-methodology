#!/usr/bin/env python3
"""
Cron job script for Drift Monitor.
Runs hourly (configurable in crontab).

Usage:
    # Add to crontab:
    0 * * * * /path/to/venv/bin/python /path/to/project/scripts/cron_drift_monitor.py
"""

import sys
import os
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from detection import DriftDetector  # noqa: E402


def main():
    """Cron entry point: run drift monitor and log results."""
    project_path = os.environ.get("DRIFT_PROJECT_PATH", str(project_root))
    logs_dir = project_root / "logs"
    logs_dir.mkdir(exist_ok=True)
    log_file = logs_dir / "drift_monitor.log"

    original_stdout = sys.stdout
    with open(log_file, "a") as f:
        sys.stdout = f
        try:
            _run_monitor(project_path)
        except Exception as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.stdout = original_stdout
            print(f"DRIFT MONITOR ERROR: {e}")
            return 1
        finally:
            sys.stdout = original_stdout
    return 0


def _run_monitor(project_path: str):
    """Execute drift detection and emit alert if threshold exceeded."""
    detector = DriftDetector(project_path)
    results = detector.detect_all()

    drifts = {k: v for k, v in results.items() if v.drifted}
    if not drifts:
        import datetime
        print(f"[{datetime.datetime.now().isoformat()}] No drift detected.")
        return

    avg_score = sum(r.score for r in drifts.values()) / len(drifts) * 100
    for key, result in drifts.items():
        severities = [item.severity.value for item in result.drift_items] if result.drift_items else []
        worst = max(severities, default="UNKNOWN")
        print(f"[DRIFT] {key}: score={result.score:.2f} items={len(result.drift_items)} worst={worst}")
    print(f"  Total drifts: {len(drifts)}, Avg score: {avg_score:.0f}%")
    raise SystemExit(1)


if __name__ == "__main__":
    sys.exit(main())
