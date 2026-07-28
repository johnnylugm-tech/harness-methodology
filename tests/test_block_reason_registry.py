"""Round 24 站1 — every gate-block cause must have a registered remediation.

`harness_bridge.finalize_gate` raises `GateBlockedError` from ten sites. Nine
attach a `details` dict whose key names the cause. If a new site adds a key
that `core/quality_gate/block_reason.py` does not know, the agent sees the raw
detail with a "no remediation registered" banner instead of an actionable fix —
survivable at runtime (see `_unknown_detail_reason`, deliberately not an
exception on the BLOCKED path), but it must never ship. This suite is the
mechanism that stops it shipping.

The scanner reads detail dicts passed **either** by keyword (`details={...}`)
**or** positionally (`GateBlockedError(gate, result, {...})` — the `da_waiver`
site). That distinction is not hypothetical: the first version of this scan
only walked `node.keywords` and silently reported 6 keys instead of 7, which is
the same "the checker's own coverage was never checked" shape Round 24 exists
to close.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.quality_gate.block_reason import (
    _DETAIL_REGISTRY,
    BlockReason,
    derive_block_reasons,
)

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent
BRIDGE = REPO / "harness" / "harness_bridge.py"


def _dict_keys(node: ast.expr) -> list[str] | None:
    """Literal string keys of a dict node, or None if it isn't a literal dict."""
    if not isinstance(node, ast.Dict):
        return None
    keys = []
    for k in node.keys:
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            keys.append(k.value)
    return keys


def scan_bridge_detail_keys(source: str) -> tuple[set[str], int, int]:
    """(detail keys, total raise sites, sites carrying details).

    Factored out so the negative test can prove the scan sees positional args.
    """
    tree = ast.parse(source)
    keys: set[str] = set()
    total = 0
    with_details = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
            continue
        func = node.exc.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "GateBlockedError":
            continue
        total += 1
        found: list[str] | None = None
        for kw in node.exc.keywords:
            if kw.arg == "details":
                found = _dict_keys(kw.value)
        if found is None and len(node.exc.args) >= 3:
            # GateBlockedError(gate_num, result, details) — positional third arg.
            found = _dict_keys(node.exc.args[2])
        if found:
            with_details += 1
            keys.update(found)
    return keys, total, with_details


def test_every_bridge_detail_key_has_a_registered_remediation():
    keys, total, with_details = scan_bridge_detail_keys(BRIDGE.read_text(encoding="utf-8"))
    assert total >= 10, f"expected at least 10 GateBlockedError raise sites, found {total}"
    assert with_details >= 8, (
        f"expected at least 8 raise sites carrying details, found {with_details} — "
        "if a site lost its details dict, the agent lost the reason it blocked"
    )
    missing = sorted(keys - set(_DETAIL_REGISTRY))
    assert not missing, (
        f"harness_bridge raises GateBlockedError with details key(s) {missing} that "
        f"core/quality_gate/block_reason.py cannot explain. Add each to _DETAIL_REGISTRY "
        f"with a headline and a remediation the agent can act on — a block reason with "
        f"no remediation is the Round 24 defect verbatim."
    )


def test_registry_has_no_entries_the_bridge_never_raises():
    """Reverse direction: a stale entry means a cause was removed and the
    remediation text was left to rot (and would mislead if the key came back
    with different semantics)."""
    keys, _, _ = scan_bridge_detail_keys(BRIDGE.read_text(encoding="utf-8"))
    stale = sorted(set(_DETAIL_REGISTRY) - keys)
    assert not stale, (
        f"_DETAIL_REGISTRY has entries {stale} that harness_bridge never raises. "
        "Remove them, or point them at the site that still raises them."
    )


def test_scan_sees_positionally_passed_details():
    """Negative control: the `da_waiver` site passes details as the third
    positional argument. A scan that only reads keywords loses it."""
    src = (
        "def f():\n"
        "    raise GateBlockedError(4, result, {'positional_only_key': ['x']})\n"
    )
    keys, total, with_details = scan_bridge_detail_keys(src)
    assert keys == {"positional_only_key"}
    assert (total, with_details) == (1, 1)


