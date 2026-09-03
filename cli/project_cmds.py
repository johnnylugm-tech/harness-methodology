"""Project-level commands (init-project, status, load-context, read-file, effort, doctor, amend-sab, kill-switch, audit-structure, audit-phase).

Extracted verbatim from harness_cli.py (方案六); helpers moved home in
絞殺者續章 S4 — this module no longer imports harness_cli (all
dependencies are direct stdlib/core/harness imports). harness_cli still
re-exports the cmd_* names, so `from harness_cli import cmd_x` works.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from core import claude_md
from core.atomic_io import atomic_write_json
from core.ci_template import ci_template_drift, ci_template_path, deployed_ci_path
from core.phase_topology import PHASE_DIRS, VALID_PHASES
from core.sessions_spawn_logger import SessionsSpawnLogger
from core.harness_provenance import enforcer_sha
from core.state_io import load_quality_manifest, load_state
from core.utils.project_layout import ProjectLayout
from core.utils.script_loader import load_harness_script
# Module level so it is patchable by name, like every other heavyweight
# init-project step (_init_phase_dirs, _check_crg_available, ...).
from scripts.bootstrap_env import bootstrap as bootstrap_project_env
from scripts.file_loader import RELAY_MAX_BYTES as _RELAY_MAX_BYTES
from harness import tool_checks


def cmd_init_project(args: argparse.Namespace) -> int:
    """
    Initialize harness CI wiring in a target project (Context B setup).

    Automates INTEGRATION.md §3 steps:
      1. Verify harness is importable from the target project
      2. Write .github/workflows/harness_quality_gate.yml
      3. Optionally run setup-git-hooks.sh
      4. Initialize .methodology/state.json (phase source of truth)
      5. Print drift monitor crontab suggestion
    """
    import subprocess  # imported here (not at module level) to keep startup cost low

    project = Path(args.project).resolve()
    phase = args.phase
    # `__file__` = harness/cli/project_cmds.py → `__file__.parent` = harness/cli/.
    # The actual harness root (containing scripts/, templates/, CLAUDE.md.template,
    # INTEGRATION.md) is the parent of cli/. This bug shipped during the Phase 3
    # cmd_* extraction when project_cmds moved from the top-level harness_cli.py
    # into harness/cli/ — the harness_root anchor stayed one level too deep,
    # making init-project silently skip hook install ("WARNING:
    # .../harness/cli/scripts/setup-git-hooks.sh not found") and template copy.
    harness_root = Path(__file__).parent.parent.resolve()

    # Resolve project language before any writes — every later gate run reads
    # the persisted value from state.json (toolchain resolution, S2 checks).
    from harness.toolchains import (
        detect_language,
        detect_test_runner,
        supported_languages,
    )
    language = getattr(args, "language", None)
    if language is None:
        language = detect_language(project)
        if language is None:
            print(
                "[BLOCKED] Ambiguous project language: both Python and JS/TS "
                "manifests found.\n"
                "          Re-run with an explicit flag, e.g.: "
                "init-project --language typescript"
            )
            return 1
    if language not in supported_languages():
        print(
            f"[BLOCKED] Unsupported language '{language}'. "
            f"Registered toolchains: {', '.join(supported_languages())}\n"
            "          See docs/ADDING_LANGUAGE_SUPPORT_SOP.md to register a "
            "new language toolchain."
        )
        return 1
    test_runner = getattr(args, "test_runner", None)
    if test_runner is None and language in ("javascript", "typescript"):
        test_runner = detect_test_runner(project)
        if test_runner is None:
            print(
                "   WARNING: could not detect a unique test runner (vitest/jest) "
                "from package.json.\n"
                "            Coverage/benchmark dimensions resolve to the vitest "
                "toolchain by default; pass --test-runner to override."
            )

    print(f"\n{'='*60}")
    print(f"init-project  target={project}  phase={phase}  language={language}"
          + (f"  test_runner={test_runner}" if test_runner else ""))
    print(f"{'='*60}")

    # 1. Verify harness is importable
    print("\n[1/11] Checking harness importability...")
    importable = (
        (project / "harness" / "core" / "quality_gate" / "__init__.py").exists()
        or (project / "core" / "quality_gate" / "__init__.py").exists()
        or (project / "harness_cli.py").exists()
        or (project / "harness" / "harness_cli.py").exists()
    )
    if importable:
        print("   OK — harness is importable")
    else:
        print("   WARNING: harness not found in target project.")
        print(f"   Run:  git submodule add {harness_root} {project}/harness")
        print(f"   Or:   export PYTHONPATH=\"{harness_root}:$PYTHONPATH\"")
        if not args.overwrite:
            return 1

    # 1b. Submodule layout: create harness_cli.py root wrapper so every plan
    #     command (`python3 harness_cli.py ...`) works from the project root
    #     without any path adjustment.  Only written when harness lives at
    #     project/harness/ (submodule) and no wrapper exists yet.
    _WRAPPER_MARKER = "# auto-generated by init-project (harness submodule layout)"
    _submodule_cli = project / "harness" / "harness_cli.py"
    _root_cli = project / "harness_cli.py"
    _root_cli_is_ours = (
        _root_cli.exists()
        and _WRAPPER_MARKER in _root_cli.read_text(encoding="utf-8")
    )
    if _submodule_cli.exists():
        if not _root_cli.exists() or _root_cli_is_ours or args.overwrite:
            print("\n[1b/11] Writing harness_cli.py root wrapper (submodule layout)...")
            _root_cli.write_text(
                f'{_WRAPPER_MARKER}\n'
                '"""Delegates `python3 harness_cli.py <cmd>` to harness/harness_cli.py.\n'
                'Auto-generated by `init-project`; do not edit manually.\n'
                'Re-generate: python3 harness/harness_cli.py init-project --project . --overwrite\n'
                '"""\n'
                'import subprocess, sys, pathlib\n'
                '_target = pathlib.Path(__file__).parent / "harness" / "harness_cli.py"\n'
                'raise SystemExit(\n'
                '    subprocess.run(\n'
                '        [sys.executable, str(_target), *sys.argv[1:]]\n'
                '    ).returncode\n'
                ')\n',
                encoding="utf-8",
            )
            print(f"   OK — wrote {_root_cli}")
        else:
            print("\n[1b/11] harness_cli.py root wrapper...")
            print(f"   SKIP: {_root_cli} exists and was not created by init-project")
            print("         (use --overwrite to replace it)")

    # 2. Write CI workflow
    print("\n[2/11] Writing CI workflow...")
    workflow_path = deployed_ci_path(project)
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    if workflow_path.exists() and not args.overwrite:
        # An existing copy may be an older template. Round 40: say so here
        # rather than only in doctor — this is the moment someone is looking.
        drift = ci_template_drift(project)
        print(f"   SKIP: {workflow_path} already exists (use --overwrite to overwrite)")
        if drift:
            print("   WARN: the existing copy is not the template this harness ships")
    else:
        try:
            workflow_path.write_text(_harness_workflow_template())
        except FileNotFoundError as e:
            print(f"   ERROR: Cannot write CI workflow — {e}")
            print("   The template file is missing from the harness installation.")
            print("   Re-run harness-init.sh or ensure templates/harness_quality_gate.yml exists.")
            return 1
        print(f"   OK — wrote {workflow_path}")

    # 2a. Write gitleaks scope config (Round 92). Unlike the CI workflow this
    # file is one projects already hand-author with their own allowlist
    # entries — SKIP unconditionally (no --overwrite escape hatch) so a
    # project's own config is never clobbered.
    print("\n[2a/11] Writing gitleaks scope config...")
    gitleaks_cfg_path = project / ".gitleaks.toml"
    gitleaks_cfg_template = ci_template_path().parent / ".gitleaks.toml"
    if gitleaks_cfg_path.exists():
        print(f"   SKIP: {gitleaks_cfg_path} already exists (project-owned, never overwritten)")
    elif not gitleaks_cfg_template.exists():
        print(f"   WARN: {gitleaks_cfg_template} not found — skipping")
    else:
        gitleaks_cfg_path.write_text(gitleaks_cfg_template.read_text())
        print(f"   OK — wrote {gitleaks_cfg_path}")

    # 3. Git hooks
    print("\n[3/11] Git hooks...")
    hooks_script = harness_root / "scripts" / "setup-git-hooks.sh"
    if args.ci_only:
        print("   SKIP: --ci-only flag set (hooks not installed)")
    elif not hooks_script.exists():
        print(f"   WARNING: {hooks_script} not found — skipping hooks")
    else:
        hooks_dir = project / ".git" / "hooks"
        if (hooks_dir / "prepare-commit-msg").exists() and not args.overwrite:
            print("   SKIP: hooks already installed (use --overwrite to reinstall)")
        else:
            result = subprocess.run(
                ["bash", str(hooks_script)],
                cwd=str(project),
                input=f"{phase}\ny\n",  # auto-answer prompts
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                print("   OK — git hooks installed")
            else:
                print(f"   WARNING: hook install failed:\n{result.stderr[-500:]}")

    # 4. Phase state — managed via .methodology/state.json (written in step 7).
    #    The deprecated `git config quality.phase` knob is no longer set;
    #    state.json is the single source of truth read by hooks and CI.
    print("\n[4/11] Phase state...")
    print(f"   OK — phase {phase} will be written to .methodology/state.json (step 7)")

    # 5. Create canonical phase directory structure
    print("\n[5/11] Creating phase directory structure...")
    _init_phase_dirs(project)

    # 6. Copy template artifacts into phase directories
    print("\n[6/11] Copying artifact templates...")
    _init_copy_templates(project, harness_root, overwrite=args.overwrite)

    # 6a. Initialize .gitignore with harness runtime + dev artifact entries
    # (prevents pipeline-mode `git add -A` from committing .venv/ — semgrep-core
    # is 197MB and trips GH001 large-file rejection; bug discovered during
    # integration-test E2E bootstrap, 2026-06-15)
    print("\n[6a/11] Initializing .gitignore for pipeline mode...")
    from harness.git_strategy import GitStrategy
    _git_ignore_helper = GitStrategy(project, enabled=True, push=False)
    _git_ignore_helper.ensure_gitignore()

    # 6b. JS/TS quality toolchain (pinned devDeps + lint/type/test/bench configs)
    if language in ("javascript", "typescript"):
        print("\n[6b/11] Setting up JS/TS quality toolchain...")
        _init_js_toolchain(
            project, harness_root, language, test_runner, overwrite=args.overwrite
        )

    # 7. Initialize FSM state.json (required by run-phase preflight)
    print("\n[7/11] Initializing FSM state...")
    from datetime import datetime, timezone
    state_path = project / ".methodology" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists():
        # state.json is the FSM source of truth — never overwrite it, even with
        # --overwrite.  Overwriting mid-project would reset current_phase to 1,
        # destroying phase progress.  --overwrite is intentionally scoped to
        # templates / CI workflow / harness_cli.py wrapper, not FSM state.
        print(f"   SKIP: {state_path} already exists (FSM state is never reset by init-project; delete it manually to reinitialize)")
        _existing_lang = load_state(project, lenient=True).get("language", "python")
        if _existing_lang != language:
            print(
                f"   WARNING: persisted language '{_existing_lang}' differs from "
                f"requested/detected '{language}' — keeping '{_existing_lang}'. "
                f"A project cannot change toolchain mid-flight."
            )
        language = _existing_lang
    else:
        _state: dict = {
            "state": "RUNNING",
            "current_phase": phase,
            "last_gate": None,
            "last_fr": None,
            "last_update": datetime.now(timezone.utc).isoformat(),
            "language": language,
        }
        if test_runner:
            _state["test_runner"] = test_runner
        atomic_write_json(state_path, _state)
        print(f"   OK — state.json initialized (phase={phase}, language={language})")
    # Refresh CLAUDE.md harness status block now that state.json exists
    claude_md.update_claude_md(project)

    # 7a. Initialize trace attestation.json (required by pre-commit-check trace_dirt probe).
    # Without it, every fresh project's first commit fails pre-flight on
    # `attestation.json missing` — discovered during integration-test E2E
    # bootstrap, 2026-06-17. `--overwrite` re-creates it (cheap; SAD.md is
    # not yet authored at init-project time so the matrix is empty).
    print("\n[7a/11] Initializing trace attestation...")
    from scripts.build_trace_attestation import (
        build_attestation,
        write_attestation,
    )
    attestation_path = project / ".methodology" / "trace" / "attestation.json"
    if attestation_path.exists() and not args.overwrite:
        print(f"   SKIP: {attestation_path} already exists")
    else:
        _attestation = build_attestation(project)
        _canonical_path, _ = write_attestation(project, _attestation)
        print(f"   OK — wrote {_canonical_path}")

    # 8. (cron drift monitor removed in 減法 T4 — drift is already checked
    #    twice per push: preflight_drift_detection + postflight_drift_check)
    print("\n[8/11] Drift protection: enforced at every push (no cron needed)")

    # 9. ECC hooks (Claude Code session layer — blocks git --no-verify)
    print("\n[9/11] ECC hooks (git --no-verify blocker)...")
    _check_and_offer_ecc_hooks(harness_root)

    # 10. Branch protection (GitHub server-side — bypass-proof)
    print("\n[10/11] GitHub branch protection...")
    if args.setup_branch_protection:
        rc = _setup_branch_protection(project)
        if rc != 0:
            _print_manual_branch_protection_guide()
    else:
        # Auto-detect gh availability and offer setup
        _auto_offer_branch_protection(project)

    # 10b. The interpreter the harness will actually run from.
    #
    # Round 47 站2 replaced this step's body. It used to decide importability
    # with `__import__(pkg)` — which runs in THIS process — and then install
    # into project/.venv. Any host whose ambient interpreter already had pyyaml
    # got "OK — all runtime Python deps importable" over an empty project venv.
    # It also checked 2 of the 20 packages requirements.txt pins, and when no
    # venv existed it printed the `python -m venv` command rather than running
    # it — which is why the framework depended on a virtualenv nothing built.
    #
    # scripts/bootstrap_env.py creates it, installs every pip step from the
    # SSOT, and re-measures importability IN THAT interpreter.
    print("\n[10b/11] Project interpreter + pinned toolchain...")
    _report = bootstrap_project_env(project)
    print(f"   interpreter: {_report.python} "
          f"({'created' if _report.venv_created else 'existing'})")
    print(f"   pip steps  : {', '.join(_report.steps_run) or 'none'}")
    for _failure in _report.failures:
        print(f"   [BLOCKED] {_failure}")
    if _report.still_missing_imports:
        print("   [BLOCKED] still not importable in that interpreter: "
              + ", ".join(_report.still_missing_imports))
    if not _report.ok:
        return 1
    print("   OK — installed and importable in the project's own interpreter")

    # 11. Gate tool availability (blocking — all Tier 1 tools required before project start).
    # Driven by gate YAMLs so new requires_tool_execution entries are auto-detected.
    print("\n[11/11] Gate tool availability check...")

    def _gate_tool_gaps() -> "tuple[list[str], list[str]]":
        """(human diagnostics, tool_ids) across all four gates.

        Gate configs come from the harness checkout; the language comes from
        the target project's freshly written state.json (state_root).
        """
        diagnostics: list[str] = []
        ids: list[str] = []
        for _gate_num in (1, 2, 3, 4):
            _, _missing = tool_checks.verify_gate_tools(
                _gate_num, str(harness_root), state_root=str(project)
            )
            for _m in _missing:
                if _m not in diagnostics:
                    diagnostics.append(_m)
            for _t in tool_checks.missing_gate_tool_ids(
                _gate_num, str(harness_root), state_root=str(project)
            ):
                if _t not in ids:
                    ids.append(_t)
        return diagnostics, ids

    _missing_init, _missing_ids = _gate_tool_gaps()
    if _missing_init:
        # Round 47 站3: init-project exists to make a project ready, so it
        # installs rather than printing three lines of unpinned prose (which
        # were also the fourth and fifth copies of pins stated elsewhere —
        # `pip install scancode-toolkit` unpinned next to CI's ==32.4.1).
        from harness.env_repair import repair_missing_tools
        _outcome = repair_missing_tools(project, _missing_ids)
        if _outcome.attempted_steps:
            print(f"  [REPAIR] installed {', '.join(_outcome.attempted_steps)}")
        _missing_init, _missing_ids = _gate_tool_gaps()
    if _missing_init:
        print("  [BLOCKED] Required Tier 1 gate tools are not installed:")
        for _m in _missing_init:
            print(f"    ✗ {_m}")
        print(
            "\n  Repair was attempted and did not resolve them. The framework\n"
            "  installs pip packages into the project venv and nothing else —\n"
            "  external binaries and npm-owned tools are yours to install.\n"
            "  tool_score=null is not accepted for Tier 1/2 dimensions (score.py R8).\n"
            "  Install commands: harness/toolchains/bootstrap.py.\n"
            "  Re-run init-project after installing."
        )
        return 1
    print("  OK — all required gate tools are available.")

    # CRG (Code Review Graph) — mandatory for Gate 3/4 structural dimensions.
    # Core tools (build, detect_changes, minimal_context) are imported at module
    # level in crg_bridge.py — import failure means CRG MCP is not configured.
    _crg_ok = _check_crg_available()
    if _crg_ok:
        print("  OK — CRG (Code Review Graph) MCP server reachable (Gate 3/4 ready)")
    else:
        print("  INFO: CRG MCP server not detected.")
        print("        CRG is mandatory for Gate 3/4 (same tier as ruff/mypy/pytest).")
        print("        prepare_gate() will fail for Gates 3/4 if CRG is not installed.")
        print("        Install before reaching P4:")
        print("          pip install code-review-graph")
        print("          code-review-graph register  # registers repo in ~/.code-review-graph/")
        print("        OK to proceed with P1/P2 — CRG is not required for these phases.")

    # Phase-aware human checklist
    _checklist: list[str] = [
        "  ╔══════════════════════════════════════════════════════════════╗",
        f"  ║  HUMAN CHECKLIST — Phase {phase} — verify before starting         ║",
        "  ╠══════════════════════════════════════════════════════════════╣",
        "  ║  [ ] Tier 1 tools installed (ruff, mypy, pytest-cov, ...)   ║",
        "  ║  [ ] gitleaks installed (secrets scanning)                  ║",
        "  ║  [ ] GitHub branch protection enabled on main               ║",
        "  ║      → Settings → Branches → main → Block force push+delete ║",
        "  ║  [ ] ECC hooks installed (blocks git --no-verify)           ║",
        "  ║      → bash scripts/setup-ecc-hooks.sh --verify             ║",
    ]
    if phase == 1:
        _checklist += [
            "  ║  [ ] SRS.md written with ### FR-XX: sections                ║",
            "  ║  [ ] SPEC_TRACKING.md + TRACEABILITY_MATRIX.md ready        ║",
        ]
    elif phase == 2:
        _checklist += [
            "  ║  [ ] SAD.md + ADR.md written (architecture design)          ║",
            "  ║  [ ] TEST_SPEC.md ready (from derive_test_cases.md)         ║",
        ]
    else:
        _checklist += [
            "  ║  [ ] Phase entry deliverables ready (see SKILL.md §1)       ║",
        ]
    _checklist += [
        "  ║  [ ] Review generated templates in phase directories        ║",
        "  ╚══════════════════════════════════════════════════════════════╝",
    ]
    print(f"\n{'='*60}")
    print("init-project complete.")
    print(f"{'='*60}")
    print(f"  Phase {phase} → .methodology/state.json")
    print()

    # Improvement C: SAB auto-amend on P3 init.
    # P3 introduces new modules under 03-development/src/; previously the user
    # had to hand-edit .methodology/SAB.json to register them, otherwise
    # `_check_sab_module_alignment` would BLOCK the gate. Run the amender so
    # the manifest is in sync with the source tree before the user starts TDD.
    if phase == 3:
        try:
            from core.quality_gate.sab_amender import amend_sab
            added = amend_sab(project)
            if added:
                print(f"[SAB AMEND] Added {len(added)} module(s) to "
                      ".methodology/SAB.json:")
                for m in added:
                    print(f"  + {m}")
                print("  Review layer assignment and commit SAB.json.")
            else:
                print("[SAB] No new modules to register (in sync).")
        except Exception as exc:  # amend is best-effort, never blocks init
            print(f"[SAB AMEND] Warning: amend failed: {exc}")

    for line in _checklist:
        print(line)
    print(f"  Full docs: {harness_root}/INTEGRATION.md")
    return 0


def cmd_bootstrap_env(args: argparse.Namespace) -> int:
    """Create the project's virtualenv and install the pinned toolchain into it.

    Round 47 站2. Delegates to scripts/bootstrap_env.py — the same
    implementation init-project's step [10b] and the P1 workflow's first
    preflight command reach. That script is standalone and stdlib-only because
    it has to work before the venv exists; this subcommand is the convenient
    entry point once it does, and the one env repair calls in-process.
    """
    from scripts.bootstrap_env import main as _bootstrap_main

    argv = ["--project", str(Path(args.project).resolve())]
    if getattr(args, "json", False):
        argv.append("--json")
    return _bootstrap_main(argv)


def cmd_status(args: argparse.Namespace) -> int:
    """Show current manifest + FSM state, phase progress, and optionally test stats."""
    project = Path(args.project).resolve()
    manifest_path = project / ".methodology" / "quality_manifest.json"
    state_path    = project / ".methodology" / "state.json"
    json_out = getattr(args, "json", False)
    full = getattr(args, "full", False)

    # Gather state (strict: these reads were previously unguarded entirely —
    # a corrupt file raised an uncaught JSONDecodeError, misclassified
    # [HARNESS-BUG] by the crash boundary. Now [FATAL] exit 26.)
    state = {}
    if state_path.exists():
        state = load_state(project)

    manifest = {}
    if manifest_path.exists():
        manifest = load_quality_manifest(project)

    current_phase = state.get("current_phase", 0)
    fr_ids = manifest.get("fr_ids", [])
    gates = manifest.get("gate_results", {})

    # Phase progress table (short display names shared with the CLAUDE.md
    # status block — one map, keys anchored by test_phase_topology_ssot)
    phase_names = claude_md.PHASE_NAMES
    phase_status = {}
    for p in VALID_PHASES:
        if p < current_phase:
            phase_status[p] = "COMPLETE"
        elif p == current_phase:
            phase_status[p] = "IN_PROGRESS"
        else:
            phase_status[p] = "NOT_STARTED"

    # FR gate status for current phase
    fr_status = {}
    if current_phase >= 3 and gates.get("gate1"):
        for fr_id in fr_ids:
            fr_result = gates["gate1"].get(fr_id)
            if fr_result and isinstance(fr_result, dict):
                fr_status[fr_id] = {"score": fr_result.get("score", 0), "complete": fr_result.get("quality_complete", False)}
            else:
                fr_status[fr_id] = {"score": None, "complete": False}

    # Test stats (only when --full)
    test_count = None
    coverage_pct = None
    if full:
        # Bug #117 ext: route through sys.executable so the venv's pytest is
        # used; bare 'pytest' on macOS PATH resolves to CommandLineTools 3.9.
        # Round 66: and through the primitive, so this waits out a mutation
        # window and its budget reaps the run's workers.
        from core.quality_gate.source_tree_lock import run_against_source_tree
        try:
            r = run_against_source_tree([sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header"],
                                        project=project, timeout=30)
            m = re.search(r"(\d+) tests? collected", r.stdout + r.stderr)
            if m:
                test_count = int(m.group(1))
        except Exception as exc:
            print(f"[WARN] effort: pytest --collect-only failed, test_count stays "
                  f"unknown: {exc}", file=sys.stderr)
        try:
            r = run_against_source_tree([sys.executable, "-m", "pytest", "--cov=.", "--cov-report=term", "--tb=no", "-q"],
                                        project=project, timeout=120)
            m = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", r.stdout + r.stderr)
            if m:
                coverage_pct = int(m.group(1))
        except Exception as exc:
            print(f"[WARN] effort: pytest --cov failed, coverage_pct stays "
                  f"unknown: {exc}", file=sys.stderr)

    # Auto-fix rounds
    auto_fix_rounds_used = 0
    if full and gates:
        for gate_name in ["gate1", "gate2", "gate3", "gate4"]:
            gv = gates.get(gate_name)
            if isinstance(gv, dict) and "rounds_used" in gv:
                auto_fix_rounds_used = max(auto_fix_rounds_used, gv.get("rounds_used", 0))

    if json_out:
        result = {
            "project": str(project),
            "fsm": {"state": state.get("state", "UNKNOWN"), "current_phase": current_phase,
                    "last_update": state.get("last_update", "-")},
            "phase_progress": {str(p): phase_status[p] for p in VALID_PHASES},
            "fr_ids": fr_ids,
            "gates": gates,
        }
        if full:
            result["test_count"] = test_count
            result["coverage_pct"] = coverage_pct
            result["auto_fix_rounds_used"] = auto_fix_rounds_used
        print(json.dumps(result, indent=2, default=str))
        return 0

    # Text output
    print(f"\n{'='*60}\nHarness Status: {project}\n{'='*60}")

    if state:
        print("\n[FSM State]")
        print(f"  state         : {state.get('state', 'UNKNOWN')}")
        print(f"  current_phase : {current_phase}")
        print(f"  last_update   : {state.get('last_update', '-')}")
    else:
        print("\n[FSM State] .methodology/state.json not found (project not initialised)")

    # Phase progress table
    print("\n[Phase Progress]")
    for p in VALID_PHASES:
        icon = {"COMPLETE": "✅", "IN_PROGRESS": "🔄", "NOT_STARTED": "⬜"}.get(phase_status[p], "⬜")
        print(f"  {icon} P{p} {phase_names.get(p, 'Unknown'):<16} {phase_status[p]}")

    if manifest:
        print("\n[Quality Manifest]")
        print(f"  schema_version: {manifest.get('schema_version')}")
        print(f"  fr_ids        : {fr_ids}")
        for g, v in gates.items():
            if v is None:
                print(f"  {g}           : not run")
            elif isinstance(v, dict) and "score" in v:
                print(f"  {g}           : score={v['score']} complete={v.get('quality_complete', False)}")
            elif isinstance(v, dict):
                for fr, r in v.items():
                    if isinstance(r, dict):
                        print(f"  {g}/{fr}  : score={r.get('score', 0)} complete={r.get('quality_complete', False)}")
                    else:
                        print(f"  {g}/{fr}  : {r}")
    else:
        print("\n[Quality Manifest] Not found — run `harness_cli.py manifest` first")

    # FR detail for current phase
    if fr_status:
        print(f"\n[FR Gate 1 Status — Phase {current_phase}]")
        for fr_id, fs in fr_status.items():
            if fs["score"] is not None:
                print(f"  {fr_id}: score={fs['score']} complete={fs['complete']}")
            else:
                print(f"  {fr_id}: not run")

    # CRG status
    crg_status_path = project / ".sessi-work" / "crg_status.json"
    print("\n[CRG]")
    if crg_status_path.exists():
        try:
            crg_status = json.loads(crg_status_path.read_text(encoding="utf-8"))
            if crg_status.get("available"):
                nodes = crg_status.get("node_count", "?")
                action = crg_status.get("action", "")
                tag = " (auto-built)" if action == "auto_built" else ""
                print(f"  graph     : {nodes} nodes{tag}")
                # Reconnaissance
                recon_path = project / ".sessi-work" / "crg_reconnaissance.json"
                if recon_path.is_file() and recon_path.stat().st_size > 0:
                    print(f"  recon     : available ({recon_path.stat().st_size} bytes)")
                else:
                    print("  recon     : not yet run")
                # Metrics
                metrics_path = project / ".sessi-work" / "crg_metrics.json"
                if metrics_path.is_file():
                    print(f"  metrics   : available ({metrics_path.stat().st_size} bytes)")
                else:
                    print("  metrics   : not yet computed")
                # Graph stats (live from MCP — graceful degrade if unavailable)
                try:
                    from mcp_tools import (  # type: ignore[import-untyped, import-not-found, attr-defined]
                        mcp__code_review_graph__list_graph_stats_tool as _gs_fn,  # type: ignore[attr-defined]
                    )
                    _gs = _gs_fn(repo_root=str(project))
                    print(
                        f"  graph_db  : {_gs.get('total_nodes','?')} nodes · "
                        f"{_gs.get('total_edges','?')} edges · "
                        f"updated {(_gs.get('last_updated') or '')[:10]}"
                    )
                except Exception as exc:
                    print(f"  [WARN] doctor: CRG MCP graph-stats unavailable in this "
                          f"subprocess context: {exc}", file=sys.stderr)
            else:
                print(f"  status    : unavailable — {crg_status.get('reason', 'unknown')}")
        except (json.JSONDecodeError, OSError):
            print("  status    : error reading crg_status.json")
    else:
        print("  status    : not initialized — run Gate 3 or Gate 4 to build graph")

    if full:
        print("\n[Test Stats]")
        print(f"  tests collected: {test_count if test_count is not None else 'N/A'}")
        print(f"  coverage       : {coverage_pct}%" if coverage_pct is not None else "  coverage       : N/A")
        print("\n[Auto-Fix]")
        print(f"  rounds_used    : {auto_fix_rounds_used}")

    return 0


def cmd_load_context(args: argparse.Namespace) -> int:
    """Load project context for a phase and output as JSON."""
    import json as _json

    project = Path(args.project).resolve()
    phase = args.phase

    # fr_ids and gate_results from manifest
    fr_id_source: str = ""  # diagnostics: where did fr_ids come from?
    manifest = load_quality_manifest(project, lenient=True)
    fr_ids: list = manifest.get("fr_ids", [])
    gate_results: dict = manifest.get("gate_results", {})
    if fr_ids:
        fr_id_source = "quality_manifest.json"

    # P1 fallback (bug #2 fix): when quality_manifest.json is missing or empty
    # (the chicken-and-egg case at P1 entry, before P2 generates the manifest),
    # extract fr_ids from the canonical spec. Without this fallback,
    # load-context at P1 returns fr_ids=[] and the orchestrator cannot
    # enumerate FR scope. Repro: integration-test P1 bootstrap 2026-06-15.
    #
    # Round 84: the canonical spec is `ProjectLayout.spec_path`, not whatever
    # PROJECT_BRIEF.md declared. The two layout regexes this used to carry (and
    # their twin in core/quality_gate/spec_alignment.py, "kept in sync") are
    # gone with the field they parsed.
    #
    # The `### FR-NN:` regex below is deliberately left as it was: it is a
    # narrower reading than spec_alignment's `_structural_fr_ids` (which also
    # accepts table rows and the JSON block), and widening it here would change
    # what P1 enumerates. That duplication is pre-existing debt, recorded in
    # the Round 84 ledger, not this round's lesion.
    if not fr_ids:
        _spec_path = ProjectLayout(project).spec_path
        if _spec_path.exists():
            try:
                import re as _re

                _spec_text = _spec_path.read_text(encoding="utf-8")
                # Extract FR headers like `### FR-01: ...`
                _frs = _re.findall(
                    r"^###\s+FR-(\d+)\s*:", _spec_text, _re.MULTILINE
                )
                if _frs:
                    fr_ids = [f"FR-{n}" for n in _frs]
                    fr_id_source = (
                        f"{_spec_path.name} (P1 fallback, "
                        f"quality_manifest.json not yet generated)"
                    )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                print(f"[WARN] load-context: canonical spec P1 fallback parse "
                      f"failed, fr_ids stays empty: {exc}", file=sys.stderr)

    # current_phase from state.json
    current_phase = load_state(project, lenient=True).get("current_phase", 0)

    # fr_details from SRS.md (optional)
    fr_details: dict = {}
    try:
        # Round 5 建議2站2: load_harness_script, not cwd-relative import —
        # same P6-2026-07-07 bug class, never swept by the original fix.
        parse_srs_fr_sections = load_harness_script("generate_full_plan.py").parse_srs_fr_sections
        srs_path = ProjectLayout(project).srs_path
        frs = parse_srs_fr_sections(srs_path if srs_path.exists() else None)
        for fr in frs:
            fr_details[fr["fr"]] = {
                "title": fr.get("title", ""),
                "desc": fr.get("desc", ""),
                "acceptance": fr.get("requirements", []),
            }
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[WARN] load-context: SRS.md fr_details parse failed, "
              f"fr_details stays empty: {exc}", file=sys.stderr)

    # modules from SAD.md (optional)
    modules: dict = {}
    try:
        # Round 5 建議2站2: same load_harness_script migration as above.
        parse_sad_modules = load_harness_script("generate_full_plan.py").parse_sad_modules
        modules = parse_sad_modules(project)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[WARN] load-context: SAD.md modules parse failed, "
              f"modules stays empty: {exc}", file=sys.stderr)

    # Round 65 站2: where this project's tests and source are, from the one
    # resolver Gate 3 re-measures with. The P4 coverage prompt used to name
    # `03-development/{tests,src}` itself — ProjectLayout's first choice, not
    # its only one — so a root-layout project was told to run a command that
    # collects nothing. Unguarded on purpose: resolve_targets is pure path
    # resolution, and a load-context that cannot say where the tests are has
    # nothing useful to hand the next agent.
    from core.quality_gate.test_suite_run import resolve_targets

    test_target, cov_target = resolve_targets(project)

    # Round 85 站2: how long a wrapper agent must wait for one `run-fr-step`,
    # from the loop that actually runs it. Same reason as the two targets
    # above — the per-FR GATE1 / GATE1-DELTA prompts used to name the cap
    # themselves, and the literal they named was below the worst case this
    # framework produces under its own default config, let alone under the
    # per-FR `fr_config` overrides the CLI documents.
    from cli.fr_cmds import fr_step_poll_plan

    fr_step_poll_cap, fr_step_poll_interval_s = fr_step_poll_plan(project)

    result = {
        "phase": phase,
        "project_name": project.name,
        "fr_ids": fr_ids,
        "fr_details": fr_details,
        "modules": modules,
        "gate_results": gate_results,
        "current_phase": current_phase,
        "fr_id_source": fr_id_source or "none",
        "test_target": test_target,
        "cov_target": cov_target,
        "fr_step_poll_cap": fr_step_poll_cap,
        "fr_step_poll_interval_s": fr_step_poll_interval_s,
    }

    # Direction C: auto-inject relevant past-failure lessons at phase entry.
    # Relevance-gated (by this phase's FRs) + capped → cannot pollute context.
    try:
        from core.lessons import format_lessons_block, recall_lessons
        result["lessons"] = format_lessons_block(
            recall_lessons(project, fr_ids=fr_ids, limit=5))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[WARN] load-context: cross-run lessons recall failed: {exc}", file=sys.stderr)
        result["lessons"] = ""

    # Sentinel warning: existing artifacts still in template state?
    # P1/P2 entry agents must distinguish "real SRS.md" from "template placeholder
    # left by `init-project`". Without this check, Agent A might assume P1 is
    # complete because SRS.md exists — but the file is still a stub.
    # Per SKILL.md §0.3.1, stub = sentinel literal OR ≥8 {placeholder} patterns
    # (co-equal heuristic `_is_stub_template`). Paths match
    # `_init_copy_templates` artifact_map (the locations init-project writes).
    from core.quality_gate.constitution.runner import _is_stub_template
    _sentinel = "<!-- harness:template-stub -->"
    _template_artifacts = (
        "01-requirements/SRS.md",
        "02-architecture/SAD.md",
        "02-architecture/TEST_SPEC.md",
        "02-architecture/adr/ADR.md",
    )
    _warnings: list = []
    for _rel in _template_artifacts:
        _p = project / _rel
        if _p.exists():
            try:
                _content = _p.read_text(encoding="utf-8")
            except OSError:
                continue
            if (_sentinel in _content) or _is_stub_template(_content):
                _warnings.append(
                    f"{_rel} is a template stub (sentinel literal or "
                    f"≥8 {{placeholder}} patterns per SKILL.md §0.3.1) — "
                    f"this is a template placeholder, not real content. "
                    f"Remove the sentinel / fill the placeholders before "
                    f"treating it as a real artifact."
                )
    # Round 26: which framework version produced the phases this one builds on.
    #
    # Gate results have carried `enforcer_sha` since Round 19 站3; phase artifacts
    # did not, so a run whose harness was patched mid-flight left no trace of the
    # skew. taskq-plus P1-P3: five framework commits landed between 06:02 and
    # 10:24, one of them fixing the very P2 SAB-WRITE step that had completed
    # seven hours earlier — fixing a prompt does not retroactively fix the artifact
    # it produced, and FR-01..04's Gate 1 verdicts were stamped by a different
    # enforcer than FR-05's environment.
    #
    # WARN, never BLOCK: patching the framework mid-run is a deliberate operator
    # choice here. The defect was that the skew was invisible, not that it happened.
    _skew = _enforcer_skew_warnings(project)
    if _skew:
        _warnings.extend(_skew)

    if _warnings:
        result["warnings"] = _warnings

    print(_json.dumps(result, indent=2, default=str))
    return 0


