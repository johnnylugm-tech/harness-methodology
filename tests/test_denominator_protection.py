"""Round 30 站6 — the scored party must not quietly own the denominator.

taskq-advance scored `secrets_scanning: 100` on a `.gitleaksignore` carrying 8
fingerprint exemptions, and the file was UNTRACKED — a fresh clone would score
differently. Round 29 站6 closed that half (untracked exclusion file → S6
violation) and left two open:

1. Three of those 8 exemptions silence findings inside
   `.sessi-work/round_1/tools/secrets.json` — gitleaks' own report from the
   previous round — and two more silence `__pycache__/*.pyc` mirrors of a test
   fixture. Neither is a secret in the project; both are the scan reading its
   own exhaust. The fix is scope, not a growing waiver list: every fingerprint
   waiver is a hole that stays open for real findings on the same file.
2. A committed `.gitleaksignore` still moves the score when a line is added.
   "It is in git" says nothing about WHICH version produced this verdict, so the
   digest goes in beside the tool outputs (Round 27 站3's channel).

The registry is the third piece: `license_compliance: None` is a positive
statement that scancode has no exclusion FILE (its exclusions are command-line),
so the next reader does not have to guess whether it was forgotten.
"""
from __future__ import annotations

import pytest

import harness_cli  # noqa: F401  entry-first load order
import core.quality_gate.gate_thresholds as _gt  # noqa: E402
from harness.harness_bridge import (  # noqa: E402
    DIMENSION_EXCLUSION_FILES,
    _check_tool_evidence,
)

pytestmark = [pytest.mark.core]


class _Ctx:
    def __init__(self, project_root, gate_num=2):
        self.project_root = str(project_root)
        self.gate_num = gate_num
        self.work_dir = str(project_root)


def _cfg(tmp_path):
    """A gate config with one tool dimension, so S3 has something to walk."""
    path = tmp_path / "gate2_p3_exit.yaml"
    path.write_text(
        "dimensions:\n"
        "  - {name: linting, tool: ruff, requires_tool_execution: true}\n",
        encoding="utf-8",
    )
    return path


def _raw():
    return {"breakdown": {"linting": {"score": 100,
                                      "tool_evidence": "All checks passed!"}}}


# ── the registry ────────────────────────────────────────────────────────

def test_every_dimension_states_its_exclusion_file_or_states_it_has_none():
    assert DIMENSION_EXCLUSION_FILES, "an empty registry protects nothing"
    for dim, spec in DIMENSION_EXCLUSION_FILES.items():
        assert isinstance(dim, str) and dim
        if spec is None:
            continue
        # Round 32 站5: a tuple is allowed because a declaration can live in
        # more than one file — testpaths may be in pytest.ini, pyproject.toml
        # or setup.cfg, and whichever exists moves the score.
        paths = (spec,) if isinstance(spec, str) else spec
        assert isinstance(paths, tuple) and paths, (
            f"{dim}: give a project-root-relative exclusion file (or a tuple "
            f"of candidates), or None to state positively that this dimension "
            f"has no exclusion channel"
        )
        for path in paths:
            assert isinstance(path, str) and path and not path.startswith("/"), (
                f"{dim}: {path!r} is not a project-root-relative path"
            )
    assert DIMENSION_EXCLUSION_FILES["license_compliance"] is None, (
        "scancode takes exclusions on the command line — recording that as None "
        "is what stops the next reader reading it as an omission"
    )


def test_the_mutation_denominator_has_an_entry():
    """Round 31 站4. setup.cfg's [mutmut] paths_to_exclude drops files from the
    mutant pool and is written by the party being scored — the same shape as
    .gitleaksignore, which is why that one is already here. It was missing
    because the registry was built from the dimension that had a dot-file, not
    from the question the registry asks."""
    assert DIMENSION_EXCLUSION_FILES.get("mutation_testing") == "setup.cfg"


# ── the digest ──────────────────────────────────────────────────────────

