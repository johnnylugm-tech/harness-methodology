#!/usr/bin/env python3
"""ensure_project_init.py — Check and initialize harness-methodology environment & project.

Fast-paths: if the target project and execution environment are already fully
initialized, it reports skipping and exits 0 immediately (< 50ms).

Otherwise, executes the complete initialization cycle:
  1. Bootstraps the Python virtualenv (.venv) using scripts/bootstrap_env.py.
  2. Runs harness_cli.py init-project to install:
     - CI workflow (.github/workflows/harness_quality_gate.yml)
     - Gitleaks config (.gitleaks.toml)
     - Phase directory structure & templates
     - FSM state (.methodology/state.json)
     - Trace attestation (.methodology/trace/attestation.json)
     - Root wrapper (if submodule layout)
  3. Ensures Git hooks (setup-git-hooks.sh) are wired.
  4. Ensures gate tools (Tier 1 tools) are installed.
  5. If new or modified files were produced, commits and pushes to origin.

STDLIB ONLY for initial execution, so it runs reliably on any ambient Python 3.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _find_harness_root(project_root: Path) -> Path:
    """Find the harness framework root directory."""
    # 1. Submodule layout: project_root/harness/
    submodule = project_root / "harness"
    if (submodule / "harness_cli.py").exists():
        return submodule.resolve()
    # 2. Sibling / self layout: scripts/ sibling to harness_cli.py
    here = Path(__file__).resolve().parent.parent
    if (here / "harness_cli.py").exists():
        return here
    # 3. Direct project root
    if (project_root / "harness_cli.py").exists():
        return project_root.resolve()
    return here


def check_project_init(project_root: Path) -> tuple[bool, list[str]]:
    """Check if target project and execution environment are fully initialized.

    Returns:
        (is_ok, missing_items)
    """
    missing: list[str] = []

    # 1. FSM State (.methodology/state.json)
    state_file = project_root / ".methodology" / "state.json"
    if not state_file.exists():
        missing.append(".methodology/state.json")
    else:
        try:
            # state-io-exempt: stdlib-only startup script runs before core or venv imports exist
            data = json.loads(state_file.read_text(encoding="utf-8"))
            if not isinstance(data.get("current_phase"), int):
                missing.append(".methodology/state.json (invalid or missing current_phase)")
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[ensure-init] state.json unparseable: {exc}", file=sys.stderr)
            missing.append(".methodology/state.json (unparseable JSON)")

    # 2. Trace Attestation (.methodology/trace/attestation.json)
    attestation_file = project_root / ".methodology" / "trace" / "attestation.json"
    if not attestation_file.exists():
        missing.append(".methodology/trace/attestation.json")

    # 3. CI Workflow
    deployed_ci = project_root / ".github" / "workflows" / "harness_quality_gate.yml"
    framework_ci = project_root / ".github" / "workflows" / "harness_ci.yml"
    if not deployed_ci.exists() and not framework_ci.exists():
        missing.append(".github/workflows/harness_quality_gate.yml")

    # 4. Gitleaks config
    gitleaks_file = project_root / ".gitleaks.toml"
    template_gitleaks = project_root / "templates" / ".gitleaks.toml"
    if not gitleaks_file.exists() and not template_gitleaks.exists():
        missing.append(".gitleaks.toml")

    # 5. Python virtual environment and core packages
    venv_py = project_root / ".venv" / "bin" / "python"
    if sys.platform == "win32":
        win_py = project_root / ".venv" / "Scripts" / "python.exe"
        if win_py.exists():
            venv_py = win_py

    if not venv_py.exists():
        missing.append(".venv/bin/python")
    else:
        # Verify basic runtime dependencies in that interpreter
        try:
            probe = subprocess.run(
                [str(venv_py), "-c", "import yaml, pytest"],
                capture_output=True,
            )
            if probe.returncode != 0:
                missing.append("python runtime dependencies (yaml, pytest)")
        except (subprocess.SubprocessError, OSError) as exc:
            print(f"[ensure-init] python interpreter check failed: {exc}", file=sys.stderr)
            missing.append(f"python interpreter check failed: {exc}")

    # 6. Submodule root wrapper (if applicable)
    submodule_cli = project_root / "harness" / "harness_cli.py"
    root_cli = project_root / "harness_cli.py"
    if submodule_cli.exists() and not root_cli.exists():
        missing.append("harness_cli.py root wrapper")

    # 7. Git hooks
    git_dir = project_root / ".git"
    if git_dir.exists():
        hooks_path = ""
        try:
            hp = subprocess.run(
                ["git", "-C", str(project_root), "config", "core.hooksPath"],
                capture_output=True,
                text=True,
            )
            if hp.returncode == 0:
                hooks_path = (hp.stdout or "").strip()
        except (subprocess.SubprocessError, OSError) as exc:
            print(f"[ensure-init] git config core.hooksPath check failed: {exc}", file=sys.stderr)

        legacy_hook = git_dir / "hooks" / "prepare-commit-msg"
        if not hooks_path and not legacy_hook.exists():
            missing.append("git hooks (core.hooksPath or prepare-commit-msg)")

    return (len(missing) == 0, missing)


def ensure_project_init(
    project_root: Path,
    phase: int = 1,
    skip_push: bool = False,
    verbose: bool = False,
) -> int:
    """Ensure that the project and environment are fully initialized.

    If already complete, returns 0 immediately.
    Otherwise, executes bootstrap_env, init-project, git hooks, and commits/pushes.
    """
    project_root = project_root.resolve()
    is_ok, missing = check_project_init(project_root)

    if is_ok:
        sys.stderr.write(
            "[ensure-init] Project and environment are fully initialized. (skipping)\n"
        )
        return 0

    sys.stderr.write(
        f"[ensure-init] Incomplete initialization detected: {', '.join(missing)}\n"
    )
    sys.stderr.write(
        "[ensure-init] Running harness-methodology environment & project initialization...\n"
    )

    harness_root = _find_harness_root(project_root)

    # ── Step 1: Bootstrap Python Environment ────────────────────────────────
    bootstrap_script = harness_root / "scripts" / "bootstrap_env.py"
    if not bootstrap_script.exists():
        bootstrap_script = project_root / "scripts" / "bootstrap_env.py"

    if bootstrap_script.exists():
        sys.stderr.write(f"[ensure-init] [1/4] Bootstrapping python virtualenv ({bootstrap_script})...\n")
        cmd = [sys.executable, str(bootstrap_script), "--project", str(project_root)]
        proc = subprocess.run(cmd, capture_output=not verbose, text=True)
        if proc.returncode != 0:
            err = proc.stderr if proc.stderr else proc.stdout
            sys.stderr.write(f"[ensure-init] [BLOCKED] bootstrap_env failed:\n{err}\n")
            return 1
    else:
        sys.stderr.write("[ensure-init] [WARN] bootstrap_env.py not found; skipping venv bootstrap step\n")

    # ── Step 2: Run init-project ────────────────────────────────────────────
    venv_py = project_root / ".venv" / "bin" / "python"
    if sys.platform == "win32":
        win_py = project_root / ".venv" / "Scripts" / "python.exe"
        if win_py.exists():
            venv_py = win_py
    py_exec = str(venv_py) if venv_py.exists() else sys.executable

    harness_cli = project_root / "harness" / "harness_cli.py"
    if not harness_cli.exists():
        harness_cli = project_root / "harness_cli.py"
    if not harness_cli.exists():
        harness_cli = harness_root / "harness_cli.py"

    if harness_cli.exists():
        sys.stderr.write(f"[ensure-init] [2/4] Initializing project via {harness_cli}...\n")
        cmd = [py_exec, str(harness_cli), "init-project", "--project", str(project_root), "--phase", str(phase)]
        proc = subprocess.run(cmd, capture_output=not verbose, text=True)
        if proc.returncode != 0:
            err = proc.stderr if proc.stderr else proc.stdout
            sys.stderr.write(f"[ensure-init] [BLOCKED] init-project failed:\n{err}\n")
            return 1
    else:
        sys.stderr.write("[ensure-init] [BLOCKED] harness_cli.py not found — cannot initialize project\n")
        return 1

    # ── Step 3: Wire Git Hooks ──────────────────────────────────────────────
    hooks_script = harness_root / "scripts" / "setup-git-hooks.sh"
    if not hooks_script.exists():
        hooks_script = project_root / "scripts" / "setup-git-hooks.sh"

    if hooks_script.exists() and (project_root / ".git").exists():
        sys.stderr.write(f"[ensure-init] [3/4] Installing git hooks ({hooks_script})...\n")
        proc = subprocess.run(["bash", str(hooks_script)], cwd=str(project_root), capture_output=not verbose, text=True)
        if proc.returncode != 0 and verbose:
            sys.stderr.write(f"[ensure-init] [WARN] setup-git-hooks returned non-zero:\n{proc.stderr}\n")

    # ── Step 4: Commit and Push if files changed ────────────────────────────
    if (project_root / ".git").exists():
        sys.stderr.write("[ensure-init] [4/4] Checking git status for initialization changes...\n")
        try:
            status_proc = subprocess.run(
                ["git", "-C", str(project_root), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
            )
            dirty = bool(status_proc.stdout.strip())
            if dirty:
                sys.stderr.write("[ensure-init] Staging and committing project initialization artifacts...\n")
                subprocess.run(["git", "-C", str(project_root), "add", "-A"], check=True)
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(project_root),
                        "commit",
                        "-m",
                        "chore(harness): initialize project and environment via harness-methodology",
                    ],
                    check=True,
                )
                if not skip_push:
                    remotes = subprocess.run(
                        ["git", "-C", str(project_root), "remote"],
                        capture_output=True,
                        text=True,
                    ).stdout.strip()
                    if remotes:
                        sys.stderr.write("[ensure-init] Pushing initialization commit to origin...\n")
                        push_proc = subprocess.run(
                            ["git", "-C", str(project_root), "push", "origin", "HEAD"],
                            capture_output=True,
                            text=True,
                        )
                        if push_proc.returncode != 0:
                            sys.stderr.write(
                                f"[ensure-init] [WARN] push failed (non-blocking): {push_proc.stderr.strip()[:200]}\n"
                            )
            else:
                sys.stderr.write("[ensure-init] Working tree clean; nothing to commit.\n")
        except (subprocess.SubprocessError, OSError) as exc:
            print(f"[ensure-init] [WARN] Git commit/push step encountered error: {exc}", file=sys.stderr)

    # ── Final Verification ──────────────────────────────────────────────────
    still_ok, still_missing = check_project_init(project_root)
    if not still_ok:
        sys.stderr.write(
            f"[ensure-init] [BLOCKED] Incomplete after initialization: {', '.join(still_missing)}\n"
        )
        return 1

    sys.stderr.write("[ensure-init] OK: Project initialization and environment setup complete.\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check and ensure harness-methodology environment & project initialization."
    )
    parser.add_argument(
        "-p", "--project", default=".", help="Target project root directory (default: .)"
    )
    parser.add_argument(
        "--phase", type=int, default=1, help="Initial phase if initializing (default: 1)"
    )
    parser.add_argument(
        "--check-only", action="store_true", help="Only check status; do not perform repairs"
    )
    parser.add_argument(
        "--no-push", action="store_true", help="Do not git push committed changes"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Print detailed command output"
    )
    args = parser.parse_args(argv)

    project_root = Path(args.project).resolve()

    if args.check_only:
        is_ok, missing = check_project_init(project_root)
        if is_ok:
            sys.stderr.write("[ensure-init] Project and environment are fully initialized.\n")
            return 0
        sys.stderr.write(f"[ensure-init] Incomplete: {', '.join(missing)}\n")
        return 1

    return ensure_project_init(
        project_root,
        phase=args.phase,
        skip_push=args.no_push,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    raise SystemExit(main())
