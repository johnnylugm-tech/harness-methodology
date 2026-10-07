"""Round 115 站8 — an NFR's latency target is a typed fact the framework can check.

The `performance` dimension is mapped onto a project's latency NFR (the
quality manifest pins NFR-01 -> performance) and scored by a formula the
framework wrote: per benchmark, `mean > 1000 ms -> -50`, `> 3000 ms -> -25`.
It never reads the NFR's own target. taskq-open's SPEC says p95 < 30 ms and
p95 < 80 ms; its SRS says, in its own words, that "the performance section of
evaluate_dimension.md scores only mean latency ... and does not check p95 <
30/80 ms"; Gate 4 recorded performance 100. A number the framework chose was
reported as the verdict on a requirement the project stated (Round 105).

The minimal schema (HM-06, 老闆裁定 to design it this round): an SRS NFR may
carry `targets: [{ac, statistic, op, value, unit, spec_ref}]`, latency only.
`spec_ref` is `<file>:<line>`, and that line of the canonical text must carry
the value with its unit — the number is transcribed, never invented, and a
30 -> 80 drift between SPEC and SRS (which token overlap cannot see) is
caught at the Phase 1 exit. Per target, the gate records its basis:

  * tool-measured — a pytest-benchmark entry named after the AC's declared
    test, with raw rounds (`--benchmark-save-data`), compared by the framework;
  * test-asserted — the declared test ran and passed (the project's own
    assertion; whether its budget equals SPEC's is the P3 criteria review's
    question, which reads both);
  * unmeasured — neither; stated, not scored as zero (Round 35), not as a pass.

A measured violation fails the dimension. Projects that declare no target
keep the generic formula, recorded as `basis: generic`.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
from pathlib import Path
from typing import Any

__all__ = ["STATISTICS", "declared_targets", "judge_performance_by_latency_targets",
           "target_findings", "target_verdicts"]

STATISTICS = ("mean", "median", "max", "p90", "p95", "p99")
_OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b}
_UNIT_MS = {"ms": 1.0, "s": 1000.0}
_FIELDS = ("ac", "statistic", "op", "value", "unit", "spec_ref")


def declared_targets(project: "str | Path") -> list[dict]:
    """Every `targets` entry under the SRS machine block's NFRs, with its NFR id."""
    from core.utils.project_layout import ProjectLayout
    from scripts.plangen.artifact_parsers import srs_machine_block

    try:
        content = ProjectLayout(Path(project)).srs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    block = srs_machine_block(content)
    out: list[dict] = []
    for nfr in (block or {}).get("non_functional_requirements", []) or []:
        if isinstance(nfr, dict):
            for t in nfr.get("targets") or []:
                out.append({"nfr": str(nfr.get("id") or "NFR-??"),
                            **(t if isinstance(t, dict) else {"_invalid": t})})
    return out


def _literal(value: float, unit: str) -> "re.Pattern[str]":
    return re.compile(r"(?<![\d.])" + re.escape(f"{value:g}") + r"(?:\.0+)?\s*" + re.escape(unit) + r"\b")


def target_findings(project: "str | Path") -> list[str]:
    """Why a declared target is not a checkable transcription of the canonical text."""
    from core.quality_gate.content_ref import ref_file
    from core.utils.project_layout import ProjectLayout

    from scripts.plangen.artifact_parsers import srs_machine_block_span

    findings: list[str] = []
    try:
        srs_text = ProjectLayout(Path(project)).srs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        srs_text = ""
    span = srs_machine_block_span(srs_text)
    if span is not None:  # the AC must be stated in the prose, not only named by the block
        srs_text = srs_text[:span[0]] + srs_text[span[1]:]
    for t in declared_targets(project):
        where = f"{t['nfr']} target {t.get('ac', '?')}"
        missing = [f for f in _FIELDS if f not in t]
        if missing:
            findings.append(f"{where}: missing {missing} — a target is "
                            f"{{ac, statistic, op, value, unit, spec_ref}}")
            continue
        if t["statistic"] not in STATISTICS:
            findings.append(f"{where}: statistic {t['statistic']!r} is not one of {', '.join(STATISTICS)}")
        if t["op"] not in _OPS:
            findings.append(f"{where}: op {t['op']!r} is not one of {', '.join(_OPS)}")
        if t["unit"] not in _UNIT_MS:
            findings.append(f"{where}: unit {t['unit']!r} is not one of {', '.join(_UNIT_MS)}")
        if not isinstance(t["value"], (int, float)) or isinstance(t["value"], bool):
            findings.append(f"{where}: value {t['value']!r} is not a number")
            continue
        if not re.search(r"(?<![\w.-])" + re.escape(str(t["ac"])) + r"(?![\w.-])", srs_text):
            findings.append(f"{where}: {t['ac']} is not an acceptance criterion this SRS states")
        ref = str(t["spec_ref"])
        m = re.match(r"^(?P<path>.+):(?P<line>\d+)$", ref)
        path = ref_file(project, ref)
        if not m or path is None:
            findings.append(f"{where}: spec_ref {ref!r} must name a project file and line, `SPEC.md:<N>`")
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        n = int(m["line"])
        if t["unit"] in _UNIT_MS and not (1 <= n <= len(lines)
                                          and _literal(float(t["value"]), t["unit"]).search(lines[n - 1])):
            findings.append(f"{where}: {ref} does not state {t['value']:g}{t['unit']} — a target is "
                            f"transcribed from the canonical text, never chosen")
    return findings


