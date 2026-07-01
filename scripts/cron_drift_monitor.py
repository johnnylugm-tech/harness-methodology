#!/usr/bin/env python3
"""
Cron job script for Drift Monitor.
Runs hourly (configurable in crontab).

Notification channels (configured via env vars):
  DRIFT_PROJECT_PATH  — project to monitor (default: harness-methodology root)
  DRIFT_SLACK_WEBHOOK — Slack incoming webhook URL
  DRIFT_EMAIL_SMTP    — SMTP server (e.g. smtp.gmail.com:587)
  DRIFT_EMAIL_TO      — alert recipient email
  DRIFT_EMAIL_FROM    — alert sender email
  DRIFT_EMAIL_PASS    — SMTP password (app-specific password recommended)

Usage:
    # Add to crontab:
    0 * * * * /path/to/venv/bin/python /path/to/project/scripts/cron_drift_monitor.py
"""

import datetime
import json
import os
import smtplib
import sys
import traceback
import urllib.request
from email.mime.text import MIMEText
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from detection import DriftDetector  # noqa: E402


def _notify_slack(webhook_url: str, text: str) -> bool:
    """Send drift alert via Slack incoming webhook. Returns True on success."""
    try:
        payload = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"[NOTIFY] Slack send failed: {e}", file=sys.stderr)
        return False


def _notify_email(smtp_server: str, to_addr: str, from_addr: str,
                  password: str, subject: str, body: str) -> bool:
    """Send drift alert via SMTP email. Returns True on success."""
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_addr
        if ":" in smtp_server:
            host, port_str = smtp_server.rsplit(":", 1)
            port = int(port_str)
        else:
            host = smtp_server
            port = 587
        with smtplib.SMTP(host, port, timeout=10) as s:
            s.starttls()
            s.login(from_addr, password)
            s.send_message(msg)
        return True
    except Exception as e:
        print(f"[NOTIFY] Email send failed: {e}", file=sys.stderr)
        return False


def main():
    """Cron entry point: run drift monitor and notify via configured channels."""
    project_path = os.environ.get("DRIFT_PROJECT_PATH", str(project_root))
    logs_dir = project_root / "logs"
    logs_dir.mkdir(exist_ok=True)
    log_file = logs_dir / "drift_monitor.log"

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    with open(log_file, "a") as f:
        sys.stdout = f
        # Bug M07/M08 fix: redirect stderr to the log file too. Previously
        # only stdout went to the log, so the [ERROR] line bypassed it
        # entirely and was only visible on the console. Also write the full
        # traceback so root cause is recoverable from the log file.
        sys.stderr = f
        try:
            report = _run_monitor(project_path)
        except Exception as e:
            print(f"[ERROR] {e}")
            traceback.print_exc()
            sys.stdout = original_stdout
            sys.stderr = original_stderr
            print(f"DRIFT MONITOR ERROR: {e}")
            return 1
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr

    if report["drifts_detected"]:
        _send_alerts(report)
    return 1 if report["drifts_detected"] else 0


def _run_monitor(project_path: str) -> dict:
    """Execute drift detection and return structured report."""
    detector = DriftDetector(project_path)
    results = detector.detect_all()

    drifts = {k: v for k, v in results.items() if v.drifted}
    ts = datetime.datetime.now().isoformat()

    if not drifts:
        print(f"[{ts}] No drift detected.")
        return {"drifts_detected": False, "timestamp": ts, "drifts": {},
                "avg_score": 100.0}

    severities: list[str] = []
    for key, result in drifts.items():
        items_sev = [item.severity.value for item in result.drift_items] if result.drift_items else []
        worst = max(items_sev, default="UNKNOWN")
        severities.append(worst)
        print(f"[DRIFT] {key}: score={result.score:.2f} items={len(result.drift_items)} worst={worst}")

    avg_score = sum(r.score for r in drifts.values()) / len(drifts) * 100
    print(f"  Total drifts: {len(drifts)}, Avg score: {avg_score:.0f}%")

    return {
        "drifts_detected": True,
        "timestamp": ts,
        "project": project_path,
        "drifts": {k: {"score": v.score, "items": len(v.drift_items)} for k, v in drifts.items()},
        "avg_score": avg_score,
        "worst_severity": max(severities, default="UNKNOWN") if severities else "UNKNOWN",
    }


def _send_alerts(report: dict) -> None:
    """Send drift alerts via all configured notification channels."""
    lines = [
        f"[DRIFT ALERT] {report['timestamp']}",
        f"Project: {report['project']}",
        f"Drifts : {len(report['drifts'])} detected, avg score {report['avg_score']:.0f}%, "
        f"worst={report['worst_severity']}",
        "",
    ]
    for name, detail in sorted(report["drifts"].items()):
        lines.append(f"  {name}: score={detail['score']:.2f} items={detail['items']}")
    text = "\n".join(lines)

    slack_url = os.environ.get("DRIFT_SLACK_WEBHOOK", "")
    if slack_url:
        _notify_slack(slack_url, text)

    smtp = os.environ.get("DRIFT_EMAIL_SMTP", "")
    to_addr = os.environ.get("DRIFT_EMAIL_TO", "")
    if smtp and to_addr:
        _notify_email(
            smtp_server=smtp,
            to_addr=to_addr,
            from_addr=os.environ.get("DRIFT_EMAIL_FROM", "drift-monitor@localhost"),
            password=os.environ.get("DRIFT_EMAIL_PASS", ""),
            subject=f"[DRIFT] Architecture drift detected — {report['avg_score']:.0f}%",
            body=text,
        )

    if not slack_url and not (smtp and to_addr):
        print("[NOTIFY] No notification channels configured. Set DRIFT_SLACK_WEBHOOK "
              "or DRIFT_EMAIL_SMTP+DRIFT_EMAIL_TO to enable alerts.")


if __name__ == "__main__":
    sys.exit(main())
