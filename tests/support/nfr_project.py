"""A minimal project whose NFR coverage is decided by TEST_SPEC case bindings (Round 117)."""
from __future__ import annotations

from pathlib import Path


def make_nfr_project(root: Path, criteria: dict, cases: list, delivered=(),
                     tests_rel: str = "03-development/tests") -> Path:
    """`criteria`: {"NFR-01": ["AC-N1.1", ...]}; `cases`: [(test_fn, "cell citing ACs")];
    `delivered`: test functions written (passing bodies) under `tests_rel`."""
    srs = ["# SRS", ""]
    for nfr, acs in criteria.items():
        srs += [f"### {nfr}: requirement", "", "**Acceptance criteria**", ""]
        srs += [f"- **{ac}**: holds." for ac in acs] + [""]
    (root / "01-requirements").mkdir(parents=True, exist_ok=True)
    (root / "01-requirements" / "SRS.md").write_text("\n".join(srs), encoding="utf-8")
    spec = ["# TEST_SPEC.md", "", "### NFR Integration", "",
            "| # | Test Function | Inputs | Type | Derivation |", "|---|---|---|---|---|"]
    spec += [f"| {i} | `{fn}` | x=\"1\" | integration | {cite} |" for i, (fn, cite) in enumerate(cases, 1)]
    (root / "02-architecture").mkdir(parents=True, exist_ok=True)
    (root / "02-architecture" / "TEST_SPEC.md").write_text("\n".join(spec) + "\n", encoding="utf-8")
    tests = root / tests_rel
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_nfr.py").write_text(
        "".join(f"def {fn}():\n    assert True\n" for fn in delivered), encoding="utf-8")
    return root
