"""Round 24 站3 — one time base, enforced.

A naive `datetime.now()` produces a LOCAL timestamp with no offset. Written
into an artifact, it cannot be compared with anything: not with
`state.json`'s `datetime.now(timezone.utc)`, not with
`gate_timestamps.jsonl`'s epoch float, and not with another naive stamp from
a host in a different zone. Worse, it is indistinguishable from a naive UTC
stamp, so a reader cannot even tell that the comparison is invalid.

That is not hypothetical. Reading the run-all-by-workflow P1-P8 artifacts
during the Round 24 audit, `sessions_spawn.log`'s naive-local "15:44" was
lined up against `state.json`'s "07:43+00:00" and read as an eight-hour
stall. The real gap was 1h18m. The observability layer answered a question
incorrectly rather than declining to answer it.

Same shape as tests/test_exception_swallow_ratchet.py, and the same policy:
**no allowlist**. The fix is always one call — `core.utils.timefmt.utc_now_iso()`
for an artifact timestamp, or an explicit `tz=`/`timezone.utc` argument when
`datetime.now` is used for something else (duration arithmetic, display).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent
_SCAN_DIRS = ("cli", "core", "harness", "scripts", "detection")


def _has_tz_argument(call: ast.Call) -> bool:
    if call.args:
        return True
    return any(kw.arg in ("tz", "tzinfo") or kw.arg is None for kw in call.keywords)


def naive_now_sites(source: str, filename: str = "<src>") -> list[tuple[int, str]]:
    """(line, snippet) for each timezone-naive now()/utcnow() call.

    Factored out so the negative controls can prove the scan fires.

    Flags:
      datetime.now()            — naive local, the defect
      datetime.datetime.now()   — same, fully qualified
      datetime.utcnow()         — naive UTC: correct instant, no offset, so a
                                  reader still cannot tell it apart from local
    Accepts:
      datetime.now(timezone.utc) / now(tz=...) — offset-carrying
      time.time()                              — unambiguous epoch
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        if attr == "utcnow":
            found.append((node.lineno, "datetime.utcnow()"))
            continue
        if attr != "now":
            continue
        # `X.now(...)` where X names datetime (bare, or datetime.datetime).
        base = node.func.value
        base_name = (
            base.id if isinstance(base, ast.Name)
            else base.attr if isinstance(base, ast.Attribute)
            else None
        )
        if base_name not in ("datetime", "dt"):
            continue
        if not _has_tz_argument(node):
            found.append((node.lineno, "datetime.now()"))
    return found


def _scan_repo() -> dict[str, list[tuple[int, str]]]:
    violations: dict[str, list[tuple[int, str]]] = {}
    for d in _SCAN_DIRS:
        root = REPO / d
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            hits = naive_now_sites(path.read_text(encoding="utf-8"), str(path))
            if hits:
                violations[str(path.relative_to(REPO))] = hits
    return violations


def test_no_timezone_naive_timestamps_in_production_code():
    violations = _scan_repo()
    assert not violations, (
        "timezone-naive timestamp(s) — a local `datetime.now()` written into an "
        "artifact cannot be aligned with state.json's UTC or gate_timestamps.jsonl's "
        "epoch, and reads identically to a naive UTC stamp. Use "
        "core.utils.timefmt.utc_now_iso() for artifact timestamps, or pass "
        "tz=timezone.utc explicitly. No allowlist: the fix is one call.\n"
        + "\n".join(
            f"  {f}:{ln}  {snippet}"
            for f, hits in sorted(violations.items())
            for ln, snippet in hits
        )
    )


# ── negative controls: the scan must actually fire ──────────────────────

def test_scan_flags_bare_datetime_now():
    assert naive_now_sites("from datetime import datetime\nx = datetime.now()\n") == [
        (2, "datetime.now()")
    ]


def test_scan_flags_qualified_datetime_now():
    assert naive_now_sites("import datetime\nx = datetime.datetime.now()\n") == [
        (2, "datetime.now()")
    ]


def test_scan_flags_utcnow():
    """Naive UTC is the right instant with no offset — still unalignable."""
    assert naive_now_sites("from datetime import datetime\nx = datetime.utcnow()\n") == [
        (2, "datetime.utcnow()")
    ]


def test_scan_accepts_explicit_utc():
    src = "from datetime import datetime, timezone\nx = datetime.now(timezone.utc)\n"
    assert naive_now_sites(src) == []


def test_scan_accepts_tz_keyword():
    src = "from datetime import datetime, timezone\nx = datetime.now(tz=timezone.utc)\n"
    assert naive_now_sites(src) == []


def test_scan_ignores_unrelated_now_methods():
    """`clock.now()` / `Timer.now()` are not datetime constructors."""
    assert naive_now_sites("x = clock.now()\ny = self.now()\n") == []


def test_scan_ignores_time_time():
    assert naive_now_sites("import time\nx = time.time()\n") == []


# ── the artifacts themselves ────────────────────────────────────────────

def test_utc_now_iso_carries_an_offset():
    from core.utils.timefmt import utc_now_iso

    stamp = utc_now_iso()
    assert stamp.endswith("+00:00"), stamp
    from datetime import datetime

    assert datetime.fromisoformat(stamp).tzinfo is not None


def test_gate_timestamps_rows_carry_both_epoch_and_iso(tmp_path):
    """`ts` stays an epoch float (doctor does arithmetic on it); `iso` is added
    so the row can be aligned with state.json without knowing the writer's zone."""
    import json

    from core.quality_gate import gate1_evidence

    gate1_evidence.record_gate_timestamp(tmp_path, phase=3, gate_num=1, fr_id="FR-01")
    rows = [
        json.loads(ln)
        for ln in (tmp_path / ".methodology" / "gate_timestamps.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if ln.strip()
    ]
    assert len(rows) == 1
    assert isinstance(rows[0]["ts"], float)
    assert rows[0]["iso"].endswith("+00:00")


def test_gate_timestamps_reader_tolerates_rows_without_iso(tmp_path):
    """Backward compatibility: rows written before this station have no `iso`."""
    import json

    from core.quality_gate import gate1_evidence

    method = tmp_path / ".methodology"
    method.mkdir(parents=True)
    (method / "gate_timestamps.jsonl").write_text(
        json.dumps({"phase": 3, "gate": 1, "fr_id": "FR-01", "ts": 1785226543.5,
                    "source": "finalize"}) + "\n",
        encoding="utf-8",
    )
    gate1_evidence.record_gate_timestamp(tmp_path, phase=3, gate_num=1, fr_id="FR-02")
    rows = [
        json.loads(ln)
        for ln in (method / "gate_timestamps.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert len(rows) == 2
    assert "iso" not in rows[0] and "iso" in rows[1]


def test_spawn_log_timestamps_are_offset_aware(tmp_path):
    import json

    from core.sessions_spawn_logger import SessionsSpawnLogger

    logger = SessionsSpawnLogger(tmp_path)
    logger.log_spawn(role="developer", task="t", session_id="s1", status="complete")
    rows = [
        json.loads(ln)
        for ln in (tmp_path / ".methodology" / "sessions_spawn.log")
        .read_text(encoding="utf-8")
        .splitlines()
        if ln.strip()
    ]
    assert rows and rows[-1]["timestamp"].endswith("+00:00")
