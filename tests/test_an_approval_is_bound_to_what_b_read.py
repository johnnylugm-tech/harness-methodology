"""Round 113 站1/站2 — an approval is bound to the bytes Agent B reviewed.

taskq-sol's SAD.md approval says it reviewed "the full 565-line SAD.md"; the
committed SAD.md is 755 lines. The 190-line difference is exactly the SAB
block that the Phase 2 `sab-generation` step wrote into SAD §5 an hour after B
approved (sessions_spawn.log 11:50 approve, 12:51 sab-generation). Nothing
noticed, because the approval record carried no binding to content at all:
`fr`, `review_status`, `reason`, `citations`, `docs_embedded`, `confidence`.
Every later step that edits a deliverable — SAB generation, constitution
fixes, peer-review fixer, preview fixer, push and advance retries — could
leave an approval describing a file that no longer exists.

The record now carries `reviewed_sha256`: the sha the workflow's relay frame
reported for the bytes B was given (read-file computes it over the whole
file), or, when no relay preceded the write (Phase 6's reviewer reads files
itself; a CLI user writes approvals by hand), the file as it stood when the
approval was written. advance-phase compares it with the file now.

Station 2 rides on the same record: B's `gaps` were dropped by the payload —
the three "non-blocking documentation defects" taskq-sol's P1 approvals name
are on disk nowhere but in the prose of `reason`.

Honest limit: approvals are still written by a command an LLM shell wrapper
runs, so a deliberately forged sha is not caught — the same limit the relay
frame states for itself (Round 86). What is caught is a file changed after
review with nobody re-reviewing it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from cli.checks.approvals import cmd_stale_approvals, cmd_write_approval
from core.quality_gate.agent_b_approvals import stale_approvals, verify_agent_b_approvals_core

_REASON = "Reviewed every section against SRS.md; all ten FRs map to modules. " * 2


def _project(tmp_path: Path) -> Path:
    arch = tmp_path / "02-architecture"
    (arch / "adr").mkdir(parents=True)
    (arch / "SAD.md").write_text("# Software Architecture Document\nline two\n", encoding="utf-8")
    (arch / "adr" / "ADR.md").write_text("# Architecture Decision Records\n", encoding="utf-8")
    (arch / "TEST_SPEC.md").write_text("# TEST_SPEC.md\n", encoding="utf-8")
    return tmp_path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(project: Path, fr_id: str, **extra) -> int:
    payload = {"fr": fr_id, "review_status": "APPROVE", "reason": _REASON,
               "citations": ["02-architecture/SAD.md:1"],
               "docs_embedded": ["SRS.md", "SAD.md"], "confidence": 0.9, **extra}
    return cmd_write_approval(argparse.Namespace(
        project=str(project), fr_id=fr_id, stdin=False, json=json.dumps(payload)))


def _record(project: Path, fr_id: str) -> dict:
    return json.loads((project / ".methodology" / "agent_b_approvals" / f"{fr_id}.json").read_text())


def test_the_relay_sha_is_what_is_recorded(tmp_path):
    proj = _project(tmp_path)
    relayed = _sha(proj / "02-architecture" / "SAD.md")
    assert _write(proj, "SAD.md", reviewed_sha256=relayed) == 0
    assert _record(proj, "SAD.md")["reviewed_sha256"] == relayed


def test_without_a_relay_the_file_at_write_time_is_recorded(tmp_path):
    proj = _project(tmp_path)
    assert _write(proj, "SAD.md") == 0
    assert _record(proj, "SAD.md")["reviewed_sha256"] == _sha(proj / "02-architecture" / "SAD.md")


def test_a_malformed_sha_is_refused(tmp_path):
    proj = _project(tmp_path)
    assert _write(proj, "SAD.md", reviewed_sha256="not-a-sha") == 1


def test_an_edit_after_review_makes_the_approval_stale(tmp_path):
    proj = _project(tmp_path)
    for did in ("SAD.md", "ADR.md", "TEST_SPEC.md"):
        assert _write(proj, did) == 0
    assert stale_approvals(proj, 2) == []
    # sab-generation writes the SAB block into SAD §5 after B approved
    sad = proj / "02-architecture" / "SAD.md"
    sad.write_text(sad.read_text() + "## 5. SAB\n```yaml\nsab: {}\n```\n", encoding="utf-8")
    assert [s["id"] for s in stale_approvals(proj, 2)] == ["SAD.md"]
    passed, report = verify_agent_b_approvals_core(proj, 2, ["SAD.md", "ADR.md", "TEST_SPEC.md"])
    assert not passed and "SAD.md" in report and "changed after Agent B reviewed it" in report


def test_a_legacy_approval_with_no_binding_is_stale(tmp_path):
    proj = _project(tmp_path)
    approvals = proj / ".methodology" / "agent_b_approvals"
    approvals.mkdir(parents=True)
    (approvals / "SAD.md.json").write_text(json.dumps(
        {"fr": "SAD.md", "review_status": "APPROVE", "reason": _REASON,
         "citations": ["02-architecture/SAD.md:1"], "docs_embedded": ["SRS.md", "SAD.md"]}))
    assert [s["id"] for s in stale_approvals(proj, 2)] == ["SAD.md"]


def test_a_file_the_gates_rewrite_is_not_bound(tmp_path):
    # quality_manifest.json is rewritten by every gate run; binding it would
    # make every Phase 6 approval stale by construction.
    proj = _project(tmp_path)
    meth = proj / ".methodology"
    meth.mkdir()
    (meth / "quality_manifest.json").write_text("{}", encoding="utf-8")
    assert _write(proj, "quality_manifest") == 0
    assert "reviewed_sha256" not in _record(proj, "quality_manifest")
    assert stale_approvals(proj, 6) == []


def test_the_cli_names_the_stale_ids_for_the_workflow(tmp_path, capsys):
    proj = _project(tmp_path)
    for did in ("SAD.md", "ADR.md", "TEST_SPEC.md"):
        _write(proj, did)
    (proj / "02-architecture" / "TEST_SPEC.md").write_text("# TEST_SPEC.md\nedited\n")
    capsys.readouterr()
    assert cmd_stale_approvals(argparse.Namespace(project=str(proj), phase=2)) == 0
    out = capsys.readouterr().out
    assert json.loads(out.split("STALE:", 1)[1].strip()) == ["TEST_SPEC.md"]


def test_b_gaps_reach_the_record(tmp_path):
    proj = _project(tmp_path)
    gaps = [{"severity": "low", "description": "stale SPEC line references in SRS"}]
    assert _write(proj, "SAD.md", gaps=gaps) == 0
    assert _record(proj, "SAD.md")["gaps"] == gaps


def test_gaps_must_be_a_list(tmp_path):
    proj = _project(tmp_path)
    assert _write(proj, "SAD.md", gaps="none") == 1
