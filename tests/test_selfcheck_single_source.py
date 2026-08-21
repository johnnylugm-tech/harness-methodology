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

There IS a pre-push hook, and it IS active (`core.hooksPath = scripts/hooks`).
It runs the regression-guard registry and `run-phase` preflight. It does not
run ruff and it does not run pytest — so two places each hold their own answer
to "what must be green before this lands", and only one of them is the one
that turns the build red.

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
