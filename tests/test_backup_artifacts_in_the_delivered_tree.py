"""Round 72 站7 — one `git add -A` and the scope guard goes blind.

`cli/phase_cmds.py::_scope_violation_scripts` has guarded this ground since a
workflow advance agent stranded `_diag_constitution.py` at a repo root. It asks
`git status --porcelain` for UNTRACKED files — and the gate/release path
commits with `git add -A`. One commit later the same file is tracked and that
check can never see it again: the detector runs at a moment strictly after the
thing it detects has been made permanent.

taskq-new shipped two through P1-P8 and a Gate 4 of 94.6:

    03-development/src/taskq/migrations/versions/
        v3_split_result_json_to_task_results.py.bak   added by 5b3db94
    file:does-not-exist.db                            added by bc5c519
                                                      "test(P4): Gate3 PASS"

and a third, `repository/results.py.bak`, from the mutmut incident Round 71
站1 closed at its source — `_custody_paths` has recorded `<file>.bak` siblings
as absent and deleted them on restore since Round 53 站1, but the twin that
runs in production had no custody until d552fc35.

`file:does-not-exist.db` is deliberately not matched. A rule for "the filename
looks like a URI" would be a judgement invented here rather than one applied;
it is recorded in docs/PROPOSAL_ADJUDICATIONS.md.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


def _repo(tmp_path: Path, files: list[str], *, commit: bool = True) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "-C", str(proj), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(proj), "config", "user.email", "t@e.com"], check=True)
    subprocess.run(["git", "-C", str(proj), "config", "user.name", "t"], check=True)
    for rel in files:
        path = proj / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
    if commit:
        subprocess.run(["git", "-C", str(proj), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(proj), "commit", "-qm", "release"], check=True,
        )
    return proj


def test_a_committed_backup_is_still_found(tmp_path):
    """The whole point: being tracked is not being invisible."""
    from core.utils.delivery_scope import backup_artifacts

    proj = _repo(tmp_path, [
        "src/app/results.py",
        "src/app/results.py.bak",
        "src/app/migrations/v3.py.bak",
    ])
    assert backup_artifacts(proj) == [
        "src/app/migrations/v3.py.bak",
        "src/app/results.py.bak",
    ]


def test_every_backup_suffix_is_matched_at_any_depth(tmp_path):
    """`.bak` is mutmut's, `.orig`/`.rej` a failed patch's, `~` an editor's.

    Depth is unrestricted here and top-level-only in the sibling check, and
    the difference is deliberate: recursing on debug NAMES would flag
    legitimate new modules mid-phase, while a `.bak` is never a legitimate
    new module at any depth.
    """
    from core.utils.delivery_scope import BACKUP_SUFFIXES, backup_artifacts

    # Written out, not read from BACKUP_SUFFIXES. The first version of this
    # test built its fixture from the constant, so shrinking the constant
    # shrank the fixture with it and the test stayed green — the fixture and
    # the rule sharing a source is Round 19's mother defect, and the
    # counter-proof for this station is what found it.
    expected = {".bak", ".orig", ".rej", "~"}
    assert set(BACKUP_SUFFIXES) == expected, (
        "a suffix was added or removed; decide whether it belongs and update "
        "this list deliberately"
    )
    proj = _repo(tmp_path, [f"a/b/c/mod{sfx}" for sfx in sorted(expected)])
    assert len(backup_artifacts(proj)) == len(expected)


def test_ordinary_source_is_not_a_leftover(tmp_path):
    """The counter-direction: eight of the nine projects here report zero,
    and a guard that fired on them would stop every one."""
    from core.utils.delivery_scope import backup_artifacts

    proj = _repo(tmp_path, [
        "src/app/backup.py",          # "backup" in the NAME is not a suffix
        "src/app/rejections.py",
        "docs/original.md",
        "src/app/__init__.py",
    ])
    assert backup_artifacts(proj) == []


def test_the_advance_prechecks_refuse_and_name_each_file(tmp_path, capsys):
    """A detector with no executor is Round 43's defect."""
    from cli import phase_cmds

    proj = _repo(tmp_path, ["src/app/results.py", "src/app/results.py.bak"])
    rc = phase_cmds._advance_prechecks(proj, 4)

    out = capsys.readouterr().out
    assert rc == 21, f"expected the WRITE_SCOPE refusal, got {rc}\n{out}"
    assert "src/app/results.py.bak" in out
    assert "Fix:" in out


def test_a_uri_shaped_filename_is_recorded_not_matched(tmp_path):
    """taskq-new's `file:does-not-exist.db`, and the decision not to guess.

    It reached the tree the same way and through the same blind spot, but
    matching it needs a rule about what a filename "looks like" — inventing a
    judgement rather than applying one. Pinned so the omission is a decision
    on record and not an oversight the next round quietly widens.
    """
    from core.utils.delivery_scope import backup_artifacts

    proj = _repo(tmp_path, ["file:does-not-exist.db"])
    assert backup_artifacts(proj) == []
