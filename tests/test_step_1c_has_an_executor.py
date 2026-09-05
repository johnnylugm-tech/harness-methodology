"""A MANDATORY authoring rule whose executor was an LLM.

Round 98 station 4.

`harness/ssi/prompts/derive_test_cases.md` Step 1c maps every STRIDE category
to a forced negative-path pattern and says, unconditionally:

> **Required case shape**: the threat's own `verified_by` name (already
> declared in SAD.md §6) IS the forced case ... Add ONE row to the owning FR's
> TEST_SPEC table using that exact name

and names its enforcer: "an Agent B REJECT". Measured across the thirteen
corpus projects with a SAD and a TEST_SPEC, counting how many declared
`verified_by` names appear anywhere in the P2 TEST_SPEC.md:

    100%  taskq 7/7, taskq-api 11/11, taskq-cc 8/8, taskq-final 15/15,
          taskq-plus 9/9, taskq-redo 10/10
      0%  taskq-advance, taskq-cc-new, taskq-done, taskq-new, taskq-renew,
          taskq-super, taskq-wow

Six at 100%, seven at zero, nothing between. That is the distribution of a
rule nobody executes, not of a rule projects find hard.

SEC-R8 does catch it — at Phase 5, against the test sources. The five finished
projects in the zero column all reached 100% implemented, so nothing is lost
in the end; what is paid is three phases of delay on an obligation that was
decidable when TEST_SPEC.md was written. taskq-wow is the live case: ten
threats, ten `verified_by` names, zero of them in TEST_SPEC.md and zero of
them on disk, sitting at Phase 3.

R9 is the same rule with a decidable reader. Round 42 declined to change
STRIDE *scoring* (the framework deliberately asks only for per-boundary
coverage) and this does not: it adds no threshold, no vocabulary and no
number. The name is the project's own, and TEST_SPEC.md is a document the
framework's own `check_ac_test_spec_coverage` already reads.
"""

from __future__ import annotations

import pytest

from core.quality_gate.security_design import (
    check_security_design,
    render_canonical_security_template,
)

pytestmark = [pytest.mark.core]


def _sad(project, *, threats_yaml: str, applicability: str = "full") -> None:
    arch = project / "02-architecture"
    arch.mkdir(parents=True, exist_ok=True)
    (arch / "SAD.md").write_text(
        "# SAD\n\n## 6. Security Design\n\n<!-- SEC:START -->\n```yaml\n"
        "security_design:\n"
        '  version: "1.0"\n'
        f"  applicability: {applicability}\n"
        '  justification: "a pure formatting library with no attack surface"\n'
        "  trust_boundaries:\n"
        "    - id: TB-01\n"
        '      name: "external HTTP input"\n'
        '      description: "requests crossing from the network into the app"\n'
        f"{threats_yaml}"
        "```\n<!-- SEC:END -->\n",
        encoding="utf-8",
    )


_ONE_THREAT = (
    "  threats:\n"
    "    - id: T-01\n"
    "      boundary: TB-01\n"
    "      category: tampering\n"
    '      description: "a malformed payload reaches the parser"\n'
    '      mitigation: "schema validation at the edge"\n'
    '      owner_module: "pkg.api"\n'
    '      verified_by: "test_sec_t01_malformed_payload_rejected"\n'
)


def _test_spec(project, body: str) -> None:
    arch = project / "02-architecture"
    arch.mkdir(parents=True, exist_ok=True)
    (arch / "TEST_SPEC.md").write_text(body, encoding="utf-8")


def _r9(project, phase):
    return [v for v in check_security_design(project, phase=phase)
            if v.rule_id == "SEC-R9"]


def test_a_declared_verification_test_missing_from_test_spec_is_an_error(tmp_path):
    """taskq-wow's shape: ten names in SAD.md §6, none in the P2 deliverable."""
    _sad(tmp_path, threats_yaml=_ONE_THREAT)
    _test_spec(tmp_path, "# TEST_SPEC\n\n| # | Case |\n|---|---|\n| 1 | `test_fr01_ok` |\n")
    found = _r9(tmp_path, 3)
    assert found, "the Step-1c row was never written and nothing said so"
    assert "test_sec_t01_malformed_payload_rejected" in found[0].message
    assert found[0].severity == "error"


def test_a_declared_verification_test_present_in_test_spec_is_silent(tmp_path):
    """The six corpus projects that did write the rows must stay quiet."""
    _sad(tmp_path, threats_yaml=_ONE_THREAT)
    _test_spec(
        tmp_path,
        "# TEST_SPEC\n\n| # | Case | Type | Derivation |\n|---|---|---|---|\n"
        "| 1 | `test_sec_t01_malformed_payload_rejected` | nfr_pattern | Q6/1c/NP-04 |\n",
    )
    assert _r9(tmp_path, 3) == []


def test_it_does_not_fire_before_the_test_spec_is_due(tmp_path):
    """Same convention as R1-R7 and check_nfr_adr_coverage: the SEC block is a
    P2 deliverable, so its rules are informational until P3."""
    _sad(tmp_path, threats_yaml=_ONE_THREAT)
    _test_spec(tmp_path, "# TEST_SPEC\n")
    assert _r9(tmp_path, 2) == []


def test_an_honest_none_is_silent(tmp_path):
    """A project with no real attack surface declares it and skips the block;
    R9 must not turn that into an obligation."""
    _sad(tmp_path, threats_yaml="", applicability="none")
    _test_spec(tmp_path, "# TEST_SPEC\n")
    assert _r9(tmp_path, 3) == []


def test_a_missing_test_spec_is_not_an_accusation(tmp_path):
    """Round 32/35: a document that is not there yet is not a document that
    dropped the row. The P2 handoff validator owns its existence."""
    _sad(tmp_path, threats_yaml=_ONE_THREAT)
    assert _r9(tmp_path, 3) == []


def test_r8_still_owns_phase_five(tmp_path):
    """Counter-control: R9 is an additional reader, not a replacement. R8's
    phase>=5 test-existence rule must be untouched."""
    _sad(tmp_path, threats_yaml=_ONE_THREAT)
    _test_spec(
        tmp_path,
        "| 1 | `test_sec_t01_malformed_payload_rejected` | nfr_pattern |\n")
    at_five = check_security_design(tmp_path, phase=5)
    assert [v for v in at_five if v.rule_id == "SEC-R8"], (
        "R8 stopped demanding the test exists on disk")


def test_the_canonical_template_satisfies_its_own_new_rule():
    """The template is what P2 pastes. If its example threat could not satisfy
    R9, the rule would be teaching projects to fail — Round 91's shape."""
    assert "verified_by" in render_canonical_security_template()
