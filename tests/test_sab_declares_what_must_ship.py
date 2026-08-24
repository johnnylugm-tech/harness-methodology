"""Round 72 站5 — `required_artifacts` is stated, or the SAB does not validate.

Round 68 站1 built the reader and the block: `required_artifacts` is checked
against the delivered tree at every finalize, and a declared path that is
absent — or shipped somewhere else — stops the gate and names itself.

Nothing required the declaration. `render_canonical_sab_template` emits the
section, `validate_sab_block` never mentioned it, and `SABSpec`'s
`default_factory=list` turns "the project said nothing" into `[]` the instant
the document is parsed. So the whole mechanism resolves to
`record_required_artifacts`'s own ledger row, which reads:

    the SAB declares no required_artifacts — nothing states which files this
    project must ship, so no check can tell a deliverable that was never
    written from one that was. Declare the spec's mandatory config files
    under `required_artifacts` in the SAB block of SAD.md

Measured on taskq-new: 186 of those rows, none blocking. Across all nine
projects here, `grep required_artifacts 02-architecture/SAD.md` returns zero.

WHAT THE MEASUREMENT DID NOT SHOW, recorded because the first draft of this
station claimed it: none of those nine ignored a template that asked. Round 68
landed at 02:29 on 2026-08-22 and taskq-new's `phase2_plan.md` — the document
carrying the template to the agent — was generated at 02:23 the same morning.
No project has yet run a P2 whose template contained this section. The defect
is that the framework states an obligation it does not enforce (Round 30's
abstain-equals-pass, Round 46's absent witness), not that an agent skipped a
field it was shown.

An empty list is accepted. "This spec names no mandatory files" is a judgement
somebody made; an absent key is nobody having considered it, and those are
different statements — the same distinction Round 60 drew between declaring
and delivering.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

_SAD = """\
# Software Architecture Document

## 5. SAB

<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  created_at: "2026-08-24"
  phase: 2
  project: "demo"
  layers:
    - name: service
      modules:
        - name: "demo.service"
      allowed_dependencies: []
  nfr_traceability: {}
%s
```
<!-- SAB:END -->
"""


def _sad(tmp_path: Path, extra: str) -> Path:
    path = tmp_path / "SAD.md"
    path.write_text(_SAD % extra, encoding="utf-8")
    return path


def test_a_sab_that_never_mentions_required_artifacts_does_not_validate(tmp_path):
    from core.quality_gate.sab_parser import validate_sab_block

    errors = validate_sab_block(_sad(tmp_path, "  high_risk_modules: []"))
    assert any("required_artifacts" in e for e in errors), errors


def test_an_explicit_empty_list_is_a_decision_and_validates(tmp_path):
    """The counter-direction. A rule that forced a non-empty list would force
    projects whose spec names no mandatory files to invent one."""
    from core.quality_gate.sab_parser import validate_sab_block

    assert validate_sab_block(_sad(tmp_path, "  required_artifacts: []")) == []


def test_a_declared_list_validates(tmp_path):
    from core.quality_gate.sab_parser import validate_sab_block

    errors = validate_sab_block(
        _sad(tmp_path, '  required_artifacts:\n    - ".env.example"')
    )
    assert errors == []


def test_the_canonical_template_satisfies_the_rule_it_is_pasted_under(tmp_path):
    """The template the P2 prompt hands the agent must pass validation.

    A template that fails the validator it is offered alongside is the shape
    Round 70 found in the HARNESS-BUG prompt: an agent that obeys its
    instructions cannot satisfy the check.
    """
    from core.quality_gate.sab_parser import (
        SAB_BLOCK_TEMPLATE, validate_sab_block,
    )

    path = tmp_path / "SAD.md"
    path.write_text(
        "# SAD\n\n<!-- SAB:START -->\n```yaml\n"
        + SAB_BLOCK_TEMPLATE
        + "\n```\n<!-- SAB:END -->\n",
        encoding="utf-8",
    )
    assert validate_sab_block(path) == []


def test_the_p2_plan_counts_the_fields_sabspec_actually_has():
    """The plan's field list is rendered from SABSpec, not typed beside it.

    It read "all 14 fields" and named fourteen; `required_artifacts` had been
    the fifteenth since Round 68 站1. A sentence that restates a value is a
    sentence that will one day disagree with it (Round 39 站3), and this one
    disagreed about the very field this station is about.
    """
    import dataclasses

    from core.quality_gate.sab_parser import SABSpec
    from scripts.plangen.phase_tasks import generate_phase2_tasks

    plan = "\n".join(generate_phase2_tasks(Path("."), Path("nonexistent-srs.md")))
    names = [f.name for f in dataclasses.fields(SABSpec)]

    assert f"contains all {len(names)} fields from `SABSpec`" in plan
    for name in names:
        assert name in plan, f"{name} is a SABSpec field the P2 plan omits"
