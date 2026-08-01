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
