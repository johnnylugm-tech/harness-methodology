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

def test_an_unknown_version_is_not_treated_as_unsupported(tmp_path, monkeypatch):
    """The precondition refuses what it KNOWS is wrong, and nothing else.

    Round 80 站11. See test_a_version_that_cannot_be_read_does_not_block_the_run
    for why: refusing on None broke the pinned 2.5.1, and the zero-mutant
    refusal already catches an unsupported tool by its output.
    """
    monkeypatch.setattr(me.shutil, "which", lambda name: "/usr/bin/mutmut"
                        if name == "mutmut" else None)
    monkeypatch.setattr(me, "mutmut_major_version", lambda *_a, **_k: None)

    _ok, _score, msg = me.compute_mutation_score(tmp_path)

    assert "major version" not in msg, (
        f"an unreadable version refused the run instead of letting it speak: {msg!r}"
    )


def test_an_unsupported_mutmut_major_is_refused_rather_than_scored(tmp_path, monkeypatch):
    monkeypatch.setattr(me.shutil, "which", lambda name: "/usr/bin/mutmut"
                        if name == "mutmut" else None)
    monkeypatch.setattr(me, "mutmut_major_version", lambda *_a, **_k: 3)

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
    monkeypatch.setattr(me, "mutmut_major_version", lambda *_a, **_k: 2)

    _ok, _score, msg = me.compute_mutation_score(tmp_path)

    assert "version" not in msg.lower(), (
        f"a supported mutmut was refused by the version check: {msg!r}"
    )


def test_the_version_is_read_from_the_install_behind_the_binary(tmp_path):
    """Not from a CLI flag, and not from this process's own metadata.

    Round 80 站11 — the first draft of this ran `mutmut --version`, and that is
    a flag mutmut 2.x DOES NOT HAVE. Measured against a real 2.5.1 install:

        $ /tmp/mutmut25/bin/mutmut --version
        Error: No such option '--version'                        exit 2
        $ mutmut --version            # 3.3.1
        mutmut, version 3.3.1                                    exit 0

    So the probe answered None for the only version this framework supports,
    and the precondition built on it refused the pinned tool. Every unit test
    stayed green because they all stub `mutmut_major_version`, and the one that
    exercised it fed canned stdout that 2.5.1 never produces — the same shape
    of lie the `_split_runner` double told in 站10.

    Choosing between `mutmut --version` (3.x only) and the `mutmut version`
    subcommand (2.x only) would be sniffing which flag happens to work, which
    is a proxy for the question rather than the question. A console script's
    shebang names the interpreter that will import the package, and that
    interpreter's distribution metadata is the version that will run.

    The two resolution failures are exercised here; the positive direction is
    pinned by test_the_probe_agrees_with_the_mutmut_actually_installed, which
    runs against whatever is really on PATH. (An earlier draft of this test
    asserted None for a shebang pointing at THIS interpreter — which has a
    mutmut installed, so the probe correctly answered 3 and the test's premise
    was the thing that was wrong.)
    """
    not_a_script = tmp_path / "mutmut-binary"
    not_a_script.write_bytes(b"\x7fELF not a console script\n")
    assert me.mutmut_major_version(str(not_a_script)) is None, (
        "a file with no shebang names no interpreter, so there is nothing to ask"
    )

    dangling = tmp_path / "mutmut-dangling"
    dangling.write_text(f"#!{tmp_path}/no-such-python\n", encoding="utf-8")
    assert me.mutmut_major_version(str(dangling)) is None, (
        "an interpreter that will not run answers nothing, and nothing is not "
        "a version"
    )


@pytest.mark.integration
def test_the_probe_agrees_with_the_mutmut_actually_installed():
    """The test that was missing, and whose absence let 站2 ship broken.

    Every other test here stubs the probe. This one asks it about the real
    binary and cross-checks the answer against that install's own metadata by
    a different route, so a probe that works only for the version the author
    happened to have installed cannot stay green.
    """
    import shutil as _shutil
    import subprocess as _subprocess

    path = _shutil.which("mutmut")
    if path is None:
        pytest.skip("no mutmut on PATH — the probe has nothing to be asked about")

    major = me.mutmut_major_version(path)
    assert major is not None, (
        f"the probe could not read the version of the mutmut it will actually "
        f"run ({path}) — this is the exact failure that refused the pinned "
        f"2.5.1 for a round"
    )

    shebang = open(path, encoding="utf-8", errors="replace").readline()
    assert shebang.startswith("#!"), f"not a console script: {shebang!r}"
    interpreter = shebang[2:].strip().split()[0]
    reported = _subprocess.run(  # nosec B603
        [interpreter, "-c",
         "import importlib.metadata as m; print(m.version('mutmut'))"],
        capture_output=True, text=True, timeout=60,
    ).stdout.strip()
    assert reported, f"{interpreter} has no mutmut distribution"
    assert major == int(reported.split(".")[0]), (
        f"probe said major {major}, the install behind {path} is {reported}"
    )


def test_a_version_that_cannot_be_read_does_not_block_the_run(tmp_path):
    """Unknown is not the same as unsupported, and must not refuse.

    Round 80 站11's correction to 站2. The first version of this refused on
    None, on the reasoning that assuming is what produced a 0.0 score out of a
    run that measured nothing. That reasoning was already obsolete in the same
    commit: 站2's other half turns a zero-mutant run into `unscoreable`, so an
    unsupported mutmut is caught by the RUN whether or not the probe answers.
    The version check buys a better diagnosis, not the safety property — and a
    check that can only improve a message must never be able to stop a working
    setup. It did exactly that to the pinned 2.5.1 for the length of one round.
    """
    missing = tmp_path / "not-a-real-binary"

    assert me.mutmut_major_version(str(missing)) is None
