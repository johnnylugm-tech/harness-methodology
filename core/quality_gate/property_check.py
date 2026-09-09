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

import ast as _ast
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

__all__ = ["check_property_spec", "parse_property_tables", "property_mapping_findings"]

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
        i_test = _find(header, "test_function")
        i_review = _find(header, "review_ref")
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
            test_function = None
            if i_test is not None and i_test < len(cells):
                test_function = cells[i_test].strip().strip("`").strip() or None
            review_ref = None
            if i_review is not None and i_review < len(cells):
                review_ref = cells[i_review].strip().strip("`").strip() or None
            props.append(SubAssertion(
                rid, pred, applies, fulfill_phase, test_function, review_ref
            ))
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


def _named_property_test(test_function: str, test_blobs: list[str]) -> bool:
    """True only when the named test's own body/decorators use a PBT tool."""
    for blob in test_blobs:
        try:
            tree = _ast.parse(blob)
        except SyntaxError:
            # JS/TS: bind the tool marker and function name to the same nearby
            # declaration block instead of accepting an unrelated file token.
            pattern = re.compile(
                rf"(?:test|it)\s*\(\s*['\"]{re.escape(test_function)}['\"]"
                rf"[\s\S]{{0,2000}}?(?:fast-check|fast_check|\bfc\.(?:assert|property)\b)"
            )
            if pattern.search(blob):
                return True
            continue
        lines = blob.splitlines()
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)) \
                    and node.name == test_function:
                start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
                end = getattr(node, "end_lineno", node.lineno)
                return bool(_PROP_TOOL.search("\n".join(lines[start - 1:end])))
    return False


def property_mapping_findings(project: str | Path) -> list[str]:
    """P2 transition findings for missing/duplicate property→test identities."""
    path = ProjectLayout(Path(project)).test_spec_path
    if not path.is_file():
        return []
    content = path.read_text(encoding="utf-8", errors="replace")
    props = parse_property_tables(content)
    cases_by_fr = {fr: cases for fr, (cases, _) in SpecAssertionParser.parse(content).items()}
    findings: list[str] = []
    property_ids: set[str] = set()
    test_names: set[str] = set()
    for fr_id, rows in props.items():
        for row in rows:
            if not row.rule_id:
                findings.append(f"{fr_id} property has no property_id")
            elif row.rule_id in property_ids:
                findings.append(f"property_id {row.rule_id} is declared more than once")
            property_ids.add(row.rule_id)
            if not row.test_function:
                findings.append(f"{fr_id} property {row.rule_id} has no test_function")
            elif row.test_function in test_names:
                findings.append(f"property test_function {row.test_function} is mapped more than once")
            else:
                test_names.add(row.test_function)
            reviews = check_test_spec_consistency(cases_by_fr.get(fr_id, []), [row])
            if any(v.severity != "error" for v in reviews):
                if not row.review_ref:
                    findings.append(
                        f"{fr_id} property {row.rule_id} needs_review but has no review_ref"
                    )
                else:
                    if not _review_disposition_resolves(
                        Path(project), row.review_ref, row.rule_id
                    ):
                        findings.append(
                            f"{fr_id} property {row.rule_id} review_ref has no matching "
                            "accepted/rejected/revised disposition: "
                            f"{row.review_ref}"
                        )
    return findings


def _review_disposition_resolves(project: Path, ref: str, property_id: str) -> bool:
    """Resolve ``path:line`` and prove the line closes this exact property."""
    match = re.fullmatch(r"(.+):(\d+)", ref.strip())
    if not match:
        return False
    rel, number = match.group(1), int(match.group(2))
    if Path(rel).is_absolute() or number < 1:
        return False
    path = project / rel
    if not path.is_file():
        return False
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if number > len(lines):
        return False
    evidence = lines[number - 1].lower()
    return property_id.lower() in evidence and any(
        disposition in evidence for disposition in ("accepted", "rejected", "revised")
    )


