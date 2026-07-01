"""bug_hunt_verifier.py — framework-owned verdict for the adversarial_review dim.

v2.9 C1: reads .methodology/bug_hunt_report.json (contract:
schemas/bug_hunt_report.schema.json, produced per
harness/ssi/prompts/hunt_bugs.md) and decides whether Gate 3 may pass.

Blocking rules (老闆決策: Critical + High both block):
  * report missing/unparseable/structurally invalid     → block
  * confirmed critical/high finding with status=open    → block
  * status=resolved without fix_commit or repro_test    → block
    (repro_test must EXIST under the project — a resolution claim needs
    verifiable evidence; anti-fabrication, same spirit as score.py R2)
  * status=refuted without refute_evidence              → block

Non-blocking: git_sha drift since the scan (warning only — the report's
content, not its age, is the gate evidence), medium/low findings,
unconfirmed findings (the adversarial verify already rejected them).

Mirrors claims_verifier.py style: pure reader, never mutates project state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

REPORT_RELPATH = Path(".methodology") / "bug_hunt_report.json"

_BLOCKING_SEVERITIES = frozenset({"critical", "high"})
_VALID_STATUSES = frozenset({"open", "resolved", "refuted"})
_REQUIRED_TOP_FIELDS = ("generated_at", "git_sha", "lenses", "findings")
_REQUIRED_FINDING_FIELDS = (
    "id", "module", "lens", "severity", "title", "file",
    "line_start", "reasoning", "confidence", "confirmed", "resolution",
)


@dataclass
class BugHuntVerdict:
    """Gate verdict for adversarial_review (score is 100 pass / 0 block)."""

    ok: bool
    score: float
    report_found: bool = False
    stale: bool = False
    open_blocking: int = 0
    reasons: list[str] = field(default_factory=list)


def _current_git_sha(project_root: Path) -> Optional[str]:
    import subprocess
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project_root,
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() or None
    except (subprocess.SubprocessError, OSError):
        return None


def verify_bug_hunt_report(project_root: str) -> BugHuntVerdict:
    """Validate the hunt report and return the adversarial_review verdict."""
    root = Path(project_root)
    report_path = root / REPORT_RELPATH

    if not report_path.exists():
        return BugHuntVerdict(
            ok=False, score=0.0, report_found=False,
            reasons=[
                f"{REPORT_RELPATH} not found — run the Gate-3 bug hunt "
                f"(harness/ssi/prompts/hunt_bugs.md; targeting: "
                f"python harness_cli.py bug-hunt-targets --project .)"
            ],
        )

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return BugHuntVerdict(
            ok=False, score=0.0, report_found=True,
            reasons=[f"bug_hunt_report.json unreadable: {exc}"],
        )

    reasons: list[str] = []
    for f_name in _REQUIRED_TOP_FIELDS:
        if f_name not in report:
            reasons.append(f"report missing required field '{f_name}'")
    findings = report.get("findings")
    if not isinstance(findings, list):
        reasons.append("'findings' must be a list")
        findings = []

    open_blocking = 0
    for i, finding in enumerate(findings):
        if not isinstance(finding, dict):
            reasons.append(f"finding[{i}] is not an object")
            continue
        fid = str(finding.get("id", f"finding[{i}]"))
        missing = [k for k in _REQUIRED_FINDING_FIELDS if k not in finding]
        if missing:
            reasons.append(f"{fid}: missing field(s) {missing}")
            continue
        if not finding.get("confirmed"):
            continue  # adversarial verify rejected it — never blocks

        severity = str(finding.get("severity", "")).lower()
        _resolution = finding.get("resolution")
        resolution = _resolution if isinstance(_resolution, dict) else {}
        status = str(resolution.get("status", "")).lower()
        if status not in _VALID_STATUSES:
            reasons.append(f"{fid}: invalid resolution.status {status!r}")
            continue

        if status == "open":
            if severity in _BLOCKING_SEVERITIES:
                open_blocking += 1
                reasons.append(f"{fid}: confirmed {severity} is OPEN — fix or refute")
            continue

        if status == "resolved":
            repro = resolution.get("repro_test")
            fix_commit = resolution.get("fix_commit")
            if not repro and not fix_commit:
                reasons.append(
                    f"{fid}: resolved without evidence — needs fix_commit or "
                    f"repro_test (anti-fabrication)"
                )
            elif repro and isinstance(repro, str) and not (root / repro).is_file():
                reasons.append(
                    f"{fid}: repro_test '{repro}' does not exist in the project"
                )
            continue

        # refuted
        if not str(resolution.get("refute_evidence", "")).strip():
            reasons.append(
                f"{fid}: refuted without refute_evidence — the refuter must "
                f"cite a counterexample or documented exception"
            )

    stale = False
    head = _current_git_sha(root)
    report_sha = str(report.get("git_sha", ""))
    if head and report_sha and head != report_sha:
        stale = True  # warning only — content, not age, is the evidence

    ok = not reasons
    return BugHuntVerdict(
        ok=ok, score=100.0 if ok else 0.0, report_found=True,
        stale=stale, open_blocking=open_blocking, reasons=reasons,
    )
