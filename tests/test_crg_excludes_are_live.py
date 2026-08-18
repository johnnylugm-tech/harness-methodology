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
`tests/test_nfr_floor_units.py`. The two dead globs were written against
another tree's layout and committed into the tree being judged.

The two dead entries are **deleted rather than corrected**:
`compute_community_cohesion_score` already excludes test communities twice
over (by name, and by >50% of members living under a `tests/` directory), so
a corrected glob would be a second statement of a rule that already holds.

The root cause is not two wrong globs. It is that `crg_metrics.json` recorded
`_extra_excludes` — the *input* — and nothing recorded the *effect*, so a glob
that matched nothing looked exactly like one that matched everything.
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
    """Dogfood: the framework's own calibration must not be aspirational."""
    cfg = json.loads(
        (_REPO / ".methodology" / "harness_config.json").read_text(encoding="utf-8")
    )
    files = _tracked_files()
    dead = [
        g for g in cfg.get("crg_excludes", [])
        if not any(fnmatch(f, g) for f in files)
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
         "members": [f"{root}/src/a.py::f", f"{root}/src/b.py::g"]},
        {"name": "generated", "cohesion": 0.0,
         "members": [f"{root}/.claude/workflows/x.js::h"]},
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