def _enforcer_skew_warnings(project: Path) -> list[str]:
    """One warning per completed phase produced by a different harness commit.

    Phases recorded before Round 26 carry no `enforcer_sha`; those are reported as
    "not recorded" rather than as agreement — an unstated gap must not read as
    coverage. Never raises: provenance reporting may not break load-context.
    """
    try:
        state = load_state(project, lenient=True)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[WARN] load-context: enforcer-skew check skipped: {exc}",
              file=sys.stderr)
        return []
    completed = state.get("phase_completed")
    if not isinstance(completed, dict) or not completed:
        return []

    current = enforcer_sha()
    stale: list[str] = []
    unrecorded: list[str] = []
    for phase_key in sorted(completed, key=lambda k: str(k)):
        entry = completed[phase_key]
        if not isinstance(entry, dict):
            continue
        recorded = str(entry.get("enforcer_sha") or "")
        if not recorded:
            unrecorded.append(str(phase_key))
        elif recorded != current:
            stale.append(f"P{phase_key} by {recorded[:12]}")

    out: list[str] = []
    if stale:
        out.append(
            f"enforcer skew: the current harness is {current[:12]} but "
            f"{', '.join(stale)} — those phases' artifacts were produced by a "
            f"different framework version, and a later fix to a prompt or checker "
            f"does NOT retroactively correct what it already wrote. If a phase you "
            f"depend on predates a relevant fix, re-run that phase rather than "
            f"trusting its output."
        )
    if unrecorded:
        out.append(
            f"enforcer provenance missing for phase(s) {', '.join(unrecorded)} "
            f"(completed before Round 26 recorded it) — skew for those cannot be "
            f"determined either way."
        )
    return out


