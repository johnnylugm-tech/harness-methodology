"""Two things a delivery states that nothing checked.

Round 97.

D — the architecture score is measured on a floor the project may lower, and
the framework never compares the two. `crg_analysis.COHESION_HEALTHY` is 0.3,
and `harness_config`'s own docstring gives the single legitimate reason to go
below it: "Small packages (<= ~10 source files) may calibrate below the 0.3
default because Leiden community detection over-fragments at that scale."
Measured across the corpus:

    projects with crg_cohesion_healthy set     11 / 11   (0.15 - 0.25)
    their source_files                         41 - 65
    matching the documented reason              0 / 11
    QUALITY_REPORT.md that says so              0 / 11

taskq-final's report lists communities at 0.22 and 0.29 — both below the
default — beside an architecture score of 100.0, and never states that the
floor was moved to 0.2. Round 42 站4 already put `cohesion_healthy` and
`source_files` into the gate result's `calibration` block; no line of code
compares them, and no human-facing artefact carries them.

Blocking on this reds every project in the corpus. That is a deliberate
decision taken with the 11/11 number in hand, and the ledger records it as
evidence that 0.3 may be the wrong default — this round does not invent a new
one, because a threshold reverse-engineered from the corpus is the ruler being
fitted to the data.

E — a P8 config record names a module that does not exist.
`08-config/CONFIG_RECORDS.md` is where the delivery states how to start.
taskq-final's says `uvicorn taskq_api.main:app`; the package has `app.py` and
`__main__.py` and no `main.py`, so following the document raises
ModuleNotFoundError before the process starts. Measured across the corpus:
one project names a module that resolves (`taskq_api.app`), one names one that
does not, nine name none. The first draft of this check also flagged
`config.py` — a filename inside backticks looks exactly like a dotted module —
which is why a trailing known extension is excluded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


# ── D: the lowered floor ────────────────────────────────────────────────────

# Round 113 站L retired the predicate these tests pinned
# (`lowered_cohesion_floor_reason`): cohesion is no longer scored, so a floor
# the project lowered moves nothing the gate reads. What remains of D is the
# report — the floor still draws the low-cohesion list, and the report says so.
#   test_harness_bridge.py::TestFinalizeGate
#       ::test_a_lowered_cohesion_floor_does_not_block_once_cohesion_is_not_scored


def _calibration(cohesion: float, source_files: int) -> dict:
    return {"cohesion_healthy": cohesion, "community_oversized": 50,
            "graph_files": source_files, "source_files": source_files}


def _report_project(tmp_path: Path, calibration: dict | None) -> Path:
    import json as _json

    project = tmp_path / "proj"
    (project / ".methodology").mkdir(parents=True)
    (project / ".sessi-work").mkdir(parents=True)
    (project / ".methodology" / "quality_manifest.json").write_text(
        _json.dumps({"schema_version": "1.0", "fr_ids": [],
                     "gate_results": {}}), encoding="utf-8")
    arch: dict = {"score": 100.0, "threshold": 80.0}
    if calibration is not None:
        arch["calibration"] = calibration
    (project / ".sessi-work" / "gate4_result.json").write_text(
        _json.dumps({"score": 92.0, "breakdown": {"architecture": arch}}),
        encoding="utf-8")
    return project


def test_the_report_carries_the_floor_the_score_was_measured_on(tmp_path):
    """Round 42 站4, one layer out: the number reaches the gate JSON and stops.

    A reader of the delivery sees communities at 0.22 and a perfect
    architecture score, with nothing saying the two are consistent because the
    floor moved.

    Rendered end to end, not by calling the helper. The first version of this
    called `_calibration_note` directly, and its counter-proof — deleting the
    line in `generate_quality_report` that emits the note — left it green.
    The note existing is not the report carrying it.
    """
    from scripts.generate_quality_report import generate_quality_report

    out = Path(generate_quality_report(
        str(_report_project(tmp_path, _calibration(0.2, 54)))))
    text = out.read_text(encoding="utf-8")
    assert "0.2" in text and "0.3" in text, (
        "the report states an architecture score without the floor it was "
        "measured on"
    )


def test_a_report_on_the_default_floor_says_nothing(tmp_path):
    """Negative control: a note that fires on every delivery is not a note."""
    from scripts.generate_quality_report import _calibration_note, generate_quality_report

    assert _calibration_note(_calibration(0.3, 54)) == ""
    assert _calibration_note(None) == ""
    out = Path(generate_quality_report(
        str(_report_project(tmp_path, _calibration(0.3, 54)))))
    assert "cohesion floor" not in out.read_text(encoding="utf-8")


# ── E: the module a config record names ─────────────────────────────────────

def _config_records(project: Path, body: str) -> Path:
    d = project / "08-config"
    d.mkdir(parents=True, exist_ok=True)
    (d / "CONFIG_RECORDS.md").write_text(body, encoding="utf-8")
    src = project / "03-development" / "src" / "taskq_api"
    src.mkdir(parents=True, exist_ok=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "app.py").write_text("app = 1\n", encoding="utf-8")
    return project


def test_a_named_module_that_does_not_exist_is_reported(tmp_path):
    """taskq-final's `uvicorn taskq_api.main:app` against a package with
    `app.py`."""
    from core.quality_gate.cross_artifact import check_named_modules_resolve

    _config_records(tmp_path, "| Production | `uvicorn taskq_api.main:app --workers 4` |\n")
    v = check_named_modules_resolve(tmp_path, 8)
    assert v, "the delivery names a module that is not there and nothing said so"
    assert "taskq_api.main" in v[0]["issue"]
    assert "app.py" in v[0]["suggestion"], (
        "the report does not name the file that is there, which is the fix"
    )


def test_a_named_module_that_exists_is_silent(tmp_path):
    """taskq-cc-new names `taskq_api.app`, which resolves."""
    from core.quality_gate.cross_artifact import check_named_modules_resolve

    _config_records(tmp_path, "| Production | `uvicorn taskq_api.app:app` |\n")
    assert check_named_modules_resolve(tmp_path, 8) == []


def test_a_filename_where_a_module_goes_is_not_an_accusation(tmp_path):
    """A trailing file extension makes it a filename, and this check does not
    grade spelling.

    The counter-proof for this rewrote it. The first draft's probe took any
    dotted path inside backticks and flagged taskq-cc's prose `config.py`;
    the shipped extractor requires `:attr` or `-m`, so that prose never
    reaches the suffix filter and the original fixture proved nothing —
    removing the filter left it green. What does reach it is a launch command
    spelled with the file rather than the module: `uvicorn main.py:app` yields
    `main.py`, and `python -m config.py` yields `config.py`.

    Dropping those is the limit that keeps the false-positive rate at zero
    across the corpus. It is also this check's second blind spot, beside the
    unverified `<attr>`: a launch command that names the file instead of the
    module is broken, and saying so is a spelling judgement on the command,
    not a statement about whether the module is there.
    """
    from core.quality_gate.cross_artifact import check_named_modules_resolve

    p = _config_records(tmp_path, "| Production | `uvicorn main.py:app` |\n")
    (p / "03-development" / "src" / "main.py").write_text("app = 1\n",
                                                          encoding="utf-8")
    assert check_named_modules_resolve(tmp_path, 8) == []

    _config_records(tmp_path, "Run `python -m config.py` to load settings.\n")
    assert check_named_modules_resolve(tmp_path, 8) == []


def test_prose_that_names_no_command_is_not_read(tmp_path):
    """A backticked filename in a sentence is not a launch command."""
    from core.quality_gate.cross_artifact import check_named_modules_resolve

    _config_records(tmp_path, "Settings live in `config.py` and `pyproject.toml`.\n")
    assert check_named_modules_resolve(tmp_path, 8) == []


def test_python_dash_m_is_read_too(tmp_path):
    """`python -m taskq_api.cli` is the other way a delivery names a module."""
    from core.quality_gate.cross_artifact import check_named_modules_resolve

    _config_records(tmp_path, "Run `python -m taskq_api.nope` to migrate.\n")
    v = check_named_modules_resolve(tmp_path, 8)
    assert v and "taskq_api.nope" in v[0]["issue"]


def test_a_package_directory_resolves(tmp_path):
    """`pkg.sub` where sub/ is a package, not a module file."""
    from core.quality_gate.cross_artifact import check_named_modules_resolve

    p = _config_records(tmp_path, "Run `python -m taskq_api.api`.\n")
    api = p / "03-development" / "src" / "taskq_api" / "api"
    api.mkdir(parents=True, exist_ok=True)
    (api / "__init__.py").write_text("", encoding="utf-8")
    assert check_named_modules_resolve(tmp_path, 8) == []


def test_earlier_phases_are_not_judged_on_a_phase_eight_document(tmp_path):
    """Negative control: this is Phase 8's artefact."""
    from core.quality_gate.cross_artifact import check_named_modules_resolve

    _config_records(tmp_path, "| Production | `uvicorn taskq_api.main:app` |\n")
    assert check_named_modules_resolve(tmp_path, 4) == []


def test_no_config_records_is_not_an_accusation(tmp_path):
    from core.quality_gate.cross_artifact import check_named_modules_resolve

    assert check_named_modules_resolve(tmp_path, 8) == []


def test_python_dash_m_stdlib_and_tooling_are_not_accused(tmp_path):
    """Commands like `python -m venv .venv`, `python -m pip install`,
    `python -m http.server 8000` are standard tooling / stdlib, not missing
    project deliverables."""
    from core.quality_gate.cross_artifact import check_named_modules_resolve

    _config_records(tmp_path, (
        "Setup:\n"
        "- `python -m venv .venv`\n"
        "- `python3 -m pip install -r requirements.txt`\n"
        "- `python -m http.server 8000`\n"
        "- `python -m unittest discover`\n"
    ))
    assert check_named_modules_resolve(tmp_path, 8) == []

