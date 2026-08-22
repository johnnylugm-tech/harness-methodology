"""A registry member that cannot reach its consumer (Round 69 站2).

`preview_next_phase_blocking` filters with::

    if res.get("passed") or not res.get("blocking"):
        continue

so a preflight whose result dict never carries a `blocking` key is dropped
unconditionally, however it fails. Round 43 站1 already fixed one instance of
this — `_DELAYED_BLOCKING_PREFLIGHTS` carried the METHOD name `sab_check`
instead of the registry key `sab`, so no SAB finding ever became an
obligation — and the guard it left behind pins the two registries to each
other by NAME. It does not ask whether the named method can produce the key
the consumer reads.

Two of the ten members cannot. AST scan of `core/phase_hooks.py`:

    artifact_consistency      lines 1208, 1227
    config_liveness           line  1440
    drift_detection           line   647
    fr_spec_consistency       line  1018
    property_spec             lines 1126, 1147
    reliability_lint          lines 1272, 1287, 1324
    sab                       lines  674,  683,  746
    traceability              lines  768,  852
    previous_phase_artifacts  NONE
    bvs_phase_order           NONE

`previous_phase_artifacts` is an unconditional blocker (a broken ASPICE
artifact chain blocks at any phase ≥ 2) that simply never said so, and its
finding — "phase N's deliverable is missing" — is exactly a carry-over
obligation. It gets the key.

`bvs_phase_order` is structurally un-previewable, which is why it is removed
from the set rather than given one: the preview builds a sibling at
`phase=next_phase` while `state.json` still records N, so BVS's phase
prerequisite is unmet *by construction* on every preview. Giving it a
`blocking` key would mean no preview is ever clean.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.phase_hooks import PREFLIGHT_CHECKS, _DELAYED_BLOCKING_PREFLIGHTS

_SRC = Path("core/phase_hooks.py").read_text(encoding="utf-8")
_TREE = ast.parse(_SRC)
_FUNCS = {n.name: n for n in ast.walk(_TREE) if isinstance(n, ast.FunctionDef)}
_METHOD_FOR = dict(PREFLIGHT_CHECKS)


def _writes_blocking(fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            if any(isinstance(k, ast.Constant) and k.value == "blocking"
                   for k in node.keys):
                return True
        if (isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value == "blocking"):
            return True
    return False


@pytest.mark.parametrize("check_id", sorted(_DELAYED_BLOCKING_PREFLIGHTS))
def test_every_delayed_blocking_member_can_write_the_key_its_consumer_reads(
    check_id: str,
) -> None:
    method = _METHOD_FOR[check_id]
    assert _writes_blocking(_FUNCS[method]), (
        f"{check_id} → {method}() never writes a `blocking` key, so "
        f"preview_next_phase_blocking drops it however it fails"
    )


def test_bvs_phase_order_is_not_previewable() -> None:
    """Removed deliberately; a future round putting it back has to say why."""
    assert "bvs_phase_order" not in _DELAYED_BLOCKING_PREFLIGHTS


def test_the_preview_sibling_inherits_the_projects_drift_threshold(
    tmp_path,
) -> None:
    """`cmd_preview_next_phase` reads `drift_threshold` out of the project's
    own config and hands it to the outer PhaseHooks; the sibling built inside
    `preview_next_phase_blocking` was constructed without it, so a project
    that set 70 had its preview measured at the default 85 and could be told
    to fix a drift its own entry preflight would not have blocked on."""
    import core.phase_hooks as ph

    seen: "list[float | None]" = []
    real = ph.PhaseHooks

    class Recording(real):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            seen.append(kwargs.get("drift_threshold"))
            super().__init__(*args, **kwargs)

        def _do_preflight_all(self):
            return {"all_passed": True, "details": {}}

    hooks = real(str(tmp_path), phase=3, enable_kill_switch=False,
                 drift_threshold=70.0)
    ph.PhaseHooks = Recording
    try:
        hooks.preview_next_phase_blocking(4)
    finally:
        ph.PhaseHooks = real
    assert seen == [70.0], f"sibling was built with {seen}, not the project's 70.0"
