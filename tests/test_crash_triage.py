"""Round 13 站3 — crash-triage: group harness-methodology's own crash
bundles by cause, optionally file each unfiled cause as a CR-BUG in
harness's own maintenance queue (core/maintenance/cr_manager.py).

--open-cr's target is cli.cr_cmds.harness_repo_root() — a public seam
(never a private one; see tests/test_patch_discipline.py) redirected in
every --open-cr test so nothing here ever lands in this checkout's real
.methodology/change_requests/.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cli.cr_cmds as cr_cmds
from core.maintenance import CRManager


def _bundle(crash_dir: Path, ts: str, pid: int, *, exc_type: str = "ValueError",
            frame: str = 'File "/x/harness_cli.py", line 42, in main') -> Path:
    crash_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "timestamp": ts,
        "exc_type": exc_type,
        "exc_message": "boom",
        "traceback": (
            "Traceback (most recent call last):\n"
            f"  {frame}\n"
            "    raise\n"
            f"{exc_type}: boom\n"
        ),
        "argv": ["status"],
        "cwd": str(crash_dir),
        "project": str(crash_dir.parent.parent),
        "harness_git_sha": "deadbeef",
        "python_version": "3.11.0",
        "repro_command": "python3 harness_cli.py status",
        "maintenance_prompt": "Diagnose and fix the root cause in harness's own code.",
    }
    path = crash_dir / f"crash_{ts}_{pid}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _args(project: Path, *, open_cr: bool = False) -> argparse.Namespace:
    return argparse.Namespace(project=str(project), open_cr=open_cr)


def _fake_harness_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "_fake_harness_repo"
    root.mkdir()
    monkeypatch.setattr(cr_cmds, "harness_repo_root", lambda: root)
    return root


class TestHarnessRepoRoot:
    def test_points_at_this_checkout(self):
        # Unpatched — sanity-checks the real seam resolves to a real
        # harness-methodology checkout, read-only (no --open-cr call here).
        root = cr_cmds.harness_repo_root()
        assert (root / "harness_cli.py").is_file()
        assert (root / "cli" / "cr_cmds.py").is_file()


class TestNoBundles:
    def test_no_crash_dir_prints_clean_message_and_returns_0(self, tmp_path, capsys):
        rc = cr_cmds.cmd_crash_triage(_args(tmp_path))
        assert rc == 0
        assert "no crash bundles found" in capsys.readouterr().out


class TestGrouping:
    def test_same_signature_bundles_group_into_one_row(self, tmp_path, capsys):
        crash_dir = tmp_path / ".sessi-work" / "crash"
        _bundle(crash_dir, "20260101T000000Z", 1)
        _bundle(crash_dir, "20260101T000100Z", 2)
        rc = cr_cmds.cmd_crash_triage(_args(tmp_path))
        assert rc == 0
        out = capsys.readouterr().out
        rows = [line for line in out.splitlines() if "harness_cli.py:42:ValueError" in line]
        assert len(rows) == 1
        assert rows[0].split()[0] == "2"  # COUNT column

    def test_different_signatures_get_separate_rows(self, tmp_path, capsys):
        crash_dir = tmp_path / ".sessi-work" / "crash"
        _bundle(crash_dir, "20260101T000000Z", 1, exc_type="ValueError")
        _bundle(crash_dir, "20260101T000100Z", 2, exc_type="RuntimeError",
                frame='File "/x/other.py", line 7, in helper')
        rc = cr_cmds.cmd_crash_triage(_args(tmp_path))
        assert rc == 0
        out = capsys.readouterr().out
        assert "harness_cli.py:42:ValueError" in out
        assert "other.py:7:RuntimeError" in out

    def test_unfiled_signature_shows_dash_in_cr_column(self, tmp_path, capsys):
        crash_dir = tmp_path / ".sessi-work" / "crash"
        _bundle(crash_dir, "20260101T000000Z", 1)
        cr_cmds.cmd_crash_triage(_args(tmp_path))
        out = capsys.readouterr().out
        data_row = [line for line in out.splitlines() if "ValueError" in line][0]
        assert "—" in data_row


class TestUnreadableBundle:
    def test_corrupt_bundle_is_skipped_and_recorded_on_the_ledger(self, tmp_path, capsys):
        crash_dir = tmp_path / ".sessi-work" / "crash"
        crash_dir.mkdir(parents=True)
        (crash_dir / "crash_bad.json").write_text("{not json", encoding="utf-8")
        rc = cr_cmds.cmd_crash_triage(_args(tmp_path))
        assert rc == 0
        assert "no crash bundles found" in capsys.readouterr().out
        ledger = tmp_path / ".sessi-work" / "degradations.jsonl"
        assert ledger.is_file()
        entry = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
        assert entry["component"] == "crash-triage"
        assert "crash_bad.json" in entry["what"]

    def test_one_corrupt_bundle_does_not_hide_the_readable_ones(self, tmp_path, capsys):
        crash_dir = tmp_path / ".sessi-work" / "crash"
        _bundle(crash_dir, "20260101T000000Z", 1)
        (crash_dir / "crash_bad.json").write_text("{not json", encoding="utf-8")
        rc = cr_cmds.cmd_crash_triage(_args(tmp_path))
        assert rc == 0
        assert "harness_cli.py:42:ValueError" in capsys.readouterr().out


class TestOpenCr:
    def test_files_one_cr_per_signature(self, tmp_path, capsys, monkeypatch):
        fake_root = _fake_harness_root(tmp_path, monkeypatch)
        crash_dir = tmp_path / ".sessi-work" / "crash"
        _bundle(crash_dir, "20260101T000000Z", 1)  # signature A
        _bundle(crash_dir, "20260101T000100Z", 2)  # signature A (repeat)
        _bundle(crash_dir, "20260101T000200Z", 3, exc_type="RuntimeError",
                frame='File "/x/other.py", line 7, in helper')  # signature B
        rc = cr_cmds.cmd_crash_triage(_args(tmp_path, open_cr=True))
        assert rc == 0
        crs = CRManager(fake_root).list_all()
        assert len(crs) == 2
        assert all(cr["type"] == "bug" for cr in crs)
        assert "2 new CR(s)" in capsys.readouterr().out

    def test_stamps_every_bundle_in_the_group_not_just_the_newest(self, tmp_path, monkeypatch):
        fake_root = _fake_harness_root(tmp_path, monkeypatch)
        crash_dir = tmp_path / ".sessi-work" / "crash"
        p1 = _bundle(crash_dir, "20260101T000000Z", 1)
        p2 = _bundle(crash_dir, "20260101T000100Z", 2)
        cr_cmds.cmd_crash_triage(_args(tmp_path, open_cr=True))
        assert p1.with_name(p1.name + ".triaged").is_file()
        assert p2.with_name(p2.name + ".triaged").is_file()
        assert fake_root  # keep the fixture referenced

    def test_rerun_does_not_open_a_second_cr(self, tmp_path, capsys, monkeypatch):
        fake_root = _fake_harness_root(tmp_path, monkeypatch)
        crash_dir = tmp_path / ".sessi-work" / "crash"
        _bundle(crash_dir, "20260101T000000Z", 1)
        cr_cmds.cmd_crash_triage(_args(tmp_path, open_cr=True))
        capsys.readouterr()
        rc = cr_cmds.cmd_crash_triage(_args(tmp_path, open_cr=True))
        assert rc == 0
        assert "0 new CR(s)" in capsys.readouterr().out
        assert len(CRManager(fake_root).list_all()) == 1

    def test_late_arriving_bundle_for_a_known_signature_reuses_the_existing_cr(
        self, tmp_path, monkeypatch,
    ):
        fake_root = _fake_harness_root(tmp_path, monkeypatch)
        crash_dir = tmp_path / ".sessi-work" / "crash"
        _bundle(crash_dir, "20260101T000000Z", 1)
        cr_cmds.cmd_crash_triage(_args(tmp_path, open_cr=True))
        cr_id = CRManager(fake_root).list_all()[0]["id"]

        p2 = _bundle(crash_dir, "20260102T000000Z", 2)  # same signature, later
        cr_cmds.cmd_crash_triage(_args(tmp_path, open_cr=True))

        assert len(CRManager(fake_root).list_all()) == 1  # no duplicate CR
        assert p2.with_name(p2.name + ".triaged").read_text(encoding="utf-8").strip() == cr_id

    def test_description_carries_repro_command_and_maintenance_prompt(self, tmp_path, monkeypatch):
        fake_root = _fake_harness_root(tmp_path, monkeypatch)
        crash_dir = tmp_path / ".sessi-work" / "crash"
        _bundle(crash_dir, "20260101T000000Z", 1)
        cr_cmds.cmd_crash_triage(_args(tmp_path, open_cr=True))
        cr = CRManager(fake_root).list_all()[0]
        assert "python3 harness_cli.py status" in cr["description"]
        assert "Diagnose and fix the root cause in harness's own code." in cr["description"]

    def test_open_cr_false_never_calls_harness_repo_root(self, tmp_path, monkeypatch):
        """Without --open-cr, the command must never resolve (let alone
        write to) the harness repo at all."""
        def _boom():
            raise AssertionError("harness_repo_root() must not be called without --open-cr")
        monkeypatch.setattr(cr_cmds, "harness_repo_root", _boom)
        crash_dir = tmp_path / ".sessi-work" / "crash"
        _bundle(crash_dir, "20260101T000000Z", 1)
        rc = cr_cmds.cmd_crash_triage(_args(tmp_path, open_cr=False))
        assert rc == 0
