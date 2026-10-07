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
  * Round 115 站2, confirmed critical/high only: resolved needs BOTH, the
    fix_commit a real commit on HEAD's history whose diff changes the
    finding's file and the repro, the repro under the framework's test dir;
    refuted needs an upheld adjudication bound to the refutation's text
    (`_resolution_defects` / `_adjudication_defects`)

Non-blocking: git_sha drift since the scan (warning only — the report's
content, not its age, is the gate evidence), medium/low findings,
unconfirmed findings (the adversarial verify already rejected them).

Mirrors claims_verifier.py style: pure reader, never mutates project state.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
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
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project_root,
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() or None
    except (subprocess.SubprocessError, OSError):
        return None


#: A verdict that names no line is an opinion (huntCited in spec_phase4, the
#: same test the workflow applies to a verifier's confirmation).
_CITED = re.compile(r"(:\d+|line\s*\d+|L\d+)", re.IGNORECASE)


def refutation_sha(text: str) -> str:
    """What an adjudication is bound to: the refutation exactly as it reads."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git(root: Path, *args: str) -> "subprocess.CompletedProcess[str]":
    from core.utils.subprocess_group import run_isolated

    return run_isolated(["git", "-C", str(root), *args], timeout=30)


def _resolution_defects(root: Path, finding: dict, resolution: dict, *,
                        blocking: bool) -> list[str]:
    """Why a `resolved` claim is not evidence, measured against the repository.

    Round 115 站2. The resolver is told to write a RED repro, apply the fix,
    go GREEN and make ONE `fix(<module>)` commit, then record both. 28 of 28
    resolved blocking findings in the corpus did exactly that, so a blocking
    finding is held to it: a real commit on HEAD's history whose own diff
    changes the finding's file and the repro, the repro lying under the test
    directory the framework's suite runs (anything else the gate never
    executes). A non-blocking finding keeps the either/or rule, minus the
    fabrications: a fix_commit must still be a commit on HEAD's history.
    """
    fix_commit = resolution.get("fix_commit")
    repro = resolution.get("repro_test")
    if repro is not None and not isinstance(repro, str):
        return [f"repro_test must be a string path, got {type(repro).__name__}"]
    if blocking and not (fix_commit and repro):
        return ["a confirmed critical/high is resolved by a fix_commit and repro_test "
                "together — the repro that goes RED on the bug, committed with the fix"]
    if not fix_commit and not repro:
        return ["resolved without evidence — needs fix_commit or repro_test (anti-fabrication)"]

    defects: list[str] = []
    changed: "set[str] | None" = None
    if fix_commit:
        sha = str(fix_commit)
        if _git(root, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
            return [f"fix_commit {sha!r} is not a commit in this repository"]
        if _git(root, "merge-base", "--is-ancestor", sha, "HEAD").returncode != 0:
            return [f"fix_commit {sha} is not on HEAD's history — the delivered tree does not contain it"]
        changed = set(_git(root, "show", "--format=", "--name-only", sha).stdout.split())
        if blocking and str(finding.get("file", "")) not in changed:
            defects.append(f"fix_commit {sha[:12]} does not change {finding.get('file')}, the file the finding is in")
    if repro:
        from core.utils.project_layout import ProjectLayout

        layout = ProjectLayout(root)
        test_dir = layout.active_test_dir.resolve()
        path = (root / repro).resolve()
        if not path.is_relative_to(test_dir):
            defects.append(
                f"repro_test {repro!r} is not under {layout.get_relative_str(layout.active_test_dir)}, "
                f"the directory the framework's suite runs — the gate never executes it")
        elif not path.is_file():
            defects.append(f"repro_test '{repro}' does not exist in the project")
        elif blocking and changed is not None and repro not in changed:
            defects.append(f"fix_commit {str(fix_commit)[:12]} does not change {repro} — "
                           f"the repro is committed with the fix it proves")
    return defects


def _adjudication_defects(resolution: dict) -> list[str]:
    """Why a resolver's refutation of a confirmed critical/high does not stand.

    Round 115 站2 (老闆裁定: the resolver may refute, an independent
    adjudication decides). Two verifiers confirmed the finding; the party
    that wrote the code may not overrule them in prose. `adjudicate-bug-hunt`
    records what two fresh verifiers concluded about the refutation, bound to
    its text — a refutation rewritten afterwards is not the one they judged.
    """
    adj = resolution.get("adjudication")
    if not isinstance(adj, dict):
        return ["refuting a confirmed critical/high needs an adjudication — two independent "
                "verifiers judge the refutation (`adjudicate-bug-hunt`); none is recorded"]
    if adj.get("refutation_sha") != refutation_sha(str(resolution.get("refute_evidence", ""))):
        return ["refute_evidence changed after it was adjudicated — the verdict is about other words"]
    if adj.get("verdict") != "upheld":
        return [f"the adjudication {adj.get('verdict') or '(none)'} the refutation — "
                f"the finding stands; resolve it"]
    evidence = adj.get("evidence")
    if not (isinstance(evidence, list) and len(evidence) == 2
            and all(_CITED.search(str(e)) for e in evidence)):
        return ["an upheld adjudication must carry both verifiers' evidence, each citing a line"]
    return []


def _adjudicated_as_written(resolution: dict) -> bool:
    """An adjudication exists and is about the refutation as it now reads."""
    adj = resolution.get("adjudication")
    return isinstance(adj, dict) and adj.get("refutation_sha") == refutation_sha(
        str(resolution.get("refute_evidence", "")))


def pending_findings(project_root: "str | Path") -> list[dict]:
    """Confirmed critical/high findings Gate 3 would still block on, for the resolver.

    Open ones, and refuted ones whose refutation has no standing adjudication
    (none, rejected, or about different words). Resolved ones with weak
    evidence are not listed: they are the resolver's own claim to repair, and
    `verify_bug_hunt_report` names them.
    """
    path = Path(project_root) / REPORT_RELPATH
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for f in report.get("findings") or []:
        if not (isinstance(f, dict) and f.get("confirmed")
                and str(f.get("severity", "")).lower() in _BLOCKING_SEVERITIES):
            continue
        res = f.get("resolution")
        res = res if isinstance(res, dict) else {}
        status = str(res.get("status", "")).lower()
        if status == "open" or (status == "refuted" and _adjudication_defects(res)):
            out.append({k: f.get(k) for k in ("id", "severity", "title", "file", "line_start",
                                              "reasoning", "verify_evidence")}
                       | {"status": status,
                          "refute_evidence": res.get("refute_evidence", ""),
                          "adjudication": res.get("adjudication"),
                          "needs_adjudication": status == "refuted" and not _adjudicated_as_written(res)})
    return out


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

        blocking = severity in _BLOCKING_SEVERITIES
        if status == "resolved":
            reasons.extend(f"{fid}: {r}" for r in _resolution_defects(
                root, finding, resolution, blocking=blocking))
            continue

        # refuted
        if not str(resolution.get("refute_evidence", "")).strip():
            reasons.append(
                f"{fid}: refuted without refute_evidence — the refuter must "
                f"cite a counterexample or documented exception"
            )
        elif blocking:
            reasons.extend(f"{fid}: {r}" for r in _adjudication_defects(resolution))

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