def cmd_read_file(args: argparse.Namespace) -> int:
    from scripts.file_loader import load_file  # lazy import — file_loader.py is heavy

    result = load_file(
        file_path=args.file,
        expect_prefix=args.expect_prefix,
        min_length=args.min_length,
        max_length=args.max_length,
        include_content=args.content,
        relay=args.relay,
        relay_max_bytes=args.relay_max_bytes,
    )

    json_text = json.dumps(result, indent=2, ensure_ascii=False)

    if args.json_out:
        Path(args.json_out).write_text(json_text, encoding="utf-8")
    else:
        print(json_text)

    if args.content_out and result.get("content") is not None:
        Path(args.content_out).write_text(result["content"], encoding="utf-8")

    if not args.quiet:
        status = result["status"]
        sha = result["content_sha256"]
        sha_short = (sha[:12] + "...") if sha else "(none)"
        msg = (
            f"[read-file] {status} "
            f"file={args.file} "
            f"sha256={sha_short} "
            f"bytes={result['byte_size']} "
            f"lines={result['line_count']}"
        )
        if status != "OK":
            msg += f" — {result['diagnostic']}"
        print(msg, file=sys.stderr)

    if result["status"] == "OK":
        return 0
    if result["status"] in {"MISSING", "PREFIX_MISMATCH", "TOO_SHORT", "TOO_LONG"}:
        return 1
    return 2  # READ_ERROR


