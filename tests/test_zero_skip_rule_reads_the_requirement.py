"""Round 73 站2 — eight projects wrote the zero-skip rule, two were enforced.

`check_srs_mandatory_reconciliation` is the one check that reads a hard SRS
clause and compares it to live measurement. Its docstring says so: no
percentage-scored dimension can enforce a zero-tolerance rule, because
"test_assertion_quality is a 0-100 blend a single skip barely moves".

It finds the clause with two literal phrases, markdown emphasis included:

    r"skipped count is \\*\\*0\\*\\*|report \\*\\*0 skipped\\*\\*"

Measured across the corpus, NFR-09 as each project actually wrote it:

    taskq-plus     must report **0 skipped**                  HIT
    taskq-renew    must report **0 skipped**                  HIT
    taskq-new      reports **skipped = 0**                    miss
    taskq-api      的 **skipped 計數必須為 0**                 miss
    taskq-advance  reports `skipped = 0`                      miss
    taskq-super    **skipped count must / be 0**  (wrapped)   miss
    taskq-cc       reports a skipped count of 0               miss

and `tests/test_phase_truth_verifier.py:367/379/390` write the phrase the
rule wants in all three of their fixtures. The rule and its fixtures share a
source, which is Round 19's mother defect: the guard was green for as long as
nobody wrote the sentence a different way, and six of eight projects did.

The second escape is `_skip_sites`. It globs `test_*.py` and recognises a
`pytest.skip(...)` call or an `@pytest.mark.skip` decorator. taskq-new's ten
FR-02 skips are neither: its PROJECT-ROOT `conftest.py` builds
`pytest.mark.skip(reason=...)` into a local and hands it to
`item.add_marker(...)` from `pytest_collection_modifyitems`. Its
`pyproject.toml` says why in as many words — "They are NOT deselected here on
purpose", because a deselected test leaves junit-xml and Gate 3 traceability
4a counts what junit-xml has. The project read the anti-fabrication list and
picked the shape that was not on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


# Verbatim from each project's 01-requirements/SRS.md NFR-09 section. Not one
# of them is invented here: a fixture written in the vocabulary of the rule is
# what let the rule ship with a 25% hit rate.
_REAL_WORDINGS = [
    pytest.param("- `pytest` output must report **0 skipped**\n", id="taskq-plus"),
    pytest.param("- `pytest 03-development/tests -q` output must report **0 skipped**\n",
                 id="taskq-renew"),
    pytest.param("`pytest tests -q` reports **skipped = 0** (SPEC.md §4 NFR-09).\n",
                 id="taskq-new-en"),
    pytest.param("- `pytest ${SOURCE_ROOT}/tests -q` 的 **skipped 計數必須為 0**\n",
                 id="taskq-api-zh"),
    pytest.param("- **AC-N09.2**: `pytest 03-development/tests -q` reports "
                 "`skipped = 0`. Source: `SPEC.md` §4 NFR-09.\n", id="taskq-advance"),
    pytest.param("`pytest 03-development/tests -q` **skipped count must\nbe 0**. "
                 "Every test function has at least one `assert`.\n", id="taskq-super"),
    pytest.param("#### AC-N9.1: `pytest 03-development/tests -q` reports a "
                 "skipped count of 0\n", id="taskq-cc"),
]


def _srs(tmp_path: Path, nfr_body: str) -> None:
    path = tmp_path / "01-requirements" / "SRS.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# SRS\n\n### NFR-09: 驗證真實性(零 skip 鐵律)\n\n" + nfr_body,
        encoding="utf-8",
    )


@pytest.mark.parametrize("wording", _REAL_WORDINGS)
def test_every_wording_the_corpus_actually_uses_is_read(tmp_path, wording):
    """The measurement, turned into the guard."""
    from core.quality_gate.phase_truth_verifier import _demands_zero_skips

    _srs(tmp_path, wording)
    section = (tmp_path / "01-requirements" / "SRS.md").read_text(encoding="utf-8")
    assert _demands_zero_skips(section), f"not read: {wording!r}"


def test_a_zero_that_is_not_about_skipping_does_not_arm_the_rule(tmp_path):
    """The counter-direction, and why the skeleton is narrow.

    NFR-02 sections in this corpus read "bandit: 0 HIGH / 0 MEDIUM" and go on
    to forbid skipping a bandit rule. A proximity rule alone would arm the
    zero-skip reconciliation against NFR-02 and report a violation there for a
    skip somewhere else entirely. The count word has to belong to the skip:
    `skipped count`, `skipped =`, `0 skipped` — not "0 … skip" at a distance.
    """
    from core.quality_gate.phase_truth_verifier import _demands_zero_skips

    assert not _demands_zero_skips(
        "### NFR-02: Security\n\n"
        "- `bandit -r src/`: **0 HIGH、0 MEDIUM**;不得 skip 任何 bandit 規則\n"
    )
    assert not _demands_zero_skips(
        "### NFR-09: Honesty\n\n"
        "- Every skipped test is recorded in the degradation ledger with a reason\n"
    )


def test_the_rule_reaches_the_verdict_with_a_real_wording(tmp_path):
    """End to end: the clause is read, the measurement is compared, it fails.

    Before this station taskq-new's wording armed nothing, so a suite with
    skips reconciled clean and `check_srs_mandatory_reconciliation` returned
    100.0.
    """
    from unittest.mock import patch

    from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
    from core.quality_gate.test_suite_run import SuiteResult

    _srs(tmp_path, "`pytest tests -q` reports **skipped = 0** (SPEC.md §4 NFR-09).\n")
    result = SuiteResult(  # type: ignore[call-arg]
        passed=True, coverage=100.0, test_target="tests", cov_target="src",
        returncode=0, output="", ran=True, skipped=4,
    )
    with patch("core.quality_gate.test_suite_run.run_suite", return_value=result):
        passed, score, details = PhaseTruthVerifier(
            str(tmp_path), 3).check_srs_mandatory_reconciliation()

    assert passed is False
    assert score == 0.0
    assert "NFR-09" in details and "4 skipped" in details


def test_a_clause_belongs_to_the_requirement_whose_heading_it_sits_under(tmp_path):
    """The last NFR section used to run to the end of the file.

    `re.split(r"(?=^###\\s+NFR-\\d+)")` gave the final NFR everything below it,
    and every SRS in this corpus ends with an "Acceptance Criteria Summary"
    table whose first row is NFR-09's own command:

        | 1 | `pytest ${SOURCE_ROOT}/tests -q` | 全綠,**skipped 計數為 0**(NFR-09) |

    With the skeleton matcher that clause armed NFR-12 in six of seven
    projects — a violation reported against a requirement that never wrote it,
    which is worse than the silence it replaced. Measured after the split
    moved to any H2/H3: seven projects, NFR-09 only, nothing else armed.
    """
    from unittest.mock import patch

    from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
    from core.quality_gate.test_suite_run import SuiteResult

    path = tmp_path / "01-requirements" / "SRS.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "# SRS\n\n"
        "### NFR-09: Honesty\n\n- No skips.\n\n"
        "### NFR-12: System verification\n\n"
        "- `make verify-system` exits 0.\n\n"
        "## 5. Acceptance Criteria Summary\n\n"
        "| # | Command | Expected |\n|---|---|---|\n"
        "| 1 | `pytest tests -q` | all green, skipped count is 0 (NFR-09) |\n",
        encoding="utf-8",
    )
    result = SuiteResult(  # type: ignore[call-arg]
        passed=True, coverage=100.0, test_target="tests", cov_target="src",
        returncode=0, output="", ran=True, skipped=4,
    )
    with patch("core.quality_gate.test_suite_run.run_suite", return_value=result):
        passed, _, details = PhaseTruthVerifier(
            str(tmp_path), 3).check_srs_mandatory_reconciliation()

    assert passed is True, f"the summary table armed a requirement: {details}"


def test_a_marker_built_into_a_local_and_added_later_is_a_skip(tmp_path):
    """taskq-new's shape, reduced to the three lines that carry it.

    The marker never appears in a decorator list, so scanning
    `node.decorator_list` cannot see it. What is decidable is that
    `pytest.mark.skip` was named at all — a project that writes that
    expression has written a skip, wherever it later attaches it.
    """
    from core.quality_gate.phase_truth_verifier import _skip_sites

    (tmp_path / "conftest.py").write_text(
        "import pytest\n"
        "_REASON = 'drain never observes proc.wait() returning here'\n"
        "def pytest_collection_modifyitems(config, items):\n"
        "    skip_marker = pytest.mark.skip(reason=_REASON)\n"
        "    for item in items:\n"
        "        if item.name in _HANG_NAMES:\n"
        "            item.add_marker(skip_marker)\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_ok.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")

    sites = _skip_sites(tmp_path)

    assert len(sites) == 1, sites
    assert "conftest.py:4" in sites[0], sites


def test_a_conftest_beside_the_tests_is_scanned_too(tmp_path):
    """pytest loads every conftest.py from the rootdir down to the test file.

    The project-root one is the case taskq-new hit, and the one in the test
    directory is the same file one level down; scanning only `test_*.py` sees
    neither.
    """
    from core.quality_gate.phase_truth_verifier import _skip_sites

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "conftest.py").write_text(
        "import pytest\n"
        "collect_skip = pytest.mark.skipif(True, reason='n/a here')\n",
        encoding="utf-8",
    )
    (tests / "test_ok.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")

    assert len(_skip_sites(tmp_path)) == 1


def test_a_clean_tree_with_a_conftest_is_still_clean(tmp_path):
    """The counter-direction: a conftest is an ordinary file, not a suspect."""
    from core.quality_gate.phase_truth_verifier import _skip_sites

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "conftest.py").write_text(
        "import pytest\n\n"
        "@pytest.fixture\n"
        "def client():\n    return object()\n",
        encoding="utf-8",
    )
    (tests / "test_ok.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")

    assert _skip_sites(tmp_path) == []