def test_the_exclusion_file_is_fingerprinted_into_the_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(_gt, "gate_config_path", lambda _n: _cfg(tmp_path))
    (tmp_path / ".gitleaksignore").write_text(
        "path/to/file.py:generic-api-key:42\n", encoding="utf-8"
    )
    digests: dict = {}
    _check_tool_evidence(_Ctx(tmp_path), _raw(), digests)
    key = "secrets_scanning::.gitleaksignore"
    assert key in digests, (
        f"the exclusion list that moved the score is not in the verdict: "
        f"{sorted(digests)}"
    )
    assert digests[key].get("sha256")


def test_changing_the_exclusion_list_changes_the_fingerprint(tmp_path, monkeypatch):
    """The point of the digest: two verdicts scored under different exemption
    lists must be distinguishable from the artifacts alone."""
    monkeypatch.setattr(_gt, "gate_config_path", lambda _n: _cfg(tmp_path))
    key = "secrets_scanning::.gitleaksignore"
    excl = tmp_path / ".gitleaksignore"

    excl.write_text("a.py:generic-api-key:1\n", encoding="utf-8")
    first: dict = {}
    _check_tool_evidence(_Ctx(tmp_path), _raw(), first)

    excl.write_text("a.py:generic-api-key:1\nb.py:generic-api-key:2\n", encoding="utf-8")
    second: dict = {}
    _check_tool_evidence(_Ctx(tmp_path), _raw(), second)

    assert first[key]["sha256"] != second[key]["sha256"]


def test_no_exclusion_file_means_no_digest_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(_gt, "gate_config_path", lambda _n: _cfg(tmp_path))
    digests: dict = {}
    _check_tool_evidence(_Ctx(tmp_path), _raw(), digests)
    assert not any("gitleaksignore" in k for k in digests)


# ── the scan's own scope ────────────────────────────────────────────────

def test_the_scan_scope_change_stays_withdrawn(tmp_path):
    """Round 30 站6's third piece was WITHDRAWN. This pins the reason.

    The plan was to exclude .sessi-work/ and __pycache__ from the framework's
    gitleaks run, because taskq-advance's .gitleaksignore silences 3 findings
    inside gitleaks' own prior report and 2 inside .pyc mirrors of a fixture.
    Both halves of that reasoning turned out to be wrong:

      - `--exclude-path` does not exist. gitleaks 8.30.1 `detect` takes
        --source / --no-git / --config / --baseline-path; path allowlists live
        in .gitleaks.toml. The flag was assumed rather than checked, and it made
        the call exit non-zero → rc 20 "secrets detected" on 6 unrelated tests.
      - The invocation is git mode, which scans COMMITS. With .sessi-work/
        gitignored it is not in any commit: a probe reports "1 commits scanned,
        ~20 bytes" against ~56 for the same tree under --no-git. Those
        .gitleaksignore entries come from the AGENT's own working-tree run,
        which the framework neither issues nor can scope from here.

    Re-open only with a measurement, not a plan: if the framework ever runs
    gitleaks in --no-git mode, the exclusions belong in a generated
    .gitleaks.toml, and this test should be replaced rather than deleted.
    """
    from tests.support.pipeline import pipeline_source

    # Round 82 站2: the invocation moved to cli/advance_prechecks.py with the
    # rest of `_precheck_p3_security_and_quality`. Reading `cli.phase_cmds`
    # whole was never the subject — the subject is the precheck pipeline that
    # issues the scan, and `pipeline_source` follows it wherever it lives.
    # Naming the new module here instead would put the same defect one move
    # further down the road.
    src = pipeline_source("cli/phase_cmds.py", "_advance_prechecks",
                          helper_prefix="_precheck_")
    assert '"gitleaks", "detect", "--source", "."' in src
    # Matches the ARGUMENT form (a string literal in the arg list), not the
    # flag name in the docstring above — which quotes it on purpose.
    assert '"--exclude-path' not in src and "f\"--exclude-path" not in src, (
        "gitleaks has no --exclude-path flag; passing one makes the scan exit "
        "non-zero, which this code reads as 'secrets detected'"
    )
