"""A deferral is a structure, not a sentence (Round 69 站5).

`core/quality_gate/spec_alignment.py`'s own module docstring states the
contract: "Only *structural* FR forms are read (never prose mentions), so a
stray 'FR-01' in a sentence cannot create a phantom requirement." Every
pattern in the module honours it except one — `_FR_DEFERRED` scans the whole
SRS text with no anchor.

Until 6181d52 that only widened the dropped-requirement branch (a prose
mention could excuse an FR the canonical spec declared). 6181d52 subtracted
the same unanchored set on the *invented* axis too, where it does something
stronger: an SRS with a complete `### FR-12:` section is silenced as long as
the two words `FR-12-deferred` appear anywhere in the file — a comment, a
table cell, a sentence explaining that FR-12 was *not* deferred.

The three structural forms below are the ones the corpus actually uses:
heading (taskq-new SRS.md:1402), table row (taskq SRS.md:599-603) and bold
bullet (taskq-super SRS.md:1085). Restricting to them changes the verdict on
none of the nine corpus projects.
"""
from __future__ import annotations

from pathlib import Path

from core.quality_gate.spec_alignment import _deferred_fr_ids, check_spec_alignment
from tests.test_spec_alignment import _project


def test_a_prose_mention_does_not_silence_an_invented_requirement(
    tmp_path: Path,
) -> None:
    proj = _project(
        tmp_path,
        canonical="### FR-01: login\n",
        srs=(
            "### FR-01: login\n"
            "### FR-12: audit log\n"
            "\nNote: this is not the same as the FR-12-deferred proposal we "
            "rejected in review.\n"
        ),
    )
    invented = [v for v in check_spec_alignment(proj)
                if v.check_type == "invented_requirement"]
    assert [v.rule_id for v in invented] == ["FR-12"], (
        "a prose mention of `FR-12-deferred` excused a structurally declared FR"
    )


def test_the_three_structural_deferral_forms_all_count() -> None:
    assert _deferred_fr_ids("### FR-99-deferred: out of scope\n") == {"FR-99"}
    assert _deferred_fr_ids("| FR-01-deferred | none | — | — |\n") == {"FR-01"}
    assert _deferred_fr_ids(
        "- **FR-08-deferred** — FR-08's interrupted state is out of scope.\n"
    ) == {"FR-08"}
    assert _deferred_fr_ids("we rejected the FR-12-deferred proposal\n") == set()


def test_a_structurally_deferred_fr_is_still_not_dropped(tmp_path: Path) -> None:
    """The property 6181d52's sibling branch has always had must survive the
    tightening: a canonical FR the SRS records as deferred is not dropped."""
    proj = _project(
        tmp_path,
        canonical="### FR-01: login\n### FR-02: logout\n",
        srs="### FR-01: login\n### FR-02-deferred: out of scope this round\n",
    )
    assert [v for v in check_spec_alignment(proj) if v.severity == "error"] == []
