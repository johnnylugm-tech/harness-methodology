"""Round 42 站5 — when the unhealthy community is one file's internals.

taskq-renew's architecture score of 77.8 rests on two communities:
`storage-load-sub1` (size 12, cohesion 0.00) and `storage-load-sub2` (size 11,
cohesion 0.00). Both are Leiden splitting the INTERNALS of a single file —
`storage/task_store.py` — which at 12,575 bytes is *larger* than taskq-plus's
4,608-byte version of the same module, with both projects shipping five files
in `storage/`. Nothing is fragmented on disk.

That shape matters because of what the gate then tells the agent to do.
`harness_bridge` prints four remedies: add cross-module imports, merge small
communities, split communities over 50, or calibrate `crg_excludes` /
`crg_cohesion_healthy`. The first three all assume a community is a set of
modules. None of them can act on one file's internal clusters — so the only
executable advice left is the fourth, and Round 38 removed the waiver that
used to be the other way out. Every project hitting this shape is pushed
toward loosening its own ruler.

This does NOT change the score. Whether a 12.5 KB file whose internals form
two disconnected clusters is an architecture finding is not a question this
round has standing to answer, and excluding it by fiat would be the waiver
Round 38 removed, rebuilt. What changes is that the remedy names the file.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SSI = str(Path(__file__).resolve().parents[1] / "harness" / "ssi" / "scripts")
if _SSI not in sys.path:
    sys.path.insert(0, _SSI)

from crg_analysis import compute_community_cohesion_score  # noqa: E402


def _members(path: str, n: int) -> list[str]:
    return [f"/r/src/{path}::fn{i}" for i in range(n)]


def test_a_community_that_is_one_file_names_that_file():
    """The taskq-renew shape: 11 of 12 members from one module."""
    out = compute_community_cohesion_score(
        [{"name": "storage-load-sub1", "cohesion": 0.0, "size": 12,
          "files": _members("storage/task_store.py", 11)
                   + _members("storage/atomic.py", 1)}],
        cohesion_healthy=0.25,
        project_root="/r",
    )
    assert out["unhealthy"][0]["dominant_file"] == "src/storage/task_store.py"


def test_a_genuinely_multi_module_community_names_no_file():
    """Positive control: the shape the three module-level remedies DO fit.

    Without this, "name the dominant file" could degrade into "name whichever
    file appears most", which would attach a file to every finding and make
    the distinction useless.
    """
    out = compute_community_cohesion_score(
        [{"name": "mixed", "cohesion": 0.0, "size": 6,
          "files": _members("a.py", 2) + _members("b.py", 2) + _members("c.py", 2)}],
        cohesion_healthy=0.25,
        project_root="/r",
    )
    assert "dominant_file" not in out["unhealthy"][0]


def test_a_plurality_is_not_a_majority():
    """Two of five is the largest share and still not most of the community."""
    out = compute_community_cohesion_score(
        [{"name": "spread", "cohesion": 0.0, "size": 5,
          "files": _members("a.py", 2) + _members("b.py", 1)
                   + _members("c.py", 1) + _members("d.py", 1)}],
        cohesion_healthy=0.25,
        project_root="/r",
    )
    assert "dominant_file" not in out["unhealthy"][0]


def test_a_healthy_community_carries_no_finding_at_all():
    """The field rides on unhealthy records only — there is nothing to remedy."""
    out = compute_community_cohesion_score(
        [{"name": "cohesive", "cohesion": 0.9, "size": 6,
          "files": _members("storage/task_store.py", 6)}],
        cohesion_healthy=0.25,
        project_root="/r",
    )
    assert out["unhealthy"] == []
    assert out["score"] == 100
