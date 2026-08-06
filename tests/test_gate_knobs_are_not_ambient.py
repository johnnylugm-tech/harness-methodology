"""A number that decides a gate must not come from the ambient shell.

Round 40 站0. `docs/CONFIGURATION.md` has a section titled "Deliberately NOT
configurable (anti-backdoor)" whose first sentence is that a configurable floor
is a backdoor, and Round 38 wrote there, about the architecture dimension:

    "What they cannot do: excuse a genuinely oversized community. A ~100-member
     community is a finding, and `_community_oversized` is deliberately not
     calibratable."

Measured 2026-08-06, that sentence was false. `harness/ssi/scripts/crg_analysis.py`
read all three of the constants that decide the architecture score from the
environment:

    COHESION_HEALTHY   = _tf("CRG_COHESION_HEALTHY",   0.3)
    COMMUNITY_OVERSIZED= _ti("CRG_COMMUNITY_OVERSIZED", 50)
    COMMUNITY_MIN_SIZE = _ti("CRG_COMMUNITY_MIN_SIZE",   5)

and `compute_community_cohesion_score` — the formula `run_independent_crg`
reuses to produce the framework-owned `architecture_score`, the number
`crg-arch-check` blocks CI on — reads all three straight out of module scope.
`CRG_COMMUNITY_OVERSIZED=1000` in a shell profile turns a 97-member god
community into a healthy one, in CI as easily as locally, leaving no trace in
the metrics file beyond a `_community_oversized` field nobody diffs.

The committed answer already exists and is the one Round 38 sanctioned:
`crg_cohesion_healthy` in `.methodology/harness_config.json`, which is
reviewed, versioned, and applies to CI and to a local run alike. An env var is
the opposite of all three.

So the rule is narrower than "no env vars": *these* numbers, the ones that move
a gate verdict, come from the config file or from nowhere. The six other
`CRG_*` knobs in that module classify recon severity and never reach
`architecture_score`; they stay overridable and are registered in
docs/CONFIGURATION.md instead.
"""

from __future__ import annotations

import importlib

import pytest

pytestmark = [pytest.mark.core]

_MODULE = "harness.ssi.scripts.crg_analysis"

# Every constant `compute_community_cohesion_score` consults, paired with an
# env var that would move it far enough to flip a verdict.
_VERDICT_CONSTANTS = {
    "COHESION_HEALTHY": ("CRG_COHESION_HEALTHY", "0.01"),
    "COMMUNITY_OVERSIZED": ("CRG_COMMUNITY_OVERSIZED", "1000"),
    "COMMUNITY_MIN_SIZE": ("CRG_COMMUNITY_MIN_SIZE", "999"),
}

# One community that fails on size and one that fails on cohesion — the two
# shapes taskq-renew's 57.1 was made of.
_COMMUNITIES = [
    {"name": "god-module", "cohesion": 0.9, "size": 97, "files": ["src/a.py"]},
    {"name": "loose", "cohesion": 0.05, "size": 8, "files": ["src/b.py"]},
]


def _fresh_module(monkeypatch: pytest.MonkeyPatch, env: "dict[str, str]"):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(importlib.import_module(_MODULE))


def test_the_baseline_score_is_what_makes_this_test_meaningful(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive control: both communities must be unhealthy with no env set,
    otherwise the assertions below would hold for an uninteresting reason."""
    mod = _fresh_module(monkeypatch, {})
    result = mod.compute_community_cohesion_score(_COMMUNITIES)
    assert result["score"] == 0.0, result
    assert result["total"] == 2 and result["healthy"] == 0


@pytest.mark.parametrize("const,env,value", [
    (c, e, v) for c, (e, v) in _VERDICT_CONSTANTS.items()
])
def test_no_env_var_moves_a_constant_the_architecture_score_reads(
    monkeypatch: pytest.MonkeyPatch, const: str, env: str, value: str,
) -> None:
    baseline = getattr(_fresh_module(monkeypatch, {}), const)
    under_env = getattr(_fresh_module(monkeypatch, {env: value}), const)
    assert under_env == baseline, (
        f"{const} changed from {baseline} to {under_env} because {env} was set. "
        f"It decides the framework-owned architecture_score that crg-arch-check "
        f"blocks CI on, so an ambient shell variable is a gate backdoor. "
        f"Per-project calibration belongs in .methodology/harness_config.json "
        f"(crg_cohesion_healthy / crg_excludes)."
    )


@pytest.mark.parametrize("env,value", list(_VERDICT_CONSTANTS.values()))
def test_no_env_var_moves_the_architecture_score_itself(
    monkeypatch: pytest.MonkeyPatch, env: str, value: str,
) -> None:
    """The constant is the mechanism; the score is what anyone actually cares
    about. Asserting on both means a future indirection cannot pass this file
    while still letting the shell decide the verdict."""
    mod = _fresh_module(monkeypatch, {env: value})
    result = mod.compute_community_cohesion_score(_COMMUNITIES)
    assert result["score"] == 0.0, (
        f"setting {env}={value} moved the architecture score to "
        f"{result['score']} — {result}"
    )


def test_the_committed_calibration_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing the env layer must not remove the sanctioned route.

    `crg_cohesion_healthy` in harness_config.json reaches the formula as the
    `cohesion_healthy` parameter (plumbed by crg_independent via
    core.harness_config.get_crg_settings). That is the reviewed, committed
    knob Round 38 named as the answer to a CRG false positive, and it has to
    keep working after this file's rule is in force.
    """
    mod = _fresh_module(monkeypatch, {})
    result = mod.compute_community_cohesion_score(
        [{"name": "loose", "cohesion": 0.05, "size": 8, "files": ["src/b.py"]}],
        cohesion_healthy=0.01,
    )
    assert result["score"] == 100.0, result
