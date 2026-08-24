"""Round 72 站6 — the framework may not delete evidence its own rules demand.

`advance-phase` clears `core.evidence_retention.ADVANCE_CLEARED_DIRS`
(`.sessi-work`) at every transition, deliberately: stale artifacts there made
the next phase's gate skip re-computation. Round 46 站1 requires that a
requirement's witnesses actually ran — a skipped test makes the NFR PARTIAL,
not VERIFIED. Both are right, and together they are a loop.

taskq-new walked it twice. `03-development/tests/test_nfr07_08_11_lint.py`
reads `.sessi-work/round_1/tools/pip_licenses.json` (three sites) and
`readability_v2.txt` (one), and `pytest.skip`s when they are absent:

    advance clears .sessi-work
      → next phase those four tests skip
      → NFR-07 / NFR-11 go PARTIAL
      → completeness falls below 90%
      → advance is refused
      → the agent regenerates the artifacts and re-renders the matrix

`cd47fae` (leaving P5) and `8b9a309` (leaving P7) carry the same subject and
the same body, differing only in the phase number.

Nothing told the project where evidence may live, although this framework
already knew: `cited_evidence_dir` is under `.methodology/`, and
`delivery_fingerprint.py:156` states the reason outright — "advance-phase
clears the work directory at every transition, and a fact recorded for a
future round to compare against has to outlive the run that recorded it".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "-C", str(proj), "init", "-q"], check=True)
    for rel, body in files.items():
        path = proj / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    subprocess.run(["git", "-C", str(proj), "add", "-A"], check=True)
    return proj


def test_a_test_reading_a_cleared_directory_is_found_with_its_line(tmp_path):
    """taskq-new's shape, reduced to the two lines that carry it."""
    from core.evidence_retention import evidence_in_cleared_dirs

    proj = _repo(tmp_path, {
        "tests/test_nfr.py": (
            "from pathlib import Path\n"
            "ROOT = Path(__file__).parent.parent\n"
            "def test_licenses():\n"
            "    p = ROOT / '.sessi-work/round_1/tools/pip_licenses.json'\n"
            "    assert p.is_file()\n"
        ),
    })
    rows = evidence_in_cleared_dirs(proj)

    assert len(rows) == 1, rows
    assert rows[0]["path"] == "tests/test_nfr.py"
    assert rows[0]["line"] == 4
    assert "pip_licenses.json" in rows[0]["literal"]


def test_a_comment_about_the_path_is_not_a_dependency_on_it(tmp_path):
    """The measurement that shaped the rule.

    Nine grep hits across the corpus, all in taskq-new; only four are real.
    One is `conftest.py`'s ``# ``.sessi-work/benchmark_report.json``.`` — a
    comment recording where a file used to be written. A comment reads
    nothing, so the scan is over string literals via `ast`, and docstrings are
    excluded for the same reason: prose about a path is not a use of it.
    """
    from core.evidence_retention import evidence_in_cleared_dirs

    proj = _repo(tmp_path, {
        "tests/test_ok.py": (
            '"""This suite once wrote .sessi-work/benchmark_report.json."""\n'
            "# see .sessi-work/round_1/tools/ for the old location\n"
            "def test_nothing():\n"
            "    assert True\n"
        ),
    })
    assert evidence_in_cleared_dirs(proj) == []


def test_untracked_and_unparseable_files_are_not_findings(tmp_path):
    """The delivered tree is what git has (Round 44 站2), and a file that will
    not parse is could-not-measure, not a finding (Rounds 32/35)."""
    from core.evidence_retention import evidence_in_cleared_dirs

    proj = _repo(tmp_path, {"tests/test_ok.py": "def test_x():\n    assert True\n"})
    (proj / "scratch.py").write_text("P = '.sessi-work/x.json'\n", encoding="utf-8")
    (proj / "tests" / "broken.py").write_text(
        "def (:\n  '.sessi-work/y.json'\n", encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(proj), "add", "tests/broken.py"], check=True)

    assert evidence_in_cleared_dirs(proj) == []


def test_the_advance_prechecks_refuse_and_name_the_place_evidence_may_live(
    tmp_path, capsys,
):
    """The finding reaches a refusal, and the refusal carries the remedy.

    Round 22's rule: a block that does not say what to do is half a block.
    Round 53's: the framework does not repair the tree it judges, so the files
    are named and not moved — whether an artifact should be retained or
    regenerated is the project's decision.
    """
    from cli import phase_cmds

    proj = _repo(tmp_path, {
        "tests/test_nfr.py": (
            "from pathlib import Path\n"
            "def test_licenses():\n"
            "    assert (Path('.sessi-work/round_1/tools/pip_licenses.json')).is_file()\n"
        ),
    })
    # No stubs and no seeded gate state: this check runs ahead of everything
    # in `_advance_prechecks` that reads a manifest, which is what makes it
    # answerable on a bare repository. The first draft of this test sat behind
    # the manifest-integrity check and needed a hand-written finalize receipt
    # to get past it — writing fake gate evidence to test a guard is the thing
    # the guard exists to stop.
    rc = phase_cmds._advance_prechecks(proj, 4)

    out = capsys.readouterr().out
    assert rc == 21, f"expected the WRITE_SCOPE refusal, got {rc}\n{out}"
    assert "tests/test_nfr.py:3" in out
    assert ".methodology/gate_evidence" in out


def test_a_project_with_no_such_reference_is_untouched(tmp_path):
    """The counter-direction: eight of the nine projects here have zero hits,
    and a guard that fired on them would stop every one of them."""
    from core.evidence_retention import evidence_in_cleared_dirs

    proj = _repo(tmp_path, {
        "tests/test_ok.py": (
            "from pathlib import Path\n"
            "def test_x():\n"
            "    assert Path('.methodology/gate_evidence/x.json') is not None\n"
        ),
    })
    assert evidence_in_cleared_dirs(proj) == []
