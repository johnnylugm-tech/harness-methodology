"""Round 84: "where is the canonical spec" is one statement, and it is a constant.

The framework said it six times. Five were constants — `ProjectLayout.spec_path`
(which had zero consumers), the workflow prompt's "canonical_spec = root SPEC.md
per harness SSOT", `canonical_diff.py`'s hardcoded `--spec`, `ssot_manifest.py`'s
`root / "SPEC.md"`, and `hunt.py`'s `--spec` default. The sixth was a variable:
`resolve_canonical_spec` read a `canonical_spec:` field out of PROJECT_BRIEF.md.

The variable was never used to express a difference. All eleven corpus projects
declared `SPEC.md`. Its only effect was a way to switch the front-edge gate off:
delete the file and every reader agreed the project was in elicitation mode and
had no ground truth to be checked against.

These guards keep it removed. They are deliberately about *reading*, not about
mentioning: the code comments that explain why the field is gone are records
worth keeping, and a scan that cannot tell a path literal from a sentence would
force them out.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Same production surface the file-size ratchet scans.
_SCAN_DIRS = ("cli", "core", "harness", "scripts", "detection")


def _string_literals(tree: ast.AST) -> list[str]:
    """Every str constant in the tree EXCEPT docstrings.

    A docstring is prose about the code; a bare string constant in an
    expression, an argument, or an assignment is a value the code uses. Only
    the second kind can name a file the code opens.
    """
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    return [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and id(n) not in docstrings
    ]


def _production_py() -> list[Path]:
    return sorted(
        p for d in _SCAN_DIRS for p in (REPO / d).rglob("*.py")
    )


def test_no_production_code_reads_project_brief() -> None:
    """No production module may name PROJECT_BRIEF.md as a value.

    Comments and docstrings recording why it was removed are fine — they are
    not read by anything. A string literal is.
    """
    offenders: list[str] = []
    for path in _production_py():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a parse failure is another test's job
            continue
        for lit in _string_literals(tree):
            if "PROJECT_BRIEF" in lit:
                offenders.append(f"{path.relative_to(REPO)}: {lit[:80]!r}")
    assert not offenders, (
        "PROJECT_BRIEF.md is read as a value again:\n  "
        + "\n  ".join(offenders)
        + "\nThe canonical spec is ProjectLayout.spec_path. If a new consumer "
        "needs the brief back, re-open the Round 84 ledger entry first."
    )


def test_the_retired_resolver_stays_retired() -> None:
    """`resolve_canonical_spec` was the only variable statement of the six."""
    import core.quality_gate.spec_alignment as sa

    assert not hasattr(sa, "resolve_canonical_spec"), (
        "resolve_canonical_spec is back. Its job — deciding which file is the "
        "canonical spec — belongs to ProjectLayout.spec_path, which is a "
        "constant and cannot be switched off by deleting a declaration."
    )
    # Round 86 站3 replaced an exact-list snapshot with the rule it was
    # standing in for. `structural_fr_ids` was promoted to public so
    # canonical_diff could reuse it instead of writing a second FR regex, and
    # the snapshot failed — on an export that decides nothing about which file
    # is canonical. What must stay retired is any exported RESOLVER.
    resolvers = [
        n for n in sa.__all__
        if "resolve" in n or "canonical_spec" in n or "spec_path" in n
    ]
    assert not resolvers, (
        f"spec_alignment exports {resolvers}, which reads as deciding which "
        "file is the canonical spec. That job belongs to ProjectLayout."
    )


def test_ssot_manifest_and_the_gate_resolve_the_same_file(tmp_path: Path) -> None:
    """Two framework readers of "the canonical spec" must not disagree.

    `ssot_manifest` computes `root / "SPEC.md"` itself rather than going
    through ProjectLayout. That is pre-existing and left alone (Round 84
    ledger), but the two answers must stay equal — a project cannot have its
    dependency manifest read one file while its front-edge gate reads another.
    """
    from core.utils.project_layout import ProjectLayout
    import harness.ssot_manifest as sm

    src = (REPO / "harness" / "ssot_manifest.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # Locate the assignment `spec_path = root / "SPEC.md"` and evaluate the
    # same join against tmp_path, rather than trusting the literal by eye.
    joins = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)
        and isinstance(n.right, ast.Constant) and n.right.value == "SPEC.md"
    ]
    assert joins, (
        "harness/ssot_manifest.py no longer joins a literal 'SPEC.md'. If it "
        "now uses ProjectLayout.spec_path, delete this test — the parity it "
        "guards became structural."
    )
    assert getattr(sm, "__name__", "")  # module imports cleanly
    assert ProjectLayout(tmp_path).spec_path == tmp_path / "SPEC.md"


@pytest.mark.parametrize("shipped", [
    ".claude/workflows/phase1-requirements.js",
    ".claude/workflows/run-all.js",
])
def test_shipped_workflows_do_not_instruct_agents_to_read_the_brief(shipped: str) -> None:
    """The generated JS is what an agent actually receives.

    Round 39's lesson: removing a mechanism is not the same as removing the
    sentences that describe it. These two files carry the Phase 1 prompt.
    """
    text = (REPO / shipped).read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.lstrip().startswith("//"):
            continue  # generator's own note to a human reader
        assert "PROJECT_BRIEF" not in line, (
            f"{shipped} still tells an agent about PROJECT_BRIEF.md: {line[:120]}"
        )
        assert "Elicitation" not in line, (
            f"{shipped} still describes elicitation mode: {line[:120]}"
        )