def _ac_tests(project: Path) -> dict[str, list[str]]:
    """AC -> declared test functions, from TEST_INVENTORY.yaml's rows."""
    import yaml

    from core.utils.project_layout import ProjectLayout

    try:
        doc = yaml.safe_load(ProjectLayout(project).test_inventory_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    out: dict[str, list[str]] = {}

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("ac"), str) and isinstance(node.get("test_function"), str):
                out.setdefault(node["ac"], []).append(node["test_function"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(doc)
    return out


def _statistic_ms(rounds_s: list, statistic: str) -> float:
    """The statistic over raw rounds (seconds), in ms. Percentiles: nearest rank."""
    xs = sorted(float(x) for x in rounds_s)
    if statistic == "mean":
        v = sum(xs) / len(xs)
    elif statistic == "median":
        mid = len(xs) // 2
        v = xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2
    elif statistic == "max":
        v = xs[-1]
    else:
        v = xs[max(0, math.ceil(int(statistic[1:]) / 100 * len(xs)) - 1)]
    return v * 1000.0


def target_verdicts(project: "str | Path", test_outcomes: "dict | None",
                    benchmark_report: "dict | None") -> list[dict]:
    """One verdict per well-formed target: basis, passed (None = unmeasured)."""
    from core.quality_gate.spec_coverage import _outcome_key_names, delivery_outcome

    project = Path(project)
    tests_of = _ac_tests(project)
    benches = {b.get("name"): b for b in (benchmark_report or {}).get("benchmarks") or []
               if isinstance(b, dict)}
    collected = {_outcome_key_names(k) for k in (test_outcomes or {})}
    out = []
    for t in declared_targets(project):
        if any(f not in t for f in _FIELDS) or t["unit"] not in _UNIT_MS or t["op"] not in _OPS \
                or t["statistic"] not in STATISTICS:
            continue
        tests = tests_of.get(str(t["ac"]), [])
        verdict = {k: t[k] for k in ("nfr", "ac", "statistic", "op", "value", "unit")} | {"tests": tests}
        budget_ms = float(t["value"]) * _UNIT_MS[t["unit"]]
        bench = next((benches[n] for n in tests if (benches.get(n) or {}).get("stats", {}).get("data")), None)
        if bench is not None:
            measured = _statistic_ms(bench["stats"]["data"], t["statistic"])
            verdict |= {"basis": "tool-measured", "measured_ms": round(measured, 3),
                        "passed": _OPS[t["op"]](measured, budget_ms)}
        elif test_outcomes:
            outcomes = [delivery_outcome(n, collected, test_outcomes) for n in tests]
            if "delivered" in outcomes:
                verdict |= {"basis": "test-asserted", "passed": True}
            elif any(o in ("failed", "error", "skipped") for o in outcomes):
                verdict |= {"basis": "test-asserted", "passed": False, "outcomes": outcomes}
            else:
                verdict |= {"basis": "unmeasured", "passed": None,
                            "why": "no declared test for this AC ran" if tests else
                                   "TEST_INVENTORY declares no test for this AC"}
        else:
            verdict |= {"basis": "unmeasured", "passed": None, "why": "suite outcomes unavailable"}
        out.append(verdict)
    return out


def judge_performance_by_latency_targets(
    dims: list, project_root: str, raw: dict, test_outcomes: Any = None,
) -> "tuple[list, bool]":
    """Judge finalize-gate's `performance` dimension against the declared targets.

    Each target's verdict is recorded on the breakdown entry
    (`latency_targets`, which finalize persists); a measured violation sets
    the score to 0. Unmeasured targets are recorded and do not move the
    score. No targets: the entry records `latency_basis: generic`.
    `test_outcomes` defaults to the suite this gate already ran.
    """
    entry = (raw.get("breakdown") or {}).get("performance") if isinstance(raw, dict) else None
    if not isinstance(entry, dict) or not any(d.name == "performance" for d in dims):
        return dims, False
    from core.quality_gate.spec_coverage import LIVE_OUTCOMES, _live_test_outcomes

    if not declared_targets(project_root):
        entry["latency_basis"] = "generic"
        return dims, False
    try:
        report = json.loads((Path(project_root) / ".sessi-work" / "benchmark_report.json")
                            .read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        report = None
    outcomes = (_live_test_outcomes(Path(project_root))
                if test_outcomes in (None, LIVE_OUTCOMES) else test_outcomes)
    verdicts = target_verdicts(project_root, outcomes, report)
    entry["latency_basis"] = "declared targets"
    entry["latency_targets"] = verdicts
    failed = [v for v in verdicts if v.get("passed") is False]
    if not failed:
        return dims, False
    issues = [{"severity": "high", "message": (
        f"{v['ac']} ({v['nfr']}): {v['statistic']} {v['op']} {v['value']:g}{v['unit']} not met — "
        f"{v['basis']}" + (f", measured {v['measured_ms']} ms" if "measured_ms" in v else ""))}
        for v in failed]
    print(f"[harness] performance: {len(failed)} declared latency target(s) not met — score 0")
    return [dataclasses.replace(d, score=0.0, issues=list(d.issues or []) + issues)
            if d.name == "performance" else d for d in dims], True