def _is_structural_tautology(predicate: str) -> bool:
    """Detect canonical placeholder invariants by AST structure.

    A structural tautology is an identity that is True by definition regardless
    of input values — it expresses no constraint on system behaviour and is used
    as a placeholder to bind a future obligation (documented in TEST_SPEC
    guidance as "degenerate tautology, degrading to needs_review").

    Detected patterns (purely structural, not evaluative):

    * ``VAR == VAR`` — same ``ast.Name`` node on both sides of ``==``
    * ``LITERAL == LITERAL`` — same ``ast.Constant`` value on both sides
    * ``LITERAL != ""`` — a non-empty string literal compared to ``""``

    Returns False for anything else, including predicates that happen to be
    true for a specific input but are not identity statements (e.g. ``x > 0``
    when ``x = 1`` is NOT a tautology — it constrains the input).
    """
    try:
        tree = _ast.parse(predicate.strip(), mode="eval").body
    except SyntaxError:
        return False

    # VAR == VAR  — same Name node on both sides
    if (
        isinstance(tree, _ast.Compare)
        and len(tree.ops) == 1
        and isinstance(tree.ops[0], _ast.Eq)
    ):
        left, right = tree.left, tree.comparators[0]
        if isinstance(left, _ast.Name) and isinstance(right, _ast.Name):
            return left.id == right.id
        if isinstance(left, _ast.Constant) and isinstance(right, _ast.Constant):
            return left.value == right.value

    # LITERAL != ""  — non-empty string literal compared to empty string
    if (
        isinstance(tree, _ast.Compare)
        and len(tree.ops) == 1
        and isinstance(tree.ops[0], _ast.NotEq)
    ):
        left, right = tree.left, tree.comparators[0]
        if (
            isinstance(left, _ast.Constant)
            and isinstance(left.value, str)
            and left.value != ""
            and isinstance(right, _ast.Constant)
            and right.value == ""
        ):
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
    # Also collect which invariants are structural tautologies per FR.
    _tautology_frs: set[str] = set()
    _non_tautology_frs: set[str] = set()
    for fr_id, props in props_by_fr.items():
        cases = cases_by_fr.get(fr_id, [])
        for v in check_test_spec_consistency(cases, props):
            violations.append(Violation(
                check_type=v.check_type, rule_id=v.rule_id or fr_id, severity=v.severity,
                message=f"{fr_id} property {v.rule_id or ''}: {v.message}".replace("  ", " "),
                extra=v.extra))
        # Classify each invariant as tautology or real.  A tautology whose free
        # variables are not all in the case inputs goes through Decider B and is
        # NOT a structural identity — only Decider-A tautologies count.
        _all_tautology = True
        for p in props:
            try:
                _free = {n.id for n in _ast.walk(_ast.parse(p.predicate.strip(), mode="eval"))
                         if isinstance(n, _ast.Name) and isinstance(n.ctx, _ast.Load)}
            except SyntaxError:
                _free = set()
            _case_ids = set(p.applies_to)
            _case_inputs: set[str] = set()
            for cid in _case_ids:
                for case in cases:
                    if case.case_id == cid:
                        _case_inputs |= set(case.inputs)
            # Only classify as tautology if all free variables are case inputs
            # (Decider A — the invariant is evaluable) AND the predicate is a
            # structural identity.  A tautology that references outputs (Decider B)
            # is meaningful — it constrains the output relative to itself.
            if _free <= _case_inputs and _is_structural_tautology(p.predicate):
                violations.append(Violation(
                    check_type="tautology_placeholder", rule_id=fr_id, severity="info",
                    message=(f"{fr_id} property {p.rule_id!r}: predicate "
                             f"{p.predicate!r} is a structural tautology — "
                             f"real invariant and property-based test expected "
                             f"by fulfill_phase {p.fulfill_phase or 4}")))
            else:
                _all_tautology = False
        if _all_tautology and props:
            _tautology_frs.add(fr_id)
        elif props:
            _non_tautology_frs.add(fr_id)

    # 2. execution existence — declaring an invariant obliges a property test
    if require_execution:
        test_blobs = _load_test_sources(project)
        for fr_id in sorted(props_by_fr):
            # FRs whose only declared invariants are structural tautologies
            # have nothing to verify — skip the execution check.  A single
            # real invariant keeps the FR in the execution path.
            if fr_id in _tautology_frs and fr_id not in _non_tautology_frs:
                continue
            named = [p.test_function for p in props_by_fr[fr_id] if p.test_function]
            missing_named = [name for name in named
                             if not _named_property_test(name, test_blobs)]
            executed = (not missing_named and bool(named)) if named else \
                _fr_has_property_test(fr_id, test_blobs)
            if not executed:
                violations.append(Violation(
                    check_type="property_not_executed", rule_id=fr_id, severity="error",
                    message=(f"{fr_id} declares a property invariant but no matching property-based "
                             f"test (hypothesis @given / fast-check) executes it — an "
                             f"unverified invariant proves nothing"
                             + (f"; missing exact test(s): {', '.join(missing_named)}"
                                if missing_named else ""))))
    return violations
