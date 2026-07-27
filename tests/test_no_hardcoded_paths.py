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


# ── Round 20 站2: the other half of ProjectLayout ────────────────────────────
#
# The guard above covers phase directories ("03-development" & friends). The
# test/src directories were never covered, and that is exactly where the same
# class kept recurring — three times, each found by a real run rather than by a
# test:
#
#   Round 22 (4aa6ff2)  advance-phase's pytest had no explicit path, so cwd
#                       discovery also collected the vendored harness/tests/.
#   Round 25 (7af95ba)  build_traceability's NFR scan read `project / "tests"`,
#                       empty for 03-development/tests/ layouts, so every NFR
#                       rendered PENDING. Its own commit message notes the fix
#                       "was already proven correct at spec_tracking_checker.py"
#                       — a sibling that had been fixed and not swept.
#   Round 20 站2        core/auto_fix/strategies.fix_low_coverage did the same
#                       and then mkdir'd the wrong directory and wrote stubs
#                       into it, reporting the coverage deficit fixed.
#
# What counts as an offence is a path built from a project ROOT, bypassing
# ProjectLayout. Building from a layout accessor
# (`ProjectLayout(p).phase3_development_dir / "tests"`) is how the SSOT itself
# composes paths and is not flagged.
# Detected on the AST, not by line regex. The first version of this guard
# scanned text and flagged its own docstring — prose ABOUT the pattern read
# identically to the pattern. Same lesson as the Round 11 站5 conventions lint:
# a rule about code shape has to look at code shape.
_ROOT_NAMES = frozenset({"project", "project_root", "root", "cwd", "repo_root"})
_LEAF_DIRS = frozenset({"tests", "test", "src"})


def _root_relative_test_src_offences(source: str) -> "list[tuple[int, str]]":
    """(lineno, rendering) for every `<root> / "tests"`-shaped Path division.

    Matches a bare name (`project_root / "tests"`) or an attribute on self
    (`self.root / "src"`). Deliberately does NOT match a layout accessor —
    `ProjectLayout(p).phase3_development_dir / "tests"` is how the SSOT itself
    composes paths, and its left operand is neither a plain root name nor
    self.root.
    """
    import ast as _ast
    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return []
    found: list[tuple[int, str]] = []
    for node in _ast.walk(tree):
        if not (isinstance(node, _ast.BinOp) and isinstance(node.op, _ast.Div)):
            continue
        right = node.right
        if not (isinstance(right, _ast.Constant) and right.value in _LEAF_DIRS):
            continue
        left = node.left
        if isinstance(left, _ast.Name) and left.id in _ROOT_NAMES:
            found.append((node.lineno, f"{left.id} / {right.value!r}"))
        elif (
            isinstance(left, _ast.Attribute)
            and left.attr in _ROOT_NAMES
            and isinstance(left.value, _ast.Name)
            and left.value.id == "self"
        ):
            found.append((node.lineno, f"self.{left.attr} / {right.value!r}"))
    return found

# Files allowed to build these paths from a root, each with the reason. Anything
# not listed here must go through ProjectLayout. Keep this list short: every
# entry is a place the next recurrence can hide.
_ALLOWED_ROOT_TEST_SRC: dict[str, str] = {
    "core/utils/project_layout.py":
        "the SSOT itself — these literals are the definition every other "
        "module resolves through",
    "core/quality_gate/spec_coverage.py":
        "_get_test_directories deliberately collects BOTH the root and the "
        "canonical location and returns a list; it is a union, not a choice",
    "scripts/build_traceability.py":
        "FR-scan branch selection probes both layouts explicitly and warns on "
        "fallback (Round 25 fixed the NFR scan, which did NOT; the remaining "
        "references are the intentional predicates)",
    "scripts/generate_fr_mapping.py":
        "builds a candidate list of every plausible location, both layouts "
        "included, and filters by existence",
    "enforcement/framework_enforcer.py":
        "if/elif over both layouts — the same explicit two-branch probe",
    "cli/gate_cmds.py":
        "one last-resort default when _get_test_directories returns empty, "
        "immediately adjacent to that call",
}


def test_no_root_relative_test_or_src_dirs_outside_layout():
    offenders = []
    for rel, path in _framework_sources():
        if rel in _ALLOWED_ROOT_TEST_SRC:
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        for lineno, rendering in _root_relative_test_src_offences(src):
            offenders.append(f"{rel}:{lineno}: {rendering}")
    assert not offenders, (
        "test/src directory built from a project root — use "
        "ProjectLayout(...).active_test_dir / .active_src_dir "
        "(core/utils/project_layout.py). A project whose tests live under "
        "03-development/tests/ silently gets an empty or wrong directory, which "
        "has now shipped three times (Rounds 22, 25, and 20 站2):\n  "
        + "\n  ".join(offenders)
    )


def test_every_root_test_src_exemption_is_real_and_justified():
    """A stale exemption is worse than none: it keeps a file permanently outside
    the guard after the reason has gone away."""
    for rel, reason in _ALLOWED_ROOT_TEST_SRC.items():
        assert (REPO / rel).is_file(), f"exempted file no longer exists: {rel}"
        assert reason.strip(), f"{rel} exempted with no reason"
        assert _root_relative_test_src_offences(
            (REPO / rel).read_text(encoding="utf-8", errors="replace")
        ), (
            f"{rel} no longer builds a root-relative test/src path — remove it "
            f"from _ALLOWED_ROOT_TEST_SRC so the guard covers it again"
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
    # Round 20 站4: generated workflow JS is read by every project's
    # orchestrator agent, which makes it a shipped surface too — it was simply
    # never listed. Round 23 (e2b98b6) landed a comment naming a downstream
    # project and one of its workflow run ids into five of these files; the
    # generator's own text had since been de-identified, but the artifacts were
    # not regenerated, so the leak sat in the committed output where no guard
    # looked. Same shape as this file's other gap (Round 20 站2): the rule
    # existed, its scope did not reach where the recurrence happened.
    ".claude/workflows",
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
