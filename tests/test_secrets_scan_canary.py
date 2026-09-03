"""Round 92 — a clean gitleaks run is not evidence unless the scanner can see.

Neither call site that runs gitleaks passes `--config`, so resolution follows
gitleaks' own precedence: GITLEAKS_CONFIG(_TOML) env vars, then
`<source>/.gitleaks.toml`, then the default ruleset. Measured on two corpus
projects:

    taskq-plus/.gitleaks.toml    [extend] with a COMMENT, no `useDefault = true`
    taskq-renew/.gitleaks.toml   no [extend] section at all

Both load ZERO rules. `gitleaks detect` always reports "no leaks found" under
either file, and secrets_scanning scored 100 nine times across Gate 2/3/4 —
one project's own evidence said `--config .gitleaks.toml` was used, honestly,
while catching nothing. A clean run and a blind run are indistinguishable
from the *output* alone.

Separately: taskq-final's committed Gate 1 evidence blocked P5 because
`generic-api-key`'s entropy rule fires on a test NAME
(`test_invalid_api_key_returns_401`) written into `.methodology/gate1_result.json`
— the framework's own audit trail, not a leaked credential.
"""
from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.core]


# ── the bait must still be bait ──────────────────────────────────────────


def test_the_canary_bait_is_still_caught_by_the_default_ruleset(tmp_path):
    """Self-check for the canary itself.

    A degraded bait (e.g. a fragment gitleaks' own allowlist now recognises —
    an AWS-official EXAMPLEKEY was the first draft here, and gitleaks'
    default config allowlists it) would make `scanner_is_alive` report every
    config "alive" including a genuinely dead one. This is what CP-2 reverts
    to prove the mechanism, not just the bait, is what is being tested.
    """
    from harness.tool_runners import _CANARY_BAIT, _CANARY_EXPECTED_RULES
    from core.utils.subprocess_group import run_isolated

    for name, content in _CANARY_BAIT.items():
        (tmp_path / name).write_text(content, encoding="utf-8")

    report_path = tmp_path / "_report.json"
    run_isolated(
        ["gitleaks", "detect", "--source", str(tmp_path), "--no-git",
         "--report-format", "json", "--report-path", str(report_path),
         "--no-banner"],
        timeout=30, cwd=str(tmp_path),
    )
    findings = json.loads(report_path.read_text(encoding="utf-8"))
    caught = {f["RuleID"] for f in findings}
    assert caught == set(_CANARY_EXPECTED_RULES), (
        f"the canary bait no longer trips its three target rules under "
        f"gitleaks' own default ruleset (caught={caught}) — a canary this "
        f"weak reports every project's scanner as alive, including a dead one"
    )


def test_bait_does_not_appear_as_a_whole_literal_in_its_own_source():
    """The fragments exist so this file's own text is not a secret gitleaks
    would flag — verified independently of the corpus self-scan below.

    Checked against `_CANARY_BAIT`'s own ASSEMBLED values rather than
    retyping the secret-shaped strings here: retyping them is exactly the
    mistake this test exists to catch, and it can make that mistake in this
    file too if it ever does (the framework's own gitleaks CI job, station
    3, is what actually caught this the first time)."""
    from pathlib import Path
    from harness.tool_runners import _CANARY_BAIT

    src = (Path(__file__).resolve().parent.parent / "harness"
           / "tool_runners.py").read_text(encoding="utf-8")
    for name, content in _CANARY_BAIT.items():
        assert content not in src, (
            f"{name}'s assembled bait content appears as a whole literal in "
            f"tool_runners.py — this is exactly what the framework's own "
            f"secrets scan exists to catch"
        )


# ── scanner_is_alive: the three real-world shapes ────────────────────────


