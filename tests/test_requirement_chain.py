"""The requirement chain has four links; the framework checked one, backwards.

Round 87 站4.

    SPEC -> SRS/SAD -> TEST_SPEC -> delivered source

`canonical_diff._best_match_ratio`'s own docstring states the direction it
scores: "the anti-over-spec goal is to detect A ADDING content NOT in
canonical". Nothing asked what the canonical text declared and never arrived.
taskq-redo's `srs_vs_spec_diff.json` records `invention_count: 0,
high_score_count: 22` — full marks — over an SRS that dropped `rate_buckets`
entirely and reduced `TASKQ_DB_URL` to NFR-04's "must not appear in logs".
SPEC.md's own line 24 asks for "no invention, **no omission**".

THE SHAPE THAT SURVIVED THREE MEASUREMENTS

Three drafts of the extractor were tried against the eight corpus projects,
all built from the same SPEC.md, and the first two were discarded by what they
measured rather than by review:

  1. every backtick identifier in a SPEC table — 81 names, roughly half of them
     the framework's OWN vocabulary (dimension names out of the
     `framework 對齊` table)
  2. minus the framework's dimension registry, restricted to names the SRS
     acknowledges — still `DEBUG` / `INFO` / `WARNING` / `ERROR` (log-level
     VALUES), `TBD`, `TODO`, `Makefile`, `created_at`; fired 2-16 times per
     project and did not separate the project that failed from the ones that
     did not
  3. ALL-CAPS with an underscore — twelve keys in every project, zero noise,
     and **taskq-cc reads all twelve**

That clean sheet is the evidence the rule discriminates. A check that fires on
every project measures the corpus, not the defect.

THE OTHER HALF: SRS HAS TWO READERS

`_without_machine_block` (Round 42 站1) STRIPS the fenced JSON before scoring
conformance. `scripts/plangen/artifact_parsers` reads ONLY that JSON. So a
requirement can be complete in the half nobody scores and absent from the half
everything downstream quotes — measured twice in taskq-redo: FR-05's
`per-token` and NFR-12's four chained steps both live only in the block.
Reported rather than blocked, because the same rule finds 4 in taskq-cc, which
implemented `per-token` correctly anyway.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.quality_gate.spec_alignment import check_spec_alignment, spec_config_keys
from scripts.canonical_diff import build_diff_report, machine_block_parity

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]
CORPUS = Path("/Users/johnny/projects")

_SPEC_TABLE = """\
## 5. Configuration

### 5.1 Environment

