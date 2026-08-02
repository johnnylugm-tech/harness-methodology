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

import inspect

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
    for dim, path in DIMENSION_EXCLUSION_FILES.items():
        assert isinstance(dim, str) and dim
        assert path is None or (isinstance(path, str) and path.startswith(".")), (
            f"{dim}: give a project-root-relative exclusion file, or None to "
            f"state positively that this dimension has no exclusion channel"
        )
    assert DIMENSION_EXCLUSION_FILES["license_compliance"] is None, (
        "scancode takes exclusions on the command line — recording that as None "
        "is what stops the next reader reading it as an omission"
    )


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

def test_the_frameworks_gitleaks_run_excludes_its_own_output(tmp_path):
    """Scope, not waivers. Asserted against the call site because building a
    project that actually runs gitleaks would test gitleaks, not this decision.
    """
    import cli.phase_cmds as pc

    src = inspect.getsource(pc)
    assert '"gitleaks", "detect", "--source", ".", *_gl_excludes' in src, (
        "the framework's gitleaks invocation stopped passing exclusions"
    )
    for path in (".sessi-work", "__pycache__"):
        assert f'"{path}",' in src or f'"{path}"' in src, (
            f"{path} is back in the secrets-scan scope — findings there are the "
            f"scanner reading its own exhaust, and each one becomes a "
            f"fingerprint waiver that also silences real findings on that file"
        )
