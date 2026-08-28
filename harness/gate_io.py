"""The one writer every gate-result mutation goes through.

Round 81 站2. Moved out of harness/harness_bridge.py verbatim; the body here is
byte-identical to the one that was there, which
tests/test_god_file_split_safety.py asserts by AST source segment.

WHY IT LEFT ON ITS OWN, AHEAD OF ITS ONLY REASON FOR LEAVING

Round 80 recorded `_crg_enrich_gate_findings` as not-moving, with this reason:

    它的閉包會拉進 `_atomic_write_gate_result`(8 個呼叫點的共用寫入器)。搬那個
    是重構不是搬移,會造成 `gate_crg` ↔ `harness_bridge` 循環

    re-open: 共用寫入器先被移到中立模組

The obstacle was real and the estimate of it was not. This function's own
closure is `json` and a guarded `core.atomic_io.atomic_write_json` — it is a
leaf, and moving it is twelve lines, not a refactor. harness_bridge keeps its
own `atomic_write_json` import because `generate_quality_manifest` uses it
directly, and re-exports the name below so all eight call sites there, and
every test, keep the spelling they had.
"""

from __future__ import annotations

import json
from pathlib import Path

try:
    from core.atomic_io import atomic_write_json  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover  (graceful degrade)
    atomic_write_json = None  # type: ignore[assignment]


def _atomic_write_gate_result(path: Path, data: dict) -> None:
    """Atomic JSON write for gate_result.json (and any other
    pipeline-critical JSON state). Falls back to direct write if
    core.atomic_io is unavailable.
    """
    if atomic_write_json is not None:
        atomic_write_json(path, data)
    else:  # pragma: no cover
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
