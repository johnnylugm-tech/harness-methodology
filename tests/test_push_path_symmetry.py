"""Push-path attestation symmetry (弱點強化 Round 8, Station 2).

The pre-push attestation refresh ritual — re-derive attestation.json so the
`_trace_dirty_state` pre-commit probe doesn't fire mid-push — existed as
three verbatim inline copies whose "every push path is symmetric" invariant
lived in comments alone. It broke exactly the way comment-borne invariants
break: 90e35b2 bug 4 found cmd_advance_phase was the one push path that
skipped the refresh, stranding a handover commit whose stale attestation SHA
only surfaced as a blocking failure at the next P5+ push.

The ritual body is now scripts.build_trace_attestation.refresh_attestation;
this suite makes the invariant mechanical:

  1. every registered push path references refresh_attestation in its source
     (plus a negative test proving the checker fires on a missing ritual);
  2. every cmd_* in cli/push_cmds.py is registered, so a future push command
     added there cannot silently opt out — cmd_advance_phase is the one push
     path living outside push_cmds and is hand-registered;
  3. the helper itself never raises: True on success, WARN + False on failure.

phase_hooks' F-2.5 auto-fix block re-derives the same files but is NOT a
push path (different reporting contract) — deliberately outside this
registry; see refresh_attestation's docstring.
"""

from __future__ import annotations

import inspect

import pytest

import scripts.build_trace_attestation as bta
from cli import phase_cmds, push_cmds


pytestmark = [pytest.mark.core]


# Every command that ends in a git commit/push of methodology artifacts.
PUSH_PATH_COMMANDS = [
    (push_cmds, "cmd_push_checkpoint"),
    (push_cmds, "cmd_push_milestone"),
    (phase_cmds, "cmd_advance_phase"),
]


def _symmetry_violations(commands) -> list[str]:
    """Factored out so the negative test can prove the check fires."""
    violations = []
    for module, func_name in commands:
        # Round 81 站7: `cmd_advance_phase` delegates its push to an
        # `_advance_*` helper, so its ritual is one level down and reading the
        # caller alone would report a violation that is not one. The fallback
        # is for the synthetic module the negative control below builds, which
        # has no file to read.
        from tests.support.pipeline import pipeline_source
        try:
            source = pipeline_source(
                "cli/" + module.__name__.rsplit(".", 1)[-1] + ".py", func_name,
                helper_prefix="_advance_")
        except (FileNotFoundError, AssertionError):
            source = inspect.getsource(getattr(module, func_name))
        if "refresh_attestation" not in source:
            violations.append(
                f"{module.__name__}.{func_name} never references "
                f"refresh_attestation — a push path without the pre-push "
                f"attestation refresh is 90e35b2 bug 4 again"
            )
    return violations


def test_every_push_path_refreshes_attestation():
    assert not _symmetry_violations(PUSH_PATH_COMMANDS), "\n".join(
        _symmetry_violations(PUSH_PATH_COMMANDS)
    )


def test_symmetry_check_fires_on_a_missing_ritual():
    """Negative: the checker must flag a push path lacking the ritual."""

    class _FakeModule:
        __name__ = "fake_push_cmds"

        @staticmethod
        def cmd_push_tag(_args):
            return 0  # deliberately skips the pre-push ritual

    violations = _symmetry_violations([(_FakeModule, "cmd_push_tag")])
    assert len(violations) == 1 and "cmd_push_tag" in violations[0]


def test_all_push_cmds_commands_are_registered():
    """A new cmd_* in cli/push_cmds.py must join the registry (or this fails,
    forcing the author to decide whether it is a push path)."""
    defined = {
        name for name, _obj in inspect.getmembers(push_cmds, inspect.isfunction)
        if name.startswith("cmd_")
    }
    registered = {name for mod, name in PUSH_PATH_COMMANDS if mod is push_cmds}
    assert defined == registered, (
        f"cli/push_cmds.py commands not in PUSH_PATH_COMMANDS: "
        f"{sorted(defined - registered)} — register them (they push) or "
        f"document why they don't"
    )


# ---------------------------------------------------------------------------
# The helper's own contract: never raises
# ---------------------------------------------------------------------------

def test_refresh_attestation_success_builds_and_writes(tmp_path, monkeypatch):
    calls = {}
    monkeypatch.setattr(bta, "build_attestation", lambda _project: {"schema": "x"})
    monkeypatch.setattr(
        bta, "write_attestation",
        lambda project, att: calls.setdefault("written", (project, att)),
    )
    assert bta.refresh_attestation(tmp_path) is True
    assert calls["written"] == (tmp_path, {"schema": "x"})


def test_refresh_attestation_failure_warns_and_returns_false(tmp_path, monkeypatch, capsys):
    def _boom(_project):
        raise RuntimeError("matrix rebuild exploded")

    monkeypatch.setattr(bta, "build_attestation", _boom)
    assert bta.refresh_attestation(tmp_path) is False
    out = capsys.readouterr().out
    assert "[WARN] attestation pre-refresh failed: matrix rebuild exploded" in out
