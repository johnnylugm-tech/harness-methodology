"""Round 99 站4 — the framework may not ship its own unfinished file.

`scaffold_project_manifest_from_ssot` writes `requirements.txt` when a
project has none, transcribing the dependencies its SSOTs name, and it puts
this line at the top of what it wrote:

    # WARNING: AUTO-SCAFFOLDED FROM SSOT - REVIEW AND PIN VERSIONS BEFORE COMMIT

It also records a `gate:env-repair` degradation row, `owner="harness"`,
naming the file and the dependency count. Two statements of an obligation,
and no reader of either.

Measured over the 17 corpus projects at the moment this was written:

    8 have the ledger row (cc, cc-new, final, new, omnibot-new, redo,
      super, wow)
    5 of those still ship every dependency unpinned — omnibot-new 1/1,
      cc-new 12/12, redo 10/10, super 11/11, wow 10/10
    3 did the review: cc 11/11, final 12/12 and new 10/10 are fully pinned

This is the mechanism behind an audit finding on taskq-done, whose SSOTs
said "Production: PostgreSQL" three times and whose scaffolded manifest
named no DBAPI driver: nothing ever asked whether the file had been read.

WHAT THIS DOES NOT CLAIM

It does not check that the manifest is CORRECT. Knowing that a declared
PostgreSQL runtime implies psycopg needs a technology-to-package table,
which is domain knowledge and would tie one ecosystem into a
language-agnostic pipeline. What it enforces is the sentence the framework
already wrote, against the file the framework already wrote.

WHY THE LEDGER AND NOT THE BANNER

The banner is a comment; one line removes it. The ledger row is the
framework's own record that it authored the file, and it does not move when
the comment does. taskq-new is the shape that proves the rule is about
substance and not the marker: it still carries the banner and every version
is pinned, and it must not block.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.ssot_manifest import unfinished_scaffolded_manifest

pytestmark = [pytest.mark.core]

_BANNER = ("# proj — auto-scaffolded from SSOT\n"
           "# WARNING: AUTO-SCAFFOLDED FROM SSOT - REVIEW AND PIN VERSIONS "
           "BEFORE COMMIT\n#\n")

_UNPINNED = "fastapi\nsqlalchemy\n"
_PINNED = "fastapi==0.115.0\nsqlalchemy==2.0.32\n"


def _project(tmp_path: Path, *, manifest: "str | None", ledger: bool) -> Path:
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        (project / "requirements.txt").write_text(manifest, encoding="utf-8")
    if ledger:
        meth = project / ".methodology"
        meth.mkdir(exist_ok=True)
        (meth / "degradations.jsonl").write_text(json.dumps({
            "component": "gate:env-repair",
            "what": "SSOT scaffold wrote requirements.txt (2 deps from 3 SSOT file(s))",
            "owner": "harness",
        }) + "\n", encoding="utf-8")
    return project


# ---- blocks ---------------------------------------------------------------

def test_a_scaffolded_manifest_with_unpinned_deps_is_named(tmp_path) -> None:
    project = _project(tmp_path, manifest=_BANNER + _UNPINNED, ledger=True)
    reason = unfinished_scaffolded_manifest(project)
    assert reason, "the framework's own unreviewed manifest passed"
    assert "fastapi" in reason and "sqlalchemy" in reason, reason


def test_the_ledger_row_locates_it_after_the_banner_is_deleted(tmp_path) -> None:
    """The marker is a comment. The record of authorship is not."""
    project = _project(tmp_path, manifest=_UNPINNED, ledger=True)
    assert unfinished_scaffolded_manifest(project), (
        "deleting the banner cleared the check — the cheapest satisfying "
        "action would then be removing a comment")


def test_the_banner_alone_locates_it_when_the_ledger_is_gone(tmp_path) -> None:
    """A ledger can be reset mid-run; the banner is the fallback, not the
    primary. Both present is the normal case."""
    project = _project(tmp_path, manifest=_BANNER + _UNPINNED, ledger=False)
    assert unfinished_scaffolded_manifest(project)


# ---- stays quiet ----------------------------------------------------------

def test_a_scaffolded_manifest_that_was_pinned_is_silent(tmp_path) -> None:
    """taskq-new's shape: banner still there, every version pinned. The
    obligation is the pinning, not the comment."""
    project = _project(tmp_path, manifest=_BANNER + _PINNED, ledger=True)
    assert unfinished_scaffolded_manifest(project) is None


def test_a_project_authored_manifest_is_not_judged(tmp_path) -> None:
    """No ledger row, no banner: this framework did not write it and has no
    standing to require a pinning policy it never stated to that project."""
    project = _project(tmp_path, manifest=_UNPINNED, ledger=False)
    assert unfinished_scaffolded_manifest(project) is None


def test_a_project_with_no_manifest_is_not_judged(tmp_path) -> None:
    project = _project(tmp_path, manifest=None, ledger=True)
    assert unfinished_scaffolded_manifest(project) is None


@pytest.mark.parametrize("spec", [
    "fastapi==0.115.0",
    "fastapi>=0.115.0",
    "fastapi~=0.115.0",
    "sqlalchemy[asyncio]==2.0.32",
    "mypkg @ https://example.invalid/mypkg.whl",
    "-r other-requirements.txt",
    "--index-url https://example.invalid/simple",
])
def test_a_line_carrying_a_version_or_an_option_is_not_unpinned(
        tmp_path, spec: str) -> None:
    """A pip option line and a URL requirement are not missing a version;
    reading them as unpinned would block a project for a line that names
    an exact artifact."""
    project = _project(tmp_path, manifest=_BANNER + spec + "\n", ledger=True)
    assert unfinished_scaffolded_manifest(project) is None, spec


def test_comments_and_blank_lines_are_not_requirements(tmp_path) -> None:
    project = _project(
        tmp_path, manifest=_BANNER + "\n# a note\n\n" + _PINNED, ledger=True)
    assert unfinished_scaffolded_manifest(project) is None


# ---- the message ----------------------------------------------------------

def test_the_message_says_it_was_the_framework_that_wrote_the_file(
        tmp_path) -> None:
    """A block that reads as "your manifest is wrong" sends the agent to
    audit a file it did not author."""
    project = _project(tmp_path, manifest=_BANNER + _UNPINNED, ledger=True)
    reason = unfinished_scaffolded_manifest(project) or ""
    assert "scaffold" in reason.lower(), reason
    assert "→" in reason or "fix" in reason.lower(), reason
