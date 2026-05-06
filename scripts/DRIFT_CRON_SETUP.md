# Drift Monitor Cron Setup

Automatically monitors architecture drift, runs hourly.

## Quick Start

### 1. Manual Test

```bash
cd /path/to/project
./.venv/bin/python scripts/cron_drift_monitor.py
```

### 2. Install Crontab

```bash
# View existing crontab
crontab -l

# Add drift monitor (runs at minute 0 of every hour)
crontab -e
# Paste:
# 0 * * * * /path/to/venv/bin/python /path/to/project/scripts/cron_drift_monitor.py >> /path/to/logs/drift_monitor.log 2>&1
```

Or use the example file:

```bash
crontab scripts/drift_crontab.example
```

### 3. View Logs

```bash
tail -f logs/drift_monitor.log
```

## Notification Setup

> **Note**: Email/Slack notification channels (`drift_notifier`, `EmailChannel`, `SlackChannel`) are **planned but not yet implemented**. Currently log-only via `cron_drift_monitor.py` → `detection.DriftDetector`.

### Default (Log)

Script writes drift alerts to `logs/drift_monitor.log`.

## File Structure

```
scripts/
+-- cron_drift_monitor.py      # Cron runner (uses detection.DriftDetector)
+-- DRIFT_CRON_SETUP.md        # This file

detection/
+-- drift_detector.py          # Drift detection engine (DriftDetector)
```

## Acceptance Criteria

- [x] `cron_drift_monitor.py` runs standalone
- [ ] `DriftNotifier` supports multiple channels (log, email, slack) — **planned**
- [ ] `DriftMonitor` integrates `notifier` — **planned**
