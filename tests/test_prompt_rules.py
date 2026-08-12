"""No-fork lint for extracted prompt rules (弱點強化 Station D).

harness/prompts/rules/<id>.md is the single source of truth for prompt-rule
prose (extracted from scripts/generate_full_plan.py inline strings,
byte-identical output proven at extraction). These gates keep it that way:

  * every rules/*.md is referenced by a _rule_block("<id>") call (no orphans)
  * every _rule_block("<id>") call has a backing file (no dangling loads)
  * rule prose does not ALSO appear inline in any .py — a copy pasted back
    into Python would fork the text and the two would drift apart silently
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RULES_DIR = REPO / "harness" / "prompts" / "rules"

_SCAN_DIRS = ("cli", "core", "harness", "scripts", "detection")

# BOTH loaders. `_rule_block` renders into markdown plans, `render_rule_prose`
# into generated workflow JS; a check that knows only the first reports every
# JS-side rule as an orphan and every JS-side citation as unverifiable.
_RULE_CALL = re.compile(
    r'(?:_rule_block|render_rule_prose)\(\s*["\'](R-[A-Z0-9-]+)["\']\s*\)'
)

# The citation as it reaches the agent. Round 48: `@rule R-SRS-FR-BLOCK-001`
# and `@rule R-CANONICAL-SPEC-PATH-001` were both written into phase-1 prompts
# with the prose inline and no rule file behind either. Nothing caught them,
# because the lint only ever looked at loader CALLS — and a citation with no
# loader call is exactly the shape that has no loader call to look at.
_RULE_MARKER = re.compile(r'@rule\s+(R-[A-Z0-9-]+)')


def _py_sources() -> "list[tuple[Path, str]]":
    out = []
    for base in _SCAN_DIRS:
        for f in (REPO / base).rglob("*.py"):
            out.append((f, f.read_text(encoding="utf-8", errors="replace")))
    return out


def _referenced_ids() -> set[str]:
    ids: set[str] = set()
    for _, src in _py_sources():
        ids.update(_RULE_CALL.findall(src))
    return ids


def test_rules_dir_exists_and_nonempty():
    assert RULES_DIR.is_dir()
    assert list(RULES_DIR.glob("*.md")), "rule extraction produced no files?"


def test_every_rule_file_is_loaded_somewhere():
    referenced = _referenced_ids()
    orphans = [
        f.name for f in sorted(RULES_DIR.glob("*.md"))
        if f.stem not in referenced
    ]
    assert not orphans, (
        f"rule files never loaded by _rule_block(): {orphans} — dead prose "
        "drifts; wire it in or delete it"
    )


def test_every_rule_load_has_a_file():
    dangling = [
        rid for rid in sorted(_referenced_ids())
        if not (RULES_DIR / f"{rid}.md").exists()
    ]
    assert not dangling, f"_rule_block() ids with no .md file: {dangling}"


def test_every_rule_citation_has_a_rule_behind_it():
    """A prompt that cites `@rule R-XXX` is telling the agent the rule exists.

    Measured 2026-08-13: two citations named no file. R-SRS-FR-BLOCK-001 came
    in with 3dad941 (its prose hand-written inline, twice, in two generated
    .js files); R-CANONICAL-SPEC-PATH-001 has been there since Round 15 站5.
    Both passed every check in this file, because the checks above are about
    loader CALLS and neither citation had one — the gap was shaped exactly
    like the defect.
    """
    dangling: list[str] = []
    for path, src in _py_sources():
        for rid in sorted(set(_RULE_MARKER.findall(src))):
            if not (RULES_DIR / f"{rid}.md").exists():
                dangling.append(f"{path.relative_to(REPO)} cites {rid}")
    assert not dangling, (
        "prompt cites a rule with no harness/prompts/rules/<id>.md:\n  "
        + "\n  ".join(dangling)
        + "\n\nEither extract the prose into that file and render it with "
          "render_rule_prose()/_rule_block(), or drop the citation. Prose that "
          "interpolates a JS value cannot be a rule at all — rules are shared "
          "with the markdown plan renderer, which has no such value."
    )


def test_rule_prose_not_forked_into_python():
    """The first 40 chars of each rule's prose must not appear in any .py —
    that would be a second copy diverging from the SSOT."""
    forks = []
    for md in sorted(RULES_DIR.glob("*.md")):
        probe = md.read_text(encoding="utf-8").strip()[:40]
        assert len(probe) >= 20, f"{md.name} suspiciously short"
        for f, src in _py_sources():
            if probe in src:
                forks.append(f"{md.name} prose found in {f.relative_to(REPO)}")
    assert not forks, "rule prose forked back into Python:\n  " + "\n  ".join(forks)


def test_loader_strips_exactly_the_trailing_newline():
    """_rule_block output must embed the prose with no trailing newline —
    the byte-identity of rendered plans depends on it."""
    from core.utils.script_loader import load_harness_script

    mod = load_harness_script("generate_full_plan.py")
    for md in sorted(RULES_DIR.glob("*.md")):
        block = mod._rule_block(md.stem)
        assert block.startswith(f"<!-- @rule {md.stem} -->")
        assert block.endswith("<!-- @end-rule -->")
        assert "\n\n<!-- @end-rule -->" not in block
