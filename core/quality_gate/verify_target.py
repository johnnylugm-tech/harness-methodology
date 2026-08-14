"""Whether the project's own verification target verifies anything (Round 52 站1).

`execute_verification_target` is the only dimension in Gate 4 that executes the
delivered system rather than reading its text or running its test suite. Its
command is `make verify-system` — see `harness/toolchains/registry.py`'s
`system-verification` ToolSpec, which this module reads rather than restating —
and the recipe behind that target is written by the project being judged.

Round 46 站5 moved the dimension from gate 2 alone into gates 2, 3 and 4, so
the target now runs at every exit. Nothing has ever read the recipe.
`tests/test_verify_target_regated.py`'s own docstring records the consequence —
taskq-advance's target "chains `test lint coverage`, none of the four steps its
own SPEC §NFR-12 requires" — and that observation produced no check. Round 24's
mother pattern (the field exists, nobody asked whether its content is true),
arriving at the one place where the framework depends on the judged party to
run its own product.

Measured 2026-08-14 with `make -n verify-system` on the six projects here:

    taskq                 clean            -m taskq --help + integration suite
    taskq-plus            clean            submit/run/status/graph/export/clear
    taskq-renew           --exit-zero      never names the delivered package
    taskq-advance         --exit-zero      never names the delivered package
    taskq-api             || true          -m taskq_api --help, behind that || true
    run-all-by-workflow   || true          -m taskq submit/list/clear

Two findings, deliberately narrow:

*Swallowed verdict* — a step whose failure cannot reach make. `|| true` and a
leading `-` are make/shell idioms for exactly that; `--exit-zero` is ruff's own.
Round 37 站4 removed `|| true` from the framework's CI template five rounds ago
and no check has ever read a project's Makefile, where the same idiom sits in
front of the only command that touches the product.

*Tautological target* — no step invokes the delivered entry point, so the
target re-runs dimensions the gate has already scored and calls that
end-to-end verification. The condition is that single one, not "every step is
a tool some gate scores": station 0's premise P5 measured the second and it is
not decidable from the registry. `ToolSpec.cmd` heads are `pytest` / `ruff` /
`pyright`, every real recipe spells them `.venv/bin/python -m pytest`, and
`coverage` and `alembic` are not registered at all. One non-fuzzy condition
reproduces the same two-project blast radius without a classifier that guesses.

Two sources, because neither can answer the other's question. `make -n` is
authoritative for what runs: it resolves variables, prerequisites and the
transitive closure, and it is what the gate itself will execute. It cannot
report a leading `-`, which make consumes before printing. That one is read
from the Makefile text, over the closure of `verify-system` computed here.

When either source is unreadable the answer is `unmeasured`, never clean:
`$(shell …)` runs while make parses, so `make -n` on such a Makefile is a
partial execution and not a dry run; `include` and a prerequisite built from a
variable both put recipe lines outside the text closure. None of the six
projects here has any of the three, which is an observation about this corpus
and not a guarantee about the next one.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

__all__ = [
    "STATUS_EXPANDED",
    "STATUS_MISSING",
    "STATUS_UNMEASURED",
    "blocking_reason",
    "expand_recipe",
    "record_verify_target_status",
    "swallowed_verdicts",
    "verify_target_findings",
    "verify_target_name",
]

STATUS_EXPANDED = "expanded"
STATUS_MISSING = "missing"
STATUS_UNMEASURED = "unmeasured"

# Idioms visible in the expanded command. Each is a way for a step's verdict
# not to reach make; the leading `-` is handled separately because make strips
# it before printing.
_EXPANDED_IDIOMS: tuple[str, ...] = ("|| true", "--exit-zero")

# `TARGET: prereqs`, excluding `VAR := value` and `VAR = value`.
_RULE_RE = re.compile(r"^([^\t#=][^:=]*?):(?!=)\s*(.*?)\s*$")
# A recipe line's ignore-errors prefix, in any of make's orders (`-@`, `@-`).
_DASH_PREFIX_RE = re.compile(r"^\t[@+]*-")

_MAKE_TIMEOUT = 60


def verify_target_name() -> str:
    """The target the gate runs, read from the ToolSpec that runs it."""
    from harness.toolchains.registry import get_tool_spec

    spec = get_tool_spec("system-verification")
    if spec is None or not spec.cmd or len(spec.cmd) < 2:
        raise RuntimeError("system-verification ToolSpec has no `make <target>` cmd")
    return spec.cmd[1]


def _unreadable_reason(text: str) -> "str | None":
    """Why this Makefile cannot be reasoned about, or None."""
    if "$(shell" in text or "${shell" in text:
        return ("Makefile uses $(shell …), which runs while make parses — "
                "`make -n` on it is a partial execution, not a dry run")
    for line in text.splitlines():
        if line.startswith(("include ", "-include ", "sinclude ")):
            return f"Makefile includes another file ({line.strip()!r})"
    return None


def _rules(text: str) -> dict:
    """target -> {"prereqs": [...], "recipe": [raw lines]} for literal rules."""
    rules: dict[str, dict] = {}
    current: "str | None" = None
    for line in text.splitlines():
        if line.startswith("\t"):
            if current is not None:
                rules[current]["recipe"].append(line)
            continue
        match = _RULE_RE.match(line)
        if not match:
            if line.strip():
                current = None
            continue
        # `a b: c` declares two targets sharing one recipe.
        targets = match.group(1).split()
        prereqs = match.group(2).split()
        for target in targets:
            rules.setdefault(target, {"prereqs": [], "recipe": []})
            rules[target]["prereqs"].extend(prereqs)
        current = targets[-1] if targets else None
    return rules


def _closure(rules: dict, start: str) -> "tuple[list[str], list[str]]":
    """Targets reachable from *start*, and the prerequisites we could not resolve."""
    seen: list[str] = []
    unresolved: list[str] = []
    stack = [start]
    while stack:
        target = stack.pop()
        if target in seen or target not in rules:
            continue
        seen.append(target)
        for prereq in rules[target]["prereqs"]:
            if "$(" in prereq or "${" in prereq:
                unresolved.append(prereq)
                continue
            stack.append(prereq)
    return seen, unresolved


def _delivered_packages(project: Path) -> list[str]:
    """Top-level importable packages under the project's active src directory."""
    from core.utils.project_layout import ProjectLayout

    src = ProjectLayout(str(project)).active_src_dir
    if not src.is_dir():
        return []
    return sorted(
        d.name for d in src.iterdir()
        if d.is_dir() and (d / "__init__.py").is_file()
    )


