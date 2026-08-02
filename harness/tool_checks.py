"""Tool availability checks (S2: prevents LLM guessing when tools are missing).

Moved verbatim from harness_cli.py (絞殺者續章 S2). Lives in harness/ because
resolution goes through the toolchain registry (harness/toolchains) and
harness_bridge — core/ must not grow a core→harness edge for this.

Tool-id → (check_cmd, human_name) lives in the toolchain registry
(harness/toolchains/registry.py) and is resolved per project language.
Only the dimension-name fallback for older YAML configs without a tool field
remains here. Dimension names that don't map to a dedicated tool
(LLM-evaluated dimensions) have no tool requirement.
"""

from __future__ import annotations

import subprocess
import sys

__all__ = [
    "DIM_FALLBACK_CHECKS",
    "run_tool_check",
    "check_tool_for_dim",
    "verify_gate_tools",
    "verify_all_gate_tools",
]

DIM_FALLBACK_CHECKS: dict[str, tuple[str, str]] = {
    "secrets_scanning": ("gitleaks version 2>&1", "gitleaks"),
    "mutation_testing": ("mutmut --help 2>&1", "mutmut"),
    "license_compliance": ("scancode --version 2>&1", "scancode-toolkit"),
    "linting": ("ruff --version 2>&1 || python3 -m ruff --version 2>&1", "ruff"),
    "type_safety": ("mypy --version 2>&1", "mypy"),
    "test_coverage": ("pytest --version 2>&1", "pytest + coverage"),
    "architecture": ("code-review-graph status 2>&1", "code-review-graph"),
}


def run_tool_check(check_cmd: str, cwd: str | None = None) -> bool:
    """Run a shell availability probe; True when it exits 0.

    *cwd* is the target project root: some check_cmds are cwd-relative —
    `npx --no-install <tool>` resolves node_modules from cwd, and the
    tsc-checkjs probe does `test -f tsconfig.checkjs.json`. Passing it
    explicitly decouples the probe from the harness's ambient cwd.
    """
    result = subprocess.run(
        ["bash", "-c", check_cmd],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=10, text=True, cwd=cwd,
    )
    return result.returncode == 0


def check_tool_for_dim(
    dim_name: str, tool_name: str | None, language: str = "python",
    project_root: str | None = None,
) -> tuple[bool, str]:
    """Check if the required tool for a dimension is installed.

    Resolves the YAML tool name through the toolchain registry (for python the
    YAML name passes through unchanged; other languages resolve by dimension
    via DIMENSION_TOOLS). Falls back to the dimension-name table for older
    configs without a tool field. *project_root* is the cwd for cwd-relative
    probes (npx, tsconfig.checkjs.json). Returns (available, diagnostic).
    """
    from harness.toolchains import get_tool_spec, resolve_tool_id

    resolved = resolve_tool_id(dim_name, language, yaml_tool=tool_name)
    if resolved is None and language != "python":
        # Registered languages must cover every tool-scored dimension (R8);
        # reaching here means the toolchain registry has no entry.
        return False, (
            f"{dim_name}: no '{language}' toolchain entry — "
            f"language not fully supported (see harness/toolchains/registry.py)"
        )

    spec = get_tool_spec(resolved) if resolved else None
    if spec is not None:
        try:
            ok = run_tool_check(spec.check_cmd, cwd=project_root)
            return ok, (
                "" if ok else f"{dim_name}: {spec.human_name} ({resolved}) not found"
            )
        except Exception:
            return False, f"{dim_name}: {spec.human_name} ({resolved}) check failed"

    # Fall back to dimension name lookup (older configs without tool field)
    info = DIM_FALLBACK_CHECKS.get(dim_name)
    if info is None:
        return True, ""  # No tool requirement — pass (LLM-evaluated dimension)
    check_cmd, human_name = info
    try:
        ok = run_tool_check(check_cmd, cwd=project_root)
        return ok, ("" if ok else f"{dim_name}: {human_name} not found")
    except Exception:
        return False, f"{dim_name}: {human_name} check failed"


