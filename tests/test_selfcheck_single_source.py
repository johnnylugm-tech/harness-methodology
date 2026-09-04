"""Round 67 站0 — push-time and CI-time read one list of checks.

Of the last 25 CI runs on this repo, 12 were red. Of the 9 whose logs are
still retrievable, 6 failed on a check that is fully deterministic and runs in
seconds locally:

    test_file_size_ratchet::test_production_file_line_ratchet          ×3
    test_workflow_js_conventions::(headroom / measured-size ratchet)   ×2
    test_patch_discipline::test_private_patch_ratchet                  ×1
    test_spec_contract::test_id_06_type_safety_clean                   ×1
    Lint (ruff)                                                        ×1

None of these needs a runner, a network, or luck. Every one of them was
knowable before the push.

There IS a pre-push hook. It runs the regression-guard registry and `run-phase`
preflight. It does not run ruff and it does not run pytest — so two places each
hold their own answer to "what must be green before this lands", and only one
of them is the one that turns the build red.

Round 80 站3 removed a sentence that used to sit here: "and it IS active
(`core.hooksPath = scripts/hooks`)". Nothing in this file asserted it, and in
the clone Round 80 was written in it was false — core.hooksPath was unset and
`.git/hooks/pre-push` did not exist, so none of the hook's checks could fire.
Whether the hook is wired is now asked by `scripts/check_hook_wiring.sh`, the
first step of self_check.sh, and pinned by
tests/test_hook_wiring_is_verified.py. A premise stated in a guard's docstring
is not a guard.

This is the same defect shape as the rest of Round 67, in the process layer:
two derivations of one rule. The fix is one script both call.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SELF_CHECK = REPO / "scripts" / "self_check.sh"
CI_WORKFLOW = REPO / ".github" / "workflows" / "harness_ci.yml"
PRE_PUSH = REPO / "scripts" / "hooks" / "pre-push"


def test_the_self_check_script_exists_and_is_executable():
    assert SELF_CHECK.is_file(), (
        f"{SELF_CHECK.relative_to(REPO)} does not exist — there is no single "
        f"statement of what must be green before a push"
    )
    import os
    assert os.access(SELF_CHECK, os.X_OK), (
        "the script both CI and the hook invoke has to be executable"
    )


def test_ci_runs_the_self_check_script():
    body = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/self_check.sh" in body, (
        "the Framework Self-Tests job does not call the shared script — it is "
        "still holding its own copy of the list"
    )


def test_the_pre_push_hook_runs_the_self_check_script():
    body = PRE_PUSH.read_text(encoding="utf-8")
    assert "self_check.sh" in body, (
        "the pre-push hook does not run the checks CI runs. Six of the last "
        "twelve red builds were deterministic failures this hook could have "
        "caught"
    )


def test_the_hook_actually_reaches_the_self_check(tmp_path):
    """Naming the script is not running it.

    Round 67 站8's counter-proof CP-6 found this hole in the test above: the
    mutation `if false && [ -x .../self_check.sh ]` disables the call
    completely and leaves the string in the file, so the grep-shaped guard
    stayed green. That is Round 64's shape — a guard that reads as enforcement
    and is not — so it is closed here rather than noted.

    This runs the real hook over a scratch repo with stand-ins for everything
    it shells out to, and asks the only question that matters: did
    self_check.sh execute? The hook's later steps (run-phase) fail in this
    fixture and that is fine — the sentinel is written before them.
    """
    import subprocess

    def _git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args],  # nosec B603 B607
                       capture_output=True, check=False)

    _git("init", "-q")
    _git("config", "user.email", "t@t")
    _git("config", "user.name", "t")

    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests").mkdir()
    sentinel = tmp_path / "self_check_ran"
    (tmp_path / "scripts" / "self_check.sh").write_text(
        f"#!/bin/bash\ntouch {sentinel}\nexit 0\n", encoding="utf-8")
    (tmp_path / "scripts" / "self_check.sh").chmod(0o755)
    (tmp_path / "scripts" / "verify_regression_guards.py").write_text(
        "import sys; sys.exit(0)\n", encoding="utf-8")
    (tmp_path / "tests" / "REGRESSION_GUARDS.yaml").write_text("[]\n", encoding="utf-8")
    (tmp_path / "harness_cli.py").write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".methodology" / "state.json").write_text(
        '{"current_phase": 1}', encoding="utf-8")
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-qm", "feat: a change that is not a harness chore")

    head = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False).stdout.strip()

    subprocess.run(  # nosec B603
        ["bash", str(PRE_PUSH)], cwd=str(tmp_path),
        input=f"refs/heads/main {head} refs/heads/main {'0' * 40}\n",
        capture_output=True, text=True, timeout=120, check=False,
    )

    assert sentinel.exists(), (
        "the pre-push hook ran to completion without executing "
        "scripts/self_check.sh. The call site is present in the file and "
        "unreachable — which is exactly what a text search cannot tell you"
    )


def test_ci_does_not_keep_a_second_copy_of_the_checks():
    """The point is one source, not two that agree today.

    A `run: ruff check .` step beside the script call is how they drift: the
    next check added to one of them is missing from the other, and the gap is
    invisible until a push goes red.
    """
    body = CI_WORKFLOW.read_text(encoding="utf-8")
    # Only the Framework Self-Tests job is in scope; the other jobs run
    # different things (manifests, cross-refs, the P8 archive check) and are
    # not what a pre-push hook should be replaying.
    job = re.split(r"\n  \w[\w-]*:\n", "\n" + body)
    self_tests = next((b for b in job if "Framework Self-Tests" in b), "")
    assert self_tests, "could not locate the Framework Self-Tests job"

    strays = [
        line.strip() for line in self_tests.splitlines()
        if re.search(r"run:\s*(ruff check|pytest |python scripts/verify_regression)",
                     line)
    ]
    assert not strays, (
        f"Framework Self-Tests still runs checks outside the shared script: "
        f"{strays}. Each of these is a rule the pre-push hook cannot know about"
    )


def test_pre_push_hook_passes_no_tests_flag():
    """Unit tests run in CI rather than blocking every local push for minutes."""
    body = PRE_PUSH.read_text(encoding="utf-8")
    assert "self_check.sh\" --no-tests" in body or "self_check.sh --no-tests" in body, (
        "pre-push hook should pass --no-tests to self_check.sh so unit tests run in CI"
    )


def test_self_check_script_supports_no_tests_flag():
    body = SELF_CHECK.read_text(encoding="utf-8")
    assert "--no-tests" in body, (
        "scripts/self_check.sh must support --no-tests to skip unit tests"
    )

