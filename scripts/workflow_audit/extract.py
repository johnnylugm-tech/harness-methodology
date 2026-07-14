"""Plan <-> workflow JS structural extraction for the alignment audit.

Machine-decidable comparison points (NOT a full-fidelity diff — prose-only
steps with no CLI invocation are not decidable this way; the alignment test
buckets those separately for human review):

  - MARKER steps: `- **[NAME]**` bullets in a generated phase plan. Each
    marker's "span" is the text from its bullet up to (not including) the
    next marker bullet or the next Markdown heading.
  - CLI invocations: a harness_cli.py subcommand (read live from
    harness_cli.build_parser() — never hardcoded, so a newly-added
    subcommand is automatically in scope) counts as "invoked" in a span of
    text only when it is a genuine command token, not an English-word
    coincidence. Several registered subcommand names ARE common English
    words (status, effort, dispatch, doctor, manifest) and appear
    constantly as prose — "dispatch as separate subagent", "current
    status", "{status, files, ...}" JSON field names. A bare word-boundary
    substring scan false-positives on all of these. The precision fix:
    require EITHER (a) the word is immediately preceded by the literal
    `harness_cli.py` (the canonical invocation prefix), OR (b) the word is
    immediately followed by whitespace + `--` (an argparse flag — the
    shape of every real invocation in this codebase, matching both the
    full `python3 harness_cli.py <sub> --flag` form and the plans' bare
    inline `` `run-fr-step --phase 5 ...` `` form).
  - JS `phase('...')` titles and `agent(..., {label: '...'})` label values.

Plan-side text is generated FRESH from the current HEAD plangen generator
(scripts.generate_full_plan), never read from a committed snapshot — a
stale external copy as a test baseline is exactly how
test_workflow_artifacts_commit_pattern.py went silently dead (its REPO
constant pointed at a path that stopped existing, and skipif made that
invisible instead of failing).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

MARKER_RE = re.compile(r"^[ \t]*-[ \t]+\*\*\[([^\]]+)\]\*\*", re.MULTILINE)
HEADING_RE = re.compile(r"^#{1,4}[ \t]+\S", re.MULTILINE)
JS_PHASE_RE = re.compile(r"\bphase\(\s*['\"]([^'\"]*)['\"]\s*\)")
JS_LABEL_RE = re.compile(r"label:\s*['\"]([^'\"]*)['\"]")

_HARNESS_CLI_PREFIX = "harness_cli.py"


def known_subcommands() -> set[str]:
    """Every registered harness_cli.py subcommand, read live from the CLI's
    own argparse parser — the SSOT, not a hand-maintained list that can
    drift the moment a subcommand is added or renamed."""
    import harness_cli

    parser = harness_cli.build_parser()
    subparsers = parser._subparsers  # noqa: SLF001 — argparse has no public introspection API for this
    if subparsers is None:
        return set()
    for action in subparsers._group_actions:  # noqa: SLF001
        choices = getattr(action, "choices", None)
        if choices:
            return set(choices.keys())
    return set()


def _is_genuine_invocation(text: str, start: int, end: int) -> bool:
    """True when the [start:end) token is a real CLI invocation, not an
    English-word coincidence. See module docstring for the precision
    rationale (status/effort/dispatch/doctor/manifest are all real
    subcommand names AND common English words)."""
    preceding = text[max(0, start - len(_HARNESS_CLI_PREFIX) - 5):start]
    if preceding.rstrip().endswith(_HARNESS_CLI_PREFIX):
        return True
    following = text[end:end + 6]
    return bool(re.match(r"\s+--", following))


def _find_invocations(text: str, subcommands: set[str]) -> set[str]:
    found: set[str] = set()
    for sc in subcommands:
        pat = re.compile(r"\b" + re.escape(sc) + r"\b")
        for m in pat.finditer(text):
            if _is_genuine_invocation(text, m.start(), m.end()):
                found.add(sc)
                break
    return found


def _span_boundaries(text: str) -> list[int]:
    starts = {m.start() for m in MARKER_RE.finditer(text)}
    heading_starts = {m.start() for m in HEADING_RE.finditer(text)}
    return sorted(starts | heading_starts | {len(text)})


def extract_plan_markers(text: str, subcommands: set[str]) -> dict[str, set[str]]:
    """marker name -> set of genuinely-invoked subcommands within its span.

    A marker name repeated across the file (e.g. inside a per-FR dynamic
    template that's conceptually rendered once per FR but appears once in
    the static template text) accumulates commands from every occurrence.
    """
    bounds = _span_boundaries(text)
    result: dict[str, set[str]] = {}
    for m in MARKER_RE.finditer(text):
        name = m.group(1).strip()
        start = m.start()
        end = next((b for b in bounds if b > start), len(text))
        found = _find_invocations(text[start:end], subcommands)
        result.setdefault(name, set()).update(found)
    return result


def extract_js_subcommands(js_text: str, subcommands: set[str]) -> set[str]:
    """Whole-file subcommand set — the plan-alignment check is "this command
    appears SOMEWHERE in the JS", not spatially matched to an analogous
    phase() region (see plan Context: workflow phase() grouping doesn't
    necessarily mirror the plan's per-marker granularity 1:1)."""
    return _find_invocations(js_text, subcommands)


def extract_js_phases(js_text: str) -> list[str]:
    return [m.group(1) for m in JS_PHASE_RE.finditer(js_text)]


def extract_js_agent_labels(js_text: str) -> list[str]:
    return [m.group(1) for m in JS_LABEL_RE.finditer(js_text)]


def make_fixture_project(root: Path) -> Path:
    """Minimal project tree the plan generator needs (SRS.md + a manifest
    with >=1 FR so per-FR template sections render instead of being empty)."""
    from core.utils.project_layout import ProjectLayout

    proj = root / "audit-fixture-project"
    layout = ProjectLayout(proj)
    layout.methodology_dir.mkdir(parents=True, exist_ok=True)
    layout.quality_manifest_path.write_text(
        json.dumps({"fr_ids": ["FR-01", "FR-02"], "gate_results": {}}),
        encoding="utf-8",
    )
    layout.srs_path.parent.mkdir(parents=True, exist_ok=True)
    layout.srs_path.write_text("# SRS\n", encoding="utf-8")
    return proj


def generate_dynamic_plan(phase: int, project_root: Path) -> str:
    """Render phase N's CURRENT plangen output (dynamic mode) — the audit's
    plan-side source of truth."""
    from scripts.generate_full_plan import generate_full_plan

    text: Optional[str] = generate_full_plan(phase, project_root, None, dynamic=True)
    if text is None:
        raise RuntimeError(f"generate_full_plan(phase={phase}, dynamic=True) returned None")
    return text