def _invokes_package(line: str, packages: list[str]) -> bool:
    """Does this expanded command run one of the delivered packages?

    `-m pkg` is how all six projects spell it; an executable whose basename is
    the package covers a console script installed under the same name.
    """
    tokens = line.split()
    for i, token in enumerate(tokens):
        if token == "-m" and i + 1 < len(tokens):
            module = tokens[i + 1]
            if any(module == p or module.startswith(p + ".") for p in packages):
                return True
    for token in tokens:
        if "=" in token and not token.startswith("-"):
            continue  # leading `FOO=bar` env assignment
        if Path(token).name in packages:
            return True
        break
    return False


def expand_recipe(project: "str | Path") -> dict:
    """`make -n <target>` for the project, or why we would not run it.

    Returns ``{"status", "reason", "lines", "closure"}``. ``lines`` is the
    fully expanded command list and ``closure`` the raw recipe lines of the
    targets `verify-system` reaches; both are None unless status is
    ``expanded``.
    """
    project = Path(project)
    blank: dict = {"lines": None, "closure": None}
    target = verify_target_name()

    makefile = project / "Makefile"
    if not makefile.is_file():
        return {"status": STATUS_MISSING,
                "reason": f"no Makefile in {project}", **blank}
    if shutil.which("make") is None:
        return {"status": STATUS_UNMEASURED,
                "reason": "make is not installed; the recipe cannot be expanded",
                **blank}

    try:
        text = makefile.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"status": STATUS_UNMEASURED,
                "reason": f"Makefile unreadable: {exc}", **blank}

    unreadable = _unreadable_reason(text)
    if unreadable:
        return {"status": STATUS_UNMEASURED, "reason": unreadable, **blank}

    rules = _rules(text)
    if target not in rules:
        return {"status": STATUS_MISSING,
                "reason": f"Makefile declares no `{target}` target", **blank}
    reached, unresolved = _closure(rules, target)
    if unresolved:
        return {"status": STATUS_UNMEASURED,
                "reason": (f"`{target}` has prerequisites built from variables "
                           f"({', '.join(sorted(set(unresolved)))}); the recipe "
                           f"closure is incomplete"),
                **blank}

    try:
        proc = subprocess.run(
            ["make", "-n", target], cwd=str(project), capture_output=True,
            text=True, timeout=_MAKE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": STATUS_UNMEASURED,
                "reason": f"`make -n {target}` did not run: {exc}", **blank}
    if proc.returncode != 0:
        return {"status": STATUS_UNMEASURED,
                "reason": (f"`make -n {target}` exited {proc.returncode}: "
                           f"{proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else ''}"),
                **blank}

    closure_lines: list[str] = []
    for name in reached:
        closure_lines.extend(rules[name]["recipe"])
    return {
        "status": STATUS_EXPANDED,
        "reason": "",
        "lines": [ln for ln in proc.stdout.splitlines() if ln.strip()],
        "closure": closure_lines,
    }


