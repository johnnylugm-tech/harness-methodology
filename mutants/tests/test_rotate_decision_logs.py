"""
tests/test_rotate_decision_logs.py — Unit tests for scripts/rotate_decision_logs.py.

Covers: _parse_date, _archive_dir, rotate, main (via argv).
"""
from __future__ import annotations

import datetime as _dt
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.rotate_decision_logs import _archive_dir, _parse_date, main, rotate

pytestmark = pytest.mark.gate


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------

class TestParseDate:
    @pytest.mark.parametrize("name, expected", [
        ("2024-01-15", _dt.date(2024, 1, 15)),
        ("2024-12-31", _dt.date(2024, 12, 31)),
        ("2000-01-01", _dt.date(2000, 1, 1)),
    ])
    def test_valid_dates(self, name, expected):
        assert _parse_date(name) == expected

    @pytest.mark.parametrize("name", [
        "not-a-date",
        "2024-1-1",
        "20240115",
        "",
        "2024-13-01",   # month 13 is invalid
        "2024-00-15",   # month 0 is invalid
        "some-dir-name",
        "2024-01-15-extra",
    ])
    def test_invalid_names_return_none(self, name):
        assert _parse_date(name) is None


# ---------------------------------------------------------------------------
# _archive_dir
# ---------------------------------------------------------------------------

