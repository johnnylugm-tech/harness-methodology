"""Lint tests: path resolution goes through ProjectLayout, and framework code
carries no target-project assumptions.

History: hand-built `project / "0X-…"` paths coexisted with ProjectLayout in
15 files, producing the path-doubling, three-way SRS.md location mismatch,
and src-layout blindness (9feafc0) bug class. And verify_spec_compliance.py
shipped for months hardcoding another project's module names
(text_processor.py / retry_handler.py / prosody_manager.py), false-positive
failing every other project (E2E round 2 HIGH finding).

Round 8 station 3 extends the foreign-token surface after the third
recurrence of the pollution class: the June guard locked exactly the 3
module names that incident named and scanned only .py lines, so 2026-07-12's
TASKQ_* leak into harness_bridge.py's new [SCHEMA CONTRACT] prompt f-string
(76b849c, cleaned within the hour by 7fe0fdd/454c780) sailed through, and
templates/TEST_SPEC.md + derive_test_cases.md still shipped the tts
project's bopomofo examples (June round 2's AC5 cross-project HIGH bug
shape) to every new project. Two scan faces now:

  Face A — shipped/LLM-facing surfaces (templates/, harness/prompts/,
  harness/ssi/prompts/): raw-text scan, every file type, zero allowlist —
  this content is copied into new projects or sent to every project's
  agents, and the fix is always free (use a generic name).

  Face B — framework .py string constants (AST-level, docstrings excluded):
  catches the 76b849c shape (foreign tokens inside prompt f-strings) without
  flagging comments/docstrings that cite past incidents as history — those
  never leave this repo.

Registry maintenance: when a new target project drives an E2E round, add its
identifying tokens here. Plain foreign *domain content* (e.g. bare CJK words)
is out of registry reach — only known identifier tokens and the bopomofo
block are matched.
"""

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Path-arithmetic on phase directories: `... / "0X-name"`. Plain path STRINGS
# inside generated-document templates are display content, not resolution,
# and are deliberately out of scope.
_PHASE_DIR_DIVISION = re.compile(
    r'/\s*"0[1-9]-(?:requirements|architecture|development|testing|'
    r'verification|quality|risk|config|maintenance)"'
)

# Identifiers of past target projects that must never appear in framework
# code as module references.
_FOREIGN_MODULES = re.compile(r"(?:text_processor|retry_handler|prosody_manager)\.py")

_ALLOWED_PATH_FILES = {
    "core/utils/project_layout.py",  # the path SSOT itself
    "core/phase_topology.py",        # phase-dir name registry
}

_SKIP_PREFIXES = ("tests/", ".venv/", ".git/", ".sessi-work/", "node_modules/")


def _framework_sources():
    for path in sorted(REPO.rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith(_SKIP_PREFIXES):
            continue
        yield rel, path


def test_no_phase_dir_path_arithmetic_outside_layout():
    offenders = []
    for rel, path in _framework_sources():
        if rel in _ALLOWED_PATH_FILES:
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if _PHASE_DIR_DIVISION.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Hand-built phase-directory paths found — use ProjectLayout "
        "(core/utils/project_layout.py) instead:\n  " + "\n  ".join(offenders)
    )


def test_no_foreign_project_module_references():
    offenders = []
    for rel, path in _framework_sources():
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if _FOREIGN_MODULES.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Framework code references another project's modules — checks must "
        "derive their targets from the project's own SAD.md/SRS.md:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Foreign-project token registry (Round 8 station 3)
# ---------------------------------------------------------------------------

# Identifying tokens of past E2E target projects. Word-ish boundaries keep
# substrings inside unrelated identifiers from matching.
FOREIGN_PROJECT_TOKENS = re.compile(
    r"\btaskq\b"            # 2026-07 target project (task-queue CLI)
    r"|\bTASKQ_[A-Z_]+"     # its env-var namespace (76b849c/7fe0fdd leak)
    r"|\bbopomofo\b"        # tts target project's phonetic domain (June AC5)
    r"|[ㄅ-ㄯ]"     # bopomofo characters themselves (ㄅ…ㄩ)
    r"|\bprosody_manager\b|\btext_processor\b|\bretry_handler\b"  # tts modules
)

# Face A: content copied into new projects (templates/) or sent to every
# project's LLM agents (prompt directories). Zero allowlist.
_SHIPPED_SURFACE_DIRS = (
    "templates",
    "harness/prompts",
    "harness/ssi/prompts",
)


def _shipped_surface_files():
    for base in _SHIPPED_SURFACE_DIRS:
        root = REPO / base
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                yield path.relative_to(REPO).as_posix(), path


def test_shipped_surfaces_carry_no_foreign_project_tokens():
    offenders = []
    for rel, path in _shipped_surface_files():
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if FOREIGN_PROJECT_TOKENS.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Templates/prompts ship to every project — replace foreign-project "
        "tokens with generic names:\n  " + "\n  ".join(offenders)
    )


# Face B: string constants in framework .py files. Comments never reach the
# AST; docstrings are excluded on purpose — both may cite past incidents as
# history and neither leaves this repo. What DOES leave is string content
# (prompt f-strings, templates, messages), which is exactly where 76b849c's
# TASKQ_* leak lived.

def _string_constants_excluding_docstrings(tree: ast.AST):
    docstring_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_nodes.add(id(body[0].value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_nodes
        ):
            yield node.lineno, node.value


def test_framework_string_constants_carry_no_foreign_project_tokens():
    offenders = []
    for rel, path in _framework_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for lineno, value in _string_constants_excluding_docstrings(tree):
            if FOREIGN_PROJECT_TOKENS.search(value):
                snippet = value.strip().splitlines()[0][:80] if value.strip() else repr(value)
                offenders.append(f"{rel}:{lineno}: {snippet}")
    assert not offenders, (
        "Foreign-project tokens inside framework string constants (the "
        "76b849c prompt-leak shape) — use generic names:\n  "
        + "\n  ".join(offenders)
    )
