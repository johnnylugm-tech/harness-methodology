"""Round 13 站1 — core/degradation_ledger.py.

Each test uses a unique component name so the module-level once-per-process
dedup set never collides across tests (avoids patching the private
_warned set — tests/test_patch_discipline.py's ratchet has no escape
hatch for new private-seam patches by design).
"""

from __future__ import annotations

import json

from core.degradation_ledger import record_degradation


def test_record_degradation_appends_jsonl_entry(tmp_path):
    record_degradation(tmp_path, "test_appends", "coverage fallback to 0", why="pytest --cov timed out")
    ledger = tmp_path / ".methodology" / "degradations.jsonl"
    assert ledger.exists()
    entry = json.loads(ledger.read_text(encoding="utf-8").strip())
    assert entry["component"] == "test_appends"
    assert entry["what"] == "coverage fallback to 0"
    assert entry["why"] == "pytest --cov timed out"
    assert isinstance(entry["ts"], float)


def test_record_degradation_prints_degraded_line_to_stderr(tmp_path, capsys):
    record_degradation(tmp_path, "test_prints", "sentinel missing, used manifest fallback")
    err = capsys.readouterr().err
    assert "[DEGRADED] test_prints: sentinel missing, used manifest fallback" in err


def test_record_degradation_dedups_stderr_print_but_keeps_appending(tmp_path, capsys):
    record_degradation(tmp_path, "test_dedups", "same thing")
    record_degradation(tmp_path, "test_dedups", "same thing")
    record_degradation(tmp_path, "test_dedups", "same thing")
    err = capsys.readouterr().err
    assert err.count("[DEGRADED]") == 1, "repeated identical degradation must not spam stderr"
    ledger = tmp_path / ".methodology" / "degradations.jsonl"
    lines = [line for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 3, "every occurrence must still be recorded in the ledger"


def test_record_degradation_different_keys_each_print(tmp_path, capsys):
    record_degradation(tmp_path, "test_diff_a", "thing one")
    record_degradation(tmp_path, "test_diff_b", "thing two")
    err = capsys.readouterr().err
    assert err.count("[DEGRADED]") == 2


def test_record_degradation_never_raises_on_ledger_write_failure(tmp_path, capsys):
    blocker = tmp_path / "blocked"
    blocker.write_text("x")
    record_degradation(blocker, "test_write_failure", "thing")  # project path is a FILE, not a dir
    err = capsys.readouterr().err
    assert "[DEGRADED]" in err
    assert "failed to write degradation ledger entry" in err


class _BrokenStderr:
    """A stderr whose reader has gone away — a pipe into `head`, a killed
    `tee`, a closed terminal. `BrokenPipeError` is an `OSError`."""

    def write(self, _s):
        raise BrokenPipeError(32, "Broken pipe")

    def flush(self):
        raise BrokenPipeError(32, "Broken pipe")


def test_a_broken_stderr_does_not_end_the_run(tmp_path, monkeypatch):
    """Round 83 站2 — the function that records quiet failures, failing loudly.

    This module has promised "Never raises" since Round 13 站1. The promise
    covered the `try` around the file write; the `[DEGRADED]` print sat OUTSIDE
    it. taskq-new's committed crash bundle
    `.methodology/crash/crash_20260821T211052Z_33516.json` is that line:

        exc_type : BrokenPipeError  [Errno 32] Broken pipe
        argv     : ['advance-phase', '--completed', '2', '--project', ...]
        frame    : core/degradation_ledger.py:77 in record_degradation
                   print(f"[DEGRADED] ...", file=sys.stderr)

    A phase transition ended by its own logging.
    """
    import sys

    monkeypatch.setattr(sys, "stderr", _BrokenStderr())
    record_degradation(tmp_path, "test_broken_stderr", "thing", why="reason")


def test_a_broken_stderr_still_leaves_the_record(tmp_path, monkeypatch):
    """And the part that matters after the run is over.

    The stderr line is a courtesy to whoever is watching live; the JSONL is
    the artefact that outlives the run (Round 27 站3 moved it out of
    .sessi-work for exactly that reason). Before the fix the print came first
    and unguarded, so a broken stderr meant the append never ran — the record
    was lost in precisely the runs worth debugging.
    """
    import sys

    monkeypatch.setattr(sys, "stderr", _BrokenStderr())
    record_degradation(tmp_path, "test_broken_stderr_record", "thing",
                       why="reason", owner="harness")

    ledger = tmp_path / ".methodology" / "degradations.jsonl"
    assert ledger.exists(), (
        "stderr going away must not take the ledger with it — the JSONL is "
        "the half of this function that survives the run"
    )
    entry = json.loads(ledger.read_text(encoding="utf-8").strip())
    assert entry["component"] == "test_broken_stderr_record"
    assert entry["owner"] == "harness"


def test_a_broken_stderr_and_an_unwritable_ledger_together(tmp_path, monkeypatch):
    """Both halves failing at once, which is the shape that actually escaped.

    The `except OSError` handler's own remedy was a second unguarded print to
    the same stderr, so a broken pipe there re-raised out of the handler that
    existed to stop exactly this. A guard that only covered the first print
    would leave this path open and this test is what says so.
    """
    import sys

    blocker = tmp_path / "blocked"
    blocker.write_text("x")  # project path is a FILE — the append will fail
    monkeypatch.setattr(sys, "stderr", _BrokenStderr())
    record_degradation(blocker, "test_broken_both", "thing")
