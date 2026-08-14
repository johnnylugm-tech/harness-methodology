"""The product-side facts a delivery leaves behind (Round 52 站3).

Every guard the framework applies to a judged project is a unary predicate:
`f(tree) ≥ threshold`. "Regression" is a binary relation, `f(new) < f(old)`, so
a system built only from unary predicates cannot express it — it can say "below
the bar" and nothing else. Round 51's two trees were both above the bar; the
framework was not wrong, it was mute.

The one exception in this repository is
`harness/harness_bridge.py`'s `_architecture_regression_reason`: Gate 4 only,
the same project's own P4 baseline only, CRG structural metrics only. It cannot
see a function body that is one `raise`.

Meanwhile the harness ratchets *itself* — line counts, swallowed exceptions, the
guard registry, golden bytes. It knows the shape and has never applied it to
what it judges.

This writes the facts down. It does not judge them, and that is a decision with
a reason rather than an omission: a cross-project corpus has nowhere to live —
the harness is a submodule of each project and cannot see the others' runs — so
an outlier verdict would need a hand-curated reference distribution checked in
here, which is one more thing declared with no executor (Round 43). The reopen
condition is in docs/PROPOSAL_ADJUDICATIONS.md.

Nothing here measures anything new. Every field is a value some existing
producer already computed:

    stubbed_boundaries   Round 51 站3  core/quality_gate/boundary_realism.py
    architecture         Round 51 站2  core/quality_gate/arch_constraints.py
    coverage             Round 51 站4  core/quality_gate/cov_utils.py
    acceptance_criteria  Round 51 站5  core/quality_gate/artifact_consistency.py
    verify_system        Round 52 站1  core/quality_gate/verify_target.py
                         Round 52 站2  core/quality_gate/verify_system_reach.py

A table that restates a value is a table that will one day disagree with it
(Round 39 站3), so each field is the producer's own return value and
tests/test_delivery_fingerprint.py asserts that equality directly.
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = ["FINGERPRINT_RELPATH", "build_fingerprint", "write_fingerprint"]

FINGERPRINT_RELPATH = ".methodology/delivery_fingerprint.json"


def _sab(project: Path) -> dict:
    path = project / ".methodology" / "SAB.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def build_fingerprint(project: "str | Path") -> dict:
    """The product-side facts, each read from the producer that owns it."""
    from core.quality_gate.arch_constraints import (
        STATUS_DECLARED_ONLY,
        classify_constraints,
        contract_coverage_gap,
    )
    from core.quality_gate.artifact_consistency import (
        check_ac_identifiers,
        check_ac_test_spec_coverage,
    )
    from core.quality_gate.boundary_realism import stubbed_boundaries
    from core.quality_gate.cov_utils import coverage_denominator, read_coveragerc_omit
    from core.quality_gate.verify_system_reach import unmet_obligations
    from core.quality_gate.verify_target import verify_target_findings

    project = Path(project)

    stubbed = stubbed_boundaries(project)
    constraints = classify_constraints(
        list(_sab(project).get("architecture_constraints") or []), project)
    denominator = coverage_denominator(project)
    ac_unnumbered = check_ac_identifiers(project)
    ac_uncited = check_ac_test_spec_coverage(project)
    target = verify_target_findings(project)
    reach = unmet_obligations(project)

    swallowed = target["swallowed"]
    return {
        "stubbed_boundaries": {
            "count": len(stubbed),
            "modules": sorted({r["module"] for r in stubbed}),
        },
        "architecture": {
            "declared_only": [r["constraint"] for r in constraints
                              if r["status"] == STATUS_DECLARED_ONLY],
            "modules_outside_every_contract": contract_coverage_gap(project),
        },
        "coverage": {
            "omit": read_coveragerc_omit(project),
            "statements_omitted": denominator.get("statements_omitted"),
            "statements_measured": denominator.get("statements_measured"),
        },
        "acceptance_criteria": {
            # `check_ac_identifiers` returns two kinds of row and they are not
            # the same fact: `ac_unnumbered` is a criterion with no id,
            # `ac_parse_gap` is the parser saying it could not attribute an
            # identifier it saw (Round 46 站1 — abstaining is not passing).
            # Summing them would hide the second inside the first, which is
            # what Round 51's own six-project table did.
            "unnumbered": sum(1 for v in ac_unnumbered
                              if v.check_type == "ac_unnumbered"),
            "parse_gap": sum(1 for v in ac_unnumbered
                             if v.check_type == "ac_parse_gap"),
            "uncited_by_test_spec": len(ac_uncited),
        },
        "verify_system": {
            "status": target["status"],
            "tautological": target["tautological"],
            "swallowed": len(swallowed) if swallowed is not None else None,
            "reach_status": reach["status"],
            "obligations_unmet": [f"{r['module']}.{r['attr']}"
                                  for r in (reach.get("unmet") or [])],
        },
    }


def write_fingerprint(project: "str | Path") -> Path:
    """Write the fingerprint beside the SAB and the CRG baselines.

    `.methodology/`, not `.sessi-work/`: advance-phase clears the work
    directory at every transition, and a fact recorded for a future round to
    compare against has to outlive the run that recorded it (Round 45 站1).
    """
    project = Path(project)
    out = project / FINGERPRINT_RELPATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(build_fingerprint(project), indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return out
