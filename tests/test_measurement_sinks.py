"""Round 67 站8 — a measurement with nowhere to go stops being possible.

Five of this round's eight defects had one shape: the framework computed the
true thing and nothing read it. `score_source` reached `measurement_scope`
and neither the verdict nor the committed artifact. `contract_coverage_gap`
had one caller and it wrote a log row. The CI verdict for a pinned harness
commit was answerable by a function that already existed. Each was a station;
this is for the next one.

`tests/MEASUREMENT_SINKS.yaml` names every `record_degradation` component in
the tree and where it ends up: `verdict` (something refuses to proceed, and
`where` names it), `report-only` (deliberate, and `why` is required), or
`unreviewed` (nobody has read the site). The last is a real answer — Round 50
站4 kept `unknown` as an owner for the same reason — but its count only
ratchets down, and a NEW producer cannot claim it.

The scan is AST, not grep: a component is either a literal or an f-string,
and an f-string is keyed by its literal prefix. All nine f-string sites in
the tree have one. A producer whose component starts with a substitution
fails here, because a sink nobody can name is a sink nobody can check.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
REGISTRY = Path(__file__).resolve().parent / "MEASUREMENT_SINKS.yaml"

# Producers that are still `unreviewed`. Down only. Reviewing one means
# deciding whether anything acts on it and saying so in the registry — not
# moving this number.
_UNREVIEWED_CEILING = 30

_SKIP_DIRS = {".venv", "tests", ".git", "node_modules", "__pycache__"}


def _load() -> dict:
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))["sinks"]


def _component_key(node: ast.AST) -> "str | None":
    """The registry key for a `component=` argument, or None if unnamable."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        head = node.values[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str) and head.value:
            return head.value + "*"
    return None


def _producers() -> "list[tuple[str | None, str, int]]":
    """(key, file, line) for every record_degradation call in production code."""
    out: list[tuple[str | None, str, int]] = []
    for path in sorted(REPO.rglob("*.py")):
        if _SKIP_DIRS & set(path.relative_to(REPO).parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name != "record_degradation":
                continue
            arg = (node.args[1] if len(node.args) > 1
                   else next((k.value for k in node.keywords
                              if k.arg == "component"), None))
            rel = path.relative_to(REPO).as_posix()
            out.append((_component_key(arg) if arg is not None else None,
                        rel, node.lineno))
    return out


def test_every_producer_names_a_sink():
    """The rule this round exists to make structural."""
    registry = _load()
    unregistered = sorted({
        f"{key} ({rel}:{line})" for key, rel, line in _producers()
        if key is not None and key not in registry
    })
    assert not unregistered, (
        "these write to the degradation ledger and MEASUREMENT_SINKS.yaml does "
        "not say where the finding goes:\n  " + "\n  ".join(unregistered)
        + "\n\nAdd each with sink: verdict (and `where`), report-only (and a "
        "`why` that is not 'not yet'), or — only for a site that already "
        "existed — unreviewed."
    )


def test_every_component_can_be_named():
    """A component whose text starts with a substitution cannot be registered."""
    unnamable = sorted(
        f"{rel}:{line}" for key, rel, line in _producers() if key is None
    )
    assert not unnamable, (
        "these build their `component` with no literal prefix, so no registry "
        "key can refer to them and no reader can tell what they report:\n  "
        + "\n  ".join(unnamable)
        + "\n\nGive the string a literal prefix (`f\"gate:{name}\"`, not "
          "`f\"{name}:gate\"`)."
    )


def test_the_registry_has_no_entries_without_a_producer():
    """The other direction: a key whose producer was deleted is a stale claim."""
    keys = {key for key, _, _ in _producers() if key}
    orphans = sorted(set(_load()) - keys)
    assert not orphans, (
        f"MEASUREMENT_SINKS.yaml registers {orphans} and nothing writes them. "
        f"Remove the entries — a registry that describes producers that no "
        f"longer exist is how Round 39's removed-mechanism survivors happened"
    )


@pytest.mark.parametrize("sink,required", [("verdict", "where"),
                                           ("report-only", "why")])
def test_a_decided_sink_says_what_decided_it(sink, required):
    """`verdict` names its enforcer; `report-only` gives a reason.

    Round 43's finding, as a schema: a detection with no named executor is a
    detection nobody acts on, and "report-only" with no reason is the same
    sentence with better manners.
    """
    missing = sorted(
        key for key, entry in _load().items()
        if entry.get("sink") == sink and not str(entry.get(required, "")).strip()
    )
    assert not missing, (
        f"{missing} are marked {sink} without `{required}`"
    )


def test_unreviewed_only_ratchets_down():
    """Down only, and a new producer may not be added as unreviewed.

    The number is not a budget to spend. Reviewing a site means deciding what
    acts on it and recording that — which lowers this by one.
    """
    unreviewed = sorted(
        key for key, entry in _load().items()
        if entry.get("sink") == "unreviewed"
    )
    assert len(unreviewed) <= _UNREVIEWED_CEILING, (
        f"{len(unreviewed)} unreviewed measurement sinks > ceiling "
        f"{_UNREVIEWED_CEILING}. A new producer must declare where its "
        f"finding goes; `unreviewed` is only for the sites that predate this "
        f"registry.\n  " + "\n  ".join(unreviewed)
    )
    assert len(unreviewed) == _UNREVIEWED_CEILING, (
        f"{len(unreviewed)} unreviewed sinks, ceiling {_UNREVIEWED_CEILING} — "
        f"lower the ceiling in the same commit that reviewed one, or the "
        f"ratchet stops being one (Round 66's rule for the spawn ratchet)"
    )


def test_the_sink_vocabulary_is_closed():
    bad = sorted(
        f"{key}={entry.get('sink')!r}" for key, entry in _load().items()
        if entry.get("sink") not in {"verdict", "report-only", "unreviewed"}
    )
    assert not bad, f"unknown sink value(s): {bad}"
