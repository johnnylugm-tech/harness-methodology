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

### Default (Log)

Script uses `LogChannel` by default, writing alerts to `logs/drift_alerts.log`.

### Email

```python
from quality_gate.drift_notifier import DriftNotifier, EmailChannel

notifier = DriftNotifier(channels=[
    EmailChannel(
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        from_addr="alerts@yourdomain.com",
        to_addrs=["admin@yourdomain.com"],
    ),
])
```

### Slack

```python
from quality_gate.drift_notifier import DriftNotifier, SlackChannel

notifier = DriftNotifier(channels=[
    SlackChannel(webhook_url="https://hooks.slack.com/services/YOUR/WEBHOOK/URL"),
])
```

## File Structure

```
scripts/
+-- cron_drift_monitor.py      # Cron runner
+-- drift_crontab.example      # Crontab example
+-- DRIFT_CRON_SETUP.md        # This file

quality_gate/
+-- drift_monitor.py           # Updated: supports notifier param
+-- drift_notifier.py          # Notification system (log, email, slack)
```

## Acceptance Criteria

- [x] `cron_drift_monitor.py` runs standalone
- [x] `drift_crontab.example` contains full crontab config
- [x] `DriftNotifier` supports multiple channels (log, email, slack)
- [x] `DriftMonitor` integrates `notifier`
