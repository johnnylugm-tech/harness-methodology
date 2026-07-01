#!/usr/bin/env python3
"""
state_monitor.py - Runtime Metrics Trend Monitor
================================================
Runs every 5 minutes, checks alert thresholds, sends Telegram notifications.

Usage:
    python state_monitor.py --check-trends
    python state_monitor.py --project-path /path/to/project

Crontab:
    */5 * * * * cd /path/to/project && python3 .methodology/state_monitor.py --check-trends
"""

import argparse
import json
import sys
import traceback
from pathlib import Path
from datetime import datetime, timezone

THRESHOLDS = {
    "blocks": 5,             # BLOCK count warning
    "ab_rounds": 5,          # A/B rounds warning (HR-12)
    "elapsed_minutes": 120,  # Phase elapsed time warning
    "integrity_min": 40,     # Integrity score floor (HR-14)
}

DEFAULT_STATE_PATH = ".methodology/state.json"


def check_trends(project_path: str) -> int:
    """Check trends and send alerts. Returns 0=ok, 1=alert triggered."""
    state_path = Path(project_path) / DEFAULT_STATE_PATH
    if not state_path.exists():
        print(f"  state.json not found at {state_path}")
        print("  Phase may not have started yet.")
        return 0

    try:
        state = json.loads(state_path.read_text())
    except Exception as e:
        print(f"  Failed to read state.json: {e}")
        return 1

    ps = state.get("phase_state", {})

    # Calculate elapsed time
    started = ps.get("started_at")
    elapsed_minutes = 0
    started_at_parse_failed = False
    if started:
        try:
            start_time = datetime.fromisoformat(started)
            elapsed_minutes = int((datetime.now(timezone.utc) - start_time).total_seconds() // 60)
            ps["elapsed_minutes"] = elapsed_minutes
        except Exception:
            # Bug M01 fix: surface traceback so operator sees real cause.
            # Silent print made timeout-alert logic appear disabled with no indication.
            started_at_parse_failed = True
            print(
                f"  Failed to parse started_at={started!r}:",
                file=sys.stderr,
            )
            traceback.print_exc(file=sys.stderr)

    alerts = []

    blocks = ps.get("blocks", 0)
    if blocks >= THRESHOLDS["blocks"]:
        alerts.append({"type": "BLOCK_COUNT_HIGH", "current": blocks,
                       "message": f"BLOCK count too high: {blocks} (threshold: {THRESHOLDS['blocks']})"})

    ab_rounds = ps.get("ab_rounds", 0)
    if ab_rounds >= THRESHOLDS["ab_rounds"]:
        alerts.append({"type": "AB_ROUND_HIGH", "current": ab_rounds,
                       "message": f"A/B rounds too high: {ab_rounds} (threshold: {THRESHOLDS['ab_rounds']})"})

    estimated_minutes = ps.get("estimated_minutes", THRESHOLDS["elapsed_minutes"] // 3)
    timeout_threshold = estimated_minutes * 3
    if elapsed_minutes >= timeout_threshold:
        alerts.append({"type": "PHASE_TIMEOUT", "current": elapsed_minutes,
                       "message": f"Phase running too long: {elapsed_minutes} min (estimated: {estimated_minutes}, threshold: {timeout_threshold})"})

    # Bug M02 fix: missing integrity_score must NOT silently default to 100.
    # Defaulting to 100 hid the case where the field was never written
    # (pipeline skipped integrity check, crash before write, schema drift, …).
    # Treat missing as 0 (below floor) so an alert is raised.
    integrity_score = ps.get("integrity_score", 0)
    if integrity_score < THRESHOLDS["integrity_min"]:
        alerts.append({"type": "INTEGRITY_LOW", "current": integrity_score,
                       "message": f"Integrity score too low: {integrity_score} (floor: {THRESHOLDS['integrity_min']})"})

    ps["last_check_at"] = datetime.now(timezone.utc).isoformat()
    state["phase_state"] = ps
    state["trend_alerts"] = [a["type"] for a in alerts]

    state_write_failed = False
    try:
        state_path.write_text(json.dumps(state, indent=2))
    except Exception:
        # Bug M03 fix: surface traceback so operator sees WHY state was not
        # persisted. Silent return masked permission/ENOSPC/disk-full issues.
        state_write_failed = True
        print("  Failed to write state.json:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

    # Bug M01 followup: return non-zero when started_at parsing failed
    # so the cron job sees the degraded state.
    if started_at_parse_failed and not alerts:
        return 1

    if alerts:
        print(f"  {len(alerts)} alert(s) found:")
        for alert in alerts:
            print(f"    {alert['message']}")
        send_telegram_alert(state, alerts)
        return 1
    elif state_write_failed:
        # Bug M03 followup: state could not be persisted, even though no
        # threshold alerts. Surface non-zero so cron knows state is stale.
        return 1
    else:
        print(f"  No alerts. Phase {state.get('current_phase', '?')} running for {elapsed_minutes} min")
        return 0


def send_telegram_alert(state: dict, alerts: list):
    """Send Telegram alert notification."""
    phase = state.get("current_phase", "?")
    ps = state.get("phase_state", {})
    lines = [f"[Phase {phase} Runtime Alert]", ""]
    for alert in alerts:
        lines.append(alert["message"])
    lines.extend(["",
        "Current Status:",
        f"  - BLOCK: {ps.get('blocks', 0)}",
        f"  - A/B rounds: {ps.get('ab_rounds', 0)}",
        f"  - Elapsed: {ps.get('elapsed_minutes', 0)} min",
        f"  - Last gate score: {ps.get('last_gate_score', 'N/A')}",
        "",
        "Recommendation: Check FrameworkEnforcer output or consider splitting Phase"
    ])
    message = "\n".join(lines)
    try:
        import subprocess  # nosec B404
        result = subprocess.run(  # nosec B603 B607
            ["openclaw", "message", "--action", "send", "--channel", "telegram", "--message", message],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            print("    Telegram notification sent")
        else:
            print(f"    Telegram notification failed: {result.stderr}")
    except FileNotFoundError:
        print("    openclaw not found, skipping Telegram notification")
    except Exception as e:
        print(f"    Failed to send Telegram: {e}")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Runtime Metrics State Monitor")
    parser.add_argument("--check-trends", action="store_true", help="Run trend check")
    parser.add_argument("--project-path", type=str, default=".", help="Project path (default: .)")
    parser.add_argument("--dry-run", action="store_true", help="Display only, no notifications")
    args = parser.parse_args()

    if len(sys.argv) == 1:
        print(__doc__)
        print("Alert thresholds:")
        for key, value in THRESHOLDS.items():
            print(f"  - {key}: {value}")
        return 0

    return check_trends(args.project_path)


if __name__ == "__main__":
    sys.exit(main())