def swallowed_verdicts(project: "str | Path") -> "list[dict] | None":
    """Steps whose failure cannot reach make, or None if we could not expand.

    None rather than [] on failure: a scan whose input it could not read has
    abstained, not passed (Round 46 站1). Each row carries the line, so the
    operator is told which one to edit (Round 48).
    """
    recipe = expand_recipe(project)
    if recipe["status"] != STATUS_EXPANDED:
        return None

    rows: list[dict] = []
    for line in recipe["lines"]:
        for idiom in _EXPANDED_IDIOMS:
            if idiom in line:
                rows.append({"line": line.strip(), "idiom": idiom})
    for raw in recipe["closure"]:
        if _DASH_PREFIX_RE.match(raw):
            rows.append({"line": raw.strip(), "idiom": "leading-dash"})
    return rows


def verify_target_findings(project: "str | Path") -> dict:
    """Everything station 1 has to say about the project's verification target.

    ``{"status", "reason", "swallowed", "entrypoint_lines", "tautological"}``.
    The last three are None unless the recipe expanded — an unexpandable
    Makefile has not been found clean, and a caller that reads `tautological`
    as a boolean will get a TypeError rather than a wrong answer.
    """
    project = Path(project)
    recipe = expand_recipe(project)
    if recipe["status"] != STATUS_EXPANDED:
        return {"status": recipe["status"], "reason": recipe["reason"],
                "swallowed": None, "entrypoint_lines": None,
                "tautological": None}

    packages = _delivered_packages(project)
    entrypoint_lines = [
        line.strip() for line in recipe["lines"]
        if _invokes_package(line, packages)
    ]
    swallowed = swallowed_verdicts(project) or []
    return {
        "status": STATUS_EXPANDED,
        "reason": "",
        "swallowed": swallowed,
        "swallowed_product": [r for r in swallowed
                              if _invokes_package(r["line"], packages)],
        "entrypoint_lines": entrypoint_lines,
        "tautological": not entrypoint_lines,
    }


def blocking_reason(project: "str | Path") -> "str | None":
    """The reason this verification target may not be accepted, or None.

    Two conditions, and deliberately not "any swallowed verdict":

    * the target never invokes the delivered entry point, so whatever it
      verifies, it is not the system;
    * the step that DOES invoke it cannot fail, so its verdict is not in the
      target's exit code.

    Station 0's premise P6 measured the difference on the six projects here.
    Blocking on every swallowed verdict would also stop run-all-by-workflow,
    whose `coverage combine … || true` is a documented no-op when there is one
    data file and whose result the next line re-reads anyway. That is a true
    finding and a false alarm; it goes to the ledger. taskq-api's
    `-m taskq_api --help >/dev/null 2>&1 || true` is the same idiom on the one
    line that touches the product, which is the defect itself.

    Returns None when the recipe could not be expanded. An unreadable Makefile
    is a reason to report, not to fail a gate on (Round 35 站2); the ledger row
    is written by `record_verify_target_status`. It also returns None for a
    missing target: `make verify-system` then exits non-zero, the
    `execute_verification_target` dimension scores 0 against a threshold of
    100, and that block already exists — a second enforcer for one fact is
    Round 38's defect.
    """
    findings = verify_target_findings(project)
    if findings["status"] != STATUS_EXPANDED:
        return None
    if findings["tautological"]:
        return (f"`make {verify_target_name()}` never invokes the delivered "
                f"entry point — every step re-runs a tool the gate has already "
                f"scored, so the dimension that exists to execute the system "
                f"executed nothing of it")
    product_swallows = findings["swallowed_product"]
    if product_swallows:
        lines = "; ".join(f"{r['idiom']} in `{r['line']}`" for r in product_swallows)
        return (f"`make {verify_target_name()}` invokes the product behind an "
                f"idiom that cannot fail: {lines}")
    return None


def record_verify_target_status(project: "str | Path") -> dict:
    """Write what the target does to the ledger and return the findings.

    Never raises: a report about the verification target is a worse reason to
    stop a gate than the thing it was going to report.
    """
    from core.degradation_ledger import record_degradation

    project = Path(project)
    try:
        findings = verify_target_findings(project)
        if findings["status"] != STATUS_EXPANDED:
            record_degradation(
                project, "gate:verify-target",
                f"`make {verify_target_name()}` recipe not examined",
                findings["reason"],
                owner="project" if findings["status"] == STATUS_MISSING else "harness",
            )
            return findings
        benign = [r for r in findings["swallowed"]
                  if r not in findings["swallowed_product"]]
        if benign:
            record_degradation(
                project, "gate:verify-target",
                f"{len(benign)} step(s) in the verification target cannot fail",
                "their verdict is not in the target's exit code; the gate does "
                "not block on these because they do not run the product",
                data={"swallowed": benign},
                owner="project",
            )
        return findings
    except Exception as exc:  # pragma: no cover — reporting must not stop a gate
        record_degradation(
            project, "gate:verify-target",
            "verification-target inspection failed",
            f"{type(exc).__name__}: {exc}",
            owner="harness",
        )
        return {"status": STATUS_UNMEASURED, "reason": str(exc),
                "swallowed": None, "swallowed_product": None,
                "entrypoint_lines": None, "tautological": None}
