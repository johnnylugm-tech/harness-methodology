"""Lightweight property-declaration gate (Direction B).

The user-approved "lightweight正解": make property-based testing a first-class,
*opt-in*, decidably-verified practice — WITHOUT a new scored gate dimension, a
scorer, weight rebalancing, or per-FR mutation (all rejected on cost/gameability
grounds; see the design discussion in the plan).

An FR declares universal invariants in a TEST_SPEC `**Properties**` table
(columns: property_id | invariant | applies_to), structurally distinct from the
example `Sub-assertions` table (predicate | applies_to) by the `invariant`
header. Two decidable checks, no LLM, no new scoring:

  1. Self-consistency — declared invariants are fed verbatim to the existing
     red_assertion engine (check_test_spec_consistency): an invariant that is
     false for a case it `applies_to` is a spec contradiction (error), caught
     before any test is written. Universal invariants over free variables are
     not evaluable against cases → needs_review, never a false error (the engine
     "does not guess").

  2. Execution existence — once an FR declares a property, its test must EXECUTE
     it with a property-based tool (hypothesis @given / fast-check). Declaring an
     invariant and never testing it verifies nothing → blocked from P4.

The *semantic strength* of a property test (does it kill mutants?) is backed by
the existing mutation_testing dimension — deliberately not re-scored here.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.quality_gate import Violation
from core.quality_gate.parsers import SpecAssertionParser
from core.quality_gate.red_assertion_check import (
    SubAssertion,
    check_test_spec_consistency,
)
from core.quality_gate.spec_coverage import _get_test_directories
from core.utils.project_layout import ProjectLayout

__all__ = ["check_property_spec", "parse_property_tables"]

_FR_HEADER = re.compile(r"^###\s+((?:N?FR)-\d+)\b")
# property-based test markers (language-agnostic): hypothesis (py), fast-check (js/ts)
_PROP_TOOL = re.compile(
    r"@given\b|from\s+hypothesis|import\s+hypothesis|fast-check|fast_check|\bfc\.(?:assert|property)\b"
)
_TEST_EXTS = ("*.py", "*.js", "*.ts", "*.tsx", "*.jsx")


def _find(header: list[str], sub: str) -> int | None:
    for i, c in enumerate(header):
        if sub in c:
            return i
    return None


def _split_fr_sections(content: str) -> dict[str, str]:
    """Split TEST_SPEC into per-FR bodies (same convention as SpecAssertionParser)."""
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in content.splitlines():
        m = _FR_HEADER.match(line.strip())
        if m:
            if current is not None:
                sections[current] = "\n".join(buf)
            current, buf = m.group(1), []
            continue
        if line.strip().startswith("## ") and current is not None:
            sections[current] = "\n".join(buf)
            current, buf = None, []
            continue
        if current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf)
    return sections


def _parse_invariant_table(body: str) -> list[SubAssertion]:
    """Parse the `**Properties**` table (invariant | applies_to) of one FR body.

    Optional `fulfill_phase` column (v2.14 / Round 14 B): declares the earliest
    phase at which the property must be exercised by an executing test. Missing
    column / empty cell / non-int cell → ``None`` (preserves historical default
    of "any phase that has a test" — the property_spec gate will fall back to
    P4 in preflight_property_spec).
    """
    lines = body.splitlines()
    for idx, line in enumerate(lines):
        s = line.strip()
        if not (s.startswith("|") and s.endswith("|")):
            continue
        low = s.lower()
        if "invariant" not in low or "applies" not in low:
            continue
        header = [c.strip().lower() for c in s.strip("|").split("|")]
        i_id = _find(header, "property")
        i_inv = _find(header, "invariant")
        i_app = _find(header, "applies")
        i_fp = _find(header, "fulfill_phase")
        if i_inv is None or i_app is None:
            return []
        props: list[SubAssertion] = []
        for j in range(idx + 1, len(lines)):
            t = lines[j].strip()
            if not (t.startswith("|") and t.endswith("|")):
                break  # end of table
            cells = [c.strip() for c in t.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells if c):
                continue  # separator row
            if i_inv >= len(cells) or i_app >= len(cells):
                continue
            rid = (cells[i_id].strip("`").strip()
                   if i_id is not None and i_id < len(cells) else "")
            pred = cells[i_inv].strip().strip("`").strip()
            if not pred:
                continue
            applies = [int(n) for n in re.findall(r"\d+", cells[i_app])]
            # fulfill_phase: optional column. Missing column / empty cell /
            # non-int cell → None (back-compat with tables that omit the
            # column entirely; the gate will fall back to P4).
            fulfill_phase: int | None = None
            if i_fp is not None and i_fp < len(cells):
                _fp_text = cells[i_fp].strip().strip("`").strip()
                if _fp_text:
                    try:
                        fulfill_phase = int(_fp_text)
                    except ValueError:
                        fulfill_phase = None
            props.append(SubAssertion(rid, pred, applies, fulfill_phase))
        return props
    return []


def parse_property_tables(content: str) -> dict[str, list[SubAssertion]]:
    """Return {fr_id: [SubAssertion]} for every FR declaring a Properties table."""
    out: dict[str, list[SubAssertion]] = {}
    for fr_id, body in _split_fr_sections(content).items():
        props = _parse_invariant_table(body)
        if props:
            out[fr_id] = props
    return out


def _load_test_sources(project: Path) -> list[str]:
    blobs: list[str] = []
    for tdir in _get_test_directories(project):
        for pattern in _TEST_EXTS:
            for f in tdir.rglob(pattern):
                try:
                    blobs.append(f.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
    return blobs


def _fr_tokens(fr_id: str) -> tuple[str, ...]:
    """Candidate test-name / id tokens for an FR (padded + unpadded)."""
    m = re.search(r"(\d+)", fr_id)
    if not m:
        return (fr_id,)
    n = int(m.group(1))
    return (f"test_fr{n:02d}", f"test_fr{n}", f"FR-{n:02d}", f"FR-{n}")


def _fr_has_property_test(fr_id: str, test_blobs: list[str]) -> bool:
    tokens = _fr_tokens(fr_id)
    for blob in test_blobs:
        if not (_PROP_TOOL.search(blob) and any(tok in blob for tok in tokens)):
            continue

        try:
            import ast
            tree = ast.parse(blob)
            lines = blob.splitlines()
            
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    start_line = node.decorator_list[0].lineno if hasattr(node, "decorator_list") and node.decorator_list else node.lineno
                    end_line = node.end_lineno if hasattr(node, "end_lineno") and node.end_lineno else node.lineno
                    
                    # Expand range by 1 to include immediately preceding comment (e.g. # FR-XX)
                    start_idx = max(0, start_line - 2)
                    end_idx = end_line
                    
                    func_source = "\n".join(lines[start_idx:end_idx])
                    
                    if _PROP_TOOL.search(func_source) and any(tok in func_source for tok in tokens):
                        return True
                        
            # If we parsed AST but found no matching function block, check next file
            continue
            
        except (SyntaxError, ImportError, AttributeError):
            # Fallback for non-python files (JS/TS) or if parsing fails
            return True
            
    return False


def check_property_spec(project: Path, *, require_execution: bool) -> list[Violation]:
    """Return Violations for declared-property self-consistency + execution.

    require_execution: when True (P4+), an FR that declares a property but has no
    hypothesis/fast-check test is an error. Before P4 the test may not exist yet.
    """
    project = Path(project)
    spec_path = ProjectLayout(project).test_spec_path
    if not spec_path.exists():
        return []
    content = spec_path.read_text(encoding="utf-8", errors="replace")
    props_by_fr = parse_property_tables(content)
    if not props_by_fr:
        return []

    cases_by_fr = {fr: cases for fr, (cases, _) in SpecAssertionParser.parse(content).items()}

    violations: list[Violation] = []
    # 1. self-consistency of declared invariants (reuse the red_assertion engine)
    for fr_id, props in props_by_fr.items():
        cases = cases_by_fr.get(fr_id, [])
        for v in check_test_spec_consistency(cases, props):
            violations.append(Violation(
                check_type=v.check_type, rule_id=v.rule_id or fr_id, severity=v.severity,
                message=f"{fr_id} property {v.rule_id or ''}: {v.message}".replace("  ", " "),
                extra=v.extra))

    # 2. execution existence — declaring an invariant obliges a property test
    if require_execution:
        test_blobs = _load_test_sources(project)
        for fr_id in sorted(props_by_fr):
            if not _fr_has_property_test(fr_id, test_blobs):
                violations.append(Violation(
                    check_type="property_not_executed", rule_id=fr_id, severity="error",
                    message=(f"{fr_id} declares a property invariant but no property-based "
                             f"test (hypothesis @given / fast-check) executes it — an "
                             f"unverified invariant proves nothing")))
    return violations
