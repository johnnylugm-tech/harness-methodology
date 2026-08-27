"""Rewriting an attestation whose content did not change must not touch its bytes.

Round 18 站3. Staleness is probed by mtime (cli.phase_cmds._trace_dirty_state)
and git does not preserve mtimes: every pull, checkout, or fresh clone rewrites
them, so a perfectly current attestation reads as stale. Clearing that by
rewriting the file changes `git_sha` — a real diff, which has to be committed,
whose commit makes `git_sha` stale again. The loop cannot converge.

It ran six times: ad2a1db, 5b1522e, df8074e, ef7cecc, eb5f6f2, 83d605f, each a
`chore: refresh attestation post-pull`, every one carrying
content_sha256 932e6844… — the matrix never actually changed. `git_sha` has no
comparing consumer either; it is printed in three places and read by none.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.build_trace_attestation import (
    ATTESTATION_SCHEMA,
    COMMITTED_NAME,
    DEFAULT_TRACE_DIR,
    attestation_is_current,
    write_attestation,
)


def _attestation(
    git_sha: str,
    content_sha: str = "c0ffee" * 10,
    overlay_used: "str | None" = None,
    overlay_errors: "list | None" = None,
) -> dict:
    return {
        "schema": ATTESTATION_SCHEMA,
        "git_sha": git_sha,
        "content_sha256": content_sha,
        "tool": "scripts/build_trace_attestation.py",
        "overlay_used": overlay_used,
        "overlay_errors": overlay_errors if overlay_errors is not None else [],
        "matrix": {"FR-01": {"tests": ["test_fr01_01_happy"]}},
    }


def _committed(project: Path) -> Path:
    return project / DEFAULT_TRACE_DIR / COMMITTED_NAME


def test_rewrite_with_only_a_new_git_sha_leaves_the_bytes_alone(tmp_path):
    """The exact shape of the six ritual commits: same matrix, next commit."""
    write_attestation(tmp_path, _attestation("aaa1111"))
    before = _committed(tmp_path).read_bytes()

    write_attestation(tmp_path, _attestation("bbb2222"))

    assert _committed(tmp_path).read_bytes() == before, (
        "a git_sha-only difference rewrote the file — this is what produced "
        "six consecutive no-op 'refresh attestation post-pull' commits"
    )
    # The stored git_sha is the one that last attested THIS content, which is
    # what makes it meaningful; bumping it per commit is what made it useless.
    assert json.loads(before)["git_sha"] == "aaa1111"


def test_where_the_overlay_was_read_from_is_not_part_of_what_was_attested(tmp_path):
    """Round 80 站5 — the loop this file ends had come back, through a path.

    `overlay_used` is `str(overlay_path)`, and `overlay_path` defaults to
    `project / "TRACEABILITY_MATRIX.overlay.yaml"` — so its value is a function
    of how the CALLER spelled the project path, absolute or relative. It says
    nothing about the matrix, which is the same category as `git_sha`, and
    `_attested_content`'s own docstring defines that category.

    Measured on this repo at a77e1875: the committed attestation carried
    `/Users/johnny/projects/harness-methodology/TRACEABILITY_MATRIX.overlay.yaml`
    from some earlier checkout location while a fresh build produced the
    relative `TRACEABILITY_MATRIX.overlay.yaml`. `content_sha256` was IDENTICAL
    (43a6d437…) and `overlay_used` was the ONLY differing field, so
    `attestation_is_current` was permanently False,
    `_attestation_content_still_current` could never return True, and the
    mtime probe blocked every commit with no way out but the no-op rewrite
    this file exists to prevent.
    """
    write_attestation(tmp_path, _attestation("aaa1111", overlay_used="/a/b/o.yaml"))
    before = _committed(tmp_path).read_bytes()

    write_attestation(
        tmp_path, _attestation("aaa1111", overlay_used="o.yaml")
    )

    assert _committed(tmp_path).read_bytes() == before, (
        "the same matrix, read through a differently-spelled path, rewrote the "
        "file — which is the six-no-op-commits loop with a path in place of a "
        "git_sha"
    )


def test_rewrite_still_bumps_mtime_so_the_staleness_probe_clears(tmp_path):
    """Touching is the point: the mtime probe must pass without a diff."""
    write_attestation(tmp_path, _attestation("aaa1111"))
    path = _committed(tmp_path)
    os.utime(path, (1_000_000, 1_000_000))
    stale_mtime = path.stat().st_mtime

    write_attestation(tmp_path, _attestation("bbb2222"))

    assert path.stat().st_mtime > stale_mtime, (
        "mtime was not refreshed — the probe would keep blocking and the "
        "operator would have no way out except a no-op commit"
    )


def test_changed_content_is_written_normally(tmp_path):
    """Idempotence must not become 'never updates'."""
    write_attestation(tmp_path, _attestation("aaa1111"))
    before = _committed(tmp_path).read_bytes()

    write_attestation(tmp_path, _attestation("bbb2222", content_sha="dec0de" * 10))

    after = _committed(tmp_path).read_bytes()
    assert after != before
    assert json.loads(after)["content_sha256"] == "dec0de" * 10
    assert json.loads(after)["git_sha"] == "bbb2222"


@pytest.mark.parametrize(
    "field, value",
    [
        ("overlay_errors", ["FR-99: unknown FR"]),
        ("matrix", {"FR-02": {"tests": []}}),
    ],
)
def test_any_non_git_sha_field_change_counts_as_new_content(tmp_path, field, value):
    """A content field that changed must still be written — otherwise this
    optimisation would suppress a real signal. An overlay that started failing
    validation changes what was merged into the matrix; the matrix is the
    matrix.

    Round 80 站5 removed `overlay_used` from this list. The docstring used to
    read "`git_sha` is the ONLY field excluded", and that was the defect: the
    field is `str(overlay_path)`, whose value is a function of how the caller
    spelled the project path rather than of anything in the matrix. Treating it
    as content made `attestation_is_current` permanently False on a repo whose
    committed attestation was written from a different checkout location, which
    revived the six-no-op-commits loop this whole file exists to prevent —
    measured at a77e1875 with an identical content_sha256 on both sides. The
    parametrisation keeps `overlay_errors` precisely so the narrowing cannot
    widen into "ignore anything overlay-shaped"."""
    write_attestation(tmp_path, _attestation("aaa1111"))
    before = _committed(tmp_path).read_bytes()

    changed = {**_attestation("bbb2222"), field: value}
    assert not attestation_is_current(tmp_path, changed)
    write_attestation(tmp_path, changed)

    assert _committed(tmp_path).read_bytes() != before


def test_is_current_is_false_when_nothing_has_been_written(tmp_path):
    """A missing or unreadable file must never read as 'current' — that would
    turn a genuinely absent attestation into a silent pass."""
    assert not attestation_is_current(tmp_path, _attestation("aaa1111"))

    path = _committed(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    assert not attestation_is_current(tmp_path, _attestation("aaa1111"))
