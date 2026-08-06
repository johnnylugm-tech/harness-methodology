"""The obligation preview must name checks that exist, and report what blocks.

Round 43 站0. `advance-phase` computes the P(N+1) entry obligations by running
`PhaseHooks.preview_next_phase_blocking`, which filters
`_do_preflight_all`'s results through `_DELAYED_BLOCKING_PREFLIGHTS`. Two
independent conditions have to hold for a finding to become an obligation:

    if check_id not in _DELAYED_BLOCKING_PREFLIGHTS: continue
    if res.get("passed") or not res.get("blocking"): continue

Measured on HEAD, SAB findings fail BOTH:

  * `_DELAYED_BLOCKING_PREFLIGHTS` carries `"sab_check"`, which is the METHOD
    name; the result key `_do_preflight_all` produces is `"sab"` (the first
    element of the `PREFLIGHT_CHECKS` pair). The set difference against the
    registry's key set is exactly `{'sab_check'}` — so the filter drops every
    SAB finding, and `_obligations_from_preflight`'s
    `elif check_id == "sab_check"` branch has never been reachable.
  * `preflight_sab_check` returns no `blocking` key at all, so even under the
    right name `not res.get("blocking")` is True and the finding is dropped a
    second time.

This is the registry-vs-consumer disagreement Round 27 站4 caught three times
over, in a fourth place. The completeness test that already lives in
`tests/test_preflight_registry.py` pins `PREFLIGHT_CHECKS` against the methods
on `PhaseHooks`; nothing pinned the *other* registry that reads its keys.
"""

from __future__ import annotations

import json

from core.phase_hooks import (
    _DELAYED_BLOCKING_PREFLIGHTS,
    PREFLIGHT_CHECKS,
    PhaseHooks,
)


def _registry_keys() -> set:
    return {key for key, _ in PREFLIGHT_CHECKS}


def test_every_delayed_blocking_name_is_a_real_check_id():
    """A name in the set that no check produces silences that check forever."""
    unknown = _DELAYED_BLOCKING_PREFLIGHTS - _registry_keys()
    assert not unknown, (
        f"_DELAYED_BLOCKING_PREFLIGHTS names {sorted(unknown)}, which "
        f"_do_preflight_all never produces as a result key. Findings from "
        f"those checks can never become obligations. Registry keys: "
        f"{sorted(_registry_keys())}"
    )


def test_a_sab_violation_becomes_an_obligation(tmp_path, monkeypatch):
    """A blocking SAB finding must reach the obligation list, one row per violation."""
    canned = {
        "all_passed": False,
        "details": {
            key: {"passed": True, "skipped": True}
            for key, _ in PREFLIGHT_CHECKS
        },
    }
    canned["details"]["sab"] = {
        "passed": False,
        "blocking": True,
        "violations": [
            "Layer domain: 2 modules missing from codebase",
            "Layer api: deps ['ghost'] reference unknown layers",
        ],
        "layers": 3,
    }
    monkeypatch.setattr(
        PhaseHooks, "_do_preflight_all", lambda _self: canned
    )

    hooks = PhaseHooks(str(tmp_path), phase=3, enable_kill_switch=False)
    obligations = hooks.preview_next_phase_blocking(4)

    sab_rows = [o for o in obligations if o.check_id == "sab"]
    assert len(sab_rows) == 2, (
        f"expected one obligation per SAB violation; got "
        f"{[(o.check_id, o.message) for o in obligations]}"
    )
    assert any("modules missing from codebase" in o.message for o in sab_rows)
    assert all(o.target_phase == 4 for o in sab_rows)


def test_sab_check_says_whether_its_finding_blocks(tmp_path):
    """`preflight_sab_check` must report `blocking`, or the preview drops it.

    Every other member of `_DELAYED_BLOCKING_PREFLIGHTS` returns the key; the
    preview's second filter (`not res.get("blocking")`) reads it. A check that
    fails without saying so is invisible to the caller that has to decide
    whether the next phase can be entered.
    """
    (tmp_path / ".methodology").mkdir(parents=True)
    (tmp_path / ".methodology" / "SAB.json").write_text(
        json.dumps({
            "layers": [{"name": "domain", "modules": ["src/does_not_exist.py"]}],
            "dependencies": {"domain": []},
        }),
        encoding="utf-8",
    )

    hooks = PhaseHooks(str(tmp_path), phase=4, enable_kill_switch=False)
    result = hooks.preflight_sab_check()

    assert result["passed"] is False, (
        "fixture is meant to violate the SAB (module absent at P4+)"
    )
    assert "blocking" in result, (
        "preflight_sab_check reported a failure without saying whether it "
        "blocks — preview_next_phase_blocking reads `blocking` and drops the "
        "finding when the key is absent"
    )
    assert result["blocking"] is True
