"""A witness that did not appear did not testify.

Round 46 站0. taskq-advance shipped Gate 4 at composite 95.978 with
`TRACEABILITY_MATRIX.md` recording NFR-05 / NFR-07 / NFR-09 as `VERIFIED`.
Its suite, run today, reports 255 passed and 17 skipped. Fourteen of those
skips are in `03-development/tests/test_spec_nfr.py`, and every one of them is
a requirement-guard test that skips itself precisely when its requirement is
violated:

    SKIPPED test_spec_nfr.py:623  SBOM.json not found …        (NFR-07 demands it exist)
    SKIPPED test_spec_nfr.py:646  README.md missing — NFR-05 gap
    SKIPPED test_spec_nfr.py:936  project contains intentional skips (NFR-09 …)
    SKIPPED test_spec_nfr.py:975  project has 28 zero-assert stubs (NFR-09 …)

The zero-skip test skips because the project has skips. The SBOM test skips
because there is no SBOM.

Round 25's outcome-aware scanner (`Defect A fix`) already refuses to credit a
requirement whose only mention sits in a test that did not pass — the outcome
data is in hand, per function. But the credit it grants is **file**-level: at
`scanner.py:380` a function that did not pass is `continue`d and forgotten, and
one sibling in the same file that did pass credits the whole file. In
taskq-advance `test_licenses_in_allowlist` passes, so NFR-07 is VERIFIED and
`test_sbom_license_field`'s skip is never mentioned by anything.

Measured on the real project, crediting a requirement only when it has no
absent witness moves 4c from 12/12 = 100.0 to 9/12 = 75.0 — under Gate 4's 90%
threshold — and the three it names are exactly NFR-05, NFR-07 and NFR-09.

FR side: `scan_test_fr_coverage` has the same shape (`scanner.py:277`), and on
taskq-advance it changes nothing (10/10 either way). Same-shaped siblings are
fixed together anyway — Round 8 站1's rule.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


_SPEC_NFR_TEST = '''
def test_licenses_in_allowlist():
    """NFR-07: every dependency license is on the allowlist."""
    assert True


def test_sbom_license_field():
    """NFR-07: the SBOM records a license per dependency."""
    import pytest
    pytest.skip("SBOM.json not found")


def test_error_envelope():
    """NFR-10: errors carry a stable envelope."""
    assert True
'''


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A project whose NFR-07 guard is half-absent and whose NFR-10 guard is not."""
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_spec_nfr.py").write_text(_SPEC_NFR_TEST, encoding="utf-8")

    req = tmp_path / "01-requirements"
    req.mkdir()
    (req / "SRS.md").write_text(
        "# Software Requirements Specification\n\n"
        "### NFR-07: dependency and licence compliance\n"
        "### NFR-10: integration coverage\n",
        encoding="utf-8",
    )
    return tmp_path


_REL = "03-development/tests/test_spec_nfr.py"

_OUTCOMES = {
    f"{_REL}::test_licenses_in_allowlist": "passed",
    f"{_REL}::test_sbom_license_field": "skipped",
    f"{_REL}::test_error_envelope": "passed",
}


def _suite(outcomes: dict[str, str]):
    """A SuiteResult carrying nothing but the per-function outcomes."""
    from core.quality_gate.test_suite_run import SuiteResult
    return SuiteResult(
        passed=True, coverage=None, test_target="03-development/tests",
        cov_target="03-development/src", returncode=0, output="",
        ran=True, skipped=1, test_outcomes=dict(outcomes),
    )


def _pin_outcomes(monkeypatch, outcomes: dict[str, str]) -> None:
    """Pin every bound name of `run_suite` — `scripts.build_traceability`
    imports it at module scope, `compute_trace_dimension` inside the function.
    Patching one and not the other leaves half the pipeline running pytest."""
    from core.quality_gate import test_suite_run
    from scripts import build_traceability as bt

    def _fake(*_a, **_k):
        return _suite(outcomes)

    monkeypatch.setattr(test_suite_run, "run_suite", _fake)
    monkeypatch.setattr(bt, "run_suite", _fake)


def test_an_absent_witness_is_reported_not_discarded(project):
    """`scanner.py:380` drops the function it refuses to credit. It must not."""
    from core.traceability.scanner import scan_test_nfr_absent_witnesses

    absent = scan_test_nfr_absent_witnesses(
        project / "03-development" / "tests",
        test_outcomes=_OUTCOMES,
        project_root=project,
    )

    assert "NFR-07" in absent, (
        "test_sbom_license_field claims to verify NFR-07 and did not run — "
        "the framework must be able to name it"
    )
    named = " ".join(str(x) for x in absent["NFR-07"])
    assert "test_sbom_license_field" in named
    assert "skipped" in named
    assert "NFR-10" not in absent, "NFR-10's only witness passed"


def test_a_requirement_with_an_absent_witness_is_not_covered(project, monkeypatch):
    """4c must not count NFR-07 as covered while one of its guards is skipped."""
    from core.quality_gate import spec_tracking_checker as stc

    _pin_outcomes(monkeypatch, _OUTCOMES)
    result = stc.compute_trace_dimension(project, 4)

    assert result["4c_nfr_to_test_pct"] == 50.0, result
    witnesses = " ".join(str(x) for x in result.get("nfr_absent_witnesses", []))
    assert "NFR-07" in witnesses, result
    assert "test_sbom_license_field" in witnesses, result


def test_all_witnesses_passing_is_unchanged(project, monkeypatch):
    """A healthy project must read exactly as it does today."""
    from core.quality_gate import spec_tracking_checker as stc

    healthy = dict(_OUTCOMES)
    healthy[f"{_REL}::test_sbom_license_field"] = "passed"
    _pin_outcomes(monkeypatch, healthy)
    result = stc.compute_trace_dimension(project, 4)

    assert result["4c_nfr_to_test_pct"] == 100.0, result
    assert result.get("nfr_absent_witnesses") == [], result


def test_the_matrix_says_partial_and_says_why(project, monkeypatch):
    """`VERIFIED` on a requirement with an absent witness is the shipped bug."""
    from scripts import build_traceability as bt

    _pin_outcomes(monkeypatch, _OUTCOMES)
    rt = bt.build_traceability(project)
    out = project / "01-requirements" / "TRACEABILITY_MATRIX.md"
    bt.generate_markdown_matrix(rt, out)
    text = out.read_text(encoding="utf-8")

    nfr07 = next(ln for ln in text.splitlines() if ln.startswith("| NFR-07 "))
    assert "PARTIAL" in nfr07, nfr07
    assert "test_sbom_license_field" in nfr07, nfr07
    nfr10 = next(ln for ln in text.splitlines() if ln.startswith("| NFR-10 "))
    assert "VERIFIED" in nfr10, nfr10
