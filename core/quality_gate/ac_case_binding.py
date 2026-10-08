"""Which TEST_SPEC case verifies which acceptance criterion — and so which
SRS non-functional requirement a delivered test verifies.

Round 117. `check_ac_test_spec_coverage` asked only whether an AC id appeared
anywhere in TEST_SPEC.md, prose included, while its own message and Agent B's
checklist said "cited by a TEST_SPEC case". With no readable binding, the
trace dimension's 4c credited any passing test whose body contained the
string `NFR-XX` — written by the implementer, reviewed by nobody — and the P3
prompt sent agents to an FR↔NFR table (TRACEABILITY_MATRIX §5, SRS §2's
`NFR Association` column) that no project had.

A case cites an AC in one of the two shapes the corpus already writes, both
bound to one declared test: in its declaration row (any cell — taskq-wow's
Derivation, taskq-sol's Inputs, taskq-done's `[AC-1.1]` beside the name), or
in a sub-assertion whose `applies_to` names the case's `#` (taskq-final,
taskq-new, taskq-open, taskq-cc-new). A case number belongs to the most
recent declaration table: taskq-api writes its NFR sub-assertions under their
own heading below the table they refer to, and across the corpus that rule
leaves no `applies_to` reference without its case.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.quality_gate.artifact_consistency import (
    _AC_ID,
    _AC_ID_CITED,
    _DEFERRAL_TEST_FN,
    _parse_deferrals,
    _with_dash,
    srs_acceptance_criteria,
)


def cited_acs(text: str) -> set:
    return {_with_dash(t) for t in _AC_ID_CITED.findall(text)}


def subassertion_columns(header: str) -> dict:
    """`{"rule_id": i, "applies_to": j}` for a sub-assertion table header, else {}."""
    cols = [c.strip().lower() for c in header.split("|")[1:-1]]
    out: dict = {}
    for i, col in enumerate(cols):
        key = "rule_id" if col.startswith("rule") else "applies_to" if col.startswith("applies") else None
        if key and key not in out:
            out[key] = i
    return out if len(out) == 2 else {}


def resolve(token: str, declared: set) -> str:
    """The declared criterion a citation names.

    `_AC_BODY` accepts trailing `-\\d+` segments because taskq-renew numbers
    its criteria `AC-04-3`, so taskq-new's rule_id `AC10.5-422-status` reads
    as `AC-10.5-422`. Trailing segments are dropped only while that reaches
    a declared id; a token that reaches none is returned unchanged.
    """
    candidate = token
    while candidate not in declared:
        shorter = re.sub(r"-\d+$", "", candidate)
        if shorter == candidate:
            return token
        candidate = shorter
    return candidate


def declared_acs(project) -> dict:
    """`{requirement id: [AC ids]}` from SRS.md."""
    return {req: sorted({a for b in bullets for a in _AC_ID.findall(b)})
            for req, bullets in srs_acceptance_criteria(Path(project)).items()}


def ac_case_bindings(project) -> dict:
    """`{AC id: {declared test functions whose case cites it}}`."""
    from core.quality_gate.spec_coverage import _parse_test_spec
    from core.utils.project_layout import ProjectLayout

    declared = {a for acs in declared_acs(project).values() for a in acs}
    out: dict = {}
    for row in _parse_test_spec(ProjectLayout(Path(project)).test_spec_path):
        for ac in row.get("acs", ()):
            out.setdefault(resolve(ac, declared), set()).add(row["test_fn"])
    return out


def nfr_case_coverage(project, test_outcomes=None) -> dict:
    """Per SRS NFR (NFR-99 excluded): every criterion it declares is verified by
    a delivered test — one whose case cites it (the same `spec_coverage_report`
    run, and so the same `delivery_outcome` rule, as the dimension's 4b), or
    one a `Deferred:` clause names. A criterion deferred to anything else
    is listed under `not_a_test`. An NFR that declares no AC id is not covered.

    Returns `{"per_nfr": {nfr: {"tests", "absent", "not_a_test"}}, "pct",
    "untested", "not_test_verified"}`.
    """
    from core.quality_gate.spec_coverage import spec_coverage_report
    from core.traceability.scanner import extract_nfr_ids_from_srs
    from core.utils.project_layout import ProjectLayout

    project = Path(project)
    declared = {a for acs in declared_acs(project).values() for a in acs}
    nfr_ids = {n for n in extract_nfr_ids_from_srs(ProjectLayout(project).srs_path) if n != "NFR-99"}
    report = spec_coverage_report(project, test_outcomes=test_outcomes)
    delivered: dict = {}
    undelivered: dict = {}
    for row in report["covered"]:
        for ac in row.get("acs", ()):
            delivered.setdefault(resolve(ac, declared), set()).add(row["test_fn"])
    for row in report["missing"]:
        for ac in row.get("acs", ()):
            undelivered.setdefault(resolve(ac, declared), set()).add(f"{row['test_fn']} ({row['why']})")
    # A deferral that names a test function binds the criterion to that test
    # (`check_ac_deferral_targets` reads the same clause); one that names a
    # tool or a person is recorded, not counted as coverage (Round 69 站5).
    spec = ProjectLayout(project).test_spec_path
    clauses, _unattributed = _parse_deferrals(
        spec.read_text(encoding="utf-8", errors="replace")) if spec.exists() else ({}, set())
    named = {_with_dash(ac): set(_DEFERRAL_TEST_FN.findall(clause)) for ac, clause in clauses.items()}
    named_why = _deferral_target_outcomes(project, {fn for fns in named.values() for fn in fns}, test_outcomes)
    criteria = declared_acs(project)
    per_nfr: dict = {}
    for nfr in sorted(nfr_ids):
        acs = criteria.get(nfr, [])
        tests: set = set()
        absent: list = [] if acs else [f"{nfr} declares no AC-id"]
        not_a_test: list = []
        for ac in acs:
            by_deferral = {fn for fn in named.get(ac, ()) if named_why[fn] == "delivered"}
            if ac in delivered or by_deferral:
                tests |= delivered.get(ac, set()) | by_deferral
            elif ac in named and not named[ac] and ac not in undelivered:
                not_a_test.append(ac)
            else:
                absent += ([f"{ac} ← {w}" for w in sorted(undelivered.get(ac, ()))]
                           + [f"{ac} ← {fn} ({named_why[fn]})" for fn in sorted(named.get(ac, ()))]
                           or [f"{ac} ← no TEST_SPEC case cites it"])
        per_nfr[nfr] = {"tests": sorted(tests), "absent": absent, "not_a_test": not_a_test}
    # A criterion deferred to a tool or a person is outside what a test can
    # show: not coverage (Round 69 站5), and not a miss either (Round 35) —
    # it is named, and Round 69's ledger row is its cost. An NFR made only of
    # such criteria leaves the denominator; one with none left to measure in
    # a project that declares NFRs is not a pass.
    measured = {n for n, v in per_nfr.items() if v["tests"] or v["absent"]}
    covered = {n for n in measured if not per_nfr[n]["absent"]}
    if measured:
        pct = round(len(covered) / len(measured) * 100, 2)
    else:
        pct = 0.0 if nfr_ids else 100.0
    return {
        "per_nfr": per_nfr,
        "pct": pct,
        "untested": sorted(measured - covered) if measured else sorted(nfr_ids),
        "not_test_verified": sorted(set(per_nfr) - measured),
    }


def _deferral_target_outcomes(project: Path, fns: set, test_outcomes) -> dict:
    """`delivery_outcome` for each test a `Deferred:` clause names — the read
    `check_ac_deferral_targets` makes, over every test function in the tree."""
    from core.quality_gate.spec_coverage import (
        _get_test_directories, _scan_test_functions, delivery_outcome)
    from core.utils.lang_patterns import project_language

    if not fns:
        return {}
    lang = project_language(project)
    actual: set = set()
    for test_dir in _get_test_directories(project):
        actual |= _scan_test_functions(test_dir, lang)
    return {fn: delivery_outcome(fn, actual, test_outcomes) for fn in fns}
