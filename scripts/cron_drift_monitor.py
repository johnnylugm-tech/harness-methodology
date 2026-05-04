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

from quality_gate.drift_monitor import DriftMonitor  # noqa: E402
from orchestration import create_feedback_store  # noqa: E402


def main():
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
    store = create_feedback_store()
    monitor = DriftMonitor(project_path=project_path, feedback_store=store)
    alert = monitor.run_and_alert()

    if alert:
        print(f"[{alert.timestamp}] DRIFT ALERT: {alert.severity.upper()} - {alert.message}")
        print(f"  Drift score: {alert.drift_score}")
        print(f"  Artifacts: {', '.join(alert.artifacts)}")
        print(f"  Recommended action: {alert.recommended_action}")
        print(f"  Alert ID: {alert.id}")
        raise SystemExit(1)
    else:
        import datetime
        print(f"[{datetime.datetime.now().isoformat()}] No drift detected.")


if __name__ == "__main__":
    sys.exit(main())