| Var | Default | Notes |
|---|---|---|
| `TASKQ_DB_URL` | `sqlite:///./taskq.db` | connection string |
| `TASKQ_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `TASKQ_PORT` | `8000` | listen port |

### 5.2 Schema

| Table | revision | Columns |
|---|---|---|
| `rate_buckets` | v1 | `key_id`, `tokens`, `updated_at` |
"""


def test_a_declared_key_is_a_key_not_one_of_its_values() -> None:
    """`TASKQ_LOG_LEVEL` defaults to `INFO`; `INFO` is not a key.

    Draft 2 of this extractor could not tell them apart, and reported
    `DEBUG` / `INFO` / `WARNING` / `ERROR` against every project including the
    ones that were right. The underscore is what makes the distinction
    structural instead of a word list.
    """
    keys = spec_config_keys(_SPEC_TABLE)
    assert keys == {"TASKQ_DB_URL", "TASKQ_LOG_LEVEL", "TASKQ_PORT"}, keys
    for value in ("INFO", "DEBUG", "WARNING", "ERROR"):
        assert value not in keys
    for other in ("rate_buckets", "key_id", "updated_at"):
        assert other not in keys, (
            f"{other} is a schema name, not a configuration key — a rule that "
            f"collects both is the draft that fired 2-16 times per project"
        )


def _project(tmp_path: Path, *, src: str | None) -> Path:
    (tmp_path / "01-requirements").mkdir(parents=True)
    (tmp_path / "01-requirements" / "SRS.md").write_text(
        "## 3. Requirements\n\n### FR-01: config\n\nAC-1.1 reads config.\n",
        encoding="utf-8")
    (tmp_path / "SPEC.md").write_text(
        "### FR-01: config\n\n" + _SPEC_TABLE, encoding="utf-8")
    if src is not None:
        d = tmp_path / "03-development" / "src"
        d.mkdir(parents=True)
        (d / "config.py").write_text(src, encoding="utf-8")
    return tmp_path


def test_a_key_the_source_never_reads_is_blocking(tmp_path: Path) -> None:
    """taskq-redo's `TASKQ_DB_URL`, exactly: declared, and hardcoded past."""
    project = _project(tmp_path, src=(
        'import os\nDB = "sqlite:///file:shared?mode=memory"\n'
        'PORT = os.environ.get("TASKQ_PORT", "8000")\n'
        'LEVEL = os.environ.get("TASKQ_LOG_LEVEL", "INFO")\n'
    ))
    unread = [v for v in check_spec_alignment(project)
              if v.check_type == "unread_config_key"]
    assert [v.rule_id for v in unread] == ["TASKQ_DB_URL"], unread
    assert unread[0].severity == "error"


def test_every_key_read_is_no_finding(tmp_path: Path) -> None:
    project = _project(tmp_path, src=(
        'import os\n'
        'DB = os.environ["TASKQ_DB_URL"]\n'
        'PORT = os.environ["TASKQ_PORT"]\n'
        'LEVEL = os.environ["TASKQ_LOG_LEVEL"]\n'
    ))
    assert [v for v in check_spec_alignment(project)
            if v.check_type == "unread_config_key"] == []


def test_no_source_tree_is_not_a_finding(tmp_path: Path) -> None:
    """Self-gating by artifact presence, the way this module decides everything.

    Phase 1 and 2 have no `03-development/src`; a key cannot have been read by
    a tree that does not exist, and reporting it there would block every
    project at P1 for work Phase 3 has not started.
    """
    project = _project(tmp_path, src=None)
    assert [v for v in check_spec_alignment(project)
            if v.check_type == "unread_config_key"] == []


def _empty_src_project(tmp_path: Path) -> Path:
    """Build a project whose `03-development/src/` exists but is empty.

    Companion setup to `_project(src=None)`: that helper covers the
    "no source directory" case; this one covers "directory exists but
    no Python source files" — the boundary `init-project` actually
    scaffolds today and the boundary every previous corpus project
    silently skipped.
    """
    (tmp_path / "01-requirements").mkdir(parents=True)
    (tmp_path / "01-requirements" / "SRS.md").write_text(
        "## 3. Requirements\n\n### FR-01: config\n\nAC-1.1 reads config.\n",
        encoding="utf-8")
    (tmp_path / "SPEC.md").write_text(
        "### FR-01: config\n\n" + _SPEC_TABLE, encoding="utf-8")
    (tmp_path / "03-development" / "src").mkdir(parents=True)
    return tmp_path


def test_empty_src_tree_is_not_a_finding(tmp_path: Path) -> None:
    """Self-gating must NOT fire when the src dir exists but is empty.

    Companion to `test_no_source_tree_is_not_a_finding`. `init-project`
    scaffolds an empty `03-development/src/`, and without this guard
    every project would fail at P2-entry for work Phase 3 has not
    started — `taskq-done`'s first P1→P2 attempt blocked exactly here
    (wf_ea662c2c-3ce halt_step=advance-phase).
    """
    project = _empty_src_project(tmp_path)
    assert [v for v in check_spec_alignment(project)
            if v.check_type == "unread_config_key"] == []


def test_the_report_and_the_gate_share_one_extractor() -> None:
    """One document, one answer to "which keys does it declare".

    `canonical_diff` reports the set to Agent B and `spec_alignment` blocks on
    it. A second regex in the reporting half is how a document comes to have
    two answers — the defect this whole round is about, and the reason Round
    86 站3 made `structural_fr_ids` public rather than copying it.
    """
    src = (REPO / "scripts" / "canonical_diff.py").read_text(encoding="utf-8")
    assert "spec_config_keys" in src, (
        "canonical_diff no longer imports the shared extractor"
    )
    assert "A-Z][A-Z0-9]*(?:_[A-Z0-9]+)" not in src, (
        "canonical_diff has grown its own copy of the config-key pattern"
    )


def test_machine_block_terms_absent_from_prose_are_reported() -> None:
    """The half nobody scores, said out loud."""
    srs = (
        "## 3. Requirements\n\n"
        "### FR-05: rate limiting\n\n"
        "- **AC-5.1** over limit returns 429.\n\n"
        "## 9. Machine block\n\n"
        "```json\n"
        '{"functional_requirements": [{"id": "FR-05", '
        '"description": "per-token DB-backed token bucket; 429 + Retry-After"}]}\n'
        "```\n"
    )
    found = machine_block_parity(srs)
    assert found, "the block says per-token and the prose does not"
    assert found[0]["id"] == "FR-05"
    assert "per-token" in found[0]["terms_only_in_machine_block"]


