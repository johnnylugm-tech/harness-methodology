"""Round 17 站3 (finding C) — detector abstain-on-ambiguity invariant.

#23 fixed the one guessing detector: DriftDetector._resolve_import_layer used
to return the FIRST dict-iteration layer that matched a bare shared-prefix
import (`pkg` → whichever of pkg.cli / pkg.store / pkg.svc iterated first),
producing false CRITICAL architecture-violation findings. The fix: collect ALL
matches into a set and return a layer only when exactly one matches, else None
(abstain). #24 was the same family from the other side — a detector finding
with no reachable green state (scripts/ flagged "unregistered" with no way to
ever register it).

3a audit — every `break` / `next(iter)` site in detection/ + core/quality_gate/
(9 sites: __init__.py fail-fast ×2, spec_tracking single-candidate,
cov_utils/spec_coverage existence probes, sab_amender prefix-strip,
artifact_consistency/phase_truth table-and-existence breaks, cross_artifact
violation-existence, red_assertion_check len!=1-guarded next(iter)) — found NO
other guessing wound. They are fail-fast, existence probes, prefix strips, and
table-boundary breaks; none pick one of several classifications without
confirming uniqueness. So finding C had a SINGLE wound, already fixed and
behaviourally tested by #23. This station is立則-only: pin the STRUCTURE of the
fix so a future refactor cannot quietly revert it to first-hit (R6/R7
exception-swallow-ratchet AST-scan shape).

(Out of scope, flagged separately: red_assertion_check.py:647 has a live
pyright type error — a type-narrowing issue, not a guessing wound.)
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from detection.drift_detector import DriftDetector


def test_resolve_import_layer_abstains_on_ambiguous_shared_prefix(tmp_path):
    """Behaviour lock (#23): a bare top-level package shared by more than one
    layer must resolve to None, never the first-iterating layer; a
    fully-qualified submodule must still resolve uniquely."""
    d = DriftDetector(str(tmp_path))
    layer_map = {
        "cli": {"pkg.cli"},
        "store": {"pkg.store"},
        "svc": {"pkg.svc"},
    }
    assert d._resolve_import_layer("pkg", layer_map) is None
    assert d._resolve_import_layer("pkg.store", layer_map) == "store"


def test_resolve_import_layer_structurally_gates_on_uniqueness():
    """Structure lock: the fix must stay 'collect all matches, return only when
    exactly one layer matches'. A refactor that reverts to returning a layer
    name from INSIDE the match loop (the #23 first-hit shape) is a silent
    regression the behaviour test above could be edited away with — catch it at
    the source level too."""
    src = textwrap.dedent(inspect.getsource(DriftDetector._resolve_import_layer))
    fn = ast.parse(src).body[0]
    assert isinstance(fn, ast.FunctionDef)

    # (1) No `return <bare name>` from within a for-loop — that is the first-hit
    #     guessing shape (`for layer...: if match: return layer`).
    for node in ast.walk(fn):
        if isinstance(node, ast.For):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Return) and isinstance(inner.value, ast.Name):
                    raise AssertionError(
                        "_resolve_import_layer returns a bare layer name from "
                        "inside a loop — the #23 first-hit guessing shape. "
                        "Collect matches and gate the return on uniqueness "
                        "(len(...) == 1) instead of returning the first hit.")

    # (2) The return must be gated on a uniqueness check (len(...) == 1).
    has_uniqueness_gate = any(
        isinstance(n, ast.Compare)
        and any(isinstance(o, ast.Eq) for o in n.ops)
        and isinstance(n.left, ast.Call)
        and isinstance(n.left.func, ast.Name)
        and n.left.func.id == "len"
        for n in ast.walk(fn)
    )
    assert has_uniqueness_gate, (
        "_resolve_import_layer lost its `len(...) == 1` abstain gate — without "
        "it, an ambiguous import silently resolves to some layer again (#23).")
