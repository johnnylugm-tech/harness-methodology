"""
Tests for scripts/cron_drift_monitor.py — notification channels and report generation.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.cron_drift_monitor import (
    _notify_slack,
    _notify_email,
    _send_alerts,
    _run_monitor,
)


class TestDriftMonitorNotifications:
    """Tests for drift notification channels."""

    def test_notify_slack_success(self):
        """Slack notification sends valid JSON payload."""
        with patch("scripts.cron_drift_monitor.urllib.request.urlopen") as mock_urlopen:
            result = _notify_slack("https://hooks.slack.com/test", "drift alert")
            assert result is True
            mock_urlopen.assert_called_once()

    def test_notify_slack_failure_returns_false(self):
        """Slack notification returns False on connection error."""
        with patch("scripts.cron_drift_monitor.urllib.request.urlopen",
                   side_effect=OSError("connection refused")):
            result = _notify_slack("https://hooks.slack.com/test", "drift alert")
            assert result is False

    def test_notify_slack_timeout_returns_false(self):
        """Slack notification returns False on timeout."""
        import urllib.error
        with patch("scripts.cron_drift_monitor.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("timed out")):
            result = _notify_slack("https://hooks.slack.com/test", "drift alert")
            assert result is False

    def test_notify_email_success(self):
        """Email notification sends via SMTP."""
        with patch("scripts.cron_drift_monitor.smtplib.SMTP") as mock_smtp:
            instance = mock_smtp.return_value.__enter__.return_value
            result = _notify_email(
                smtp_server="smtp.example.com:587",
                to_addr="to@example.com",
                from_addr="from@example.com",
                password="pass",
                subject="Test",
                body="Test body",
            )
            assert result is True
            instance.starttls.assert_called_once()
            instance.login.assert_called_once_with("from@example.com", "pass")
            instance.send_message.assert_called_once()

    def test_notify_email_failure_returns_false(self):
        """Email notification returns False on SMTP error."""
        with patch("scripts.cron_drift_monitor.smtplib.SMTP",
                   side_effect=OSError("SMTP connection refused")):
            result = _notify_email(
                smtp_server="smtp.example.com:587",
                to_addr="to@example.com",
                from_addr="from@example.com",
                password="pass",
                subject="Test",
                body="Test body",
            )
            assert result is False

    def test_notify_email_default_port(self):
        """SMTP without port defaults to 587."""
        with patch("scripts.cron_drift_monitor.smtplib.SMTP") as mock_smtp:
            _notify_email(
                smtp_server="smtp.example.com",  # no port
                to_addr="to@example.com",
                from_addr="from@example.com",
                password="pass",
                subject="Test",
                body="Test body",
            )
            mock_smtp.assert_called_once_with("smtp.example.com", 587, timeout=10)

    def test_send_alerts_no_config_noop(self):
        """_send_alerts is no-op when no notification channels configured."""
        report = {
            "drifts_detected": True, "timestamp": "2026-01-01T00:00:00",
            "project": "/test", "avg_score": 75.0, "worst_severity": "HIGH",
            "drifts": {"sad": {"score": 0.75, "items": 3}},
        }
        with patch.dict(os.environ, {}, clear=True):
            # Should not raise — just print a message
            _send_alerts(report)  # no exception = pass

    def test_send_alerts_slack_configured(self):
        """_send_alerts calls Slack when DRIFT_SLACK_WEBHOOK is set."""
        report = {
            "drifts_detected": True, "timestamp": "2026-01-01T00:00:00",
            "project": "/test", "avg_score": 75.0, "worst_severity": "HIGH",
            "drifts": {"sad": {"score": 0.75, "items": 3}},
        }
        with patch.dict(os.environ, {"DRIFT_SLACK_WEBHOOK": "https://hooks.slack.com/x"}, clear=True), \
             patch("scripts.cron_drift_monitor._notify_slack") as mock_slack:
            _send_alerts(report)
            mock_slack.assert_called_once()

    def test_send_alerts_email_configured(self):
        """_send_alerts calls email when DRIFT_EMAIL_SMTP and DRIFT_EMAIL_TO are set."""
        report = {
            "drifts_detected": True, "timestamp": "2026-01-01T00:00:00",
            "project": "/test", "avg_score": 75.0, "worst_severity": "HIGH",
            "drifts": {"sad": {"score": 0.75, "items": 3}},
        }
        with patch.dict(os.environ, {
            "DRIFT_EMAIL_SMTP": "smtp.example.com:587",
            "DRIFT_EMAIL_TO": "alert@example.com",
        }, clear=True), \
             patch("scripts.cron_drift_monitor._notify_email") as mock_email:
            _send_alerts(report)
            mock_email.assert_called_once()

    def test_send_alerts_missing_to_addr_skips_email(self):
        """Email skipped when DRIFT_EMAIL_TO is not set."""
        report = {
            "drifts_detected": True, "timestamp": "2026-01-01T00:00:00",
            "project": "/test", "avg_score": 75.0, "worst_severity": "HIGH",
            "drifts": {"sad": {"score": 0.75, "items": 3}},
        }
        with patch.dict(os.environ, {"DRIFT_EMAIL_SMTP": "smtp.example.com:587"}, clear=True), \
             patch("scripts.cron_drift_monitor._notify_email") as mock_email:
            _send_alerts(report)
            mock_email.assert_not_called()

    def test_run_monitor_no_drift(self, tmp_path):
        """_run_monitor returns report with no drifts detected."""
        mock_result = MagicMock()
        mock_result.drifted = False
        mock_result.score = 1.0

        with patch("scripts.cron_drift_monitor.DriftDetector") as mock_detector:
            mock_detector.return_value.detect_all.return_value = {"sad": mock_result}
            report = _run_monitor(str(tmp_path))
            assert report["drifts_detected"] is False
            assert report["avg_score"] == 100.0

    def test_main_no_drift_exits_zero(self, tmp_path):
        """main() exits 0 when no drift detected."""
        mock_result = MagicMock()
        mock_result.drifted = False
        mock_result.score = 1.0

        with patch("scripts.cron_drift_monitor.DriftDetector") as mock_detector, \
             patch("sys.stdout"):
            mock_detector.return_value.detect_all.return_value = {"sad": mock_result}
            from scripts.cron_drift_monitor import main
            rc = main()
            assert rc == 0

    def test_main_drift_detected_exits_one(self, tmp_path):
        """main() exits 1 when drift detected."""
        mock_result = MagicMock()
        mock_result.drifted = True
        mock_result.score = 0.5
        mock_result.drift_items = [MagicMock()]

        with patch("scripts.cron_drift_monitor.DriftDetector") as mock_detector, \
             patch("sys.stdout"), \
             patch.dict(os.environ, {}, clear=True):
            mock_detector.return_value.detect_all.return_value = {"sad": mock_result}
            from scripts.cron_drift_monitor import main
            rc = main()
            assert rc == 1
