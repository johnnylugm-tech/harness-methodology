"""check commands: where to look next.

Split out of cli/check_cmds.py in R49-B. Two commands that produce a list of
places worth attention rather than a verdict — bug-hunt targets (Round 10's
threat-model-driven selection) and the gap analysis.

Neither blocks anything. Round 25 站3 took gap analysis out of preflight_all
for exactly that reason, and keeping the two together keeps the distinction
visible: a command in this file advises, and a command in gates.py decides.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from core.state_io import load_quality_manifest
from core.utils.project_layout import ProjectLayout

def cmd_bug_hunt_targets(args: argparse.Namespace) -> int:
    """v2.9 C4: aggregate hunt-targeting signals into bug_hunt_targets.json.

    Sources (each best-effort, provenance recorded):
      1. declared high-risk modules — quality_manifest.json "high_risk_modules"
      2. CRG hub risk — .sessi-work/crg_metrics.json hub_risk_map (critical/high)
      3. mutation survivors — .methodology/mutation_survivors.json (C5 artifact)
      4. integration_coverage — latest gate result breakdown
      5. threat_model — SAD.md §6 STRIDE-lite threats (Round 10): each
         threat's owner_module is a forced attack-vector seed, resolved to
         an on-disk path the same way preflight_sab_check resolves SAB
         module entries
      6. source inventory — remaining src files become standard (1-lens) targets

    Output feeds harness/ssi/prompts/hunt_bugs.md: high_risk modules get the
    3-lens deep scan, standard get 1 general lens; survivor entries tell
    hunters which functions have behavior no test asserts; threat_model
    entries tell hunters which declared attack vector to specifically probe.
    """
    from datetime import datetime, timezone

    from core.utils.lang_patterns import iter_source_files, project_language

    project = Path(args.project).resolve()
    language = project_language(project)
    sources: dict = {}

    # 1. Declared high-risk modules (machine-readable owner declaration)
    declared: list[dict] = []
    manifest = load_quality_manifest(project, lenient=True)
    for entry in manifest.get("high_risk_modules", []):
        if isinstance(entry, str):
            declared.append({"path": entry, "risk": ""})
        elif isinstance(entry, dict) and entry.get("path"):
            declared.append({"path": entry["path"],
                             "risk": entry.get("risk", "")})
    sources["declared"] = len(declared)

    # 2. CRG hub risk map (critical/high hubs)
    crg_hubs: list[dict] = []
    crg_path = project / ".sessi-work" / "crg_metrics.json"
    try:
        crg = json.loads(crg_path.read_text(encoding="utf-8"))
        for hub in (crg.get("hub_risk_map") or {}).get("hubs", []):
            if hub.get("severity") in ("critical", "high") and hub.get("file"):
                crg_hubs.append(hub)
    except (OSError, json.JSONDecodeError):
        pass
    sources["crg_hubs"] = len(crg_hubs)

    # 3. Mutation survivors (C5 artifact)
    survivors: list[dict] = []
    surv_path = project / ".methodology" / "mutation_survivors.json"
    try:
        survivors = json.loads(
            surv_path.read_text(encoding="utf-8")
        ).get("survivors", [])
    except (OSError, json.JSONDecodeError):
        pass
    sources["mutation_survivors"] = len(survivors)
    survivors_by_file: dict[str, int] = {}
    for s in survivors:
        if s.get("file"):
            survivors_by_file[s["file"]] = survivors_by_file.get(s["file"], 0) + 1

    # 4. integration_coverage from the latest gate result
    integration: dict | None = None
    for gate_num in (4, 3, 2):
        gpath = project / ".methodology" / f"gate{gate_num}_result.json"
        try:
            gdata = json.loads(gpath.read_text(encoding="utf-8"))
            dim = (gdata.get("breakdown") or {}).get("integration_coverage")
            if isinstance(dim, dict) and dim.get("score") is not None:
                integration = {"gate": gate_num, "score": dim["score"]}
                break
        except (OSError, json.JSONDecodeError):
            continue
    sources["integration_coverage"] = integration is not None

    # 5. Threat model (SAD.md §6, Round 10) — each threat's owner_module is a
    # forced attack-vector seed, independent of CRG/mutation signals. An
    # honest applicability: none (or a missing/malformed block) contributes
    # zero threats, same as every other best-effort source above.
    from core.quality_gate.security_design import extract_security_block
    from detection.drift_detector import sab_module_to_path_variants

    threats: list[dict] = []
    try:
        raw = extract_security_block(ProjectLayout(project).sad_path)
        sec = raw.get("security_design") if isinstance(raw, dict) else None
        if isinstance(sec, dict) and sec.get("applicability") == "full":
            threats = [t for t in sec.get("threats", []) if isinstance(t, dict)]
    except RuntimeError:
        pass
    sources["threat_model"] = len(threats)

    def _resolve_owner_module_path(dotted: str) -> str | None:
        """dotted SAB/SEC module name -> on-disk relative path, same
        candidate expansion preflight_sab_check uses for SAB modules."""
        for rel_dir in ("03-development/src", "src"):
            for cand in sab_module_to_path_variants(dotted, rel_dir):
                candidate = project / cand
                if candidate.is_file():
                    return str(candidate.relative_to(project))
        return None

    # 6. Assemble: reasons accumulate per module path
    reasons: dict[str, list[str]] = {}
    for d in declared:
        note = f"declared{': ' + d['risk'] if d['risk'] else ''}"
        reasons.setdefault(d["path"], []).append(note)
    for hub in crg_hubs:
        reasons.setdefault(hub["file"], []).append(
            f"crg_hub:{hub['severity']} fan_in={hub.get('fan_in')}"
            + (" untested" if hub.get("untested") else "")
        )
    # Survivor density ≥3 in one file promotes it to high-risk; fewer stay
    # as annotations on the standard tier.
    for fpath, count in survivors_by_file.items():
        if count >= 3:
            reasons.setdefault(fpath, []).append(f"mutation_survivors:{count}")
    for t in threats:
        owner_module = t.get("owner_module")
        resolved = _resolve_owner_module_path(owner_module) if owner_module else None
        if resolved:
            reasons.setdefault(resolved, []).append(
                f"threat_model:{t.get('id')} {t.get('category')}"
            )

    inventory: list[str] = []
    for rel_dir in ("03-development/src", "src"):
        base = project / rel_dir
        if base.is_dir():
            inventory.extend(
                str(p.relative_to(project))
                for p in iter_source_files(base, language)
            )

    high_risk = [
        {"path": p, "name": Path(p).stem, "reasons": r}
        for p, r in sorted(reasons.items())
    ]
    high_paths = set(reasons)
    standard = [
        {"path": p, "name": Path(p).stem,
         **({"survivors": survivors_by_file[p]} if p in survivors_by_file else {})}
        for p in inventory if p not in high_paths
    ]

    git_sha = ""
    try:
        git_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass

    threat_model_out = [
        {"threat_id": t.get("id"), "category": t.get("category"),
         "description": t.get("description"), "owner_module": t.get("owner_module"),
         "boundary": t.get("boundary")}
        for t in threats
    ]

    targets = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha,
        "language": language,
        "high_risk": high_risk,
        "standard": standard,
        "mutation_survivors": survivors,
        "integration_coverage": integration,
        "threat_model": threat_model_out,
        "sources": sources,
    }
    out_path = project / ".methodology" / "bug_hunt_targets.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(targets, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")

    print(f"[bug-hunt-targets] {len(high_risk)} high-risk (3-lens), "
          f"{len(standard)} standard (1-lens) → {out_path.relative_to(project)}")
    for hr in high_risk:
        print(f"  HIGH {hr['path']}  ({'; '.join(hr['reasons'])})")
    if not high_risk:
        print("  NOTE: no high-risk signals found — declare high_risk_modules in "
              ".methodology/quality_manifest.json, or run CRG recon / mutation "
              "precheck first for richer targeting.")
    return 0


def cmd_run_gap_analysis(args: argparse.Namespace) -> int:
    """Run M3 gap analysis: detect gaps between SPEC.md and codebase."""
    project = Path(args.project).resolve()
    spec = args.spec or "SPEC.md"

    print(f"\n{'='*60}\nrun-gap-analysis (M3)  project={project}\n{'='*60}")

    # Fail fast if the spec file is missing (explicit user invocation — not a pipeline skip)
    spec_path = project / spec
    if not spec_path.exists():
        print(f"[ERROR] Spec file not found: {spec_path}")
        return 1

    report = _run_gap_analysis(project, similarity=args.similarity, spec=spec)

    if report.get("skipped"):
        reason = report.get("reason") or report.get("error", "unknown")
        print(f"  Skipped: {reason}")
        return 0

    summary = report.get("summary", {})
    print(f"\n{'─'*60}")
    print("Gap Analysis Results")
    print(f"{'─'*60}")
    print(f"  Total gaps : {summary.get('total', 0)}")
    print(f"  Missing    : {summary.get('missing', 0)}")
    print(f"  Incomplete : {summary.get('incomplete', 0)}")
    print(f"  Orphaned   : {summary.get('orphaned', 0)}")
    print(f"  Critical   : {summary.get('critical', 0)}")
    print(f"  Major      : {summary.get('major', 0)}")
    print(f"  Minor      : {summary.get('minor', 0)}")

    critical = summary.get("critical", 0)
    if critical > 0:
        print(f"\n[WARN] {critical} critical gap(s) detected")
        return 2  # 2 = critical gaps (distinct from hard error = 1)
    return 0


def _run_gap_analysis(project: Path, similarity: float = 0.6, spec: str = "SPEC.md") -> dict:
    """Run M3 gap analysis. Returns gap report dict; warns on failure."""
    try:
        from gap_detector.parser import SpecParser
        from gap_detector.scanner import CodeScanner
        from gap_detector.detector import GapDetector

        spec_path = project / spec
        if not spec_path.exists():
            print(f"  [M3] {spec} not found — skipping gap analysis")
            return {"skipped": True, "reason": f"{spec} not found"}

        parsed_spec = SpecParser(str(spec_path)).parse()
        scanner = CodeScanner(str(project))
        code = scanner.scan()
        detector = GapDetector(parsed_spec, code, similarity_threshold=similarity)
        gaps = detector.detect()
        summary = detector.get_summary()

        report = {
            "summary": {
                "total": summary.total_gaps, "missing": summary.missing,
                "incomplete": summary.incomplete, "orphaned": summary.orphaned,
                "critical": summary.critical, "major": summary.major,
                "minor": summary.minor,
            },
            "gaps": [{"type": g.gap_type, "severity": g.severity,
                       "reason": g.reason, "action": g.recommended_action}
                      for g in gaps],
        }
        report_path = project / ".methodology" / "gap_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2))
        print(f"  [M3] Gap report → {report_path}  "
              f"(total={summary.total_gaps}, critical={summary.critical})")
        return report
    except ImportError:
        print("  [M3] gap_detector unavailable — skipping gap analysis")
        return {"skipped": True, "reason": "gap_detector unavailable"}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"  [M3] Gap analysis error: {exc}")
        return {"skipped": True, "error": str(exc)}


def register(sub) -> None:
    """Wire the advisory subcommands onto the main subparser action.

    R49-B 站3: a command's flags now live beside its body, so adding one
    touches this file and nothing else. Moved verbatim out of
    cli/check_cmds.py's 295-line register().
    """
    # bug-hunt-targets (v2.9 C4 — Gate-3 adversarial-review targeting manifest)
    bht = sub.add_parser(
        "bug-hunt-targets",
        help="Aggregate hunt-targeting signals (declared/CRG/survivors/coverage) "
             "into .methodology/bug_hunt_targets.json",
    )
    bht.add_argument("--project", default=".", help="Project root (default: .)")
    bht.set_defaults(func=cmd_bug_hunt_targets)

    # run-gap-analysis (M3)
    ga = sub.add_parser(
        "run-gap-analysis",
        help="M3: Detect gaps between SPEC.md and codebase implementation",
    )
    ga.add_argument("--project",    default=".", help="Project root (default: .)")
    ga.add_argument("--spec",       default="SPEC.md", help="Path to SPEC.md")
    ga.add_argument("--similarity", type=float, default=0.6,
                    help="Similarity threshold for matching (default: 0.6)")
    ga.set_defaults(func=cmd_run_gap_analysis)
