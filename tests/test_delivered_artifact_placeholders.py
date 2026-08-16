"""A delivered artifact still full of `{{…}}` is not a delivered artifact
(Round 55 站2).

`08-config/CONFIG_RECORDS.md` is Phase 8's key artifact. `templates/
CONFIG_RECORDS.md` ships it with `{{config}}` / `{{VAR}}` / `{{rollback
commands}}` for a human to fill, `scripts/phase8_doc_gen.py` copies it, and
nothing in the automatic pipeline has ever read the result. `legal_artifacts`
checks the filename; `phase_artifact_enforcer` checks that the file exists.

Measured 2026-08-17 on taskq-super, whose Phase 8 closed with
`Gate4 PASS score=93.91`: eleven unreplaced placeholders, including the entire
Rollback SOP (`**Trigger Condition**: {{condition}}` / ```` ```bash\n{{rollback
commands}}\n``` ````), the runtime configuration for both environments, and the
one row of the environment-variable table.

One correction to the obvious reading, recorded because the obvious reading is
wrong: `constitution/runner.py::_score_file_compliance` returns a vacuous
100/100/100/100 for a file `_is_stub_template` recognises, and taskq-super's
file does trip it (11 ≥ the threshold of 8). That is not the live path.
Constitution left the automatic pipeline at 減法 T3 (2026-07-07) and is
declared out of it in `phase_hooks.NON_PIPELINE_PREFLIGHTS` /
`NON_PIPELINE_POSTFLIGHTS`. The defect here is not a bad score; it is the
absence of any reader.

The verdict lands where the per-phase artifact registry already lives —
`cross_artifact.check_phase_title` names P4/P5/P6/P7/P8/P9's artifacts, and
`phase_truth_verifier.check_cross_artifact` already fails on a CRITICAL. The
detector is the regex constitution already uses, imported rather than
rewritten: a second spelling of "what a placeholder looks like" is the defect
this round is about.
"""

from __future__ import annotations

from pathlib import Path

# The verbatim shape shipped by templates/CONFIG_RECORDS.md, as taskq-super
# delivered it.
_CONFIG_RECORDS_UNFILLED = """\
# CONFIG_RECORDS.md - taskq-super

> Phase 8 configuration records.

## 2. Runtime Configuration
| Environment | Config |
|-------------|--------|
| Development | {{config}} |
| Production | {{config}} |

## 4. Environment Variables
| Variable | Type | Description |
|----------|------|-------------|
| {{VAR}} | secret | {{description}} |

## 7. Rollback SOP
**Trigger Condition**: {{condition}}
```bash
{{rollback commands}}
```
"""

_CONFIG_RECORDS_FILLED = """\
# CONFIG_RECORDS.md - taskq-super

> Phase 8 configuration records.

## 2. Runtime Configuration
| Environment | Config |
|-------------|--------|
| Development | `TASKQ_DB_URL=sqlite:///dev.db` |
| Production | `TASKQ_DB_URL` from Vault |

## 7. Rollback SOP
**Trigger Condition**: `/readyz` returns 503 for 5 consecutive minutes.
```bash
alembic downgrade -1 && systemctl restart taskq-api
```
"""


def _phase8(tmp_path: Path, body: str) -> Path:
    (tmp_path / "08-config").mkdir(parents=True)
    (tmp_path / "08-config" / "CONFIG_RECORDS.md").write_text(body, encoding="utf-8")
    (tmp_path / "08-config" / "RELEASE_CHECKLIST.md").write_text(
        "# RELEASE_CHECKLIST.md — Phase 8\n\n- [x] tag cut\n", encoding="utf-8")
    return tmp_path


def test_an_unfilled_delivered_artifact_is_critical(tmp_path):
    """Eleven `{{…}}` in the Phase 8 key artifact, and Gate 4 read 93.91."""
    from core.quality_gate.cross_artifact import check_unfilled_placeholders

    v = check_unfilled_placeholders(_phase8(tmp_path, _CONFIG_RECORDS_UNFILLED), 8)
    assert v, "the delivered configuration records are a template and nobody read it"
    assert all(x["severity"] == "CRITICAL" for x in v)
    issue = " ".join(x["issue"] for x in v)
    assert "{{rollback commands}}" in issue or "rollback commands" in issue, (
        "the block must name the placeholders, not report a count — the "
        "rollback SOP is the one an operator reaches for at 3am"
    )
    assert any(x["file"].endswith("CONFIG_RECORDS.md") for x in v)


def test_a_filled_artifact_is_silent(tmp_path):
    """The control. A check that names every artifact names none of them."""
    from core.quality_gate.cross_artifact import check_unfilled_placeholders

    assert check_unfilled_placeholders(_phase8(tmp_path, _CONFIG_RECORDS_FILLED), 8) == []


def test_the_check_runs_inside_the_phase_gate(tmp_path):
    """Detection without an executor is Round 43's mother pattern.

    `run_cross_artifact_checks` is what `phase_truth_verifier.check_cross_artifact`
    calls, and a CRITICAL there is what turns the phase verdict false.
    """
    from core.quality_gate.cross_artifact import run_cross_artifact_checks

    result = run_cross_artifact_checks(_phase8(tmp_path, _CONFIG_RECORDS_UNFILLED), 8)
    assert result["critical_count"] >= 1, (
        "the unfilled artifact produced no CRITICAL, so the phase verdict "
        "never sees it"
    )
    assert result["passed"] is False


def test_the_placeholder_pattern_has_one_owner():
    """Constitution's regex is imported, not re-spelled.

    Two spellings of "what an unfilled placeholder looks like" drift, and the
    one the gate reads is the one that goes stale (Round 36).
    """
    from core.quality_gate import cross_artifact
    from core.quality_gate.constitution import runner

    assert cross_artifact._STUB_PLACEHOLDER_RE is runner._STUB_PLACEHOLDER_RE