def cmd_effort(args: argparse.Namespace) -> int:
    """Show gate effort metrics summary."""
    from harness.effort_tracker import EffortTracker

    tracker = EffortTracker()
    summary = tracker.summary(phase=args.phase)

    print(f"\n{'='*60}")
    title = f"Effort Summary{' | Phase ' + str(args.phase) if args.phase else ''}"
    print(f"{title}\n{'='*60}")
    print(json.dumps(summary, indent=2))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Read-only cross-file state consistency check. Reports, never repairs —
    an auto-repair path would itself become a fabrication surface."""
    from core.doctor import run_doctor

    project = Path(args.project).resolve()
    findings = run_doctor(project)

    print(f"\n{'='*60}\ndoctor  project={project}\n{'='*60}")
    if not findings:
        print("  OK: state / manifest / CLAUDE.md / attestation consistent; "
              "no interrupted transactions")
        return 0

    errors = 0
    for finding in findings:
        print(f"  [{finding.severity}] {finding.check}: {finding.message}")
        if finding.severity == "ERROR":
            errors += 1
    print(f"\n  {errors} error(s), {len(findings) - errors} other finding(s)")
    return 1 if errors else 0


def cmd_amend_sab(args: argparse.Namespace) -> int:
    """Run the SAB Architecture Amendment Protocol as a standalone subcommand.

    `run-gate --gate 1` blocks with `[BLOCKED] Architecture Amendment Protocol
    violation` when 03-development/src/ has modules absent from any SAB layer.
    The amend logic already runs inside `init-project --phase 3`, but a Phase 3
    TDD/GATE1 agent that sees the [BLOCKED] message has no CLI to recover —
    it has to either hand-edit SAB.json or restart init-project. This wraps
    `core.quality_gate.sab_amender.amend_sab` so any agent can self-heal:

        python3 harness_cli.py amend-sab --project .
        python3 harness_cli.py amend-sab --project . --dry-run
        python3 harness_cli.py amend-sab --project . --src-dir src

    Idempotent: re-running adds nothing on the second call.
    Returns 0 on success (including no-op), 1 on hard failure.

    Bug Fix R3 (2026-07-15): also surface the REVERSE direction —
    phantom_modules() detects modules SAB registers but src lacks. P2
    architecture planning often pre-registers modules (e.g. `taskq.breaker`)
    before P3 implementation catches up; without this warning, planning-
    vs-implementation drift goes undetected until Phase 4 preflight, when
    amend-sab is no longer reachable. Print a `[amend-sab] PHANTOM:` block
    listing phantom modules and exit non-zero when `--strict` is set so
    pipeline scripts can fail-fast.

    Observability (fix/round-18-dispatch-ssot, Bug C): every amend-sab
    outcome (success / failure / dry-run) is appended to
    `.methodology/sessions_spawn.log` from THIS function — the mutation
    site — so every caller (standalone `harness_cli.py amend-sab`,
    `run-fr-step --step amend-sab` delegation, and any future caller) is
    captured. Logging wraps in try/except mirroring AgentSpawner.
    _log_dispatch's swallowing pattern so a logging failure cannot break
    dispatch.
    """
    rc, outcome, project = _cmd_amend_sab_impl(args)
    _log_amend_sab_outcome(args, rc, outcome, project)
    return rc


def _cmd_amend_sab_impl(args: argparse.Namespace) -> tuple[int, str, Path]:
    """Inner implementation of cmd_amend_sab, returning (rc, outcome_tag)
    so cmd_amend_sab can wrap with logging without losing the original
    exit-code / failure-mode signals.
    """
    project = Path(args.project).resolve()
    strict = getattr(args, "strict", False)

    # Round 26: --resolve-phantom is the SAB -> code direction and does not share
    # amend_sab's code -> SAB flow. Handled first and returned: mixing an
    # architecture amendment into the same run as a discovery-append would make
    # the ADR record ambiguous about which change the reason justifies.
    _declared = getattr(args, "resolve_phantom", None)
    if _declared:
        from core.quality_gate.sab_amender import (
            PhantomResolutionError,
            resolve_phantom,
        )
        try:
            summary = resolve_phantom(
                project,
                _declared,
                to=getattr(args, "resolve_to", None),
                reason=getattr(args, "reason", "") or "",
                src_dir=args.src_dir,
                drop=bool(getattr(args, "resolve_drop", False)),
            )
        except PhantomResolutionError as exc:
            print(f"[amend-sab] REFUSED: {exc}", file=sys.stderr)
            return 1, "resolve_refused", project
        except Exception as exc:
            print(f"[amend-sab] resolve-phantom failed: {exc}", file=sys.stderr)
            return 1, "exception", project
        print(summary)
        return 0, "resolved_phantom", project

    try:
        from core.quality_gate.sab_amender import (
            amend_sab,
            discover_modules,
            phantom_modules,
        )
        added = amend_sab(project, src_dir=args.src_dir, dry_run=args.dry_run)
        # Reverse direction: SAB claims modules the codebase lacks.
        sab_path = project / ".methodology" / "SAB.json"
        phantoms: list[str] = []
        if sab_path.exists():
            from core.quality_gate.sab_amender import _safe_load
            sab_dict = _safe_load(sab_path)
            if isinstance(sab_dict, dict):
                discovered = discover_modules(project, args.src_dir)
                phantoms = phantom_modules(sab_dict, discovered, args.src_dir)
    except Exception as exc:
        print(f"[amend-sab] failed: {exc}", file=sys.stderr)
        return 1, "exception", project

    if args.dry_run:
        if added:
            print(f"[amend-sab] dry-run: would add {len(added)} module(s):")
            for m in added:
                print(f"  + {m}")
        else:
            print("[amend-sab] dry-run: SAB is already in sync (new modules).")
        if phantoms:
            print(f"[amend-sab] dry-run: {len(phantoms)} PHANTOM module(s) "
                  f"(SAB registers but src lacks):")
            for m in phantoms:
                print(f"  ! {m}")
        else:
            print("[amend-sab] dry-run: no phantom modules detected.")
        return 0, "dry_run", project

    if added:
        print(f"[amend-sab] Added {len(added)} module(s) to .methodology/SAB.json:")
        for m in added:
            print(f"  + {m}")
        print("  Review layer assignment, then commit SAB.json before re-running run-gate.")
    else:
        print("[amend-sab] SAB already in sync with 03-development/src/ (new modules).")

    # Phantom (reverse) direction is ALWAYS informational + fail-fast on --strict.
    if phantoms:
        print(f"\n[amend-sab] PHANTOM: {len(phantoms)} module(s) registered in "
              f"SAB but NOT implemented in src (planning vs implementation drift):")
        for m in phantoms:
            print(f"  ! {m}")
        print("  The GREEN step for the owning FR must create these files, "
              "or run `extract_sab_from_sad` to re-derive SAB from SAD.md.")
        if strict:
            print("[amend-sab] --strict set: exiting non-zero.", file=sys.stderr)
            return 1, "phantom_strict", project

    return 0, "completed", project


def _log_amend_sab_outcome(args: argparse.Namespace, rc: int, outcome: str, project: Path) -> None:
    """Append one entry to `.methodology/sessions_spawn.log` for every
    amend-sab invocation. Logging at the mutation site covers ALL
    callers (the `run-fr-step` dispatch delegation, the standalone
    `harness_cli.py amend-sab` subcommand, any future caller) rather
    than scattering observability into a single dispatcher.

    Schema follows the convention for non-LLM-tool entries used
    elsewhere in the project: `role` sentinel tags the source,
    `session_id=""` (per `core/agent_spawner._log_dispatch` line 811
    for failed dispatches with no real session), `status` reports the
    outcome tag.

    Logging wraps in try/except mirroring
    `core/agent_spawner._log_dispatch` lines 850-852 — a logging failure
    MUST NOT break the dispatch itself.
    """
    try:
        SessionsSpawnLogger(project).log_spawn(
            role="tool:amend-sab",
            task=f"amend-sab for {project.name} (deterministic tool)",
            session_id="",
            status=("COMPLETED" if rc == 0 else f"FAILED(rc={rc})"),
            step="AMEND-SAB",
            tool_kind="amend-sab",
            outcome=outcome,
            rc=rc,
            src_dir=getattr(args, "src_dir", None),
            dry_run=bool(getattr(args, "dry_run", False)),
            strict=bool(getattr(args, "strict", False)),
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Mirror AgentSpawner._log_dispatch swallowing pattern: a
        # logging failure is non-fatal; surface as a warning but never
        # alter the dispatch's exit code or behavior.
        print(f"[WARN] amend-sab: failed to record sessions_spawn entry: "
              f"{exc}", file=sys.stderr)


def cmd_kill_switch(args: argparse.Namespace) -> int:
    """CLI surface for the M1 KillSwitch (CV-6 from robustness audit).

    Operators previously had to write Python to manually trigger or re-enable
    an agent's circuit breaker. This subcommand wires `KillSwitch.manual_trigger`
    and `KillSwitch.re_enable` directly.

    Subcommands:
      trigger  --agent-id ID --reason TEXT [--operator ID]   open circuit
      reset    --agent-id ID --ack TEXT     [--operator ID]   re-enable agent
      status   [--agent-id ID]                                show circuit state

    Operator ID defaults to $USER (or 'operator' on systems without USER set).
    All operations are logged to KillSwitch's audit log.
    """
    try:
        from kill_switch.kill_switch import KillSwitch
    except ImportError as exc:
        print(f"[ERROR] kill_switch module unavailable: {exc}", file=sys.stderr)
        return 1

    operator = getattr(args, "operator", None) or os.environ.get("USER") or "operator"
    ks = KillSwitch()
    action = args.kill_action

    if action == "trigger":
        if not args.agent_id or not args.reason:
            print("[ERROR] kill-switch trigger requires --agent-id and --reason.", file=sys.stderr)
            return 2
        evt = ks.manual_trigger(
            agent_id=args.agent_id, reason=args.reason, operator_id=operator,
        )
        print(f"OK — agent {args.agent_id} circuit OPENED by {operator}.")
        print(f"  Reason: {args.reason}")
        print(f"  Event: {evt}")
        return 0

    if action == "reset":
        if not args.agent_id or not args.ack:
            print("[ERROR] kill-switch reset requires --agent-id and --ack.", file=sys.stderr)
            return 2
        ok = ks.re_enable(
            agent_id=args.agent_id, operator_id=operator, acknowledgment=args.ack,
        )
        if ok:
            print(f"OK — agent {args.agent_id} re-enabled by {operator}.")
            print(f"  Acknowledgment: {args.ack}")
            return 0
        print(f"[ERROR] re-enable failed for {args.agent_id}.", file=sys.stderr)
        return 1

    if action == "status":
        if args.agent_id:
            try:
                open_ = ks.is_agent_circuit_open(args.agent_id)
                state = ks.get_agent_state(args.agent_id)
                print(f"agent_id={args.agent_id}  circuit_open={open_}  state={state}")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                print(f"[ERROR] could not read status for {args.agent_id}: {exc}", file=sys.stderr)
                return 1
        else:
            agents = ks.get_registered_agents()
            if not agents:
                print("No agents currently registered with KillSwitch.")
                return 0
            for aid in agents:
                try:
                    open_ = ks.is_agent_circuit_open(aid)
                    state = ks.get_agent_state(aid)
                    marker = "🔴 OPEN" if open_ else "🟢 CLOSED"
                    print(f"  {marker}  {aid}  state={state}")
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    print(f"  ⚠  {aid}  status error: {exc}")
        return 0

    print(f"[ERROR] unknown kill-switch action: {action}", file=sys.stderr)
    return 2


def cmd_audit_structure(args: argparse.Namespace) -> int:
    """Audit target project directory structure and artifact completeness.

    Only checks phases up to current_phase — future-phase directories are
    not required to exist yet and should not be created as empty stubs.
    """
    import json as _json
    import re as _re
    from core.utils.project_layout import phase_artifacts as _phase_artifacts

    project = Path(args.project).resolve()

    # Read current phase from state.json — only audit up to this phase.
    # (missing or corrupt both fall back to auditing all phases)
    current_phase = int(load_state(project, lenient=True).get("current_phase", 8))

    # Canonical phase directory names, filtered to the audited range
    phase_dirs = {k: v for k, v in PHASE_DIRS.items() if k <= current_phase}

    # Required artifacts per phase (aligned with phase_artifact_enforcer.py)
    _ALL_PHASE_ARTIFACTS = {
        1: ["01-requirements/SRS.md", "01-requirements/SPEC_TRACKING.md",
            "01-requirements/TRACEABILITY_MATRIX.md", "TEST_INVENTORY.yaml"],
        2: ["02-architecture/SAD.md", "02-architecture/TEST_SPEC.md"],
        3: ["03-development/src/", "03-development/tests/"],
        4: ["04-testing/TEST_PLAN.md", "04-testing/TEST_RESULTS.md"],
        5: ["05-verification/BASELINE.md", "05-verification/VERIFICATION_REPORT.md"],
        6: ["06-quality/QUALITY_REPORT.md"],
        7: _phase_artifacts(7),
        8: ["08-config/CONFIG_RECORDS.md", "08-config/RELEASE_CHECKLIST.md"],
        9: ["09-maintenance/MAINTENANCE_LOG.md"],
    }
    PHASE_ARTIFACTS = {k: v for k, v in _ALL_PHASE_ARTIFACTS.items() if k <= current_phase}

    results: dict[str, Any] = {
        "project": str(project),
        "dimensions": {},
    }

    # --- Dimension 1: Directory existence (≤ current_phase only) ---
    dir_status = {}
    for num, dname in phase_dirs.items():
        dpath = project / dname
        dir_status[f"P{num}"] = {
            "dir": dname,
            "exists": dpath.is_dir(),
            "path": str(dpath),
        }
    results["dimensions"]["directory_existence"] = {
        "label": f"Directory Existence (up to P{current_phase})",
        "passed": all(v["exists"] for v in dir_status.values()),
        "details": dir_status,
    }

    # --- Dimension 2: Artifact completeness (≤ current_phase only) ---
    artifact_status = {}
    for phase_num, paths in PHASE_ARTIFACTS.items():
        phase_key = f"P{phase_num}"
        phase_files = []
        for p in paths:
            fpath = project / p
            exists = fpath.exists()
            size = fpath.stat().st_size if exists and fpath.is_file() else None
            phase_files.append({"path": p, "exists": exists, "size_bytes": size})
        artifact_status[phase_key] = {
            "dir": phase_dirs[phase_num],
            "all_present": all(f["exists"] for f in phase_files),
            "files": phase_files,
        }
    results["dimensions"]["artifact_completeness"] = {
        "label": "Artifact Completeness",
        "passed": all(v["all_present"] for v in artifact_status.values()),
        "details": artifact_status,
    }

    # --- Dimension 3: Content quality ---
    # FR-reference check applies only to phases 1–4 (phases 5–8 produce
    # operational docs that legitimately contain no FR/NFR references).
    _FR_REF_PHASES = {1, 2, 3, 4}

    def _check_content_quality(fpath: Path, phase_num: int = 0) -> dict:
        if not fpath.exists() or not fpath.is_file():
            return {"quality": "missing"}
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return {"quality": "unreadable"}
        issues = []
        if len(content.strip()) < 200:
            issues.append("content < 200 chars")
        is_yaml = fpath.name.endswith(".yaml") or fpath.name.endswith(".yml")
        # Round 29: MAINTENANCE_LOG.md's canonical shape (harness/templates/
        # MAINTENANCE_LOG.md) is a single H1 + an append-only CR table — rows
        # are appended by `cr-close`, not markdown sections, so it never gains
        # a 2nd heading even after CRs are processed. The section-count floor
        # is structurally inapplicable to this file, same reasoning as the
        # existing YAML exemption above.
        is_cr_log = fpath.name == "MAINTENANCE_LOG.md"
        if not is_yaml and not is_cr_log and len(_re.findall(r"(?:^|\n)#{1,6} ", content)) < 2:
            issues.append("< 2 markdown sections")
        # I: require CANONICAL FR-ID form (FR-NN / TASK-NN / NFR-NN, ≥2 digits).
        # Previously accepted 4 variants (FR-01, FR01, fr_01, FR(01)) which
        # masked source-code inconsistencies. Now strict — run canonical_lint
        # to find/fix variants in existing docs.
        # Round 29: TEST_RESULTS.md's canonical shape (harness/templates/
        # TEST_RESULTS.md) uses TC-XX test-case IDs, never FR/TASK/NFR refs —
        # this rule doesn't apply to it (harness's own template output would
        # itself fail this check).
        if (
            phase_num in _FR_REF_PHASES
            and fpath.name != "TEST_RESULTS.md"
            and not _re.search(r"\b(?:TASK|FR|NFR)-\d{2,}\b", content)
        ):
            issues.append("no [TASK/FR/NFR-NN] canonical references")
        return {"quality": "good" if not issues else "suspicious", "issues": issues}

    quality_status = {}
    for phase_num, paths in PHASE_ARTIFACTS.items():
        phase_key = f"P{phase_num}"
        phase_quality = []
        for art_path in paths:
            q = _check_content_quality(project / art_path, phase_num)
            q["path"] = art_path
            phase_quality.append(q)
        all_ok = all(q["quality"] == "good" for q in phase_quality
                     if not q["path"].endswith("/"))
        quality_status[phase_key] = {
            "dir": phase_dirs[phase_num],
            "all_quality_ok": all_ok,
            "files": phase_quality,
        }
    results["dimensions"]["content_quality"] = {
        "label": "Content Quality (non-hollow templates)",
        "passed": all(v["all_quality_ok"] for v in quality_status.values()),
        "details": quality_status,
    }

    # --- Dimension 4: ASPICE traceability chain ---
    try:
        from core.quality_gate.phase_artifact_enforcer import PhaseArtifactRegistry
        chain_result = PhaseArtifactRegistry(str(project)).verify_phase_chain(current_phase)
        aspice_passed = chain_result["all_verified"]
        aspice_detail = {
            "all_verified": aspice_passed,
            "stats": chain_result["stats"],
            "missing_links": chain_result.get("missing_links", []),
        }
    except Exception as exc:
        print(f"[WARN] audit-structure: ASPICE traceability chain check failed: {exc}", file=sys.stderr)
        aspice_passed = False
        aspice_detail = {"error": str(exc)}
    results["dimensions"]["aspice_chain"] = {
        "label": "ASPICE Traceability Chain (P1→P8)",
        "passed": aspice_passed,
        "details": aspice_detail,
    }

    # --- Dimension 5: Naming convention ---
    naming_issues = []
    expected_names = set(phase_dirs.values())
    # Known/unknown judged against ALL canonical phase dirs — init-project
    # pre-creates every phase directory (including future phases), so a
    # not-yet-reached phase dir (e.g. 09-maintenance at P1) is legitimate.
    # Only the phase-truncated set drives the `missing` check below.
    all_canonical_names = set(PHASE_DIRS.values())
    # Map "NN" prefix → canonical dir name, e.g. "05" → "05-verification"
    expected_by_prefix: dict[str, str] = {n.split("-")[0]: n for n in all_canonical_names}
    found_dirs = set()
    for child in project.iterdir():
        if not child.is_dir():
            continue
        if child.name in ("00-summary",):
            continue
        m = _re.match(r"^(\d{2})-", child.name)
        if m:
            found_dirs.add(child.name)
            if child.name not in all_canonical_names:
                prefix = m.group(1)
                canonical = expected_by_prefix.get(prefix)
                if canonical:
                    naming_issues.append(
                        f"naming deviation: '{child.name}' should be '{canonical}' "
                        f"— rename with: mv '{child.name}' '{canonical}'"
                    )
                else:
                    naming_issues.append(
                        f"unexpected directory '{child.name}' "
                        f"(no phase with prefix '{prefix}' in expected set)"
                    )
    missing = expected_names - found_dirs
    if missing:
        naming_issues.append(
            f"missing directories: {', '.join(sorted(missing))}"
        )
    naming_passed = len(naming_issues) == 0
    results["dimensions"]["naming_convention"] = {
        "label": "Naming Convention (0X-name/ format)",
        "passed": naming_passed,
        "details": {"issues": naming_issues},
    }

    # --- Summary ---
    dims = results["dimensions"]
    all_passed = all(d["passed"] for d in dims.values())
    results["summary"] = {
        "all_passed": all_passed,
        "pass_count": sum(1 for d in dims.values() if d["passed"]),
        "total_dims": len(dims),
    }

    if args.json:
        print(_json.dumps(results, indent=2, ensure_ascii=False))
    else:
        _print_audit_report(results)

    return 0 if all_passed else 1


def cmd_audit_phase(args: argparse.Namespace) -> int:
    """Audit a phase against GitHub or local artifacts (C1-C10 PhaseAuditor check).

    A1-2026-07-07: replace cwd-relative `from scripts.phase_auditor import …`
    with module-scope `load_harness_script()` call (see harness_cli.py:A1-2026-07-07
    docstring for path-fix rationale). Behavior is otherwise bit-equivalent —
    user-facing CLI is allowed to hard-fail if the install is corrupted (an
    ImportError means scripts/ is missing, which is a real problem worth surfacing).
    """
    _pa_mod = load_harness_script("phase_auditor.py")
    PhaseAuditor, GitHubFetcher, LocalFetcher = (
        _pa_mod.PhaseAuditor, _pa_mod.GitHubFetcher, _pa_mod.LocalFetcher,
    )

    project = getattr(args, "project", None)
    if project:
        # Local mode
        print(f"\n{'='*60}\naudit-phase [LOCAL]: Phase {args.phase} | project={project}\n{'='*60}")
        # No static annotation: GitHubFetcher/LocalFetcher are runtime-loaded via
        # load_harness_script, not real type objects pyright can resolve.
        fetcher = LocalFetcher(
            project_root=project, branch=args.branch
        )
    else:
        # GitHub mode (original)
        print(f"\n{'='*60}\naudit-phase [GITHUB]: Phase {args.phase} | repo={args.repo}\n{'='*60}")
        fetcher = GitHubFetcher(repo=args.repo, branch=args.branch)
        repo_info = fetcher.get_repo_info()
        if not repo_info:
            print(f"[ERROR] Cannot access repo: {args.repo} (check gh auth status)")
            return 1

    auditor = PhaseAuditor(fetcher=fetcher, phase=args.phase)
    result = auditor.run_all_checks()

    print(f"\n{'─'*60}")
    print(f"Audit Results — Phase {args.phase}")
    print(f"{'─'*60}")
    print(f"  Score        : {result.score:.0f}%")
    print(f"  Verdict      : {result.verdict}")
    print(f"  Critical     : {len(result.criticals())}")
    print(f"  Warnings     : {len(result.warnings())}")

    if args.save:
        save_path = Path(args.save)
        if args.output == "json":
            import json as _json
            save_path.write_text(_json.dumps({
                "phase": args.phase, "score": result.score,
                "verdict": result.verdict,
                "criticals": len(result.criticals()),
                "warnings": len(result.warnings()),
                "findings": [{"severity": f.severity, "check": f.check_id,
                              "detail": f.detail}
                             for f in result.findings],
            }, indent=2))
        else:
            save_path.write_text(str(result))
        print(f"\nReport saved → {save_path}")

    if getattr(args, "fail_on_critical", False) and result.criticals():
        return 1
    return 0 if result.verdict != "FAIL" else 1


# ---------------------------------------------------------------------------
# init-project helpers (moved verbatim from harness_cli.py, 絞殺者續章 S4)
# ---------------------------------------------------------------------------

def _harness_workflow_template() -> str:
    """Return the content of .github/workflows/harness_quality_gate.yml for a target project.

    Reads directly from templates/harness_quality_gate.yml — the single source
    of truth. This used to add "so there is no drift", which was true of the
    moment of deployment and of no moment after it: the framework edits the
    template, the copy in a consumer repo is edited by nobody, and taskq-renew
    was measured two rounds behind. `core.ci_template.ci_template_drift` is the
    part that makes the sentence true; doctor is what reads it.
    """
    template_path = ci_template_path()
    if not template_path.exists():
        raise FileNotFoundError(
            f"Workflow template not found: {template_path}\n"
            "Ensure templates/harness_quality_gate.yml exists in the harness-methodology repo."
        )
    return template_path.read_text(encoding="utf-8")

# Canonical phase directory names come from the topology SSOT — PHASE_DIRS is
# imported from core.phase_topology at the top of this module.

# Sub-directories created inside phase dirs on init (not tracked for naming checks).
_PHASE_INIT_SUBDIRS: list[str] = [
    "02-architecture/adr",
    "03-development/src",
    "03-development/tests",
]

def _init_phase_dirs(project: Path) -> None:
    """Create canonical 0X-name/ phase directory structure in target project."""
    dirs = [*PHASE_DIRS.values(), *_PHASE_INIT_SUBDIRS]
    created = 0
    skipped = 0
    for d in dirs:
        target = project / d
        if target.exists():
            skipped += 1
        else:
            target.mkdir(parents=True, exist_ok=True)
            created += 1
    if created:
        print(f"   OK — created {created} director{'y' if created == 1 else 'ies'} ({skipped} already existed)")
    else:
        print(f"   SKIP: all {skipped} directories already exist")

def _init_copy_templates(project: Path, harness_root: Path, *, overwrite: bool = False) -> None:
    """Copy artifact templates from harness templates/ into the target project."""
    templates_dir = harness_root / "templates"
    artifact_map = [
        ("01-requirements", "SRS.md"),
        ("01-requirements", "SPEC_TRACKING.md"),
        ("01-requirements", "TRACEABILITY_MATRIX.md"),
        ("", "TEST_INVENTORY.yaml"),       # project root — D4 reads from here
        ("02-architecture", "SAD.md"),
        ("02-architecture/adr", "ADR.md"),
        ("02-architecture", "TEST_SPEC.md"),
        ("09-maintenance", "MAINTENANCE_LOG.md"),  # P9 CR index (cr-close appends)
    ]
    copied = 0
    skipped = 0
    missing = 0
    protected = 0
    for subdir, filename in artifact_map:
        src = templates_dir / filename
        dst = project / subdir / filename
        if dst.exists() and not overwrite:
            skipped += 1
        elif not src.exists():
            print(f"   WARNING: template not found: {src}")
            missing += 1
        elif dst.exists() and dst.read_bytes() != src.read_bytes():
            # Deliverable differs from its template → authored in-flight state.
            # Never overwritten, even with --overwrite — mirrors the state.json
            # never-reset rule (integration-test E2E clobber, 2026-07-02).
            print(
                f"   PROTECTED: {dst} differs from template (authored content); "
                "not overwritten — delete the file manually to re-template it."
            )
            protected += 1
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1

    # CLAUDE.md.template → project/CLAUDE.md (only if no CLAUDE.md exists).
    # An existing CLAUDE.md is never re-copied, even with --overwrite: the
    # harness auto block is refreshed in place by _update_claude_md, and a
    # wholesale re-copy only destroys user custom sections below the block.
    claude_tmpl = harness_root / "CLAUDE.md.template"
    claude_dst = project / "CLAUDE.md"
    if claude_dst.exists():
        skipped += 1
    elif claude_tmpl.exists():
        shutil.copy2(claude_tmpl, claude_dst)
        # Substitute {PROJECT_NAME} so the header is immediately readable
        try:
            raw = claude_dst.read_text(encoding="utf-8").replace(
                "{PROJECT_NAME}", project.name
            )
            claude_dst.write_text(raw, encoding="utf-8")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"[WARN] init-project: CLAUDE.md {{PROJECT_NAME}} substitution "
                  f"failed, template copied verbatim: {exc}", file=sys.stderr)
        copied += 1
    else:
        missing += 1

    parts = []
    if copied:
        parts.append(f"copied {copied} template{'s' if copied != 1 else ''}")
    if skipped:
        parts.append(f"{skipped} already existed")
    if protected:
        parts.append(f"{protected} authored (protected)")
    if missing:
        parts.append(f"{missing} template(s) not found")
    if parts:
        print(f"   OK — {', '.join(parts)}")
    else:
        print("   SKIP: nothing to copy")


def _init_js_toolchain(
    project: Path,
    harness_root: Path,
    language: str,
    test_runner: str | None,
    *,
    overwrite: bool = False,
) -> None:
    """Copy the pinned JS/TS quality-toolchain templates into the project.

    package.json is MERGED (existing devDependencies/scripts win — the project
    owns its versions; the template only fills gaps). Config files are copied
    only when absent (or --overwrite). Gate commands use `npx --no-install`,
    so `npm ci` must run after this step.
    """
    src_dir = harness_root / "templates" / "js_toolchain"
    if not src_dir.is_dir():
        print(f"   WARNING: {src_dir} not found — skipping JS toolchain setup")
        return

    # 1. Merge devDependencies/scripts into package.json
    pkg_path = project / "package.json"
    tmpl = json.loads((src_dir / "package.json").read_text(encoding="utf-8"))
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8")) if pkg_path.exists() else {}
    except json.JSONDecodeError:
        print(f"   WARNING: {pkg_path} is not valid JSON — skipping merge")
        pkg = None
    if pkg is not None:
        added: list[str] = []
        for section in ("devDependencies", "scripts"):
            merged = dict(tmpl.get(section, {}))
            merged.update(pkg.get(section, {}))  # existing entries win
            added += [k for k in merged if k not in pkg.get(section, {})]
            pkg[section] = merged
        pkg_path.write_text(
            json.dumps(pkg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"   OK — package.json merged ({len(added)} entries added)")

    # 2. Config files — copy when absent
    files = ["eslint.config.mjs", "stryker.conf.json", "benchmarks/run.mjs"]
    if test_runner != "jest":
        files.append("vitest.config.ts")
    files.append("tsconfig.json" if language == "typescript" else "tsconfig.checkjs.json")
    copied = 0
    for rel in files:
        src, dst = src_dir / rel, project / rel
        if dst.exists() and not overwrite:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    print(f"   OK — {copied} toolchain config(s) copied")
    print("   NEXT: run `npm ci` in the project — gate commands use "
          "`npx --no-install` and fail without installed devDependencies.")


def _setup_branch_protection(project: Path) -> int:
    """Configure GitHub branch protection for main with required status checks.

    Requires:
      - gh CLI installed and authenticated
      - Remote 'origin' pointing to a GitHub repository

    Returns 0 on success, 1 on failure.
    """
    import subprocess

    # Detect GitHub remote URL
    try:
        remote = subprocess.run(
            ["git", "-C", str(project), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
        if remote.returncode != 0 or not remote.stdout.strip():
            print("   ERROR: No git remote 'origin' found.")
            return 1
        remote_url = remote.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("   ERROR: Failed to read git remote.")
        return 1

    # Parse owner/repo from common URL formats
    owner = repo = None
    if remote_url.startswith("https://github.com/"):
        parts = remote_url.rstrip(".git").split("/")
        if len(parts) >= 2:
            owner, repo = parts[-2], parts[-1]
    elif remote_url.startswith("git@github.com:"):
        parts = remote_url.rstrip(".git").split(":")
        if len(parts) == 2:
            parts2 = parts[1].split("/")
            if len(parts2) == 2:
                owner, repo = parts2[0], parts2[1]
    elif "github.com" in remote_url:
        # Fallback: use gh repo view to parse
        try:
            # run_isolated, not run_against_source_tree: reading GitHub
            # metadata must not queue behind a mutation window (Round 66).
            from core.utils.subprocess_group import run_isolated
            rv = run_isolated(
                ["gh", "repo", "view", "--json", "name,owner"],
                timeout=10, cwd=str(project),
            )
            if rv.returncode == 0:
                import json as _json
                data = _json.loads(rv.stdout)
                owner, repo = data["owner"]["login"], data["name"]
        except Exception as exc:
            print(f"[WARN] audit-phase: `gh repo view` owner/repo fallback "
                  f"failed: {exc}", file=sys.stderr)

    if not owner or not repo:
        print("   ERROR: Could not parse GitHub owner/repo from remote URL.")
        print(f"   Remote: {remote_url}")
        print("   Use --repo OWNER/REPO to specify explicitly.")
        return 1

    print(f"   Remote: {owner}/{repo}")

    # Verify gh is authenticated
    try:
        auth_check = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True, timeout=10,
        )
        if auth_check.returncode != 0:
            print("   ERROR: gh CLI not authenticated. Run: gh auth login")
            return 1
    except FileNotFoundError:
        print("   ERROR: gh CLI not installed. Install GitHub CLI:")
        print("     brew install gh  (macOS)")
        print("     sudo apt install gh  (Linux)")
        return 1

    api_url = f"repos/{owner}/{repo}/branches/main/protection"
    # Direct-push model: only force-push and deletion protection are enabled.
    # PR-only fields must be present (GitHub PUT requires them) but set to disabled.
    payload = {
        "required_status_checks": None,
        "enforce_admins": False,
        "required_pull_request_reviews": None,
        "required_linear_history": False,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "restrictions": None,
    }

    try:
        result = subprocess.run(
            ["gh", "api", api_url, "--method", "PUT",
             "--input", "-"],
            input=json.dumps(payload),
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            print(f"   OK — Branch protection configured for {owner}/{repo}/main")
            print("   Direct-push model: force pushes + deletions blocked.")
            _verify_no_pr_requirement(owner, repo)
            return 0
        else:
            err = result.stderr.strip() or result.stdout.strip()
            # 404 often means branch protection already exists; try PATCH
            if "404" in err or "Not Found" in err:
                # Update existing protection
                result2 = subprocess.run(
                    ["gh", "api", api_url, "--method", "PATCH",
                     "--input", "-"],
                    input=json.dumps(payload),
                    capture_output=True, text=True, timeout=30,
                )
                if result2.returncode == 0:
                    print(f"   OK — Branch protection updated for {owner}/{repo}/main (direct-push model)")
                    _verify_no_pr_requirement(owner, repo)
                    return 0
                err = result2.stderr.strip() or result2.stdout.strip()
            print(f"   ERROR: Failed to set branch protection:\n   {err[:500]}")
            return 1
    except subprocess.TimeoutExpired:
        print("   ERROR: API call timed out.")
        return 1

def _verify_no_pr_requirement(owner: str, repo: str) -> None:
    """Warn if branch protection has PR requirement — incompatible with direct-push.

    Best-effort: prints a [WARN] to stderr (not silent) when gh CLI is unavailable
    or the protection endpoint fails, so operators can see why verification was
    skipped.
    """
    import subprocess as _sp
    try:
        r = _sp.run(
            ["gh", "api", f"repos/{owner}/{repo}/branches/main/protection"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            print(
                f"   [WARN] PR-requirement verification skipped — gh api returned "
                f"non-zero exit ({r.returncode}). Verify manually: GitHub repo → "
                f"Settings → Branches → 'Require a pull request' must be OFF.",
                file=sys.stderr,
            )
            return
        cfg = json.loads(r.stdout)
        pr_reviews = cfg.get("required_pull_request_reviews")
        if pr_reviews:
            print(f"   WARNING: 'Require a pull request' is still enabled on {owner}/{repo}/main.")
            print("   This will block push-checkpoint. Disable it manually:")
            print("     GitHub repo → Settings → Branches → Edit (main)")
            print("     → Uncheck 'Require a pull request before merging'")
    except (FileNotFoundError, _sp.TimeoutExpired, json.JSONDecodeError) as exc:
        print(
            f"   [WARN] PR-requirement verification skipped: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

def _check_crg_available() -> bool:
    """Check whether CRG MCP server is reachable.

    CRG (Code Review Graph) is mandatory for Gate 3/4 structural dimensions
    (architecture, error_handling). The core tools (build, detect_changes,
    minimal_context) are imported at module level in harness/crg_bridge.py via
    ``from mcp_tools import ...`` — if the CRG MCP server is not configured,
    the import fails and the bridge is unavailable.
    """
    try:
        __import__("harness.crg_bridge")
        return True
    except (ImportError, ModuleNotFoundError):
        return False


def _check_and_offer_ecc_hooks(harness_root: Path) -> None:
    """Check for ECC hooks presence and offer to install if missing.

    ECC hooks intercept tool calls at the Claude Code session layer,
    providing a bypass-proof safety net against ``git --no-verify``.
    """
    hooks_file = Path.home() / ".claude" / "hooks" / "hooks.json"
    if hooks_file.exists():
        try:
            data = json.loads(hooks_file.read_text(encoding="utf-8"))
            if "pre:bash:dispatcher" in data:
                print("   OK — ECC hooks present (git --no-verify blocked at session layer)")
                return
            print("   WARNING: ECC hooks file exists but pre:bash:dispatcher hook is missing.")
        except Exception:
            print("   WARNING: ECC hooks file exists but is unreadable.")
    else:
        print("   WARNING: ECC hooks not installed — git --no-verify is NOT blocked.")

    # Offer installation
    setup_script = harness_root / "scripts" / "setup-ecc-hooks.sh"
    if setup_script.exists():
        print(f"   Install: bash {setup_script}")
        print(f"   Verify:  bash {setup_script} --verify")
    else:
        print("   Setup script not found in harness installation.")


def _auto_offer_branch_protection(project: Path) -> None:
    """Auto-detect gh CLI and offer to set up branch protection.

    When gh is available and authenticated, offers interactive setup.
    Otherwise prints the manual setup guide so the operator can configure
    protection via GitHub's web UI.
    """
    import subprocess
    # Check gh availability
    try:
        gh_check = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True, timeout=10,
        )
        if gh_check.returncode != 0:
            _print_manual_branch_protection_guide()
            return
    except FileNotFoundError:
        _print_manual_branch_protection_guide()
        return
    except subprocess.TimeoutExpired:
        print("   WARNING: gh CLI check timed out — skipping auto-setup.")
        _print_manual_branch_protection_guide()
        return

    # gh is available — offer setup
    print("   gh CLI detected and authenticated.")
    try:
        remote_check = subprocess.run(
            ["git", "-C", str(project), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
        if remote_check.returncode != 0 or "github.com" not in remote_check.stdout:
            print("   SKIP: git remote 'origin' not pointing to GitHub.")
            return
    except Exception:
        print("   SKIP: cannot detect git remote.")
        return

    print("   Setting up branch protection automatically...")
    rc = _setup_branch_protection(project)
    if rc != 0:
        _print_manual_branch_protection_guide()


def _print_manual_branch_protection_guide() -> None:
    """Print manual branch protection setup instructions for GitHub web UI."""
    print("   ═══════════════════════════════════════════════════════════════")
    print("   Set up GitHub branch protection manually:")
    print("     Settings → Branches → Add branch protection rule")
    print("     Branch name pattern: main")
    print("     ✅ Block force pushes")
    print("     ✅ Block deletions")
    print("     ❌ Do NOT enable 'Require a pull request'")
    print("     ❌ Do NOT enable 'Require status checks'")
    print("   ═══════════════════════════════════════════════════════════════")
    print("   Or install gh CLI for automatic setup:")
    print("     brew install gh && gh auth login")
    print("     Then re-run: python3 harness_cli.py init-project --project . --setup-branch-protection")




def _print_audit_report(results: dict) -> None:
    """Print human-readable audit-structure report."""
    print(f"\n{'='*60}")
    print("Audit-Structure Report")
    print(f"Project: {results['project']}")
    print(f"{'='*60}")

    dims = results["dimensions"]
    for key, dim in dims.items():
        icon = "PASS" if dim["passed"] else "FAIL"
        print(f"\n  [{icon}] {dim['label']}")

        if key == "directory_existence":
            for pk, dv in dim["details"].items():
                mark = "✅" if dv["exists"] else "❌"
                print(f"     {mark} {pk}  {dv['dir']}")

        elif key == "artifact_completeness":
            for pk, pv in dim["details"].items():
                mark = "✅" if pv["all_present"] else "❌"
                print(f"     {mark} {pk} ({pv['dir']})")
                if not pv["all_present"]:
                    for f in pv["files"]:
                        if not f["exists"]:
                            print(f"        ❌ MISSING: {f['path']}")

        elif key == "content_quality":
            for pk, pv in dim["details"].items():
                mark = "✅" if pv["all_quality_ok"] else "⚠️"
                print(f"     {mark} {pk} ({pv['dir']})")
                for f in pv["files"]:
                    if f["quality"] != "good" and not f["path"].endswith("/"):
                        issues = ", ".join(f.get("issues", []))
                        print(f"        ⚠️  {f['path']}: {f['quality']}"
                              + (f" ({issues})" if issues else ""))

        elif key == "aspice_chain":
            stats = dim["details"].get("stats", {})
            print(f"     Verified: {stats.get('verified', '?')}/{stats.get('total', '?')} links")
            for link in dim["details"].get("missing_links", [])[:5]:
                print(f"        ❌ {link}")

        elif key == "naming_convention":
            if dim["passed"]:
                print("     ✅ All 0X-name/ directories match expected names")
            else:
                for issue in dim["details"]["issues"]:
                    print(f"        ❌ {issue}")

    # Footer
    s = results["summary"]
    print(f"\n{'='*60}")
    if s["all_passed"]:
        print(f"RESULT: ALL PASS ({s['pass_count']}/{s['total_dims']} dimensions)")
    else:
        print(f"RESULT: FAIL — {s['total_dims'] - s['pass_count']} dimension(s) failed")
    print(f"{'='*60}")


def register(sub) -> None:
    """Wire this family's parsers onto the main subparser action."""
    # init-project
    ip = sub.add_parser(
        "init-project",
        help="Initialize harness CI wiring in a target project (Context B one-shot setup)",
    )
    ip.add_argument("--project", required=True, help="Target project root path")
    ip.add_argument("--phase",   type=int, default=1, help="Current phase (default: 1)")
    ip.add_argument("--language", default=None,
                    help="Project language (e.g. python, javascript, typescript). "
                         "Default: auto-detect from manifest files; required when "
                         "detection is ambiguous")
    ip.add_argument("--test-runner", default=None,
                    help="JS/TS test runner (vitest or jest). Default: auto-detect "
                         "from package.json; required when detection is ambiguous")
    ip.add_argument("--ci-only", action="store_true",
                    help="Write CI workflow only; skip git hooks")
    ip.add_argument("--overwrite", action="store_true",
                    help="Overwrite existing CI workflow and hooks")
    ip.add_argument("--setup-branch-protection", action="store_true",
                    help="Configure GitHub branch protection for main with required checks")
    ip.set_defaults(func=cmd_init_project)

    # bootstrap-env
    #
    # A second entry point onto scripts/bootstrap_env.py, not a second
    # implementation. The standalone script is the one that matters before a
    # venv exists (harness_cli.py cannot import on an interpreter without
    # pyyaml); this one exists so the same operation is reachable from the CLI
    # once it does, and so env repair has an in-process caller.
    be = sub.add_parser(
        "bootstrap-env",
        help="Create the project virtualenv the harness runs from and install "
             "the pinned toolchain into it",
    )
    be.add_argument("--project", default=".", help="Project root (default: .)")
    be.add_argument("--json", action="store_true", help="Machine-readable report")
    be.set_defaults(func=cmd_bootstrap_env)

    # status
    st = sub.add_parser("status", help="Show current manifest + FSM state")
    st.add_argument("--project", default=".", help="Project root (default: .)")
    st.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    st.add_argument("--full", action="store_true", help="Include test stats and auto-fix rounds")
    st.set_defaults(func=cmd_status)

    # doctor (read-only cross-file consistency check)
    dr = sub.add_parser(
        "doctor",
        help="Check state.json / manifest / CLAUDE.md / attestation consistency "
             "and detect interrupted state transactions (read-only)",
    )
    dr.add_argument("--project", default=".", help="Project root (default: .)")
    dr.set_defaults(func=cmd_doctor)

    # load-context
    lc = sub.add_parser("load-context",
                        help="Load project context for a phase (JSON output)")
    lc.add_argument("--phase",   type=int, required=True, help="Phase number (1-8)")
    lc.add_argument("--project", default=".", help="Project root (default: .)")
    lc.add_argument("--json",    action="store_true", help="Output as JSON (default behavior)")
    lc.set_defaults(func=cmd_load_context)

    # read-file (deterministic file read; CLI wrapper over scripts/file_loader.py)
    rf = sub.add_parser(
        "read-file",
        help="Deterministic file read with server-side prefix/length/SHA validation. "
             "Use from workflow JS via Bash agent to avoid LLM-as-shell hallucination "
             "of file content.",
    )
    rf.add_argument("--file", required=True, help="Path to the file to load (absolute or relative to project root)")
    rf.add_argument("--expect-prefix", default=None, help="If set, file's first line must start with this string")
    rf.add_argument("--min-length", type=int, default=0, help="Minimum byte size; below returns TOO_SHORT")
    rf.add_argument("--max-length", type=int, default=None, help="Maximum byte size; above truncates content with a suffix")
    rf.add_argument("--content", action="store_true", help="Include (possibly truncated) content text in JSON output")
    rf.add_argument("--content-out", default=None, help="If set, also write content to this path")
    rf.add_argument("--relay", action="store_true", help="Wrap content in the relay envelope; index it above the ceiling")
    rf.add_argument("--relay-max-bytes", type=int, default=_RELAY_MAX_BYTES, help=f"Relay content ceiling in bytes (default {_RELAY_MAX_BYTES})")
    rf.add_argument("--json-out", default=None, help="If set, write JSON result to this path; otherwise print to stdout")
    rf.add_argument("--quiet", action="store_true", help="Suppress the human-readable status line on stderr")
    rf.set_defaults(func=cmd_read_file)

    # effort
    ef = sub.add_parser("effort", help="Show gate effort metrics summary")
    ef.add_argument("--phase",   type=int, default=None, help="Filter by phase")
    ef.add_argument("--project", default=".", help="Project root (default: .)")
    ef.set_defaults(func=cmd_effort)

    # audit-phase
    ap = sub.add_parser(
        "audit-phase",
        help="Audit a phase against GitHub artifacts (8-dimension PhaseAuditor check)",
        description=(
            "Audit a phase against GitHub/local artifacts (8-dimension PhaseAuditor check). "
            "No workflow ever calls this automatically — run it BEFORE advance-phase for a "
            "phase-scoped audit. Running it AFTER that phase's advance-phase has already "
            "succeeded will correctly show a C10 'current_phase != audited phase' WARNING "
            "(phase drift is real once you've moved on, not a defect)."
        ),
    )
    ap.add_argument("--phase",  type=int, required=True, help="Phase number to audit (1-8)")
    _ap_src = ap.add_mutually_exclusive_group(required=True)
    _ap_src.add_argument(
        "--repo",
        help="GitHub repo in owner/repo format (e.g. johnnylugm-tech/my-project)"
    )
    _ap_src.add_argument(
        "--project",
        metavar="PATH",
        help="Local project root path for on-machine audit (e.g. /path/to/project)"
    )
    ap.add_argument("--branch", default="main", help="Target branch (default: main)")
    ap.add_argument("--output", choices=["markdown", "json"], default="markdown",
                    help="Output format (default: markdown)")
    ap.add_argument("--save",   default=None, metavar="FILE",
                    help="Save report to file")
    ap.add_argument(
        "--fail-on-critical",
        action="store_true",
        help="Exit 1 if any CRITICAL finding exists (stricter than default FAIL verdict).",
    )
    ap.set_defaults(func=cmd_audit_phase)

    # amend-sab (SAB Architecture Amendment Protocol — standalone)
    asab = sub.add_parser(
        "amend-sab",
        help="Run SAB Architecture Amendment Protocol: register 03-development/src/ modules "
             "missing from .methodology/SAB.json (recovers run-gate BLOCKED state)",
    )
    asab.add_argument("--project", required=True, help="Target project root path")
    asab.add_argument("--src-dir", default="03-development/src",
                    help="Source directory to scan (default: 03-development/src)")
    asab.add_argument("--dry-run", action="store_true",
                    help="List modules that would be added without writing SAB.json")
    asab.add_argument("--strict", action="store_true",
                    help="Exit non-zero if PHANTOM modules are detected "
                         "(SAB registers modules that src does not implement)")
    asab.add_argument("--resolve-phantom", metavar="DOTTED",
                    help="Amend the architecture: retarget or drop a PHANTOM module "
                         "SAB.json declares (Round 26). Requires --reason, and either "
                         "--to DOTTED or --drop. Records the amendment in "
                         "02-architecture/ADR.md — this is the ONLY sanctioned way to "
                         "change a declared module path; the alternative was writing "
                         "code to match a Phase 2 guess or hand-editing SAB.json")
    asab.add_argument("--to", metavar="DOTTED", dest="resolve_to",
                    help="The module that actually exists, replacing --resolve-phantom")
    asab.add_argument("--drop", action="store_true", dest="resolve_drop",
                    help="Remove --resolve-phantom's module from the architecture "
                         "entirely (use when the FR no longer needs it)")
    asab.add_argument("--reason", help="Why the declared decomposition changed (>= 20 "
                                       "chars). Written to ADR.md — an architecture "
                                       "changed without a recorded reason is "
                                       "indistinguishable from an implementation that "
                                       "drifted")
    asab.set_defaults(func=cmd_amend_sab)

    # audit-structure
    aus = sub.add_parser(
        "audit-structure",
        help="Audit target project directory structure and artifact completeness",
    )
    aus.add_argument("--project", required=True, help="Target project root path")
    aus.add_argument("--json", action="store_true", help="Output as JSON")
    aus.set_defaults(func=cmd_audit_structure)

    # kill-switch (CV-6 — operator CLI for M1 KillSwitch)
    ks = sub.add_parser(
        "kill-switch",
        help="Manually trigger/reset/inspect M1 KillSwitch circuit for an agent.",
    )
    ks_sub = ks.add_subparsers(dest="kill_action", required=True)

    kst = ks_sub.add_parser("trigger", help="Open the circuit (halt agent dispatch).")
    kst.add_argument("--agent-id", required=True, help="Agent identifier to halt.")
    kst.add_argument("--reason", required=True, help="Reason — recorded in audit log.")
    kst.add_argument("--operator", help="Operator ID (default: $USER).")

    ksr = ks_sub.add_parser("reset", help="Close the circuit (re-enable agent dispatch).")
    ksr.add_argument("--agent-id", required=True, help="Agent to re-enable.")
    ksr.add_argument("--ack", required=True, help="Acknowledgment message — audit logged.")
    ksr.add_argument("--operator", help="Operator ID (default: $USER).")

    kss = ks_sub.add_parser("status", help="Show circuit state for one or all agents.")
    kss.add_argument("--agent-id", help="Specific agent to inspect (default: list all).")

    ks.set_defaults(func=cmd_kill_switch)
