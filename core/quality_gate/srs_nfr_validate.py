"""Round 33 站3 — SRS.md's own NFR vocabulary is checked where it is written.

`485c05f` stated the defect exactly: SRS.md's `non_functional_requirements[].type`
is "parsed and legality-checked nowhere upstream of generate_sab.py --validate
in Phase 2". Verified before writing this module:

  * scripts/plangen/artifact_parsers.py reads only `functional_requirements`;
    nothing in the tree touches `non_functional_requirements`.
  * `ALL_NFR_TYPES` has exactly one enforcement site,
    core/quality_gate/sab_parser.py:532, and that is the Phase 2 SAB block.

The fix that shipped was a prose bullet in Phase 1's B-checklist. It helps,
and it leaves the verdict with the party being judged: an agent that does not
run the check writes the same illegal value, gets it approved in Phase 1, and
finds out in Phase 2 — after the value is locked into a peer-reviewed,
verbatim-transcribe deliverable. Measured cost on a real project: five B-review
rounds to the HR-12 hard cap (taskq-full SAD.md, 2026-08-03).

TWO VOCABULARIES, ONE OF THEM WITH TWO ROSTERS
----------------------------------------------
`type:` is a fixed 14-value enum owned by sab_parser. `dimension:` names a
scored gate dimension, and that roster has two sources which disagree:
`traceability` is scored by harness/gate_configs/gate4_p6_full.yaml and has no
`### traceability` section in harness/ssi/prompts/evaluate_dimension.md — the
file Phase 1's own prompt (spec_phase1.py) tells the agent to grep for "the
current roster". An NFR correctly mapped to it would be flagged by Phase 1's
checklist as naming a dimension that does not exist.

`dimension_roster()` is therefore the union, and `dimension_roster_split()`
pins the disagreement so the next divergence is a decision rather than a
surprise. Taking the union is the safe direction: a value legal in either
source is legal, and only a name absent from both is refused.
"""
from __future__ import annotations

import re
from pathlib import Path

__all__ = [
    "dimension_roster",
    "dimension_roster_split",
    "illegal_nfr_vocabulary",
]

_HARNESS_ROOT = Path(__file__).resolve().parent.parent.parent
_DIMENSION_PROMPT = _HARNESS_ROOT / "harness" / "ssi" / "prompts" / "evaluate_dimension.md"
_GATE_CONFIG_DIR = _HARNESS_ROOT / "harness" / "gate_configs"

# A `{placeholder}` still in the field means the template was never filled in.
# That is a different finding from "this value is not in the vocabulary", and
# saying so saves the reader from hunting for a typo in a value nobody chose.
_PLACEHOLDER = re.compile(r"\{[^}]*\}")


def _prompt_roster() -> "set[str]":
    """Dimension names with a `### <name>` section in evaluate_dimension.md.

    This is the roster Phase 1's prompt names, so it is read the way the
    prompt tells the agent to read it: the `### ` headers, taking the first
    token and ignoring the parenthesised tier annotation.
    """
    try:
        text = _DIMENSION_PROMPT.read_text(encoding="utf-8")
    except OSError:
        return set()
    return set(re.findall(r"^###\s+([a-z][a-z0-9_]*)\b", text, re.M))


def _gate_config_roster() -> "set[str]":
    """Dimension names any gate config actually scores."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover — yaml is a hard dependency
        return set()
    names: set[str] = set()
    if not _GATE_CONFIG_DIR.is_dir():
        return names
    for path in sorted(_GATE_CONFIG_DIR.glob("gate*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        for entry in data.get("dimensions", []) or []:
            if isinstance(entry, dict) and entry.get("name"):
                names.add(str(entry["name"]))
    return names


def dimension_roster() -> "set[str]":
    """Every dimension name an NFR may legally point at."""
    return _prompt_roster() | _gate_config_roster()


def dimension_roster_split() -> "set[str]":
    """Dimensions the gate configs score that evaluate_dimension.md omits.

    Currently `{"traceability"}`. Pinned by a test so the set changing is a
    reviewed decision — either the prompt's roster gained the section, or a
    new dimension drifted in the same way this one did.
    """
    return _gate_config_roster() - _prompt_roster()


def illegal_nfr_vocabulary(project: "str | Path") -> "list[str]":
    """Findings for SRS.md's machine-readable NFR block. Empty list = clean.

    Returns [] — never a finding — when the SRS is absent or carries no
    machine-readable block. "Could not read it" is not "it is wrong"
    (Round 31's parse-failure rule); the missing-deliverable case is C1's job,
    and the unreadable-block case now warns from `srs_machine_block`.
    """
    from core.quality_gate.sab_parser import ALL_NFR_TYPES
    from core.utils.project_layout import ProjectLayout
    from scripts.plangen.artifact_parsers import srs_machine_block

    srs = ProjectLayout(Path(project)).srs_path
    try:
        content = srs.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    data = srs_machine_block(content)
    if not isinstance(data, dict):
        return []

    roster = dimension_roster()
    findings: list[str] = []
    for entry in data.get("non_functional_requirements", []) or []:
        if not isinstance(entry, dict):
            continue
        nid = str(entry.get("id") or "NFR-??")

        raw_type = entry.get("type")
        if raw_type is not None:
            value = str(raw_type)
            if _PLACEHOLDER.search(value):
                findings.append(
                    f"{nid}: `type:` is still the template placeholder "
                    f"{value!r} — fill it in with one of: "
                    f"{', '.join(ALL_NFR_TYPES)}"
                )
            elif value not in ALL_NFR_TYPES:
                findings.append(
                    f"{nid}: `type:` {value!r} is not a legal NFR type. Legal "
                    f"types: {', '.join(ALL_NFR_TYPES)}. This is a DIFFERENT, "
                    "stricter vocabulary than `dimension:` — a value that is "
                    "a legal dimension name (e.g. `error_handling`) is still "
                    "illegal here, and generate_sab.py --validate will refuse "
                    "it in Phase 2."
                )

        raw_dim = entry.get("dimension")
        if raw_dim is not None and roster:
            value = str(raw_dim)
            if _PLACEHOLDER.search(value):
                findings.append(
                    f"{nid}: `dimension:` is still the template placeholder "
                    f"{value!r}"
                )
            elif value not in roster:
                findings.append(
                    f"{nid}: `dimension:` {value!r} names no scored dimension. "
                    f"Known: {', '.join(sorted(roster))}"
                )
    return findings
