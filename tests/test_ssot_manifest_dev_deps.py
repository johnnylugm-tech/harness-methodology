"""Round 64 站0 — the dev-deps cell nobody agreed on a separator for.

`harness/ssot_manifest.py` scaffolds a project's requirements files from the
SSOT documents. `_parse_srs_section29` reads the `requirements-dev.txt` row of
SRS.md §2.9 and, before 6e7942e, split it on `, `; that commit changed it to
split on ` / ` and described the old separator as the bug.

Measured 2026-08-20: `templates/SRS.md` has no §2.9 dev-deps table at all, and
no prompt in `scripts/` tells an author which separator to use. Neither
separator is canonical, so replacing one with the other moves the same failure
to the other half of the input space — and the failure is silent, because a
whole-cell token like `import-linter, pip-licenses` fails the PEP 508 name
regex and `_filter_known` drops it with `continue`.

These tests pin both halves: either separator parses, and a cell that yields
nothing says so.
"""

from __future__ import annotations

import pytest

from harness.ssot_manifest import _filter_known, _parse_srs_section29

_ROW = (
    "| File | Contents | Traces |\n"
    "|---|---|---|\n"
    "| `requirements-dev.txt` | {cell} | NFR-06/07/08/10 |\n"
)


def _srs(tmp_path, cell):
    path = tmp_path / "SRS.md"
    path.write_text(_ROW.format(cell=cell), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "cell",
    [
        "`import-linter` / `pip-licenses` / `mutmut` / `pytest-benchmark`",
        "`import-linter`, `pip-licenses`, `mutmut`, `pytest-benchmark`",
        "import-linter / pip-licenses, mutmut / pytest-benchmark",
    ],
)
def test_either_separator_yields_the_same_dependencies(tmp_path, cell):
    """No template and no prompt declares the separator, so the parser does
    not get to insist on one. The mixed row is the honest case: an author
    following neither convention still listed four packages plainly."""
    deps, _ = _parse_srs_section29(_srs(tmp_path, cell))
    assert deps == ["import-linter", "pip-licenses", "mutmut", "pytest-benchmark"]


def test_a_cell_that_yields_nothing_says_so(tmp_path):
    """The scaffold's failure mode is a project shipped without its dev
    dependencies. That is worth a line; before this it was a `continue`."""
    _, warnings = _parse_srs_section29(_srs(tmp_path, "`see §4.7`"))
    assert any("SRS.md §2.9" in w for w in warnings), (
        f"a dev-deps cell contributing zero dependencies produced no "
        f"diagnostic at all: {warnings!r}"
    )


def test_a_token_the_name_regex_rejects_is_reported():
    """`_filter_known` warned only about tokens that LOOK like packages but
    are not on PyPI. A token that fails the PEP 508 name regex — which is
    what an unsplit multi-package cell is — vanished without a word."""
    warnings: list[str] = []
    kept = _filter_known(["mutmut, pytest-benchmark"], warnings, "SRS.md §2.9 dev-deps")
    assert kept == []
    assert warnings, "an entire cell was discarded and nothing was recorded"