def test_the_parity_finding_reaches_the_report(tmp_path: Path) -> None:
    """Computing it and not putting it in the report is a producer with no reader.

    The first version of this guard called `machine_block_parity` directly and
    asserted on its return value, so replacing the report's field with a
    literal `[]` left it green — the counter-proof found that, not review. The
    report is what Agent B reads as DOC 3; the function's return value is read
    by nothing on its own.
    """
    project = _project(tmp_path, src="X = 1\n")
    srs = project / "01-requirements" / "SRS.md"
    srs.write_text(
        "## 3. Requirements\n\n"
        "### FR-01: config\n\n"
        "- **AC-1.1** reads config.\n\n"
        "## 9. Machine block\n\n"
        "```json\n"
        '{"functional_requirements": [{"id": "FR-01", '
        '"description": "per-token DB-backed bucket"}]}\n'
        "```\n",
        encoding="utf-8")
    report = build_diff_report(srs, project / "SPEC.md")
    assert report["machine_block_parity"], (
        "the report's machine_block_parity is empty for an SRS whose block "
        "says `per-token` and whose prose does not — the field is not wired "
        "to its producer"
    )
    assert report["machine_block_parity"][0]["id"] == "FR-01"


def test_the_new_report_keys_precede_per_ac(tmp_path: Path) -> None:
    """Head truncation must not be what decides whether they are read.

    Round 86 measured `srs_vs_spec_diff.json` at 27,762 bytes against a
    24,576-byte relay ceiling, and `per_ac` is the part that grows without
    bound. Same reason `fr_coverage` sits where it does.
    """
    project = _project(tmp_path, src="X = 1\n")
    report = build_diff_report(
        project / "01-requirements" / "SRS.md", project / "SPEC.md")
    keys = list(report)
    assert keys.index("config_keys") < keys.index("per_ac")
    assert keys.index("machine_block_parity") < keys.index("per_ac")


def test_the_rule_discriminates_on_the_corpus() -> None:
    """taskq-cc reads all twelve; taskq-redo misses six including DB_URL.

    This is the measurement that chose the rule's shape over two earlier
    drafts. If taskq-cc ever reports a finding here, the rule has drifted back
    toward the ones that fired on every project.
    """
    expected_clean = CORPUS / "taskq-cc"
    expected_dirty = CORPUS / "taskq-redo"
    if not (expected_clean / "SPEC.md").exists():
        pytest.skip("corpus projects not present on this machine")

    clean = [v.rule_id for v in check_spec_alignment(expected_clean)
             if v.check_type == "unread_config_key"]
    assert clean == [], (
        f"taskq-cc reads every declared key; a rule reporting {clean} here is "
        f"measuring the corpus rather than the defect"
    )
    dirty = [v.rule_id for v in check_spec_alignment(expected_dirty)
             if v.check_type == "unread_config_key"]
    assert "TASKQ_DB_URL" in dirty, (
        "TASKQ_DB_URL is hardcoded past in taskq-redo's session.py and is the "
        "Production-ready finding this check exists for"
    )
