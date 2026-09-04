"""The coverage command the framework documents must produce evidence R9 can read.

Round 95. `harness/ssi/scripts/score.py` rule R9 is the only rule that
re-derives a number from the tool output at the moment the score file is
written — R1/R2/R4/R5/R8 check field presence and internal consistency, so a
self-reported `tool_score` can be wrong from the moment it is typed and
survive until something unrelated happens to re-measure it. R9's own comment
records the incident: a Phase-3/4 self-reported 100.0 was 98.53% when the
documented command was re-run months later.

R9 works by parsing `tool_outputs`, and `_parse_coverage_percent` recognises
exactly two schemas — coverage.py's `coverage json` (`totals.percent_covered`)
and istanbul's coverage-summary.json (`total.lines.pct`). Anything else
returns None and **R9 skips silently**.

Round 94 replaced the documented python command's `coverage json` step with a
`--cov-report=term-missing` table. Measured, on the same claim:

    term output      agent reports 100.0, truth 45.0  ->  R9 issues: none
    coverage json    agent reports 100.0, truth 45.0  ->  R9 issues: R9 fires

Nothing turned red: the rule that catches a wrong number stopped running, and
a rule that stops running looks exactly like a rule with nothing to report.

These tests do not read the command and reason about it. They RUN it against a
throwaway project and hand the artifact to the real parser, because the two
things that have to agree are the command's output format and the parser's
input format, and only executing both can say whether they do.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from harness.ssi.scripts.score import _parse_coverage_percent, validate_score_file

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent
EVAL_DIM = REPO / "harness" / "ssi" / "prompts" / "evaluate_dimension.md"


def _documented_python_coverage_command() -> str:
    """The `python3 -m pytest --cov=…` line from evaluate_dimension.md's
    test_coverage block. One line, or this guard is reading the wrong file."""
    text = EVAL_DIM.read_text(encoding="utf-8")
    start = text.index("### test_coverage (Tier 1)")
    end = text.index("### test_assertion_quality", start)
    lines = [
        ln for ln in text[start:end].splitlines()
        if ln.startswith("python3 -m pytest") and "--cov=" in ln
    ]
    assert len(lines) == 1, (
        f"expected exactly one documented python coverage command, found "
        f"{len(lines)}: {lines}"
    )
    return lines[0]


def _sample_project(root: Path) -> str:
    """A src-layout project with one covered module and one that is not."""
    src = root / "03-development" / "src" / "taskq"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "store.py").write_text(
        "def save(x):\n    return x\n\n\ndef purge():\n    raise NotImplementedError\n",
        encoding="utf-8",
    )
    (src / "other_fr.py").write_text(
        "def f1():\n    pass\n\n\ndef f2():\n    pass\n", encoding="utf-8"
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_fr01.py").write_text(
        "import sys, os\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', "
        "'03-development', 'src'))\n"
        "from taskq.store import save\n\n\n"
        "def test_fr01_save():\n    assert save(1) == 1\n",
        encoding="utf-8",
    )
    return "03-development/src"


def _run(command: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run a documented shell command with this interpreter standing in for
    `python3`, so the run uses the same coverage/pytest-cov the repo pins."""
    return subprocess.run(  # nosec B602 — fixed command text from the repo's own docs
        command.replace("python3 -m", f"{sys.executable} -m"),
        cwd=str(cwd), shell=True, capture_output=True, text=True, timeout=180,
    )


def test_the_documented_command_writes_evidence_score_py_can_parse(tmp_path):
    """End to end: run the documented command, parse what it left behind."""
    cov_target = _sample_project(tmp_path)
    command = _documented_python_coverage_command().replace('"$COV_TARGET"', cov_target)

    result = _run(command, tmp_path)
    assert result.returncode == 0, f"documented command failed:\n{result.stdout}\n{result.stderr}"

    artifact = _artifact_named_in(command, tmp_path)
    assert ".sessi-work/" in str(artifact.relative_to(tmp_path).as_posix()), (
        "the evidence must land in the gitignored scratch dir — `.sessi-work/` "
        "is in the framework's own _GITIGNORE_ENTRIES, the project root is "
        "not, and Round 53 is the round about the framework writing into the "
        f"tree being judged. Got {artifact.relative_to(tmp_path)}"
    )
    percent = _parse_coverage_percent(artifact.read_text(encoding="utf-8"))
    assert percent is not None, (
        "score.py's _parse_coverage_percent does not recognise the schema the "
        "documented command emits — R9 skips silently for every Python project"
    )
    assert 0.0 < percent < 100.0, percent


def test_r9_actually_fires_on_a_wrong_score_backed_by_that_evidence(tmp_path):
    """The property is not "an artifact exists" — it is "R9 can convict".

    Round 46: a witness that cannot testify is not a passing test. This runs
    the documented command, writes a score file claiming 100.0 against it, and
    requires R9 to say so.
    """
    cov_target = _sample_project(tmp_path)
    command = _documented_python_coverage_command().replace('"$COV_TARGET"', cov_target)
    assert _run(command, tmp_path).returncode == 0

    issues = validate_score_file(
        "test_coverage",
        {
            "dimension": "test_coverage", "round": 1,
            "tool_score": 100.0, "score": 100.0,
            "tool_outputs": str(
                _artifact_named_in(command, tmp_path).relative_to(tmp_path)
            ),
        },
        project_root=tmp_path,
    )
    assert any(i.startswith("R9:") for i in issues), (
        "R9 did not fire on a self-reported 100.0 backed by evidence that says "
        f"otherwise — the rule is silent, not satisfied. issues={issues}"
    )


