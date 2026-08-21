"""Round 67 站0 — the committed artifact is the one the verdict was made from.

`finalize_gate` loads `.sessi-work/gate{N}_result.json` into `raw` and then
spends nine hundred lines correcting it: S4 writes back a framework-measured
score and marks the ones it could not reproduce, `_mark_framework_na` turns an
agent's null into a framework-verified N/A, `_mark_stubbed_boundary_dimensions`
marks the dimensions measured over a suite that replaced its own boundary.

`cli/gate_cmds.py` then persists the result by re-reading THE SAME FILE from
disk — the uncorrected copy the agent wrote — and copying four top-level fields
plus one field per dimension (`score`) back onto it.

Everything else the framework decided is dropped on the floor. Measured on
taskq-cc's committed `gate4_result.json` (2026-08-21): sixteen dimensions,
**zero `score_source`** — while `measurement_scope` in the same file names two
of them as unscored. The only field that could explain that number is the one
the persist step does not carry.

The comment above the sync loop already records this failure once, from
Round 30: "this block re-reads the agent-written file from disk and only ever
patched 4 top-level fields — the corrected per-dimension score never reached
the persisted breakdown". Round 30 added `score` to the whitelist. Round 50 and
51 added `score_source` to the in-memory dict and nobody came back here.

A whitelist is a list somebody has to remember to extend. The source is the
thing to fix: persist what finalize_gate is holding.
"""

from __future__ import annotations


def _framework_view() -> dict:
    """What finalize_gate is holding when it returns — corrections included."""
    return {
        "gate": 1,
        "phase": 3,
        "breakdown": {
            "test_coverage": {
                "score": 100.0,
                "threshold": 80,
                # Written by _mark_stubbed_boundary_dimensions.
                "score_source": "stubbed_boundary",
                # Rewritten by S4 when the framework re-ran the tool.
                "tool_evidence": "framework: pytest --cov → TOTAL 81%",
                "tool_output": ".methodology/gate_evidence/gate1/test_coverage.txt",
            },
            "linting": {
                "score": None,
                "threshold": 90,
                # Written by _mark_framework_na.
                "score_source": "framework_na",
                "tool_evidence": "framework: ruff → tool absent",
            },
        },
    }


def _agent_view() -> dict:
    """What is still on disk at `.sessi-work/` — the uncorrected copy."""
    return {
        "gate": 1,
        "phase": 3,
        "breakdown": {
            "test_coverage": {
                "score": 42.0,
                "threshold": 80,
                "tool_evidence": "agent: coverage looked fine to me",
                "tool_output": ".sessi-work/test_coverage.txt",
            },
            "linting": {
                "score": None,
                "threshold": 90,
                "tool_evidence": "agent: N/A",
            },
        },
    }


def test_the_persisted_breakdown_is_the_framework_view_not_the_agent_view():
    """Every key, not a whitelist of them.

    Asserting `score_source` survives would pass a fix that adds one more line
    to the sync loop — and the next field added in-memory would be lost the
    same way. The entry has to BE the framework's entry.
    """
    from cli.gate_cmds import build_persisted_gate_result

    framework, agent = _framework_view(), _agent_view()
    doc = build_persisted_gate_result(framework, agent)

    for name, entry in framework["breakdown"].items():
        persisted = doc["breakdown"].get(name) or {}
        disagreements = {
            k: (v, persisted.get(k)) for k, v in entry.items()
            if persisted.get(k, object()) != v
        }
        assert not disagreements, (
            f"persisted `{name}` disagrees with the entry the verdict was "
            f"made from, on {sorted(disagreements)}:\n"
            + "\n".join(f"  {k}: framework={f!r} persisted={p!r}"
                        for k, (f, p) in sorted(disagreements.items()))
        )


def test_a_field_the_framework_invents_tomorrow_survives_persistence():
    """The regression this round is about, stated as a property.

    `score_source` is only the field that happened to be lost this time. A
    persist step that enumerates fields loses the next one too, silently, and
    the artifact goes on looking complete.
    """
    from cli.gate_cmds import build_persisted_gate_result

    framework = _framework_view()
    framework["breakdown"]["test_coverage"]["some_future_marker"] = "measured"
    doc = build_persisted_gate_result(framework, _agent_view())

    assert doc["breakdown"]["test_coverage"].get("some_future_marker") == "measured", (
        "a field the framework wrote into the breakdown did not reach the "
        "committed file. The persist step is enumerating what to carry, so "
        "every field added upstream from now on is lost by default"
    )


def test_the_agent_copy_still_supplies_what_the_framework_did_not_touch():
    """Not a replacement — the framework's view is authoritative WHERE IT SPOKE.

    The agent writes fields finalize_gate never reads (`rounds_used`,
    per-dimension `tests_passed`, the devil's-advocate block). Dropping those
    would trade one silent loss for another.
    """
    from cli.gate_cmds import build_persisted_gate_result

    agent = _agent_view()
    agent["rounds_used"] = 3
    agent["breakdown"]["test_coverage"]["tests_passed"] = 287
    doc = build_persisted_gate_result(_framework_view(), agent)

    assert doc.get("rounds_used") == 3, (
        "a top-level field only the agent wrote was dropped"
    )
    assert doc["breakdown"]["test_coverage"].get("tests_passed") == 287, (
        "a per-dimension field only the agent wrote was dropped"
    )
