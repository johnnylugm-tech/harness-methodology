"""A calibration knob nobody can tell has fired is not calibration.

Round 57 站0 / 站5. `f2688f1` added four `crg_excludes` globs to this repo's
`.methodology/harness_config.json`, saying in its own message that they
exclude the integration suite and an NFR placeholder. Station 0 measured them
against `git ls-files` (912 tracked files)::

    '.claude/*'                            -> 13 files
    '*.mjs'                                ->  9 files
    '03-development/tests/integration/*'   ->  0 files
    '03-development/tests/test_nfr.py'     ->  0 files

This repo has no `03-development/` at all — its tests live in `tests/`, there
is no `tests/integration/` (only `tests/e2e/`), and the NFR file here is
`tests/test_nfr_floor_units.py`. Those two globs were written against another
tree's layout and committed into the tree being judged; they are **deleted
rather than corrected**, because `compute_community_cohesion_score` already
excludes test communities twice over (by name, and by >50% of members under a
`tests/` directory), so a corrected glob would restate a rule that holds.

Then 站5 measured them against the shape the matcher actually sees, and the
count was three dead, not two: CRG emits members as `path::symbol` and the
matcher never stripped the suffix, so `*.mjs` matched nothing either. A file
list could not show that. **This is the finding** — not four globs, two of
them wrong, but that `crg_metrics.json` recorded `_extra_excludes` (the
input) and nothing recorded the effect, so a glob that matched nothing looked
exactly like one that removed a community.
"""

from __future__ import annotations

import json
import subprocess
from fnmatch import fnmatch
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(_REPO), "ls-files"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.splitlines()


def test_this_repos_own_crg_excludes_all_match_something():
    """Dogfood: the framework's own calibration must not be aspirational.

    Matched against `f + "::symbol"`, not against the bare path. That is the
    string the matcher is handed, and testing the bare path is what let
    `*.mjs` read as live for a round.
    """
    cfg = json.loads(
        (_REPO / ".methodology" / "harness_config.json").read_text(encoding="utf-8")
    )
    members = [f"{f}::symbol" for f in _tracked_files()]
    dead = [
        g for g in cfg.get("crg_excludes", [])
        if not any(fnmatch(m.split("::", 1)[0], g) for m in members)
    ]
    assert dead == [], (
        f"{len(dead)} crg_excludes glob(s) match no tracked file: {dead}. "
        "A glob that cannot fire is a calibration that was never applied — "
        "delete it, or fix the path it was written against."
    )


def test_the_cohesion_score_reports_what_each_exclude_matched():
    """The effect, not only the input, reaches `crg_metrics.json`.

    Two communities, one entirely under `.claude/`. The excluded one must be
    named in the output with the glob that removed it, so a future reader can
    answer "did this calibration do anything" from the artifact alone.
    """
    import sys

    sys.path.insert(0, str(_REPO / "harness" / "ssi" / "scripts"))
    from crg_analysis import compute_community_cohesion_score

    root = "/proj"
    communities = [
        {"name": "core", "cohesion": 0.9,
         "files": [f"{root}/src/a.py::f", f"{root}/src/b.py::g"]},
        {"name": "generated", "cohesion": 0.0,
         "files": [f"{root}/.claude/workflows/x.js::h"]},
    ]

    result = compute_community_cohesion_score(
        communities, extra_excludes=[".claude/*", "*.never"],
        project_root=root,
    )

    matched = result["excludes_matched"]
    assert matched[".claude/*"] == 1, "one community was removed by this glob"
    assert matched["*.never"] == 0, (
        "a glob that removed nothing must say so — that is the whole finding"
    )


def test_a_glob_that_matches_nothing_is_reported_to_the_operator(capsys):
    """Zero-match is a WARN naming the glob, not silence."""
    import sys

    sys.path.insert(0, str(_REPO / "harness" / "ssi" / "scripts"))
    from crg_analysis import compute_community_cohesion_score

    compute_community_cohesion_score(
        [{"name": "core", "cohesion": 0.9, "members": ["/proj/src/a.py::f"]}],
        extra_excludes=["03-development/tests/integration/*"],
        project_root="/proj",
    )
    captured = capsys.readouterr()
    assert "03-development/tests/integration/*" in (captured.out + captured.err)


def test_a_glob_anchored_at_the_end_matches_a_member_entry():
    """The live bug behind the finding, and the reason the file list missed it.

    CRG emits community members as `path::symbol`. `_dominant_file` has always
    split that suffix off; the exclude matcher did not. So `*.mjs` — a glob
    that matches nine tracked files in this repo — matched zero community
    members, because every one of them ends in `::something`.

    Station 0 measured the four globs against `git ls-files` and read two as
    live. Against the shape the matcher actually sees, three of the four were
    dead, and the third was dead for a reason no file-list check could show.
    That is the finding: not the globs, but that nothing reported what they
    did.
    """
    import sys

    sys.path.insert(0, str(_REPO / "harness" / "ssi" / "scripts"))
    from crg_analysis import compute_community_cohesion_score

    root = "/proj"
    result = compute_community_cohesion_score(
        [{"name": "tooling", "cohesion": 0.0, "size": 6,
          "files": [f"{root}/scripts/workflowgen/js_src/sim_runner.mjs::run"]}],
        extra_excludes=["*.mjs"], project_root=root,
    )

    assert result["excludes_files_matched"]["*.mjs"] == 1
    assert result["excludes_matched"]["*.mjs"] == 1
    assert result["excluded_test_communities"] == 1


def test_a_glob_that_matches_files_but_never_a_majority_is_distinguishable():
    """Zero communities is not zero files, and the two need different fixes.

    A glob calibrated too narrowly matches files and excludes nothing; one
    pointed at a directory that does not exist matches nothing at all. Both
    read as `excludes_matched == 0`, so the file count is what tells them
    apart — and only the second earns the WARN.
    """
    import sys

    sys.path.insert(0, str(_REPO / "harness" / "ssi" / "scripts"))
    from crg_analysis import compute_community_cohesion_score

    root = "/proj"
    result = compute_community_cohesion_score(
        [{"name": "mixed", "cohesion": 0.9, "size": 6, "files": [
            f"{root}/src/a.py::f", f"{root}/src/b.py::g", f"{root}/gen/c.py::h",
        ]}],
        extra_excludes=["gen/*"], project_root=root,
    )

    assert result["excludes_files_matched"]["gen/*"] == 1
    assert result["excludes_matched"]["gen/*"] == 0
    assert result["excluded_test_communities"] == 0