def test_the_term_only_form_is_what_this_guard_refuses(tmp_path):
    """Negative control: the shape Round 94 shipped must be unparseable.

    Without this, a guard asserting "the parser returned a number" could pass
    against any output that happened to contain digits.
    """
    term = (
        "Name                              Stmts   Miss  Cover   Missing\n"
        "--------------------------------------------------------------\n"
        "src/taskq/store.py                    6      1    83%   8\n"
        "--------------------------------------------------------------\n"
        "TOTAL                                11      6    45%\n"
    )
    assert _parse_coverage_percent(term) is None
    artifact = tmp_path / "coverage_raw.txt"
    artifact.write_text(term, encoding="utf-8")
    issues = validate_score_file(
        "test_coverage",
        {"dimension": "test_coverage", "round": 1, "tool_score": 100.0,
         "score": 100.0, "tool_outputs": "coverage_raw.txt"},
        project_root=tmp_path,
    )
    assert not any(i.startswith("R9:") for i in issues), (
        "if R9 fires on term output too, the schema requirement above is not "
        "the thing keeping it alive and this guard is measuring nothing"
    )


def test_the_prompt_names_the_artifact_it_tells_the_agent_to_cite():
    """A produced file nobody is told to cite is a file `tool_outputs` misses.

    The command writing `coverage.json` only helps if the same section tells
    the agent that is the path to record — Round 45: a verdict must be able to
    outlive its proof, which starts with the verdict naming it.
    """
    text = EVAL_DIM.read_text(encoding="utf-8")
    start = text.index("### test_coverage (Tier 1)")
    end = text.index("### test_assertion_quality", start)
    section = text[start:end]
    assert re.search(r"\.sessi-work/coverage\.json", section), (
        "evaluate_dimension.md's test_coverage section does not name "
        ".sessi-work/coverage.json, so nothing tells the agent which file "
        "tool_outputs must point at"
    )
    assert "tool_outputs" in section, (
        "the section writes the artifact but never says it is the one to cite"
    )


def test_the_per_fr_command_also_emits_a_parseable_number(tmp_path):
    """Gate 1 is per-FR, and its printed override is a different command.

    `_print_fr_scoped_overrides_py` writes the commands the Gate-1 evaluator
    runs instead of the project-wide defaults. Its number has the same job and
    the same reader, so it has the same obligation.
    """
    from cli.gate_cmds import _print_fr_scoped_overrides_py

    cov_target = _sample_project(tmp_path)
    (tmp_path / ".methodology").mkdir()
    manifest = {
        "fr_module_traceability": {"FR-01": "taskq.store"},
        "quality_targets": {"min_coverage": 80},
    }
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _print_fr_scoped_overrides_py(
            str(tmp_path), "FR-01", "tests/test_fr01.py", cov_target,
            manifest, non_code_frs=set(), cov_threshold=80,
        )
    printed = buf.getvalue()
    # The printed form is a two-line continuation; rebuild the whole chain.
    chain = " ".join(
        ln.strip().rstrip("\\").strip()
        for ln in printed.splitlines()
        if "python3 -m pytest" in ln or "python3 -m coverage" in ln
    )
    assert "python3 -m pytest" in chain, f"no python coverage command printed:\n{printed}"

    result = _run(chain, tmp_path)
    assert result.returncode == 0, f"{chain}\n{result.stdout}\n{result.stderr}"

    artifact = _artifact_named_in(chain, tmp_path)
    percent = _parse_coverage_percent(artifact.read_text(encoding="utf-8"))
    assert percent is not None, (
        "the per-FR Gate-1 command left no schema score.py can parse, so R9 "
        f"skips for every per-FR gate. Command:\n{chain}"
    )
    # And it must be the FR's own number, not the whole tree's: store.py is
    # 3/4 statements, other_fr.py is untouched. A whole-tree read is 37.5%
    # (3 of 8) — pinned by the negative control below.
    assert percent == pytest.approx(75.0), (
        f"expected FR-01's own 75.0%, got {percent} — the command measured a "
        f"scope that is not this FR's"
    )


def _artifact_named_in(command: str, root: Path) -> Path:
    """The report path the command writes, taken from the command itself.

    Reading the path out of the command rather than hardcoding it here is the
    point: `tool_outputs` has to name a file the command actually wrote, and a
    guard that knows the filename independently would still pass if the two
    drifted apart.
    """
    match = re.search(r"(?:-o|--cov-report=json:)\s*(\S+\.json)", command)
    assert match, f"the command names no .json report to cite:\n{command}"
    path = root / match.group(1)
    assert path.is_file(), f"{match.group(1)} was named but not written:\n{command}"
    return path


def test_the_sample_project_really_has_two_different_numbers(tmp_path):
    """Negative control for the assertion above: whole-tree != per-FR here.

    Also pins the reason stdout cannot be the evidence: pytest writes its own
    summary there first, so a chained `coverage json -o -` produces a file
    that is not JSON at all — the shape that made the first draft of this
    guard fail for the wrong reason.
    """
    cov_target = _sample_project(tmp_path)
    whole = _run(
        f"python3 -m pytest tests/test_fr01.py --cov={cov_target} "
        f"--cov-report= -q && python3 -m coverage json -o whole.json",
        tmp_path,
    )
    assert whole.returncode == 0, whole.stderr
    text = (tmp_path / "whole.json").read_text(encoding="utf-8")
    assert _parse_coverage_percent(text) == pytest.approx(37.5), (
        "if the whole-tree and per-FR numbers were equal, the scope assertion "
        "above would pass without the scope filter doing anything"
    )
    assert json.loads(text)["totals"]["num_statements"] == 8
    assert _parse_coverage_percent(whole.stdout) is None, (
        "pytest's own summary shares stdout with anything chained after it, "
        "so a report written to stdout is not parseable evidence"
    )
