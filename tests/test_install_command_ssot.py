"""One tool, one way to install it, one pin.

Round 47 站0. Seven places tell somebody how to install the framework's tools,
and they disagree. code-review-graph alone has three installers:

    harness/ssi/scripts/verify_tools.py    pipx install code-review-graph
    cli/project_cmds.py:386                pip install code-review-graph
    templates/harness_quality_gate.yml     pip install code-review-graph==2.3.6

mutmut has two, and the unpinned one is wrong on purpose-built grounds:
requirements.txt pins `mutmut==2.5.1` with the comment "mutmut 3.x is
incompatible with this repo's sys.path layout", while verify_tools.py says
`pip3 install mutmut`, which resolves to 3.x.

This is not a tidiness complaint. requirements.txt's own header states the
reason the pins exist: "Floating versions risk score drift across
environments: the same code must produce the same dimension scores regardless
of when/where it is installed." The CI template repeats it at :610-624 with the
incident — architecture scored 66.7 in CI against a committed baseline of
100.0, on the same commit, source unchanged, because code-review-graph
resolved to a different version. Every unpinned install statement is a path
back into that.

Why parity tests rather than rendering the CI template: Round 40 站1's
`ci_template_drift` rests on the template and a project's deployed copy being
the same bytes, with no third state to reason about. Rendering the template
would remove that property to fix a smaller problem.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

_REPO = Path(__file__).resolve().parent.parent
_CI_TEMPLATE = _REPO / "templates" / "harness_quality_gate.yml"

_PIP_LINE = re.compile(r"pip3?\s+install\s+(?P<args>[^\n|&;]+)")


def _pip_invocations(text: str) -> list[list[str]]:
    """Every `pip install …` COMMAND in *text*, as its argument token list.

    Comments are stripped first, in both the YAML and the shell sense (the
    template's `run:` blocks are shell). A note explaining why a pip line was
    removed is prose about an install, not an install — counting it would make
    the test fail on its own explanation.
    """
    stripped = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    return [m.group("args").split() for m in _PIP_LINE.finditer(stripped)]


def test_the_ci_template_pins_what_the_ssot_pins():
    """No pip line in the shipped CI workflow may name a package unpinned,
    or pin it to a version other than the framework's."""
    from harness.toolchains import bootstrap

    text = _CI_TEMPLATE.read_text(encoding="utf-8")
    offenders: list[str] = []
    for tokens in _pip_invocations(text):
        for token in tokens:
            if token.startswith("-"):
                continue
            name = re.split(r"[=<>!~\[]", token, maxsplit=1)[0]
            if name not in bootstrap.PINS:
                continue
            expected = f"{name}=={bootstrap.PINS[name]}"
            if token != expected:
                offenders.append(f"{token!r} (SSOT says {expected!r})")

    assert not offenders, (
        "templates/harness_quality_gate.yml installs framework-pinned packages "
        "at a different version, or unpinned:\n  " + "\n  ".join(offenders)
        + "\nThe pin lives in harness/toolchains/bootstrap.py::PINS."
    )


def test_the_ci_template_does_not_reinstall_what_requirements_already_pins():
    """`pip install pyyaml` next to `pip install -r requirements.txt` is a
    second, unpinned statement about a package the first one already pinned."""
    from harness.toolchains import bootstrap

    already_pinned = bootstrap.requirements_packages()
    assert "pyyaml" in already_pinned, "fixture assumption: requirements.txt pins pyyaml"

    text = _CI_TEMPLATE.read_text(encoding="utf-8")
    offenders: list[str] = []
    for tokens in _pip_invocations(text):
        if "-r" in tokens:
            continue  # this IS the requirements install
        for token in tokens:
            if token.startswith("-"):
                continue
            name = re.split(r"[=<>!~\[]", token, maxsplit=1)[0].lower()
            if name in already_pinned:
                offenders.append(token)

    assert not offenders, (
        "CI installs packages requirements.txt already pins, outside the "
        f"requirements install: {offenders}"
    )


def test_the_vendored_tool_table_agrees_with_the_ssot():
    """verify_tools.py is a parallel (check_cmd, install_cmd) registry that
    INTEGRATION.md:77 tells users to run. Its install column must not be a
    second opinion."""
    from harness.ssi.scripts.verify_tools import (
        CORE_BY_LANG,
        CORE_COMMON,
        EXTENDED_TOOLS,
    )
    from harness.toolchains import bootstrap

    tables: dict[str, tuple] = {**CORE_COMMON, **CORE_BY_LANG["python"], **EXTENDED_TOOLS}

    mismatches: list[str] = []
    for name, entry in tables.items():
        advice = bootstrap.install_advice(name)
        if advice is None:
            continue  # not a framework-installed tool (git, python3, pip3, JS tools)
        declared = entry[1] if len(entry) > 1 else ""
        if declared != advice:
            mismatches.append(f"{name}: table says {declared!r}, SSOT says {advice!r}")

    assert not mismatches, (
        "harness/ssi/scripts/verify_tools.py disagrees with "
        "harness/toolchains/bootstrap.py:\n  " + "\n  ".join(mismatches)
    )
