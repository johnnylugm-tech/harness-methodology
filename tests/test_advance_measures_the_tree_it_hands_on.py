"""advance-phase must measure the tree it hands to the next phase.

taskq-open, P4 -> P5. `_regen_traceability_views` measures the suite
(`build_traceability` needs its outcomes), then rewrites
01-requirements/TRACEABILITY_MATRIX.md. The TDD precheck that follows asked
`run_suite` again and got the memo: its fingerprint covers `*.py` and test
config, not the view the framework had just rewritten. A project test that
reads the matrix (`test_nfr09_verified_only_when_tests_pass`) failed on the
tree being advanced, the advance passed anyway, and the failure surfaced in
P5 `verification-docs`, which may not touch tests -- run-all halted.

The matrix's NFR table also sat after `<!-- AUTO-GEN:END -->`, where it reads
as hand-owned; the P4 agent hand-edited it, and every regeneration discards
everything after START.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

_START = "<!-- AUTO-GEN:START -->"
_END = "<!-- AUTO-GEN:END -->"

_TEST_READS_VIEW = '''
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_reads_the_view():
    view = ROOT / "01-requirements" / "TRACEABILITY_MATRIX.md"
    assert "`test_reads_the_view`" in view.read_text(encoding="utf-8")
'''

_HAND_EDITED = "# Traceability Matrix\n\n| NFR-09 | `test_reads_the_view` | VERIFIED |\n"
_RENDERED = "# Traceability Matrix\n\n| NFR-09 | test_view.py | VERIFIED |\n"


@pytest.fixture()
def suite_project(tmp_path: Path):
    """A project with one test that reads the matrix view."""
    from core.quality_gate.test_suite_run import reset_suite_cache

    (tmp_path / "03-development" / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "03-development" / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir()
    (tests / "test_view.py").write_text(_TEST_READS_VIEW, encoding="utf-8")
    (tmp_path / "setup.cfg").write_text(
        "[tool:pytest]\ntestpaths = 03-development/tests\n", encoding="utf-8")
    view = tmp_path / "01-requirements" / "TRACEABILITY_MATRIX.md"
    view.parent.mkdir()
    view.write_text(_HAND_EDITED, encoding="utf-8")
    reset_suite_cache()
    yield tmp_path
    reset_suite_cache()


def _view(project: Path) -> Path:
    return project / "01-requirements" / "TRACEABILITY_MATRIX.md"


def test_a_view_the_suite_reads_is_measured_after_it_is_rewritten(suite_project, capsys) -> None:
    """RED before this round: the second `run_suite` returned the first one's pass."""
    from cli.advance_checks import _regen_and_stage_view
    from core.quality_gate.test_suite_run import run_suite

    assert run_suite(suite_project).passed

    _regen_and_stage_view(suite_project, _view(suite_project),
                          lambda p: p.write_text(_RENDERED, encoding="utf-8"))

    assert not run_suite(suite_project).passed
    out = capsys.readouterr().out
    assert "render-only view" in out and "fix the test, not the view" in out


def test_an_unchanged_view_keeps_the_measurement(suite_project, capsys) -> None:
    """A regeneration that writes the same bytes costs no second suite run."""
    from cli.advance_checks import _regen_and_stage_view
    from core.quality_gate.test_suite_run import run_suite

    first = run_suite(suite_project)

    _regen_and_stage_view(suite_project, _view(suite_project),
                          lambda p: p.write_text(_HAND_EDITED, encoding="utf-8"))

    assert run_suite(suite_project) is first
    assert "render-only view" not in capsys.readouterr().out


# -- the rendered NFR table is inside the block every regeneration owns ------

_NFR_TEST = '''
def test_error_envelope():
    """NFR-10: errors carry a stable envelope."""
    assert True
'''


@pytest.fixture()
def nfr_project(tmp_path: Path, monkeypatch) -> Path:
    from core.quality_gate import test_suite_run
    from core.quality_gate.test_suite_run import SuiteResult
    from scripts import build_traceability as bt

    tests = tmp_path / "03-development" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_spec_nfr.py").write_text(_NFR_TEST, encoding="utf-8")
    req = tmp_path / "01-requirements"
    req.mkdir()
    (req / "SRS.md").write_text(
        "# Software Requirements Specification\n\n### NFR-10: integration coverage\n\n"
        "**Acceptance criteria**\n\n- **AC-N10.1**: errors carry a stable envelope.\n",
        encoding="utf-8")
    (tmp_path / "02-architecture").mkdir()
    (tmp_path / "02-architecture" / "TEST_SPEC.md").write_text(
        "# TEST_SPEC.md\n\n### NFR Integration\n\n"
        "| # | Test Function | Inputs | Type | Derivation |\n|---|---|---|---|---|\n"
        "| 1 | `test_error_envelope` | x=\"1\" | integration | AC-N10.1 |\n", encoding="utf-8")

    def _fake(*_a, **_k):
        return SuiteResult(
            passed=True, coverage=None, test_target="03-development/tests",
            cov_target="03-development/src", returncode=0, output="", ran=True,
            skipped=0, test_outcomes={
                "03-development/tests/test_spec_nfr.py::test_error_envelope": "passed"})

    monkeypatch.setattr(test_suite_run, "run_suite", _fake)
    monkeypatch.setattr(bt, "run_suite", _fake)
    return tmp_path


def test_the_nfr_table_is_rendered_inside_the_generated_block(nfr_project) -> None:
    from scripts import build_traceability as bt

    out = nfr_project / "01-requirements" / "TRACEABILITY_MATRIX.md"
    out.write_text(f"# Traceability Matrix\n\nhand-written intro\n\n{_START}\nold\n{_END}\n"
                   "\n## Non-Functional Requirements\n\n| NFR-10 | hand edit | VERIFIED |\n",
                   encoding="utf-8")
    rt = bt.build_traceability(nfr_project)

    bt.generate_markdown_matrix(rt, out)

    text = out.read_text(encoding="utf-8")
    start, end = text.index(_START), text.index(_END)
    assert start < text.index("## Non-Functional Requirements") < end
    assert text[end + len(_END):].strip() == "", "nothing the framework renders sits outside the block"
    assert "hand-written intro" in text[:start]
    assert "hand edit" not in text
    assert any(ln.startswith("| NFR-10 ") and "VERIFIED" in ln for ln in text.splitlines())

    bt.generate_markdown_matrix(rt, out)
    assert out.read_text(encoding="utf-8") == text, "regeneration is idempotent"
