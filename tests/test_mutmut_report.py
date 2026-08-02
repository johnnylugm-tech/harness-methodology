"""Round 31 站0/站1 — one place that reads mutmut, and it reads what mutmut prints.

Four parsers of mutmut output existed before this round. Three of them could
not read any format the system actually produces, and every one of them
abstained by returning a value that means "nothing found":

  parser                                  expects            real input
  ────────────────────────────────────────────────────────────────────────
  _count_mutmut_results (sqlite)          Mutant.status      ✅ authoritative
  _parse_mutmut_survivors                 "10, 24"           ❌ 0 of 308
  _extract_mutmut_kill_rate               "Killed 240"       ❌ None
  tool_runners._score_mutmut              "Killed: 240"      ❌ dead (skip-list)

The two live consequences, both measured on a real Gate 2 run:

1. `mutmut results` groups survivor ids per file and collapses consecutive
   ids into RANGES — ``ranges()`` in mutmut/cache.py. The regex only accepted
   bare comma-separated integers, so a run reporting ``Survived 🙁 (308)``
   wrote ``survivor_count: 0`` into .methodology/mutation_survivors.json, and
   `bug-hunt-targets` (cli/check_cmds.py) read that zero as "no leads".
   308 behaviours that no test asserts were recorded as none.

2. The framework's own `mutation-test-score` prints
   ``killed=240 survived=308 score=43.8`` — and the framework's own
   anti-fabrication cross-check could not parse it. It returned None on the
   agent's tool_output, on the framework's message, AND on raw `mutmut
   results`; the only string it did parse was one nothing in the system emits.
   A cross-check that has never fired is not a cross-check.

So the format lives in ONE module with the formatter beside the parser, and a
round-trip test holds them together.
"""
from __future__ import annotations

import pytest

import harness_cli  # noqa: F401  entry-first load order
from core.quality_gate.mutation_enforcer import (  # noqa: E402
    _parse_mutmut_survivors,
)
from core.quality_gate.mutmut_report import kill_rate  # noqa: E402

pytestmark = [pytest.mark.core]


# Shaped exactly like mutmut 2.x `results` output: a banner, a per-file header
# carrying the count, a blank line, then the ids as ranges. Module names are
# generic on purpose (tests/test_no_hardcoded_paths.py).
REAL_RESULTS_OUTPUT = """To apply a mutant on disk:
    mutmut apply <id>

To show a mutant:
    mutmut show <id>


Survived \U0001f641 (18)

---- /tmp/proj/src/myapp/service/breaker.py (13) ----

233-245

---- /tmp/proj/src/myapp/storage/task_store.py (5) ----

40-41, 43-44, 50
"""


def test_survivors_parse_the_range_form_mutmut_actually_prints():
    survivors = _parse_mutmut_survivors(REAL_RESULTS_OUTPUT)
    assert len(survivors) == 18, (
        f"parsed {len(survivors)} survivors from output whose own banner says "
        f"18 — mutmut collapses consecutive ids into ranges (233-245), and a "
        f"parser that only accepts bare integers reports every run as clean"
    )
    per_file = {}
    for s in survivors:
        per_file[s["file"]] = per_file.get(s["file"], 0) + 1
    assert per_file == {
        "/tmp/proj/src/myapp/service/breaker.py": 13,
        "/tmp/proj/src/myapp/storage/task_store.py": 5,
    }, f"per-file counts must match each header's own number: {per_file}"


def test_the_banner_total_is_recorded_beside_what_was_parsed():
    """`survivor_count: 0` sitting next to a raw banner reading
    `Survived (308)` was self-refuting, and invisible because only one of the
    two numbers was a field."""
    from core.quality_gate.mutmut_report import parse_reported_total

    assert parse_reported_total(REAL_RESULTS_OUTPUT) == 18
    assert parse_reported_total("no banner here") is None


def test_the_per_file_header_count_is_checked_against_the_ids_parsed():
    """A header saying (12) with ids that sum to 3 means the parse is wrong.
    Silence on that mismatch is how the range bug survived: the artifact looked
    well-formed, it was just empty."""
    survivors = _parse_mutmut_survivors(REAL_RESULTS_OUTPUT)
    ids = [s["mutant_id"] for s in survivors]
    assert len(set(ids)) == len(ids), f"duplicate mutant ids parsed: {ids}"
    assert all(str(i).isdigit() for i in ids), (
        f"a range endpoint leaked into the id list instead of being expanded: {ids}"
    )


