"""Where the ADR lives is one question, and the framework had two answers.

Round 97. `init-project` deploys `templates/ADR.md` to
`02-architecture/adr/ADR.md` (`cli/project_cmds.py`'s artifact map), and
`legal_artifacts.DELIVERABLE_ANCHORS` keys the ADR anchor on that same path.
`ProjectLayout.adr_path` returns `02-architecture/ADR.md` — a plain join,
deliberately, so `sab_amender` has a canonical path to write to when the file
does not exist yet. `artifact_consistency._adr_path` is the only resolver that
knows both layouts: `adr/` if present, else the architecture dir.

So the writer and the reader do not point at the same file. Measured across
the eleven corpus projects:

    real ADR at 02-architecture/adr/ADR.md   11/11   (277-1069 lines)
    ProjectLayout.adr_path points there       0/11

Two projects have already paid for it. `amend-sab --resolve-phantom` appends
through `adr_path`, "creating it if absent", so it created one:

    taskq-final  02-architecture/ADR.md    8 lines   one amendment record
    taskq-new    02-architecture/ADR.md   36 lines   amendment records

Their real ADRs are 893 and 519 lines, in the other directory. The eight-line
file then satisfies the required-artifact check at the path the framework's
own `ProjectLayout` calls the ADR, and anyone reading through that API gets an
amendment log instead of the architecture decisions. The other nine projects
are one `amend-sab` away from the same file.

The docstring's reason for the plain join is real — a writer needs a path for
a file that does not exist yet. What was missing is that it never asked where
the existing one is. Both are answerable at once, and both layouts stay
supported: nothing here forces a project to move its ADR.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent

#: Where init-project deploys it, and what DELIVERABLE_ANCHORS is keyed on.
_DEPLOYED = Path("02-architecture") / "adr" / "ADR.md"
_LEGACY = Path("02-architecture") / "ADR.md"


def _arch(root: Path) -> Path:
    d = root / "02-architecture"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_the_deployed_layout_is_where_both_ends_look(tmp_path):
    """11/11 corpus projects: the real ADR is here and the writer was not."""
    from core.quality_gate.artifact_consistency import _adr_path
    from core.utils.project_layout import ProjectLayout

    (_arch(tmp_path) / "adr").mkdir()
    (tmp_path / _DEPLOYED).write_text("# Architecture Decision Records\n", encoding="utf-8")

    assert ProjectLayout(tmp_path).adr_path == tmp_path / _DEPLOYED
    assert _adr_path(tmp_path) == tmp_path / _DEPLOYED


def test_a_project_that_keeps_its_adr_in_the_architecture_dir_is_unmoved(tmp_path):
    """Both layouts stay supported — this is a resolver, not a migration."""
    from core.quality_gate.artifact_consistency import _adr_path
    from core.utils.project_layout import ProjectLayout

    (_arch(tmp_path) / "ADR.md").write_text("# ADR\n", encoding="utf-8")

    assert ProjectLayout(tmp_path).adr_path == tmp_path / _LEGACY
    assert _adr_path(tmp_path) == tmp_path / _LEGACY


def test_with_no_adr_at_all_the_writer_gets_the_deployed_path(tmp_path):
    """The docstring's requirement: a canonical path for a first write.

    It resolves to where init-project puts it, so a first write lands beside
    the template rather than in a second location.
    """
    from core.utils.project_layout import ProjectLayout

    _arch(tmp_path)
    assert ProjectLayout(tmp_path).adr_path == tmp_path / _DEPLOYED


def test_the_writer_and_the_reader_never_disagree(tmp_path):
    """The property, over every arrangement — not three separate assertions
    that could each be right while the pair is wrong."""
    from core.quality_gate.artifact_consistency import _adr_path
    from core.utils.project_layout import ProjectLayout

    for have_sub, have_root in ((True, True), (True, False), (False, True), (False, False)):
        root = tmp_path / f"p{int(have_sub)}{int(have_root)}"
        _arch(root)
        if have_sub:
            (root / "02-architecture" / "adr").mkdir()
            (root / _DEPLOYED).write_text("# sub\n", encoding="utf-8")
        if have_root:
            (root / _LEGACY).write_text("# root\n", encoding="utf-8")
        assert ProjectLayout(root).adr_path == _adr_path(root), (have_sub, have_root)


def test_amend_sab_appends_to_the_real_adr_instead_of_creating_a_stub(tmp_path):
    """The measured consequence, reproduced.

    taskq-final's 893-line ADR sits in `adr/` while an 8-line file the
    framework created holds one amendment. After this, the amendment joins the
    document it is an amendment to.
    """
    from core.quality_gate.sab_amender import _append_adr_amendment

    (_arch(tmp_path) / "adr").mkdir()
    real = tmp_path / _DEPLOYED
    real.write_text("# Architecture Decision Records\n\n## ADR-001\n\nthe real one.\n",
                    encoding="utf-8")

    _append_adr_amendment(tmp_path, "pkg.dead_module", "dropped", "zero importers",
                          ["repository"])

    assert not (tmp_path / _LEGACY).exists(), (
        "the amendment created a second ADR at the other path — the eight-line "
        "stub this round is about"
    )
    text = real.read_text(encoding="utf-8")
    assert "the real one." in text and "pkg.dead_module" in text


def test_amend_sab_still_creates_one_when_the_project_has_none(tmp_path):
    """Negative control: `creating it if absent` is the documented behaviour
    and stays. It just creates it where init-project would have."""
    from core.quality_gate.sab_amender import _append_adr_amendment

    _arch(tmp_path)
    _append_adr_amendment(tmp_path, "pkg.x", "dropped", "reason", ["repository"])

    created = tmp_path / _DEPLOYED
    assert created.is_file(), "the writer no longer has a path for a first write"
    assert created.read_text(encoding="utf-8").startswith("# Architecture Decision Records")


def test_there_is_one_resolver():
    """`_adr_path` existed because `adr_path` could not answer. Two functions
    answering one question is what put the writer and the reader on different
    files for eleven projects."""
    src = (REPO / "core" / "quality_gate" / "artifact_consistency.py").read_text(encoding="utf-8")
    body = src[src.index("def _adr_path("):]
    body = body[:body.index("\ndef ")]
    assert "adr_path" in body and "ProjectLayout" in body, body
    assert '"adr"' not in body, (
        "_adr_path still builds the path itself instead of asking ProjectLayout"
    )
