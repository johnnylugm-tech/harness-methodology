"""Round 113 站L — the architecture score keeps only the signal that measures something.

The audit found taskq-sol's SAD designing a `migrations/versions/__init__.py`
"helper hub" that every revision calls "before executing its own schema/data
operations". The framework taught it: templates/SAD.md §2.1 Principle 4 said
the hub must be called "from every accessible function body" and gave an
edge budget `I ≥ ceil(0.4286 × E)` to reach.

Why the template needed that, measured read-only on 19 corpus graph databases
(the recompute equals CRG's stored cohesion on every community): CRG counts an
edge whose far end lies in no community as external, and every library call is
such an edge. taskq-plus scored 2 of 13 product communities healthy on
library-call density; Round 97 had already found 11 of 11 projects lowering the
cohesion floor to get past it. Two corrected definitions were measured and
rejected too — counting only product-community edges gives a median of 1.00 in
all 19 projects (Leiden communities have few cross-edges by construction), and
counting over designed directories scores a leaf package such as `models/` at 0
by its role. What remains discriminating is the size cap: a community over 50
nodes is a god cluster. That is the score now; cohesion is computed and listed,
not scored, and the template stops teaching calls added for a metric.
"""

from __future__ import annotations

from pathlib import Path

from harness.ssi.scripts.crg_analysis import (
    ARCHITECTURE_FORMULA,
    compute_community_cohesion_score,
    structural_drift,
)

_ROOT = Path(__file__).resolve().parent.parent


def test_low_cohesion_is_listed_not_scored():
    out = compute_community_cohesion_score(
        [{"name": "service-calls-libraries", "cohesion": 0.13, "size": 12}])
    assert out["score"] == 100.0
    assert [c["name"] for c in out["low_cohesion"]] == ["service-calls-libraries"]


def test_a_god_cluster_is_what_scores_down():
    out = compute_community_cohesion_score([
        {"name": "god", "cohesion": 0.9, "size": 97},
        {"name": "ok", "cohesion": 0.9, "size": 20},
    ])
    assert out["score"] == 50.0
    assert out["unhealthy"][0]["issues"] == ["oversized(97)"]


def test_the_result_names_its_ruler():
    assert compute_community_cohesion_score([])["score"] == 100  # unchanged empty case
    out = compute_community_cohesion_score([{"name": "a", "cohesion": 0.5, "size": 5}])
    assert out["_formula"] == ARCHITECTURE_FORMULA


def test_drift_refuses_scores_taken_with_different_rulers():
    # A P4 baseline from before Round 113 carries no `_formula`. Its 16.7 and
    # today's 100 are not a movement of the architecture.
    old = {"community_cohesion": {"score": 16.7}}
    new = {"community_cohesion": {"score": 100.0, "_formula": ARCHITECTURE_FORMULA}}
    measured = structural_drift(old, new)
    assert measured["drift"] is None
    assert any("different formula" in a for a in measured["absent"])


def test_drift_still_compares_one_ruler_with_itself():
    a = {"community_cohesion": {"score": 100.0, "_formula": ARCHITECTURE_FORMULA}}
    b = {"community_cohesion": {"score": 50.0, "_formula": ARCHITECTURE_FORMULA}}
    assert structural_drift(a, b)["drift"] == 0.5


def test_the_template_teaches_no_calls_added_for_a_metric():
    section = (_ROOT / "templates" / "SAD.md").read_text(encoding="utf-8")
    section = section[section.index("### 2.1"):section.index("### 2.2")]
    for taught in ("every accessible function body", "edge budget", "0.4286",
                   "hub module", "Entry points must live inside a hub"):
        assert taught.lower() not in section.lower(), taught


def test_the_p2_reviewer_is_not_asked_to_check_hub_calls():
    from scripts.workflowgen.spec_phase2 import generate_phase2

    js = generate_phase2()
    blocks = (_ROOT / "scripts" / "plangen" / "blocks.py").read_text(encoding="utf-8")
    for text in (js, blocks):
        assert "per-function-body calls" not in text
        assert "CRG cohesion principles" not in text
