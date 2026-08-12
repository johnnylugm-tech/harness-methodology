"""Round 47 站1 — how the framework's tools get installed, stated once.

`ToolSpec.check_cmd` has always answered "is this tool here?". Nothing answered
"how does it get here". That half lived as prose in seven places, and they
disagreed: code-review-graph had three installers (`pipx install` in
harness/ssi/scripts/verify_tools.py, unpinned `pip install` in
cli/project_cmds.py:386, `pip install …==2.3.6` in the CI template and in
js_blocks' crg_verify_cmd), and verify_tools told users `pip3 install mutmut`
while requirements.txt pins `mutmut==2.5.1` with the comment that 3.x is
incompatible with this repo's sys.path layout.

That is not untidiness. requirements.txt's header states the stake: "Floating
versions risk score drift across environments: the same code must produce the
same dimension scores regardless of when/where it is installed." The CI
template records the incident at :610-624 — architecture scored 66.7 in CI
against a committed baseline of 100.0, same commit, source unchanged, because
code-review-graph resolved to a different version.

STDLIB ONLY, deliberately. scripts/bootstrap_env.py imports this module on
whatever interpreter the operator happens to have, before any venv exists.
harness_cli.py cannot be that entry point: sab_parser.py:45,
security_design.py:47 and traceability/overlay.py:23 are module-level
`import yaml`, so importing it on a bare interpreter raises ModuleNotFoundError
(measured 2026-08-12 on a clean 3.14 venv). harness.toolchains does import
stdlib-only, which is why the SSOT lives here rather than in core/.

Five provenances, because there are five. The approved plan named three; the
self-review said that if a fourth turned up it should be recorded rather than
forced into an existing one. Writing the field out across all 34 ToolSpecs
turned up two:

  requirements  pinned in requirements.txt; one `pip install -r` gets them all
  gate-extras   a SECOND pip round is structurally required — requirements.txt's
                own comment explains that code-review-graph pulls fastmcp, whose
                exceptiongroup>=1.2.2 conflicts with semgrep==1.165.0's
                exceptiongroup~=1.2.0, making a combined resolve
                ResolutionImpossible for the whole file
  external      not a Python package at all (gitleaks is Go, make is a system
                binary). 老闆's boundary for Round 47: the framework runs pip
                into the project venv and nothing else, so these are reported,
                never attempted
  npm           the project's own package.json owns them (templates/js_toolchain).
                A different owner, so the framework has no install statement to
                make
  builtin       the interpreter provides it (the ast-* scanners probe
                `import ast`). If that check fails the interpreter is broken;
                no install fixes it, so claiming one would be a lie

Measured 2026-08-12: a clean venv with only `pip install -r requirements.txt`
(PATH narrowed to /usr/bin:/bin plus node) still fails verify_all_gate_tools on
code-review-graph, import-linter, scancode and gitleaks — the first three are
`gate-extras`, the last is `external`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "PIP_STEPS",
    "PINS",
    "EXTERNAL_BINARIES",
    "NPM_ADVICE",
    "REQUIREMENTS_RELPATH",
    "RUNTIME_IMPORTS",
    "PipStep",
    "harness_root",
    "requirements_path",
    "requirements_packages",
    "step_for_tool",
    "pip_args",
    "install_advice",
]

# Where requirements.txt sits when the harness is a submodule of a target
# project — the path the CI template and USER_MANUAL both write.
REQUIREMENTS_RELPATH = "harness/requirements.txt"

# What must be importable in the prepared interpreter for the harness CLI to
# start at all. Both are pinned in requirements.txt; this list is the
# post-install assertion, not a second install statement.
RUNTIME_IMPORTS: tuple[str, ...] = ("yaml", "jsonschema")

# Packages the framework installs BY NAME (i.e. outside requirements.txt) and
# the version it installs. Every pin the framework states lives here or in
# requirements.txt — nowhere else.
PINS: dict[str, str] = {
    "import-linter": "2.5.2",
    "scancode-toolkit": "32.4.1",
    "code-review-graph": "2.3.6",
}

# tool_id -> the PINS key that provides it.
_GATE_EXTRA_PACKAGE: dict[str, str] = {
    "import-linter": "import-linter",
    "scancode": "scancode-toolkit",
    "code-review-graph": "code-review-graph",
}

# tool_id -> what a human runs. The framework never executes these: 老闆's
# Round 47 boundary is pip-into-.venv, and brew/curl/sudo change host state
# irreversibly.
EXTERNAL_BINARIES: dict[str, str] = {
    "gitleaks": (
        "brew install gitleaks  "
        "# or: go install github.com/gitleaks/gitleaks/v8@latest"
    ),
    "system-verification": (
        "make comes with the platform toolchain  "
        "# macOS: xcode-select --install | Debian: apt-get install make"
    ),
    "js-bench": (
        "node comes with the platform toolchain  "
        "# https://nodejs.org or: brew install node"
    ),
}

# The one thing to say about every npm-provided tool. Verbatim the string
# harness/ssi/scripts/verify_tools.py already used, so adopting the SSOT
# changes no user-facing text.
NPM_ADVICE = "npm i -D (templates/js_toolchain/package.json)"

# Provided transitively by a requirements.txt pin rather than named in it —
# `coverage` arrives with pytest-cov==7.1.0. Listed so advice for it is the
# requirements install and not a second, unpinned `pip install coverage`.
_PROVIDED_TRANSITIVELY: dict[str, str] = {"coverage": "pytest-cov"}

_PKG_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:[=<>!~]|$)")
_PIN_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9._-]*)\s*$")


@dataclass(frozen=True)
class PipStep:
    """One pip round. Separate rounds exist only when a combined resolve fails."""

    name: str
    packages: tuple[str, ...]  # empty when the step installs from a file
    from_requirements: bool
    why: str


PIP_STEPS: tuple[PipStep, ...] = (
    PipStep(
        name="requirements",
        packages=(),
        from_requirements=True,
        why="every scorer the framework pins for reproducible dimension scores",
    ),
    PipStep(
        name="gate-extras",
        packages=tuple(f"{p}=={PINS[p]}" for p in _GATE_EXTRA_PACKAGE.values()),
        from_requirements=False,
        why=(
            "a separate resolve: code-review-graph pulls fastmcp "
            "(exceptiongroup>=1.2.2), semgrep pins exceptiongroup~=1.2.0, and "
            "the combined file is ResolutionImpossible"
        ),
    ),
)


def harness_root() -> Path:
    """The harness checkout this module belongs to."""
    return Path(__file__).resolve().parent.parent.parent


def requirements_path() -> Path:
    return harness_root() / "requirements.txt"


def requirements_packages() -> set[str]:
    """Lowercased names requirements.txt pins. Read, never restated."""
    names: set[str] = set()
    try:
        text = requirements_path().read_text(encoding="utf-8")
    except OSError:
        return names
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _PKG_LINE.match(stripped)
        if match:
            names.add(match.group(1).lower())
    return names


def requirements_pins() -> dict[str, str]:
    """name -> version for every `name==version` line in requirements.txt."""
    pins: dict[str, str] = {}
    try:
        text = requirements_path().read_text(encoding="utf-8")
    except OSError:
        return pins
    for line in text.splitlines():
        match = _PIN_LINE.match(line.strip())
        if match:
            pins[match.group(1).lower()] = match.group(2)
    return pins


def pinned_spec(package: str) -> str:
    """`name==version` for *package*, from PINS or requirements.txt.

    Raises when neither pins it. An unpinned install is not a smaller version
    of a pinned one — it is the thing requirements.txt's header and the CI
    template's :610-624 incident note both exist to prevent — so a caller that
    needs a spec and cannot get one must fail loudly, not emit a bare name.
    """
    if package in PINS:
        return f"{package}=={PINS[package]}"
    version = requirements_pins().get(package.lower())
    if version:
        return f"{package}=={version}"
    raise KeyError(
        f"{package!r} has no pin in harness/toolchains/bootstrap.py::PINS or "
        f"requirements.txt; the framework must not install it unpinned"
    )


def step_for_tool(tool_id: str) -> "str | None":
    """Which pip round installs *tool_id*, or None when pip is not the answer.

    None covers three different situations on purpose — external binaries,
    npm-owned tools, and unregistered ids. Callers that need to tell them
    apart read EXTERNAL_BINARIES / the ToolSpec's install_step; callers that
    only need "can I pip this?" get one answer.
    """
    from harness.toolchains.registry import TOOL_SPECS

    spec = TOOL_SPECS.get(tool_id)
    if spec is None:
        return None
    return spec.install_step if spec.install_step in ("requirements", "gate-extras") else None


def pip_args(step: PipStep, requirements: "Path | str | None" = None) -> tuple[str, ...]:
    """The pip arguments for *step*, ready to follow `-m pip install`."""
    if step.from_requirements:
        return ("-r", str(requirements if requirements is not None else requirements_path()))
    return step.packages


def install_command(step: PipStep, requirements: str = REQUIREMENTS_RELPATH) -> str:
    """The human/CI rendering of *step* — the string the CI template must carry."""
    return "pip install " + " ".join(pip_args(step, requirements))


def install_advice(name: str) -> "str | None":
    """What to tell a human about installing *name*, or None when the framework
    has no statement to make about it.

    Answers for tool_ids the framework installs, for packages requirements.txt
    pins, and for the handful that arrive transitively. Returns None for
    npm-owned tools (the project's package.json owns those) and for anything
    unregistered — silence is correct when the framework is not the installer.
    """
    from harness.toolchains.registry import TOOL_SPECS

    spec = TOOL_SPECS.get(name)
    if spec is not None:
        if spec.install_step == "requirements":
            return install_command(PIP_STEPS[0])
        if spec.install_step == "gate-extras":
            package = _GATE_EXTRA_PACKAGE[name]
            return f"pip install {package}=={PINS[package]}"
        if spec.install_step == "external":
            return EXTERNAL_BINARIES.get(name)
        return None  # npm — a different owner

    lowered = name.lower()
    if lowered in requirements_packages() or lowered in _PROVIDED_TRANSITIVELY:
        return install_command(PIP_STEPS[0])
    return None