def verify_gate_tools(
    gate_num: int, project: str, state_root: str | None = None
) -> tuple[bool, list[str]]:
    """Check all required tools for a gate exist (S2).

    Reads gate YAML config: if a dimension has requires_tool_execution: true
    and a tool field, the tool MUST be installed. Dimensions without
    requires_tool_execution (e.g. security, architecture) are LLM-evaluated
    and skipped.

    Tool resolution is language-aware: the project language is read from
    *state_root*/.methodology/state.json (defaults to *project* — pass
    state_root explicitly when gate configs and FSM state live in different
    roots, as in init-project where configs come from the harness checkout).

    Returns (all_ok, missing_list).
    """
    from harness.toolchains import get_project_language
    # The target project (where state.json + node_modules + tsconfig live) is
    # state_root when given (init-project: configs come from the harness
    # checkout), else project itself.
    target_root = state_root or project
    language = get_project_language(target_root)
    import yaml as _yaml
    from core.quality_gate.gate_thresholds import gate_config_path as _gcp
    # gate_num comes from the framework's own call sites, never from user input.
    # A value outside 1-4 is a caller-contract violation, so the ValueError is
    # left to propagate into the Round 28 crash boundary, which produces a crash
    # bundle naming the caller. Round 29 caught it and returned `(True, [])` —
    # a programming error reported as "every tool is installed" (Round 30 站3).
    cfg_path = _gcp(gate_num)

    if not cfg_path.exists():
        return False, [
            f"gate config not found: {cfg_path} (gate {gate_num}). "
            f"Expected framework-owned asset — is the harness checkout intact?"
        ]

    try:
        cfg = _yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except (_yaml.YAMLError, OSError) as exc:
        return False, [f"gate config unreadable: {cfg_path.name} ({exc})"]

    # Round 30 站3 — no CI escape hatch here.
    #
    # 63b9399 added `if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
    # return True, []` to unbreak harness's own CI after station 1 made this
    # function fail closed. That reintroduced, one commit later, the exact
    # "abstain reads as pass" shape station 1 existed to remove — and made it
    # settable by anyone: `CI=1` locally disables tool verification for a whole
    # run. It cited phase_cmds.py's substrate-probe CI skip (e92d089) as
    # precedent, but the justification does not transfer: that probe needs an
    # interactive `claude` CLI which GitHub Actions genuinely cannot have and
    # the CI workflow's own comments call "always local". Tool availability is
    # different — harness's own CI installs ruff and mypy, and a consumer's
    # CI-run gate scoring without its tools verified is what S2 exists to stop.
    #
    # The trigger was harness's own test suite, so the skip belongs there:
    # tests monkeypatch check_tool_for_dim (or verify_all_gate_tools), the
    # technique 877c1bb already used in tests/cli/test_phase_cmds_cli.py.
    from harness.harness_bridge import filter_enabled_dimensions
    cfg["dimensions"] = filter_enabled_dimensions(
        cfg.get("dimensions", []), target_root
    )

    missing: list[str] = []
    for dim in cfg.get("dimensions", []):
        dim_name = dim.get("name", "")
        requires_tool = dim.get("requires_tool_execution", False)
        if not requires_tool:
            continue  # LLM-evaluated dimension — skip tool check
        tool_name = dim.get("tool")  # May be None for older configs
        ok, diag = check_tool_for_dim(
            dim_name, tool_name, language, project_root=target_root
        )
        if not ok and diag:
            missing.append(diag)
    return len(missing) == 0, missing


def verify_all_gate_tools(project: str) -> tuple[bool, list[str]]:
    """Check that every tool required by ANY gate config is installed.

    Run at each phase entry so missing required components (notably
    `code-review-graph`, which scores the architecture dimension) surface at
    project setup rather than deep inside Gate 3/4. CRG and the other SSI tools
    are hard dependencies — there is no graceful degradation.
    """
    all_missing: list[str] = []
    seen: set[str] = set()
    for gate_num in (1, 2, 3, 4):
        _, missing = verify_gate_tools(gate_num, project)
        for m in missing:
            if m not in seen:
                seen.add(m)
                all_missing.append(m)

    # Soft check: mutmut 2.5.x hardcodes `python` (not `python3`). If mutmut is
    # required but only python3 exists, warn (don't block — the user can symlink).
    if not all_missing:
        _mutmut_needed = any("mutmut" in m for m in seen)
        if _mutmut_needed:
            import shutil as _shutil
            if _shutil.which("mutmut") and not _shutil.which("python") and _shutil.which("python3"):
                print(
                    "  [WARN] mutmut hardcodes `python` (not `python3`).\n"
                    "    Preferred fix: activate the project venv (`.venv/bin/python` exists).\n"
                    "    Fallback: ln -s $(which python3) /usr/local/bin/python\n"
                    "    Without this, mutation_testing dimension will fail at Gate 3/4.",
                    file=sys.stderr,
                )

    return len(all_missing) == 0, all_missing
