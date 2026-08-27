"""Producing no mutants is not scoring zero on the ones you produced.

Round 80 站2, and Round 32 站4 / Round 35 站2's rule applied to the two sites
they did not reach.

`_compute_mutation_score`'s own docstring already states the contract:

    score: ... **None when success is False** — Round 35 站2. It used to be
    0.0, and every consumer reads the number rather than the flag, so "the
    framework could not measure" and "mutmut ran and every mutant survived"
    arrived as the same value.

Two branches in that same module contradicted it, and one of them sat fifteen
lines below a branch that got it right:

    total == 0 (mutmut)    -> score = 0.0, `return True, score, msg`, and on
                              the way out it wrote a mutation-score ARTIFACT
                              carrying 0.0 with Round 31 站2's provenance
                              stamp — a number that reads as framework-measured
    total == 0 (Stryker)   -> `return True, 0.0, "Stryker produced 0 mutants."`

while the sqlite-unreadable branch immediately above the first one returns
`False, None, ...` for the same underlying fact (nothing was counted). The two
answers are not interchangeable: 0% means the tests killed nothing and the
remedy is to write assertions; zero mutants means nothing was mutated and the
remedy is to fix the scope or the tool. The gate blocked either way, so the
cost was not a false pass — it was telling a project to go kill mutants that
were never produced, which is verbatim what Round 35 站2 was written about.

WHY THE VERSION IS ASKED OF THE BINARY, NOT THE METADATA

`requirements.txt` pins `mutmut==2.5.1` and this module reads mutmut 2.x's
sqlite `Mutant` table; mutmut 3.x has no such cache, so every 3.x run counts
(0, 0) and lands in exactly the branch above. Nothing checked the version —
while the Stryker path has asked `npx stryker --version` since it was written.
Measured on this machine: `mutmut --version` reports 3.3.1 and
`importlib.metadata.version("mutmut")` reports 3.5.0, because they are
different environments. The framework invokes the binary on PATH, so that is
the one whose version decides whether this module can read its output.
"""

from __future__ import annotations

import json

import pytest

import core.quality_gate.mutation_enforcer as me

pytestmark = [pytest.mark.core]


# ── Stryker: zero mutants is unscoreable ─────────────────────────────────────

def _write_stryker_report(project, mutants: list[dict]) -> None:
    report = project / "reports" / "mutation" / "mutation.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps({"files": {"src/a.js": {"mutants": mutants}}}), encoding="utf-8"
    )


def test_stryker_producing_no_mutants_is_not_a_score_of_zero(tmp_path):
    _write_stryker_report(tmp_path, [])

    ok, score, msg = me._compute_stryker_score(tmp_path)

    assert ok is False, (
        f"a Stryker run that produced no mutants measured nothing, so it "
        f"cannot report success; got ok={ok}, score={score}, msg={msg!r}"
    )
    assert score is None, (
        f"zero mutants must not arrive at the gate as the number 0.0 — that is "
        f"the value a suite which killed nothing produces, and the two have "
        f"opposite remedies; got {score!r}"
    )
    assert "0 mutants" in msg


def test_stryker_still_scores_a_run_that_did_produce_mutants(tmp_path):
    """The positive control: the fix must not turn a real measurement away."""
    _write_stryker_report(
        tmp_path, [{"status": "Killed"}, {"status": "Killed"}, {"status": "Survived"}]
    )

    ok, score, msg = me._compute_stryker_score(tmp_path)

    assert ok is True
    assert score == pytest.approx(66.7)
    assert "killed=2" in msg


# ── mutmut: the version of the binary that will actually run ─────────────────

def test_an_unreadable_mutmut_major_is_refused_rather_than_scored(tmp_path, monkeypatch):
    monkeypatch.setattr(me.shutil, "which", lambda name: "/usr/bin/mutmut"
                        if name == "mutmut" else None)
    monkeypatch.setattr(me, "mutmut_major_version", lambda _path: 3)

    ok, score, msg = me.compute_mutation_score(tmp_path)

    assert ok is False, f"got ok={ok}, score={score}, msg={msg!r}"
    assert score is None, (
        "a mutmut whose results this module cannot read produced no "
        "measurement, so it must not hand the gate a number"
    )
    # Pinned to the phrase only the version refusal produces. The first draft
    # asserted `"3" in msg`, and the counter-proof (disabling the precondition)
    # left it GREEN — with the check gone the call falls through to the
    # missing-sources refusal, whose message contains "03-development" and the
    # word "mutmut". A test that passes with the mechanism removed is the
    # R19 shape, caught here by reverting rather than by reading the code.
    assert "major version 3" in msg, (
        f"the refusal has to name the version it found, or the operator "
        f"cannot tell which mutmut is on PATH; got {msg!r}"
    )


def test_a_supported_mutmut_major_is_not_refused_by_the_version_check(tmp_path, monkeypatch):
    """The positive control: 2.x must get past the check, not be turned away.

    It still fails further down — this tmp_path has no sources — but the
    message must be about the project, not about the version.
    """
    monkeypatch.setattr(me.shutil, "which", lambda name: "/usr/bin/mutmut"
                        if name == "mutmut" else None)
    monkeypatch.setattr(me, "mutmut_major_version", lambda _path: 2)

    _ok, _score, msg = me.compute_mutation_score(tmp_path)

    assert "version" not in msg.lower(), (
        f"a supported mutmut was refused by the version check: {msg!r}"
    )


def test_the_version_is_read_from_the_binary_that_will_be_invoked(monkeypatch):
    """`mutmut --version`, not the importable package's metadata.

    Measured on this machine at the time of writing: the binary on PATH says
    3.3.1 and `importlib.metadata.version("mutmut")` says 3.5.0. They are
    different installs, and the framework runs the binary.
    """
    seen: list[list[str]] = []

    class _Res:
        returncode = 0
        stdout = "mutmut, version 2.5.1\n"
        stderr = ""

    def _fake_run(cmd, *a, **kw):
        seen.append(list(cmd))
        return _Res()

    monkeypatch.setattr(me, "run_isolated", _fake_run)

    assert me.mutmut_major_version("/usr/bin/mutmut") == 2
    assert seen and seen[0][0] == "/usr/bin/mutmut", (
        f"the version must be asked of the resolved binary path, got {seen!r}"
    )


def test_a_version_that_cannot_be_read_is_none_not_a_guess(monkeypatch):
    class _Res:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(me, "run_isolated", lambda *a, **kw: _Res())

    assert me.mutmut_major_version("/usr/bin/mutmut") is None
