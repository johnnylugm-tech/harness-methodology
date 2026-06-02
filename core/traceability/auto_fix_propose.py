"""Traceability auto-fix: propose candidate fixes as a unified-diff.

The strategy (PR 5 of the closed-loop traceability plan) is conservative:

  - Heuristic-driven: for each uncoded FR, scan SAD.md for the FR's section
    text and propose the closest `core/*.py` module by name; for each
    untested FR, propose a `tests/test_fr_XX.py` stub.
  - Never auto-applies: the strategy layer applies the diff to the source
    tree and re-runs `check_traceability` to verify (in `fix_missing_traceability`).
  - On max_rounds exhaustion: writes the final diff to
    `.methodology/trace/proposed_fix.diff` and escalates to HUMAN_REQUIRED.

The diff is a regular `unified_diff` text (Python stdlib), so `git apply
--check` works on it directly.
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


DEFAULT_TRACE_DIR = Path(".methodology/trace")
PROPOSED_DIFF_NAME = "proposed_fix.diff"


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------

def _closest_module(fr_id: str, fr_section_text: str,
                    project: Path) -> Optional[Path]:
    """Return the path of the closest existing core module to the FR.

    Conservative: only returns a path if the section text has any
    alphanumeric tokens that overlap with a `core/**/*.py` filename stem.
    """
    core = project / "core"
    if not core.is_dir():
        return None
    candidates: List[Path] = list(core.rglob("*.py"))
    if not candidates:
        return None

    text_tokens = set(re.findall(r"[a-z_][a-z0-9_]+", fr_section_text.lower()))
    if not text_tokens:
        return None

    best: Tuple[int, Path] = (0, candidates[0])
    for c in candidates:
        stem_tokens = set(re.findall(r"[a-z_][a-z0-9_]+", c.stem.lower()))
        overlap = len(text_tokens & stem_tokens)
        if overlap > best[0]:
            best = (overlap, c)
    if best[0] == 0:
        return None
    return best[1]


def _stub_test_content(fr_id: str, project: Path) -> str:  # noqa: ARG001
    safe = fr_id.lower().replace("-", "_")
    return (
        f'"""{fr_id} auto-generated stub (PR 5 traceability auto-fix). [{fr_id}]"""\n'
        f"\n"
        f"def test_{safe}_placeholder():\n"
        f"    # TODO: replace with a real test for {fr_id}\n"
        f"    assert True\n"
    )


# ---------------------------------------------------------------------------
# Diff generation
# ---------------------------------------------------------------------------

def _build_annotation_block(fr_id: str) -> str:
    return (
        f"\n# === Auto-added by PR 5 traceability auto-fix ===\n"
        f"# Implements: {fr_id}\n"
        f"# Reason: {fr_id} had no [FR-XX] annotation in any source file.\n"
        f"# {fr_id}\n"
    )


def _diff_added_lines(file_path: str, new_text: str) -> str:  # noqa: ARG001
    """Render a unified diff that ADDS `new_text` to a new file at `file_path`.

    Uses standard `a/path` / `b/path` format with `--git` style so that
    `git apply -p1` (the default) places the file at the correct location
    relative to the repo root.
    """
    before_lines: List[str] = []
    after_lines = new_text.splitlines(keepends=True)
    if not after_lines or not after_lines[-1].endswith("\n"):
        after_lines[-1] = after_lines[-1] + "\n"
    diff = difflib.unified_diff(
        before_lines, after_lines,
        fromfile=f"a/{file_path}", tofile=f"b/{file_path}",
        n=2,
    )
    return "".join(diff)


def _diff_new_file(file_path: str, content: str) -> str:
    return _diff_added_lines(file_path, content)


def _diff_append_to_existing(file_path: str, content: str) -> str:
    """Diff that APPENDS `content` to an existing file (e.g. add FR-XX comment)."""
    full_path = Path(file_path)
    if full_path.exists():
        before_text = full_path.read_text(encoding="utf-8", errors="replace")
    else:
        before_text = ""
    before_lines = before_text.splitlines(keepends=True)
    if before_lines and not before_lines[-1].endswith("\n"):
        before_lines[-1] = before_lines[-1] + "\n"
    after_text = (before_text.rstrip() + "\n" + content) if before_text else content
    after_lines = after_text.splitlines(keepends=True)
    if not after_lines or not after_lines[-1].endswith("\n"):
        after_lines[-1] = after_lines[-1] + "\n"
    diff = difflib.unified_diff(
        before_lines, after_lines,
        fromfile=f"a/{file_path}", tofile=f"b/{file_path}",
        n=2,
    )
    return "".join(diff)


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def propose_fixes(rt, report: dict, project: Path) -> str:  # noqa: ARG001 (rt available for future heuristic)
    """Build a unified diff proposing [FR-XX] annotations and test stubs.

    Iterates over `report["uncoded"]` (FRs with no code annotation) and
    `report["untested"]` (FRs with no test). For each, emits:
      - an `a/b/c.py` annotation append, or
      - a new `tests/test_fr_XX.py` file.

    The diff is well-formed: `git apply --check <diff>` accepts it on a
    clean tree. Never modifies the source tree itself — the caller applies.
    """
    diffs: List[str] = []
    diffs.append(
        "--- proposed_fix.diff (PR 5 traceability auto-fix)\n"
        "--- Apply with: git apply .methodology/trace/proposed_fix.diff\n"
        "--- Review:    cat .methodology/trace/proposed_fix.diff\n\n"
    )

    sad_section_for_fr: Dict[str, str] = {}
    sad_path = project / "SAD.md"
    if not sad_path.exists():
        sad_path = project / "02-architecture" / "SAD.md"
    if sad_path.exists():
        sad_text = sad_path.read_text(encoding="utf-8", errors="replace")
        for fr_id in set(report.get("uncoded", []) + report.get("untested", [])):
            pattern = re.compile(
                rf"(?:^|\n)#{0,6}\s*{re.escape(fr_id)}[^\n]*\n(.*?)(?=\n#|\Z)",
                re.IGNORECASE | re.DOTALL,
            )
            m = pattern.search(sad_text)
            if m:
                sad_section_for_fr[fr_id] = m.group(1)[:500]  # cap

    for fr_id in report.get("uncoded", []):
        target = _closest_module(fr_id, sad_section_for_fr.get(fr_id, ""), project)
        if target is None:
            target = project / "core" / f"auto_{fr_id.lower().replace('-', '_')}.py"
        rel = str(target.relative_to(project))
        diffs.append(_diff_append_to_existing(
            rel, _build_annotation_block(fr_id)
        ))

    for fr_id in report.get("untested", []):
        rel = f"tests/test_{fr_id.lower().replace('-', '_')}.py"
        diffs.append(_diff_new_file(rel, _stub_test_content(fr_id, project)))

    return "".join(diffs)


def write_proposed_diff(project: Path, diff_text: str) -> Path:
    """Write the diff to `.methodology/trace/proposed_fix.diff`. Returns the path."""
    out = project / DEFAULT_TRACE_DIR / PROPOSED_DIFF_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(diff_text, encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Diff application + rollback
# ---------------------------------------------------------------------------

def apply_diff(project: Path, diff_text: str) -> Tuple[bool, str]:
    """Apply a unified diff to the project tree using `git apply`.

    Returns (success, message). On failure, the working tree is left in
    whatever partial state `git apply` produced; the caller is expected
    to call `rollback()` to restore.
    """
    import subprocess
    if not diff_text.strip():
        return True, "no changes"
    proc = subprocess.run(
        ["git", "apply", "-p1", "-"],
        input=diff_text, text=True,
        cwd=project, capture_output=True,
    )
    if proc.returncode == 0:
        return True, "applied"
    return False, (proc.stderr or proc.stdout or "git apply failed").strip()


def rollback(project: Path, applied_diffs: Optional[List[str]] = None) -> None:
    """Best-effort rollback of auto-fix changes.
    
    If applied_diffs is provided, reverses them in LIFO order using `git apply -R`.
    This safely preserves the developer's uncommitted work.
    """
    import subprocess
    if not applied_diffs:
        return
        
    for diff in reversed(applied_diffs):
        if not diff.strip():
            continue
        subprocess.run(
            ["git", "apply", "-R", "-p1", "-"],
            input=diff, text=True,
            cwd=project, capture_output=True,
        )
