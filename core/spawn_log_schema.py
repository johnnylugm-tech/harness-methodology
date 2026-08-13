"""spawn_log_schema.py — one log file, one row shape (Round 50 站5).

`.methodology/sessions_spawn.log` has two writers and had no schema:

  core/agent_spawner._log_dispatch          — one row per Python-spawned agent
  cli/report_cmds.cmd_log_dispatch          — the generated workflow's rows,
                                              flushed via `log-dispatch --batch`

Measured 2026-08-13 on taskq-api's full P1-P8 run: 292 rows, 176 from the
Python side and 116 from the workflow side, with five fields in common. Every
field Round 14 站0 added so a run's cost could be answered — `total_cost_usd`,
`num_turns`, `usage`, `duration_api_ms` — exists on Python rows only, because
the Workflow sandbox has neither the process envelope nor a clock (see
`cmd_log_dispatch`'s own note). That is not a defect in either writer.

The defect is in the reader. `run-report` divided "rows carrying a cost" by
"all rows", so a run where every cost-capable dispatch recorded its cost read
as 152/292 — 48% of the run's spending apparently lost, when the missing 116
were rows that structurally cannot carry the field. Round 50's shape exactly:
a denominator standing in for a population it is not.

So this module states the row shape once, and says which half of the
population each field is answerable for. Two consumers:

  - `cmd_log_dispatch` validates each incoming workflow row, so a field the
    generated JS invents becomes visible instead of silently joining the file
  - `_spawn_log_report` divides per-field metrics by `rows_that_can_carry`

Adding a field means adding it here first. `tests/test_spawn_log_schema.py`
holds both writers to this list — the JS one by reading the shipped
`run-all.js`, the Python ones by reading their call sites.
"""

from __future__ import annotations

from typing import Any, Iterable

__all__ = [
    "REQUIRED_FIELDS", "OPTIONAL_FIELDS", "PYTHON_ONLY_FIELDS",
    "ENVELOPE_TOP_KEYS", "ENVELOPE_USAGE_KEYS",
    "SUBSTRATE_PYTHON", "SUBSTRATE_WORKFLOW",
    "substrate_of", "rows_that_can_carry", "validate_row",
]

SUBSTRATE_PYTHON = "python"
SUBSTRATE_WORKFLOW = "workflow"

# Written by SessionsSpawnLogger.log_spawn for every row, whichever writer
# called it. A row missing one of these is malformed, not merely sparse.
#
# `substrate` is NOT here, deliberately: it is stamped on every row written
# from this station on, but the 176 Python-side rows already on disk predate
# the stamp, and a validator that called the existing file malformed would be
# describing its own newness rather than a defect.
REQUIRED_FIELDS = frozenset({
    "timestamp", "role", "task", "session_id", "status",
})

# Round 14 站0: fields already present in the `claude -p --output-format json`
# envelope (confirmed live 2026-07-17 against installed claude 2.1.206) that
# were parsed then discarded — only `result`/`session_id`/`commit` were ever
# read. `duration_ms` is deliberately excluded: it duplicates the wallclock
# already measured independently via time.monotonic() (Fix H-F's
# duration_seconds).
#
# Declared here rather than in agent_spawner because they are log columns
# first and parser output second; `_extract_envelope_metrics` imports them.
ENVELOPE_TOP_KEYS: tuple[str, ...] = ("total_cost_usd", "num_turns", "duration_api_ms")
ENVELOPE_USAGE_KEYS: tuple[str, ...] = (
    "input_tokens", "output_tokens",
    "cache_read_input_tokens", "cache_creation_input_tokens",
)

# Fields only a Python-spawned row can carry, because producing them needs
# something the Workflow sandbox does not have: the subprocess envelope
# (cost, turns, token counts), a clock (durations), or an exit status.
# This is the list that keeps a per-field denominator honest.
PYTHON_ONLY_FIELDS = frozenset(ENVELOPE_TOP_KEYS) | {
    "usage",
    "duration_seconds",
    "exit_code",
    "dispatch_attempt",
    "retry_round",
    "transport_error",
    "inner_status",
    "error_class",
}

# Everything else a writer may set. Sparse by design — most rows carry a
# handful — but a name not on this list is a writer inventing a column.
_OTHER_OPTIONAL_FIELDS = frozenset({
    # per-FR dispatch context (Python side)
    "phase", "fr_id", "regression_flags", "confidence", "turn_number",
    # deterministic-tool rows: cli/project_cmds._log_amend_sab_outcome
    "step", "tool_kind", "outcome", "rc", "src_dir", "dry_run", "strict",
    # the workflow side's own context: it has no phase number, only the
    # human label of the box it is in, and no envelope, only a reply length
    "phase_label", "reply_chars",
    # both sides
    "error_output",
    # which writer produced the row (see substrate_of on why it is optional)
    "substrate",
    # stamped by log_update when a PENDING row is resolved later
    "_updated_at",
})

OPTIONAL_FIELDS = PYTHON_ONLY_FIELDS | _OTHER_OPTIONAL_FIELDS


def substrate_of(row: dict) -> str:
    """Which writer produced *row*.

    Rows written before this station carry no `substrate` on the Python side
    (the workflow side has stamped it since Round 26 站5), so absence reads as
    python. That fallback is for history only: `log_spawn` now stamps every
    row it writes, so a new row never depends on it.
    """
    value = row.get("substrate")
    return value if isinstance(value, str) and value else SUBSTRATE_PYTHON


def rows_that_can_carry(rows: "Iterable[dict]", field: str) -> list[dict]:
    """The sub-population a metric over *field* may honestly divide by.

    A workflow row without `total_cost_usd` is not a missing measurement; it
    is a row from a substrate that cannot measure that. Counting it in the
    denominator turns a complete record into a half-empty one.
    """
    if field in PYTHON_ONLY_FIELDS:
        return [r for r in rows if substrate_of(r) == SUBSTRATE_PYTHON]
    return list(rows)


def validate_row(row: "dict[str, Any]") -> list[str]:
    """Problems with *row*, most useful first. Empty list means it conforms.

    Reports rather than raises: this runs on the write path of an
    observability record, and losing the record must never break the work
    being observed (`cmd_log_dispatch`'s contract since Round 26 站5).
    """
    problems: list[str] = []
    for name in sorted(REQUIRED_FIELDS):
        if name not in row:
            problems.append(f"missing required field {name!r}")
    known = REQUIRED_FIELDS | OPTIONAL_FIELDS
    for name in sorted(row):
        if name not in known:
            problems.append(
                f"unknown field {name!r} — core/spawn_log_schema.py has never "
                f"heard of it; add it there before writing it"
            )
    return problems