class TestArchiveDir:
    def _make_date_dir(self, tmp_path: Path, name: str = "2024-01-01") -> Path:
        d = tmp_path / name
        d.mkdir()
        (d / "gate_eval.yaml").write_text("score: 90\n", encoding="utf-8")
        return d

    def test_dry_run_returns_true_without_creating_archive(self, tmp_path):
        date_dir = self._make_date_dir(tmp_path)
        ok, detail = _archive_dir(date_dir, dry_run=True)
        assert ok is True
        assert "[dry-run]" in detail
        assert not date_dir.with_suffix(".tar.gz").exists()
        assert date_dir.exists()  # source untouched

    def test_creates_archive_and_removes_source(self, tmp_path):
        date_dir = self._make_date_dir(tmp_path)
        ok, _ = _archive_dir(date_dir, dry_run=False)
        assert ok is True
        archive = date_dir.with_suffix(".tar.gz")
        assert archive.exists()
        assert not date_dir.exists()

    def test_archive_contains_correct_files(self, tmp_path):
        date_dir = self._make_date_dir(tmp_path)
        _archive_dir(date_dir, dry_run=False)
        archive = date_dir.with_suffix(".tar.gz")
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
        assert any("gate_eval.yaml" in n for n in names)

    def test_skips_if_archive_already_exists(self, tmp_path):
        date_dir = self._make_date_dir(tmp_path)
        archive = date_dir.with_suffix(".tar.gz")
        archive.write_text("existing", encoding="utf-8")
        ok, msg = _archive_dir(date_dir, dry_run=False)
        assert ok is False
        assert "already exists" in msg

    def test_cleans_up_partial_archive_on_tar_error(self, tmp_path):
        date_dir = self._make_date_dir(tmp_path)
        archive_path = date_dir.with_suffix(".tar.gz")
        with patch("tarfile.open", side_effect=tarfile.TarError("forced")):
            ok, _ = _archive_dir(date_dir, dry_run=False)
        assert ok is False
        assert not archive_path.exists()  # cleaned up

    def test_cleans_up_partial_archive_on_keyboard_interrupt(self, tmp_path):
        """B6: Ctrl-C must not leave a partial archive."""
        date_dir = self._make_date_dir(tmp_path)
        archive_path = date_dir.with_suffix(".tar.gz")
        with patch("tarfile.open", side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                _archive_dir(date_dir, dry_run=False)
        assert not archive_path.exists()  # partial archive cleaned up

    def test_returns_error_when_rmtree_fails(self, tmp_path):
        date_dir = self._make_date_dir(tmp_path)
        with patch("shutil.rmtree", side_effect=OSError("rmtree blocked")):
            ok, msg = _archive_dir(date_dir, dry_run=False)
        assert ok is False
        assert "source removal failed" in msg
        # Archive should still have been created
        assert date_dir.with_suffix(".tar.gz").exists()


# ---------------------------------------------------------------------------
# rotate
# ---------------------------------------------------------------------------

class TestRotate:
    def _setup_logs(self, tmp_path: Path, days_ago: list[int]) -> Path:
        """Create .methodology/decision_logs with date dirs at given offsets."""
        logs_dir = tmp_path / ".methodology" / "decision_logs"
        logs_dir.mkdir(parents=True)
        today = _dt.date.today()
        for offset in days_ago:
            name = (today - _dt.timedelta(days=offset)).isoformat()
            d = logs_dir / name
            d.mkdir()
            (d / "entry.yaml").write_text("x: 1\n", encoding="utf-8")
        return logs_dir

    def test_no_op_when_logs_dir_missing(self, tmp_path, capsys):
        rc = rotate(tmp_path, retention_days=30, dry_run=False)
        assert rc == 0
        assert "nothing to rotate" in capsys.readouterr().out

    def test_error_when_logs_dir_is_file(self, tmp_path):
        logs_dir = tmp_path / ".methodology" / "decision_logs"
        logs_dir.parent.mkdir(parents=True)
        logs_dir.write_text("file", encoding="utf-8")
        rc = rotate(tmp_path, retention_days=30, dry_run=False)
        assert rc == 1

    def test_archives_old_dirs(self, tmp_path):
        self._setup_logs(tmp_path, days_ago=[40, 50])
        rc = rotate(tmp_path, retention_days=30, dry_run=False)
        assert rc == 0
        logs_dir = tmp_path / ".methodology" / "decision_logs"
        archives = list(logs_dir.glob("*.tar.gz"))
        assert len(archives) == 2

    def test_skips_recent_dirs(self, tmp_path):
        self._setup_logs(tmp_path, days_ago=[1, 5, 10])
        rc = rotate(tmp_path, retention_days=30, dry_run=False)
        assert rc == 0
        logs_dir = tmp_path / ".methodology" / "decision_logs"
        archives = list(logs_dir.glob("*.tar.gz"))
        assert len(archives) == 0  # nothing old enough

    def test_mixed_old_and_recent(self, tmp_path):
        self._setup_logs(tmp_path, days_ago=[5, 35])
        rotate(tmp_path, retention_days=30, dry_run=False)
        logs_dir = tmp_path / ".methodology" / "decision_logs"
        archives = list(logs_dir.glob("*.tar.gz"))
        assert len(archives) == 1  # only the 35-day-old dir archived

    def test_dry_run_leaves_dirs_intact(self, tmp_path):
        self._setup_logs(tmp_path, days_ago=[40])
        rc = rotate(tmp_path, retention_days=30, dry_run=True)
        assert rc == 0
        logs_dir = tmp_path / ".methodology" / "decision_logs"
        dirs = [e for e in logs_dir.iterdir() if e.is_dir()]
        assert len(dirs) == 1  # source dir still present

    def test_skips_non_date_subdirs(self, tmp_path):
        """Operator-managed directories with non-date names must be left alone."""
        logs_dir = tmp_path / ".methodology" / "decision_logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "archive").mkdir()
        (logs_dir / "archive" / "old.yaml").write_text("x: 1\n", encoding="utf-8")
        rc = rotate(tmp_path, retention_days=30, dry_run=False)
        assert rc == 0
        assert (logs_dir / "archive").exists()  # untouched

    def test_returns_1_on_error(self, tmp_path):
        """If any archive operation fails, rotate returns 1."""
        self._setup_logs(tmp_path, days_ago=[40])
        with patch("scripts.rotate_decision_logs._archive_dir",
                   return_value=(False, "forced error")):
            rc = rotate(tmp_path, retention_days=30, dry_run=False)  # noqa: SIM117
        assert rc == 1

    def test_summary_printed(self, tmp_path, capsys):
        self._setup_logs(tmp_path, days_ago=[40])
        rotate(tmp_path, retention_days=30, dry_run=True)
        out = capsys.readouterr().out
        assert "retention=30d" in out
        assert "archived=" in out


# ---------------------------------------------------------------------------
# main (CLI integration)
# ---------------------------------------------------------------------------

class TestMain:
    def test_returns_0_for_missing_logs_dir(self, tmp_path):
        rc = main(["--project", str(tmp_path)])
        assert rc == 0

    def test_returns_1_for_missing_project(self, tmp_path):
        rc = main(["--project", str(tmp_path / "doesnotexist")])
        assert rc == 1

    def test_dry_run_flag(self, tmp_path):
        logs_dir = tmp_path / ".methodology" / "decision_logs"
        logs_dir.mkdir(parents=True)
        today = _dt.date.today()
        old_dir = logs_dir / (today - _dt.timedelta(days=40)).isoformat()
        old_dir.mkdir()
        (old_dir / "e.yaml").write_text("x: 1", encoding="utf-8")
        rc = main(["--project", str(tmp_path), "--dry-run"])
        assert rc == 0
        assert old_dir.exists()  # not actually removed
