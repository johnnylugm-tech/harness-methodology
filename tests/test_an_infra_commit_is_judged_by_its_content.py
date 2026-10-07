"""Round 115 站3 — an "infrastructure commit" is judged by what it changes, not by its subject.

Both git hooks skipped the whole preflight for a commit whose subject starts
`chore(harness):` — `prepare-commit-msg` for that commit, `pre-push` when
every commit in the push carries it. The label is the commit author's word
about its own content, and six workflow prompts told the agent to write it
whenever a hook blocked ("reword commit to start with `chore(harness):`
(NOT --no-verify)"). Measured: taskq-new 210a275 (a test file, HANDOVER,
state), taskq-redo 4bdbfc0 (the Phase 7 deliverables), tts-new 646f010 (a
test file, setup.cfg, the mutation cache) all crossed both hooks that way.

The framework's own producers of such commits change only the `harness`
gitlink (`submodule_sync` commits `-- <submodule>`; the repair flow
`git add harness`), plus the CI workflow `init-project --ci-only` redeploys
(R40). `ensure_project_init`'s init commit is not infrastructure-only; it was
measured to pass `pre-commit-check --phase 1` on a fresh project, so it needs
no skip.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from core.quality_gate.submodule_pin import is_infrastructure_change

REPO = Path(__file__).resolve().parents[1]
CI = ".github/workflows/harness_quality_gate.yml"


def test_the_gitlink_and_the_redeployed_ci_workflow_are_infrastructure():
    assert is_infrastructure_change(["harness"])
    assert is_infrastructure_change(["harness", CI])
    assert is_infrastructure_change([CI])


def test_anything_else_is_not():
    assert not is_infrastructure_change(["harness", "03-development/tests/test_fr02.py"])
    assert not is_infrastructure_change(["harness/core/x.py"])
    assert not is_infrastructure_change(["HANDOVER.md"])
    assert not is_infrastructure_change([]), "a commit that changes nothing is not infrastructure"


def test_the_cli_reads_paths_from_stdin():
    def run(text: str) -> "tuple[int, str]":
        out = subprocess.run([sys.executable, str(REPO / "harness_cli.py"), "infra-commit-check"],
                             input=text, capture_output=True, text=True, cwd=str(REPO))
        return out.returncode, out.stdout.strip()
    assert run("harness\n" + CI + "\n") == (0, "INFRA")
    assert run("harness\nsrc/app.py\n") == (1, "NOT-INFRA")
    assert run("") == (1, "NOT-INFRA")


def test_a_cli_that_exits_zero_without_answering_is_not_a_yes(tmp_path):
    """The hooks act on the printed INFRA, not the exit code: a harness_cli.py
    that ran nothing (edited, half-installed) must not wave a push through."""
    proj = _project(tmp_path)
    base = subprocess.run(["git", "-C", str(proj), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()
    (proj / ".git" / "hooks" / "prepare-commit-msg").unlink()
    _commit(proj, "src/app.py", "feat: x")
    (proj / "harness_cli.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    out = _pre_push(proj, base)
    assert "skipping gate check" not in out.stdout


# ── the hooks themselves ────────────────────────────────────────────────────

_STUB = f"""\
import subprocess, sys
if sys.argv[1] == "infra-commit-check":
    sys.exit(subprocess.run([{str(Path(sys.executable))!r}, {str(REPO / 'harness_cli.py')!r}, *sys.argv[1:]],
                            stdin=sys.stdin).returncode)
print("STUB PREFLIGHT RAN: " + " ".join(sys.argv[1:]))
sys.exit(1)
"""


def _project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(proj), "config", k, v], check=True)
    (proj / "harness_cli.py").write_text(_STUB, encoding="utf-8")
    (proj / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(proj), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(proj), "commit", "-q", "-m", "init"], check=True)
    hooks = proj / ".git" / "hooks"
    for name in ("prepare-commit-msg", "pre-push"):
        shutil.copy(REPO / "scripts" / "hooks" / name, hooks / name)
        (hooks / name).chmod((hooks / name).stat().st_mode | stat.S_IEXEC)
    return proj


def _commit(proj: Path, rel: str, msg: str) -> subprocess.CompletedProcess:
    path = proj / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{rel} {msg}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(proj), "add", rel], check=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(proj), "commit", "-m", msg],
                          capture_output=True, text=True, env=env)


def test_a_project_file_labelled_chore_harness_is_checked_at_commit(tmp_path):
    proj = _project(tmp_path)
    out = _commit(proj, "03-development/tests/test_fr02.py", "chore(harness): P4-mid milestone push")
    assert out.returncode != 0, out.stdout + out.stderr
    assert "STUB PREFLIGHT RAN: pre-commit-check" in out.stdout + out.stderr


def test_a_ci_redeploy_is_not_checked_at_commit(tmp_path):
    proj = _project(tmp_path)
    out = _commit(proj, CI, "chore(harness): redeploy CI template")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "STUB PREFLIGHT RAN" not in out.stdout + out.stderr


def _pre_push(proj: Path, base: str) -> subprocess.CompletedProcess:
    head = subprocess.run(["git", "-C", str(proj), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["bash", str(proj / ".git" / "hooks" / "pre-push"), "origin", "x"],
                          input=f"refs/heads/main {head} refs/heads/main {base}\n",
                          capture_output=True, text=True, cwd=str(proj), env=env)


def test_a_push_of_labelled_project_commits_runs_the_preflight(tmp_path):
    proj = _project(tmp_path)
    base = subprocess.run(["git", "-C", str(proj), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()
    (proj / ".git" / "hooks" / "prepare-commit-msg").unlink()
    _commit(proj, "07-risk/RISK_REGISTER.md", "chore(harness): P7 milestone artifacts")
    out = _pre_push(proj, base)
    assert out.returncode != 0, out.stdout + out.stderr
    assert "STUB PREFLIGHT RAN: run-phase" in out.stdout


def test_a_push_of_only_infrastructure_skips_it(tmp_path):
    proj = _project(tmp_path)
    base = subprocess.run(["git", "-C", str(proj), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()
    _commit(proj, CI, "ci: redeploy the workflow")
    out = _pre_push(proj, base)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "STUB PREFLIGHT RAN" not in out.stdout


def test_no_workflow_prompt_tells_the_agent_to_relabel_a_blocked_commit():
    from scripts.workflowgen.generate_workflows import generate

    for phase in range(1, 9):
        assert "reword commit" not in generate(phase), phase
        assert "start with `chore(harness):`" not in generate(phase), phase