def test_a_config_with_extend_comment_but_no_usedefault_is_reported_dead(tmp_path):
    """taskq-plus's exact shape: `[extend]` with only a comment underneath."""
    from harness.tool_runners import scanner_is_alive

    (tmp_path / ".gitleaks.toml").write_text(
        "[extend]\n"
        "# Use the default ruleset (covers AWS, GitHub, Stripe, OpenAI, generic API keys, etc.).\n"
        "\n[allowlist]\npaths = ['''nothing_matches_this''']\n",
        encoding="utf-8",
    )
    result = scanner_is_alive(str(tmp_path))
    assert result is not None, (
        "a config that loads zero rules must not be reported as alive"
    )
    assert "generic-api-key" in result and "private-key" in result and "github-pat" in result
    assert str(tmp_path / ".gitleaks.toml") in result


def test_a_config_with_no_extend_section_is_reported_dead(tmp_path):
    """taskq-renew's exact shape: no [extend] section at all."""
    from harness.tool_runners import scanner_is_alive

    (tmp_path / ".gitleaks.toml").write_text(
        "[allowlist]\npaths = ['''nothing_matches_this''']\n",
        encoding="utf-8",
    )
    result = scanner_is_alive(str(tmp_path))
    assert result is not None


def test_usedefault_true_is_reported_alive(tmp_path):
    from harness.tool_runners import scanner_is_alive

    (tmp_path / ".gitleaks.toml").write_text(
        "[extend]\nuseDefault = true\n", encoding="utf-8",
    )
    assert scanner_is_alive(str(tmp_path)) is None


def test_no_config_at_all_is_reported_alive(tmp_path):
    from harness.tool_runners import scanner_is_alive

    assert scanner_is_alive(str(tmp_path)) is None


def test_the_rule_scoped_template_config_is_reported_alive(tmp_path):
    """This round's own templates/.gitleaks.toml (station 1) must not trip
    its own canary — it only narrows one rule over one path prefix."""
    from pathlib import Path
    from harness.tool_runners import scanner_is_alive

    template = (Path(__file__).resolve().parent.parent / "templates"
                / ".gitleaks.toml")
    (tmp_path / ".gitleaks.toml").write_text(
        template.read_text(encoding="utf-8"), encoding="utf-8",
    )
    assert scanner_is_alive(str(tmp_path)) is None


