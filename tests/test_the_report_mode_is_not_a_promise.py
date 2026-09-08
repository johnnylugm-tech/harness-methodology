"""Round 109 站3 — a flag that stamps a label and changes no computation.

`scripts/canonical_diff.py` took a `mode` argument, offered it on the CLI with
three `choices`, and wrote it into the report:

    parser.add_argument("--mode", default="srs_vs_spec",
        choices=["srs_vs_spec", "testspec_vs_srs", "verification_vs_srs"])
    ...
    return {"deliverable": ..., "mode": mode, ...}

`mode` appeared in exactly two places in the module: the parameter list and
that dict entry. It selected nothing. The module docstring nonetheless told
its reader the engine was extensible "to TESTSPEC↔SRS (P4),
VERIFICATION↔SRS (P5), etc. via `--mode` argument".

Measured 2026-09-08:

  * callers passing `--mode`                       0
    (the only invocation is the Phase 1 workflow's
     `canonical_diff.py --srs … --spec … --out …`)
  * readers of the report's `mode` field           0
    (the one other `"mode"` in the tree is an unrelated CRG kwarg,
     harness/crg_bridge.py:305)

So it was dead — but dead is not why it had to go. **It would have lied.**
Handing it `--mode testspec_vs_srs --srs TEST_SPEC.md --spec SRS.md` runs the
srs_vs_spec computation, unchanged, and stamps the output `testspec_vs_srs`;
a downstream reader keying on that field would attribute a Phase 1 measurement
to Phase 4. Round 30/43's half-built mechanism with a Round 24 edge: the field
exists, so it reads as answered.

What replaces it is not a better flag. There is one comparison, so the report
names one comparison, as a constant — and these tests are what would notice a
second label arriving before a second computation does.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from scripts.canonical_diff import build_diff_report

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "scripts" / "canonical_diff.py"


def test_the_caller_cannot_choose_the_label() -> None:
    """The defect, stated as who owns the name of the comparison.

    A `mode` parameter means the caller names the computation; nothing here
    reads that name back to select one, so the caller was naming something
    the module then ignored.
    """
    params = inspect.signature(build_diff_report).parameters
    assert "mode" not in params, (
        f"build_diff_report still accepts a caller-supplied label: "
        f"{list(params)}. It performs one comparison. A parameter that names "
        f"the comparison, next to code that never branches on it, is a field "
        f"a reader will key on and be wrong about")


def test_the_report_names_the_comparison_it_actually_performed(
    tmp_path: Path,
) -> None:
    """And the name it publishes is that one comparison, not a parameter."""
    srs = tmp_path / "SRS.md"
    srs.write_text(
        "# SRS\n\n### FR-01: Submission\n\nA job is queued.\n", encoding="utf-8")
    spec = tmp_path / "SPEC.md"
    spec.write_text("# SPEC\n\n- A job is queued.\n", encoding="utf-8")

    assert build_diff_report(srs, spec)["mode"] == "srs_vs_spec"


def test_the_cli_offers_no_mode_it_does_not_implement() -> None:
    """The surface an operator sees.

    `choices=[...]` is a stronger promise than a bare string: it enumerates
    three comparisons the module can perform, and it could perform one.
    """
    src = SOURCE.read_text(encoding="utf-8")
    assert '"--mode"' not in src and "'--mode'" not in src, (
        "the CLI still advertises --mode. Two of its three choices name "
        "comparisons this module does not implement; selecting one relabels "
        "the srs_vs_spec result instead of computing anything different")


def test_the_module_promises_no_diff_it_cannot_do() -> None:
    """The statement, alongside the mechanism (R33/R56).

    Removing the flag and leaving the docstring's offer standing is the shape
    Round 39 named: the mechanism goes, the statement about it outlives it.

    Scoped to the OFFER, not to the string `--mode`. The first version of this
    test asserted `"--mode" not in docstring` and went red on the very commit
    that removed the flag, because the corrected docstring names the flag in
    order to record why it is gone. A scan that cannot tell an offer from an
    account of its removal is Round 46's false accusation, and this round has
    now met that shape twice — the other being the tree-wide rule-fork scanner
    站4 measured at 24 false hits out of 26 and dropped. `same engine applies`
    is the promise itself; a historical note has no reason to repeat it.
    """
    docstring = inspect.getdoc(
        __import__("scripts.canonical_diff", fromlist=["canonical_diff"])) or ""
    assert "same engine applies" not in docstring, (
        "the module docstring offers TESTSPEC↔SRS / VERIFICATION↔SRS as "
        f"something this engine already does:\n{docstring}")
    assert "performs ONE comparison" in docstring, (
        "the docstring no longer states that this module performs a single "
        "comparison. That sentence is what a future `mode` parameter would "
        "have to contradict in writing before it could be added in silence")
