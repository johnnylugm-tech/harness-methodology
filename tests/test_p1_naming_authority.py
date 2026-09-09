"""P1 wrote down which test names the project declared; nobody read it back.

Round 111 站F1. `cli/advance_prechecks.py` has hashed TEST_INVENTORY.yaml at
the P1 exit and stored the digest in `state.json.test_inventory_checksum`
since v2.6.1. Searched the whole tree: one writer, zero readers — the only
other occurrences are `tests/test_test_compliance.py` asserting the write
happened and two golden snapshots.

What the digest exists to protect is `spec_coverage`'s P1 Naming Authority
check: every name in TEST_INVENTORY.yaml must appear in TEST_SPEC.md, or the
run is BLOCKED at 0.0. That check reads the working-tree file, so shrinking
the declaration passes it. Measured over the 21 corpus projects: 15 carry a
digest that no longer matches their file, and taskq-new went from 100
declared names at P1 to 50 today — replayed against its P1 declaration the
verdict is BLOCKED, against today's it passes.

The answer is NOT to block on shrinkage. Checked what taskq-new actually
retracted: `test_fr10_ac5_status_mapping_422_401_403_404_409_429_503_500`
exists in its TEST_SPEC.md as eight separate rows (`..._422`, `..._401`, …),
and `test_fr02_ac3_state_machine_done_failed_timeout` as two. Splitting one
declaration into the tests it always meant is refinement, and blocking it
would be Round 46's false accusation aimed at the only project that moved.
So the movement is reported and recorded; the verdict is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from core.quality_gate import spec_coverage
from core.quality_gate.naming_authority import frozen_declarations


_INVENTORY_P1 = """\
fr_tests:
  FR-10:
    integration:
      - test_fr10_ac5_status_mapping_422_401_403_404_409_429_503_500
cross_cutting:
  security:
    - test_security_rate_limit_headers
"""

_INVENTORY_TODAY = """\
fr_tests:
  FR-10:
    integration:
      - test_fr10_ac5_status_mapping_422
      - test_fr10_ac5_status_mapping_401
cross_cutting:
  security:
    - test_security_rate_limit_headers
"""

_TEST_SPEC = """\
# TEST_SPEC.md

## FR-10: status mapping

| # | Test Function | Type | Derivation |
|---|---|---|---|
| 1 | `test_fr10_ac5_status_mapping_422` | happy | SPEC §8 #1 |
| 2 | `test_fr10_ac5_status_mapping_401` | happy | SPEC §8 #2 |
| 3 | `test_security_rate_limit_headers` | security | SPEC §9 #1 |
"""


def _write_state(project: Path, **keys: object) -> None:
    (project / ".methodology").mkdir(parents=True, exist_ok=True)
    (project / ".methodology" / "state.json").write_text(
        json.dumps(keys), encoding="utf-8")


def _git(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=project, check=True,
                          capture_output=True, text=True)


@pytest.fixture()
def split_project(tmp_path: Path) -> Path:
    """taskq-new's shape: P1 declared one name, the tree now declares its parts."""
    (tmp_path / "02-architecture").mkdir(parents=True)
    (tmp_path / "02-architecture" / "TEST_SPEC.md").write_text(
        _TEST_SPEC, encoding="utf-8")
    (tmp_path / "03-development" / "tests").mkdir(parents=True)
    (tmp_path / "03-development" / "tests" / "test_fr10.py").write_text(
        "def test_fr10_ac5_status_mapping_422():\n    assert True\n"
        "def test_fr10_ac5_status_mapping_401():\n    assert True\n"
        "def test_security_rate_limit_headers():\n    assert True\n",
        encoding="utf-8")
    (tmp_path / "TEST_INVENTORY.yaml").write_text(_INVENTORY_P1, encoding="utf-8")

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@x")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "add", "TEST_INVENTORY.yaml")
    _git(tmp_path, "commit", "-q", "-m", "p1")
    sha = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()

    (tmp_path / "TEST_INVENTORY.yaml").write_text(_INVENTORY_TODAY, encoding="utf-8")
    _write_state(tmp_path, phase_completed={"1": {"sha": sha}})
    return tmp_path


def test_the_p1_checksum_has_a_reader(tmp_path: Path):
    """A digest that still matches its file answers the question by itself.

    The fast path is the reader `test_inventory_checksum` never had. It is
    also the only path available to a project that is not a git repository,
    which is why this fixture deliberately is not one: remove the fast path
    and this returns None because `git show` has nothing to run against.
    """
    (tmp_path / "TEST_INVENTORY.yaml").write_text(_INVENTORY_TODAY, encoding="utf-8")
    digest = hashlib.sha256(
        (tmp_path / "TEST_INVENTORY.yaml").read_bytes()).hexdigest()
    _write_state(tmp_path, test_inventory_checksum=digest)

    names, why = frozen_declarations(tmp_path)

    assert names == {
        "test_fr10_ac5_status_mapping_422",
        "test_fr10_ac5_status_mapping_401",
        "test_security_rate_limit_headers",
    }
    assert "checksum" in why


def test_an_unreachable_p1_declaration_is_absent_not_empty(tmp_path: Path):
    """No digest, no P1 sha, no git: `None` with a reason, never `set()`.

    Round 35. An empty frozen set would report every name in the file as
    newly added — a measurement that could not be made, published as one that
    was.
    """
    (tmp_path / "TEST_INVENTORY.yaml").write_text(_INVENTORY_TODAY, encoding="utf-8")
    _write_state(tmp_path)

    names, why = frozen_declarations(tmp_path)

    assert names is None
    assert why


def test_a_split_declaration_is_reported_not_blocked(split_project: Path, capsys):
    """The retraction is named in the output and changes no verdict."""
    code, pct = spec_coverage._run_spec_coverage_check(
        split_project, threshold=0.0, verbose=True)

    assert (code, pct) != (1, 0.0), (
        "blocking a project for splitting one declared name into the two tests "
        "it always meant is a false accusation aimed at the only corpus project "
        "whose declaration moved"
    )
    out = capsys.readouterr().out
    assert "test_fr10_ac5_status_mapping_422_401_403_404_409_429_503_500" in out
    assert "retracted" in out


def test_the_movement_reaches_the_ledger(split_project: Path):
    """Recorded once per advance, at the site that owns the P1 baseline.

    Not inside `_run_spec_coverage_check`: that function runs several times
    per phase (the CLI check, `spec_tracking_checker`, each per-FR pass), and
    a ledger row per call is a ledger nobody reads.
    """
    from cli.advance_prechecks import _precheck_manifest_and_p1_baselines

    rc = _precheck_manifest_and_p1_baselines(
        {"passed": True}, 2, split_project)

    assert rc is None, "reporting a retraction must not stop the advance"
    rows = [
        json.loads(line)
        for line in (split_project / ".methodology" / "degradations.jsonl")
        .read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    naming = [r for r in rows if r["component"] == "spec-coverage:naming-authority"]
    assert len(naming) == 1
    assert naming[0]["owner"] == "project"
    assert naming[0]["data"]["retracted"] == [
        "test_fr10_ac5_status_mapping_422_401_403_404_409_429_503_500"
    ]