def test_scan_reports_a_detail_less_site():
    src = "def f():\n    raise GateBlockedError(ctx.gate_num, result)\n"
    keys, total, with_details = scan_bridge_detail_keys(src)
    assert keys == set()
    assert (total, with_details) == (1, 0)


# ── derive_block_reasons behaviour ──────────────────────────────────────

class _Dim:
    def __init__(self, name, score, threshold):
        self.name, self.score, self.threshold = name, score, threshold


class _Result:
    def __init__(self, score=0.0, dimensions=(), open_critical=0, open_high=0):
        self.score = score
        self.dimensions = list(dimensions)
        self.open_critical = open_critical
        self.open_high = open_high


@pytest.mark.parametrize("key", sorted(_DETAIL_REGISTRY))
def test_each_registered_key_produces_an_actionable_reason(key):
    reasons = derive_block_reasons(4, _Result(), {key: ["offender-a", "offender-b"]})
    match = [r for r in reasons if r.kind == key]
    assert len(match) == 1, f"{key} produced {len(match)} reasons, expected exactly 1"
    reason = match[0]
    assert isinstance(reason, BlockReason)
    assert reason.headline.strip()
    assert len(reason.remediation) >= 60, (
        f"{key}'s remediation is too short to be actionable: {reason.remediation!r}"
    )
    assert reason.items == ["offender-a", "offender-b"], (
        "the concrete offenders must reach the agent — a category name alone is "
        "what the old diagnostic already failed to provide"
    )


def test_fabrication_reason_names_the_dimensions_and_forbids_a_bare_rerun():
    """The anti-fabrication block is the whole point: the fix is never 're-run'."""
    reasons = derive_block_reasons(
        1, _Result(), {"tool_score_fabrication": ["security: claimed 95.0, tool 40.0"]}
    )
    fab = [r for r in reasons if r.kind == "tool_score_fabrication"]
    assert len(fab) == 1
    assert "security: claimed 95.0, tool 40.0" in fab[0].items
    assert "Do NOT re-run" in fab[0].remediation


def test_unknown_key_surfaces_instead_of_vanishing():
    reasons = derive_block_reasons(3, _Result(), {"brand_new_cause": ["thing"]})
    assert [r.kind for r in reasons] == ["brand_new_cause"]
    assert "no remediation registered" in reasons[0].headline
    assert "crash-triage" in reasons[0].remediation
    assert reasons[0].items == ["thing"]


def test_failing_dimension_still_produces_its_hint():
    result = _Result(dimensions=[_Dim("linting", 95.0, 100.0)])
    reasons = derive_block_reasons(1, result, None)
    assert [r.kind for r in reasons] == ["dimension_below_threshold"]
    assert "linting" in reasons[0].headline
    assert "ruff check" in reasons[0].remediation
    assert reasons[0].items == ["linting"]


def test_null_scored_dimension_is_not_reported_as_failing():
    """A null score means "not measured" (framework-owned or feature-flag
    excluded), not "scored zero" — the 5467049 / 68209a9 contract."""
    result = _Result(dimensions=[_Dim("mutation_testing", None, 70.0)], open_critical=1)
    reasons = derive_block_reasons(4, result, None)
    assert all(r.kind != "dimension_below_threshold" for r in reasons)


def test_open_critical_with_every_dimension_passing_still_explains_itself():
    """The run-all-by-workflow shape: open_critical=1, no failing dimension.

    The old diagnostic rendered an empty 'Failing Dimensions' section here and
    told the agent to re-run the gate.
    """
    result = _Result(
        score=100.0, dimensions=[_Dim("linting", 100.0, 100.0)], open_critical=1
    )
    reasons = derive_block_reasons(1, result, None)
    kinds = [r.kind for r in reasons]
    assert kinds == ["open_critical_findings"]
    assert "1 unresolved CRITICAL" in reasons[0].headline
    assert "gate1_result.json" in reasons[0].remediation


def test_reasons_are_never_empty():
    """Whatever blocked it, the agent gets at least one actionable statement."""
    reasons = derive_block_reasons(2, _Result(score=61.0), None)
    assert reasons
    assert all(r.remediation.strip() for r in reasons)