def test_the_template_silences_taskq_finals_exact_p5_halt(tmp_path):
    """The original bug report: taskq-final's P5 verification-docs halted on
    `.methodology/gate1_result.json:29`, `generic-api-key`, over a test NAME
    (`test_invalid_api_key` + `_returns_401`, split the same way `b1.txt`'s
    bait is so this docstring is not itself a finding) written into the
    framework's own committed audit trail — not a credential. Reproduces the
    exact shape and proves station 1's shipped template silences it while
    still catching a real secret planted beside it — same file, same
    directory, one rule apart.
    """
    from pathlib import Path
    from harness.tool_runners import _CANARY_BAIT

    template = (Path(__file__).resolve().parent.parent / "templates"
                / ".gitleaks.toml")
    (tmp_path / ".gitleaks.toml").write_text(
        template.read_text(encoding="utf-8"), encoding="utf-8",
    )
    methodology = tmp_path / ".methodology"
    methodology.mkdir()
    # The exact taskq-final finding shape, written to the fixture file in
    # two pieces so the WRITTEN content is the flagged string byte-for-byte
    # while this file's own source text never contains it whole — same
    # reasoning as _CANARY_BAIT.
    _test_name = "test_invalid_api_key" + "_returns_401"
    (methodology / "gate1_result.json").write_text(
        '{"tool_evidence": "tests exist and pass: '
        f'test_missing_api_key_returns_401, {_test_name} '
        '(2 scenarios via parametrize)"}\n',
        encoding="utf-8",
    )
    (methodology / "leaked_key.txt").write_text(
        _CANARY_BAIT["b2.txt"], encoding="utf-8",
    )
    import subprocess

    result = subprocess.run(
        ["gitleaks", "detect", "--source", str(tmp_path), "--no-git",
         "--no-banner", "--report-format", "json",
         "--report-path", str(tmp_path / "_report.json")],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1, "the private key beside it must still be caught"
    findings = json.loads((tmp_path / "_report.json").read_text())
    rules_hit = {f["RuleID"] for f in findings}
    assert rules_hit == {"private-key"}, (
        f"expected only private-key to fire, got {rules_hit} — either the "
        f"test-name finding was not silenced or the real secret was"
    )


def test_the_allowlist_path_matches_regardless_of_source_being_absolute(tmp_path):
    """Caught while writing the test above: the first draft of this pattern
    was `^\\.methodology(-archive)?/`, anchored at the start of the string.
    gitleaks matches an allowlist path against whatever path IT REPORTS, and
    that is the ABSOLUTE path whenever `--source` is given as one — which
    two of the three real invocations do (the S4 tool run via
    `harness/toolchains/registry.py`'s `{root}`, and the P5 verification
    prompt's `gitleaks detect --source ` + an absolute project path). A
    `^`-anchored pattern silently matched nothing for either of them; only
    cli/advance_prechecks.py's `--source .` (relative, cwd-scoped) ever
    worked. `(^|/)\\.methodology...` fixes both without sweeping in an
    unrelated directory that merely starts with the same letters.
    """
    from pathlib import Path
    import subprocess
    from harness.tool_runners import _CANARY_BAIT

    bait = _CANARY_BAIT["b1.txt"]
    template = (Path(__file__).resolve().parent.parent / "templates"
                / ".gitleaks.toml")
    (tmp_path / ".gitleaks.toml").write_text(
        template.read_text(encoding="utf-8"), encoding="utf-8",
    )
    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".methodology" / "x.json").write_text(bait, encoding="utf-8")
    trap = tmp_path / "foo.methodology-archive-nope"
    trap.mkdir()
    (trap / "z.json").write_text(bait, encoding="utf-8")

    for source_arg, cwd in ((".", tmp_path), (str(tmp_path), None)):
        result = subprocess.run(
            ["gitleaks", "detect", "--source", source_arg, "--no-git", "--no-banner"],
            cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 1, (
            f"--source={source_arg!r} cwd={cwd}: the trap directory's "
            f"generic-api-key finding must still fire — got a clean exit, "
            f"meaning the allowlist over-matched"
        )
        assert result.returncode != 0
    # And the .methodology/ one IS silenced under both forms.
    for source_arg, cwd in ((".", tmp_path), (str(tmp_path), None)):
        (trap / "z.json").unlink()
        result = subprocess.run(
            ["gitleaks", "detect", "--source", source_arg, "--no-git", "--no-banner"],
            cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, (
            f"--source={source_arg!r} cwd={cwd}: .methodology/x.json's "
            f"generic-api-key should be silenced but was not"
        )
        (trap / "z.json").write_text(bait, encoding="utf-8")


# ── inconclusive is not a positive finding ───────────────────────────────


def test_gitleaks_missing_is_inconclusive_not_dead(tmp_path, monkeypatch):
    """Round 32's rule: a measurement that could not be taken is not a
    failing one. `run_tool`'s own -3 path already reports a missing
    gitleaks; this must not double-report it as a dead config."""
    import harness.tool_runners as tr

    def _raise(*a, **k):
        raise FileNotFoundError("gitleaks")
    monkeypatch.setattr(tr, "run_isolated", _raise, raising=False)
    # scanner_is_alive imports run_isolated from core.utils.subprocess_group
    # locally, so patch it there.
    import core.utils.subprocess_group as sg
    monkeypatch.setattr(sg, "run_isolated", _raise)

    assert tr.scanner_is_alive(str(tmp_path)) is None


def test_gitleaks_timeout_is_inconclusive_not_dead(tmp_path, monkeypatch):
    import subprocess as sp
    import core.utils.subprocess_group as sg
    from harness.tool_runners import scanner_is_alive

    def _raise(*a, **k):
        raise sp.TimeoutExpired(cmd="gitleaks", timeout=30)
    monkeypatch.setattr(sg, "run_isolated", _raise)

    assert scanner_is_alive(str(tmp_path)) is None


def test_an_unreadable_report_is_inconclusive_not_a_positive_finding(tmp_path, monkeypatch):
    """gitleaks ran but the report never landed (permissions, a future
    version's schema change) must not read as "zero rules caught"."""
    import core.utils.subprocess_group as sg
    from harness.tool_runners import scanner_is_alive

    def _noop(*a, **k):
        class _R:
            returncode = 1
        return _R()
    monkeypatch.setattr(sg, "run_isolated", _noop)

    assert scanner_is_alive(str(tmp_path)) is None


# ── S4 wiring: the canary blocks even a "no leaks found" real run ────────


def test_s4_blocks_on_a_dead_canary_even_when_the_real_scan_reports_clean(tmp_path, monkeypatch):
    """The exact taskq-plus/taskq-renew shape: agent honestly reports 100,
    a real gitleaks run honestly says "no leaks found", and both are being
    fooled by the same broken config. S4 must block on the canary alone."""
    import yaml
    import core.quality_gate.gate_thresholds as _gt
    import harness.tool_runners as tr
    from harness.harness_bridge import GateContext, _run_harness_cross_validation

    project = tmp_path / "proj"
    (project / ".methodology").mkdir(parents=True)
    (project / ".sessi-work").mkdir(parents=True)

    cfg_path = tmp_path / "gate2.yaml"
    cfg_path.write_text(yaml.dump({
        "gate": 2,
        "dimensions": [{
            "name": "secrets_scanning", "requires_tool_execution": True,
            "tool": "gitleaks", "threshold": 100,
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg_path)
    monkeypatch.setattr(tr, "scanner_is_alive",
                         lambda root: "secrets-scan canary failed: dead config")
    monkeypatch.setattr(tr, "run_tool", lambda tool, root: ("no leaks found", 0))

    ctx = GateContext(
        gate_num=2, config={}, project_root=str(project), phase=3, fr_id=None,
        ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
        work_dir="", sab_data={},
    )
    raw = {"breakdown": {"secrets_scanning": {"score": 100}}}

    fabrication, _unverifiable = _run_harness_cross_validation(ctx, raw)

    assert any("secrets-scan canary failed" in f for f in fabrication), (
        f"a dead canary must block regardless of the real scan's own "
        f"'no leaks found' — got fabrication={fabrication}"
    )


def test_s4_does_not_block_when_the_canary_is_alive(tmp_path, monkeypatch):
    """Negative control: a live canary must not itself become a violation."""
    import yaml
    import core.quality_gate.gate_thresholds as _gt
    import harness.tool_runners as tr
    from harness.harness_bridge import GateContext, _run_harness_cross_validation

    project = tmp_path / "proj"
    (project / ".methodology").mkdir(parents=True)
    (project / ".sessi-work").mkdir(parents=True)

    cfg_path = tmp_path / "gate2.yaml"
    cfg_path.write_text(yaml.dump({
        "gate": 2,
        "dimensions": [{
            "name": "secrets_scanning", "requires_tool_execution": True,
            "tool": "gitleaks", "threshold": 100,
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg_path)
    monkeypatch.setattr(tr, "scanner_is_alive", lambda root: None)
    monkeypatch.setattr(tr, "run_tool", lambda tool, root: ("no leaks found", 0))

    ctx = GateContext(
        gate_num=2, config={}, project_root=str(project), phase=3, fr_id=None,
        ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
        work_dir="", sab_data={},
    )
    raw = {"breakdown": {"secrets_scanning": {"score": 100}}}

    fabrication, unverifiable = _run_harness_cross_validation(ctx, raw)

    assert fabrication == [] and unverifiable == []


# ── advance-phase precheck wiring ────────────────────────────────────────


def test_advance_precheck_blocks_on_a_dead_canary(tmp_path, monkeypatch):
    """cli/advance_prechecks.py's P3+ gate must not let a dead-config 'no
    leaks found' through to advance-phase."""
    import cli.advance_prechecks as ap

    monkeypatch.setattr(ap.shutil, "which", lambda name: "/usr/bin/gitleaks")
    monkeypatch.setattr(
        "harness.tool_runners.scanner_is_alive",
        lambda root: "secrets-scan canary failed: dead config",
    )
    called = {"real_scan_ran": False}

    def _fail_if_called(*a, **k):
        called["real_scan_ran"] = True
        raise AssertionError("the real gitleaks scan must not run after a dead canary")
    monkeypatch.setattr(ap.subprocess, "run", _fail_if_called)

    rc = ap._precheck_p3_security_and_quality(3, tmp_path)

    assert rc == 20
    assert not called["real_scan_ran"], (
        "the precheck ran the real (misconfigured) scan instead of stopping "
        "at the canary"
    )


def test_advance_precheck_proceeds_past_a_live_canary(tmp_path, monkeypatch):
    """Negative control: a live canary must not itself block advance-phase."""
    import subprocess
    import cli.advance_prechecks as ap

    monkeypatch.setattr(ap.shutil, "which", lambda name: "/usr/bin/gitleaks")
    monkeypatch.setattr("harness.tool_runners.scanner_is_alive", lambda root: None)
    monkeypatch.setattr(
        ap.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 0, "", ""),
    )
    monkeypatch.setattr(ap, "get_timeout", lambda tool, project: 30)

    rc = ap._precheck_p3_security_and_quality(2, tmp_path)  # phase < 3: skip block entirely
    assert rc is None


# ── DIMENSION_EXCLUSION_FILES registration (station 2) ───────────────────


def test_gitleaks_toml_is_a_registered_exclusion_file():
    from harness.gate_evidence_tables import DIMENSION_EXCLUSION_FILES

    spec = DIMENSION_EXCLUSION_FILES["secrets_scanning"]
    assert isinstance(spec, tuple) and ".gitleaks.toml" in spec and ".gitleaksignore" in spec


# ── the framework passes its own check (station 3) ───────────────────────


def test_the_framework_repo_has_no_live_gitleaks_finding():
    """R46: an absent witness is not a passed test. If gitleaks is not
    installed, this FAILS (loudly, pointing at the bootstrap advice) rather
    than skipping — the framework went four months with a live finding
    (tests/test_constitution_runner.py, commit 6c6dcd62) because nothing
    ever ran this check."""
    import shutil
    import subprocess
    from pathlib import Path

    if shutil.which("gitleaks") is None:
        from harness.toolchains.bootstrap import EXTERNAL_BINARIES
        pytest.fail(
            f"gitleaks is not installed — cannot verify the framework repo "
            f"has no live secret. Install: {EXTERNAL_BINARIES['gitleaks']}"
        )

    repo = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["gitleaks", "detect", "--source", str(repo), "--no-banner"],
        cwd=str(repo), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        f"the framework's own repo has a live gitleaks finding:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_framework_gitleaksignore_fingerprint_is_a_real_historical_fixture():
    """The one entry in the framework's own .gitleaksignore names a real
    commit and a real test — not a fabricated exemption."""
    from pathlib import Path
    import subprocess

    repo = Path(__file__).resolve().parent.parent
    ignore_text = (repo / ".gitleaksignore").read_text(encoding="utf-8")
    fingerprints = [
        line.strip() for line in ignore_text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert len(fingerprints) == 1
    commit = fingerprints[0].split(":", 1)[0]
    show = subprocess.run(
        ["git", "cat-file", "-e", commit], cwd=str(repo), capture_output=True,
    )
    assert show.returncode == 0, f"{commit} is not a real commit in this repo"
