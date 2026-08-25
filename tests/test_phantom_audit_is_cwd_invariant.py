"""Round 78 站1 — the phantom audit must not depend on where it was invoked.

Plan F (`da8e70fd`) added an early BLOCK to `_advance_prechecks` and resolved
the source directory with a CWD-relative path:

    _src_dir_rel = str(src_dir.relative_to(project))
    _discovered = set(_discover_modules_at(Path(_src_dir_rel)))

`discover_modules_at` opens that path. Run `advance-phase --project <path>`
from anywhere but the project root and it opens nothing, so every module the
SAB registers reads as missing. Measured across the nine corpus projects that
carry a SAB:

    project              cwd == project        cwd != project
    taskq                0 phantom  -> pass     9 phantom -> BLOCK exit 9
    taskq-plus           0 phantom  -> pass    23 phantom -> BLOCK exit 9
    taskq-renew          0 phantom  -> pass    23 phantom -> BLOCK exit 9
    taskq-api            0 phantom  -> pass    28 phantom -> BLOCK exit 9
    taskq-advance        0 phantom  -> pass    29 phantom -> BLOCK exit 9
    taskq-super          0 phantom  -> pass    28 phantom -> BLOCK exit 9
    taskq-cc             0 phantom  -> pass    31 phantom -> BLOCK exit 9
    taskq-new            0 phantom  -> pass    45 phantom -> BLOCK exit 9
    run-all-by-workflow  0 phantom  -> pass     9 phantom -> BLOCK exit 9

Nine of nine, and the message sends each of them to
`amend-sab --resolve-phantom`, which would delete a correct registration for a
module that is on disk.

The root cause is not the line, it is one name carrying two meanings. In that
block `src_dir` is handed to two functions with opposite requirements:

    discover_modules_at(src_path)             a filesystem path — must resolve
    phantom_modules(sab, discovered, src_dir) a string prefix for stripping
                                              path-form SAB entries — relative

`cli/gate_cmds.py:1053` has the identical `_src_dir_rel` and its comment spells
out the relative-prefix rule — but it passes the result to
`amend_sab(Path(project), src_dir=_src_dir_rel)`, where the ROOT travels as its
own argument. Plan F copied the rule from a call site where the root travels
separately to one where it does not.

`sab_amender.discover_modules(project_root, src_dir)` is the function that
takes exactly that pair, and three production sites already use it
(`sab_amender.py:278`, `:450`, `cli/project_cmds.py:1073`). Plan F hand-rolled
a fourth spelling.

Nothing in `_advance_prechecks` assumes the process CWD: Plan E's own audit
57 lines above passes `str(src_dir)` absolute, and the ruff/mypy subprocesses
below it pass `cwd=str(project)` explicitly. The workflow calls
`<PY> <REPO>/harness_cli.py advance-phase --project <REPO>` with absolute paths
throughout, and `run-all.js` opens with a REPO RESOLVER that walks up from the
current directory — because the current directory is not known.

Fourth occurrence of this class (Round 22's bare pytest target, Round 25's
`project / "tests"`, Round 20 站2's auto_fix mkdir). Each of those was found by
a real run rather than by a test; `tests/test_no_hardcoded_paths.py` catches
`<root> / "tests"` and cannot see the reverse shape — an already-resolved
absolute path being de-absolutised.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.quality_gate.sab_amender import phantom_module_block

pytestmark = [pytest.mark.core]


def _project(root: Path, *, modules_on_disk: list[str], sab_modules: list[str]) -> Path:
    """A project whose SAB registers `sab_modules` and whose src holds
    `modules_on_disk` (dotted names, written as real files)."""
    src = root / "03-development" / "src"
    src.mkdir(parents=True)
    for dotted in modules_on_disk:
        parts = dotted.split(".")
        pkg = src.joinpath(*parts[:-1])
        pkg.mkdir(parents=True, exist_ok=True)
        for i in range(len(parts) - 1):
            (src.joinpath(*parts[: i + 1]) / "__init__.py").touch()
        (pkg / f"{parts[-1]}.py").write_text("def f(): return 1\n", encoding="utf-8")

    meth = root / ".methodology"
    meth.mkdir()
    (meth / "SAB.json").write_text(
        json.dumps({"layers": [{"name": "core",
                                "modules": list(sab_modules),
                                "allowed_dependencies": []}]}),
        encoding="utf-8")
    return root


@pytest.fixture()
def clean_project(tmp_path: Path) -> Path:
    """Every registered module is on disk — the shape all nine corpus
    projects are in. The correct answer is [] from anywhere."""
    return _project(
        tmp_path / "proj",
        modules_on_disk=["pkg.alpha", "pkg.beta", "pkg.sub.gamma"],
        sab_modules=["pkg.alpha", "pkg.beta", "pkg.sub.gamma"],
    )


def _from(cwd: Path, project: Path) -> list[str]:
    before = Path.cwd()
    try:
        os.chdir(cwd)
        return phantom_module_block(project)
    finally:
        os.chdir(before)


def test_the_answer_is_the_same_from_inside_and_outside_the_project(clean_project, tmp_path):
    """The invariant. This is the test Plan F did not have, and the only kind
    that could have caught it: its four call-site tests all asserted that a
    string appears in `inspect.getsource(...)`, and every one of those strings
    was present while the behaviour was wrong.
    """
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    inside_verdict = _from(clean_project, clean_project)
    outside_verdict = _from(outside, clean_project)

    assert inside_verdict == [], (
        f"a project whose SAB matches its disk has no phantom modules; "
        f"got {inside_verdict}")
    assert outside_verdict == inside_verdict, (
        f"the phantom audit answered differently depending on the process's "
        f"working directory: {outside_verdict} from outside vs "
        f"{inside_verdict} from inside. advance-phase is invoked as "
        f"`harness_cli.py advance-phase --project <REPO>` from a directory "
        f"nothing pins — `--project` is the authority, cwd is not.")


def test_a_real_phantom_is_still_reported_from_either_directory(tmp_path):
    """The check must keep working. Fixing the path must not turn the audit
    into one that never finds anything — that would be a green test over a
    dead mechanism."""
    project = _project(
        tmp_path / "proj",
        modules_on_disk=["pkg.alpha"],
        sab_modules=["pkg.alpha", "pkg.never_written"],
    )
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    assert _from(project, project) == ["pkg.never_written"]
    assert _from(outside, project) == ["pkg.never_written"]


def test_no_sab_is_not_a_phantom_finding(tmp_path):
    """A project with no SAB.json, or an unreadable one, declares nothing —
    so it can have no module declared-but-missing. Plan F's inline block
    treated both as "skip"; that behaviour is preserved verbatim."""
    project = tmp_path / "proj"
    (project / "03-development" / "src").mkdir(parents=True)
    (project / ".methodology").mkdir()
    assert phantom_module_block(project) == []

    (project / ".methodology" / "SAB.json").write_text("{not json", encoding="utf-8")
    assert phantom_module_block(project) == []

    (project / ".methodology" / "SAB.json").write_text("{}", encoding="utf-8")
    assert phantom_module_block(project) == []


def test_the_root_and_the_prefix_travel_as_separate_arguments():
    """The shape of the fix, pinned.

    `discover_modules(project_root, src_dir)` takes the root and the
    relative prefix separately; `discover_modules_at(src_path)` takes one
    filesystem path. Plan F passed the relative prefix to the second. The
    block now calls the first, which is the function three other production
    sites already use, so the two meanings cannot be conflated again by
    reading one comment and applying it to both calls.
    """
    import inspect

    from core.quality_gate import sab_amender

    sig = inspect.signature(sab_amender.discover_modules)
    assert list(sig.parameters) == ["project_root", "src_dir"], (
        "discover_modules is the seam this station relies on: a root and a "
        "relative prefix, named apart")
    assert list(inspect.signature(sab_amender.discover_modules_at).parameters) == [
        "src_path"], (
        "discover_modules_at takes ONE path and opens it — handing it a "
        "relative prefix is the Plan F defect")