def test_the_bare_integer_form_still_parses():
    """mutmut prints single ids without a dash. Range support must not cost
    the form that already worked."""
    out = (
        "Survived \U0001f641 (2)\n"
        "\n"
        "---- src/myapp/a.py (2) ----\n"
        "\n"
        "10, 24\n"
    )
    survivors = _parse_mutmut_survivors(out)
    assert [s["mutant_id"] for s in survivors] == ["10", "24"]


def test_unrecognised_output_still_yields_nothing():
    """The empty list is only correct when there is genuinely nothing to read.
    Kept explicit so a future parser change cannot quietly start inventing
    survivors out of unrelated text."""
    assert _parse_mutmut_survivors("mutmut: command not found") == []
    assert _parse_mutmut_survivors("") == []


# ── format parity: the framework must be able to read the framework ──────

def test_the_frameworks_own_score_message_is_parseable_by_the_framework():
    from core.quality_gate.mutmut_report import format_score_message

    msg = format_score_message(killed=240, survived=308, score=43.8,
                               scope="03-development/src/myapp")
    rate = kill_rate(msg)
    assert rate is not None, (
        f"the anti-fabrication cross-check cannot read the string the "
        f"framework's own mutation-test-score prints: {msg!r}"
    )
    assert abs(rate - 43.8) < 0.1, rate


# ── legacy free-text shapes ─────────────────────────────────────────────
# Moved verbatim from tests/test_harness_bridge_oracle.py (Round 31 站2) along
# with the parsing they cover. Nothing in this repo emits these, but a human
# may paste output from another mutmut version into a tool_output file.

def test_kill_rate_counts_form_exact():
    """Killed 70 Survived 30 → 70.0.  Kills division and addition mutations."""
    assert kill_rate("Killed 70 Survived 30 mutation tests") == 70.0


def test_kill_rate_counts_form_is_float_division():
    """Killed 1 Survived 2 → 33.33...  Verifies float division (not int)."""
    result = kill_rate("Killed 1 Survived 2")
    assert result is not None
    assert abs(result - 33.333) < 0.01


def test_kill_rate_percentage_form():
    """'mutation score: 75%' → 75.0.  Kills regex and float() conversion."""
    assert kill_rate("Results: mutation score: 75%") == 75.0


def test_kill_rate_percentage_form_with_decimal():
    """'mutation score: 66.7%' → 66.7.  Kills decimal group in regex."""
    result = kill_rate("mutation score 66.7%")
    assert result is not None
    assert abs(result - 66.7) < 0.01


def test_kill_rate_counts_form_wins_over_percentage():
    """Both formats present → counts win.  Kills fallthrough."""
    assert kill_rate("Killed 80 Survived 20\nmutation score: 50%") == 80.0


def test_kill_rate_zero_total_returns_none():
    """Killed 0 Survived 0 → total=0 → None.  Kills the total > 0 guard."""
    assert kill_rate("Killed 0 Survived 0") is None


def test_kill_rate_no_match_returns_none():
    """No parseable data → None.  Kills the None return path."""
    assert kill_rate("no relevant content here") is None


def test_kill_rate_is_case_insensitive():
    """'killed 5 survived 5' → 50.0.  Kills IGNORECASE flag removal."""
    assert kill_rate("killed 5 survived 5") == 50.0


def test_score_message_round_trips():
    """Formatter and parser sit in one module so this can be a real
    round-trip rather than two regexes that agree by luck."""
    from core.quality_gate.mutmut_report import format_score_message, parse_score

    for killed, survived in ((240, 308), (0, 5), (7, 0)):
        msg = format_score_message(
            killed=killed, survived=survived,
            score=round(100.0 * killed / (killed + survived), 1),
            scope="src",
        )
        parsed = parse_score(msg)
        assert parsed is not None, msg
        assert parsed["killed"] == killed and parsed["survived"] == survived, (
            f"{msg!r} -> {parsed}"
        )


def test_the_producer_uses_the_shared_formatter():
    """Round 31: the whole point is that there is no second place where this
    string is spelled out. compute_mutation_score must call the formatter, not
    re-write the f-string next to it."""
    import inspect

    from core.quality_gate import mutation_enforcer

    src = inspect.getsource(mutation_enforcer.compute_mutation_score)
    assert "format_score_message" in src, (
        "compute_mutation_score builds the score message with its own f-string "
        "again — that is the drift this module exists to end"
    )
