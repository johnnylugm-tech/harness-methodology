"""Round 41 站0 — "I correctly did nothing" is a result, not an error.

Every commit-required step must produce a commit, with one carve-out:
`_validate_inner_json` lets a GATE1/GATE1-DELTA evaluator return commit=null
when it reports `pass: false`, because finalize-gate only commits on a pass.
No other step may say "nothing to commit", and a refactor step is exactly the
one that sometimes must.

taskq-api's FR-04 TDD-IMPROVE said it in the only words it had:

    {"status": "DONE", "refactored": false, "commit": null,
     "summary": "baseline test broken; no refactor performed"}

That is the correct answer — refactoring on a red baseline is how a red
baseline becomes an unattributable red baseline. The framework translated it
to `Commit-required step 'TDD-IMPROVE' returned empty commit`, EXECUTION_ERROR,
exit 1; the orchestrator re-dispatched; the agent refused again. Eight times.

Round 26 already built the machinery for "I could not run; here is why":
`_INNER_BLOCKED_SIGNATURES` -> error_class "INFRA" -> excluded from the retry
loop -> aborted with the operator's remediation. INFRA_BLOCKED is its only
member, and it means something different (the tools never ran at all). A step
whose tools DID run and reported an unmet precondition needs its own member and
its own remediation — repair the baseline, or revert the step that broke it.

And the claim has to be checked. Round 35's rule — the framework's own number
comes before the agent's claim — applies verbatim: an agent that says the
baseline is broken when the baseline is green has not hit a precondition, it
has produced a no-op with an excuse, and that stays an error.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

import harness_cli  # noqa: F401  entry-first load order
import cli.fr_cmds as fr_cmds  # noqa: E402
from cli.exit_codes import EX_FAIL, EX_STEP_PRECONDITION_BLOCKED  # noqa: E402
from core import agent_spawner as core_spawner  # noqa: E402

pytestmark = [pytest.mark.core]


_BLOCKED_REPLY = json.dumps({
    "status": "PRECONDITION_BLOCKED",
    "refactored": False,
    "commit": None,
    "summary": "baseline test broken; no refactor performed",
})

_SRC = (
    '"""Widget implementation.\n'
    "\n"
    "[FR-01]\n"
    '"""\n'
    "\n"
    "\n"
    "def handle(value):\n"
    '    """Upper-case the value. [FR-01]"""\n'
    "    return value.upper()\n"
)


def _project(tmp_path: Path, *, baseline_red: bool) -> Path:
    """A project run-fr-step can reach the dispatch loop in, whose FR-01 test
    family really passes or really fails."""
    (tmp_path / "03-development" / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "03-development" / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
    (tmp_path / "03-development" / "src" / "widget.py").write_text(_SRC, encoding="utf-8")
    expected = "'NOPE'" if baseline_red else "'X'"
    (tmp_path / "03-development" / "tests" / "test_fr01.py").write_text(
        "from widget import handle\n"
        "\n"
        "\n"
        "def test_fr01_handles_input():\n"
        f"    assert handle('x') == {expected}\n",
        encoding="utf-8",
    )
    (tmp_path / "conftest.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent / '03-development' / 'src'))\n",
        encoding="utf-8",
    )
    (tmp_path / ".methodology" / "state.json").write_text(
        json.dumps({"language": "python", "current_phase": 3}), encoding="utf-8"
    )
    (tmp_path / ".methodology" / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8"
    )
    (tmp_path / "01-requirements" / "SRS.md").write_text(
        "### FR-01: Widget\n\nMUST accept input.\n\n---\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    return tmp_path


class _BlockedSpawner:
    """Returns the shape spawn() produces for a reported precondition block."""

    def __init__(self):
        self.calls = 0

    def spawn(self, **kwargs):
        self.calls += 1
        _ = kwargs
        return {
            "status": "ERROR",
            "error_class": "INFRA",
            "inner_status": "PRECONDITION_BLOCKED",
            "exit_code": 0,
            "output": (
                "Sub-agent reported inner status 'PRECONDITION_BLOCKED'\n\n"
                + _BLOCKED_REPLY
            ),
        }


def _run(project: Path, spawner) -> int:
    args = argparse.Namespace(
        phase=3, fr_id="FR-01", step="TDD-IMPROVE", project=str(project), src_dir=None,
        dry_run=False, strict=False, srs=None, timeout=100, max_turns=30,
        max_fix_rounds=3, no_mcp=False, no_push=True, prompt_file=None,
    )
    with mock.patch.object(core_spawner, "AgentSpawner", lambda *a, **k: spawner):
        try:
            return fr_cmds.cmd_run_fr_step(args)
        except SystemExit as exc:  # pragma: no cover - defensive
            return int(exc.code or 0)


# ══════════════════════════════════════════════════════════════════════════════
# The spawner half — the status must survive validation as a block, not an error
# ══════════════════════════════════════════════════════════════════════════════


def test_a_precondition_block_is_not_a_missing_commit():
    """The refusal must classify as INFRA (blocked), not EXECUTION_ERROR.

    EXECUTION_ERROR routes into "the agent's own logic failed" — a diagnosis
    the framework can contradict, because the agent did exactly the right thing.
    """
    envelope = {"result": _BLOCKED_REPLY}
    result = core_spawner._validate_inner_json(envelope, "TDD-IMPROVE")
    assert result is not None, "a step with no commit must still be re-classified"
    assert result["error_class"] == "INFRA", (
        f"a reported precondition block classified {result['error_class']!r} — "
        f"the commit-required branch swallowed it and called it a missing commit"
    )


# ══════════════════════════════════════════════════════════════════════════════
# The command half — a verified block aborts with remediation; a lie does not
# ══════════════════════════════════════════════════════════════════════════════


def test_a_verified_precondition_block_aborts_with_remediation(tmp_path, capsys):
    """Baseline really is red: stop, name the way out, do not re-dispatch."""
    project = _project(tmp_path, baseline_red=True)
    spawner = _BlockedSpawner()
    rc = _run(project, spawner)
    err = capsys.readouterr().err
    assert rc == EX_STEP_PRECONDITION_BLOCKED, (
        f"exit {rc} — a precondition block is indistinguishable from any other "
        f"failure, so the caller's only move is to run the same command again"
    )
    assert "resume-fr-step" in err or "revert" in err, (
        "the block must tell the operator what to do; a stop with no way out is "
        "the loop this round exists to end"
    )
    assert spawner.calls == 1, (
        f"dispatched {spawner.calls}x — an identical re-dispatch cannot change a "
        f"precondition the agent already measured"
    )


def test_an_unverified_precondition_claim_is_still_an_error(tmp_path):
    """Baseline is green: the agent's excuse is false, and it stays an error.

    Round 35 — the framework's own measurement precedes the agent's claim.
    Without this, "PRECONDITION_BLOCKED" becomes a universal opt-out from the
    commit requirement.
    """
    project = _project(tmp_path, baseline_red=False)
    rc = _run(project, _BlockedSpawner())
    assert rc != EX_STEP_PRECONDITION_BLOCKED, (
        "a precondition block was accepted without checking whether the "
        "precondition was actually unmet — the claim decided the verdict"
    )
    assert rc == EX_FAIL
