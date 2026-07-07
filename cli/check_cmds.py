"""Verification/check commands (spec, trace, constitution, manifest, approvals, gap analysis).

Extracted verbatim from harness_cli.py (方案六). Free names that live
in harness_cli resolve through `_hc.` at call time, so existing
monkeypatches on harness_cli attributes keep working. harness_cli
re-exports these cmd_* names, so `from harness_cli import cmd_x`
imports are unaffected.
"""

from __future__ import annotations

import harness_cli as _hc


def cmd_bug_hunt_targets(args: _hc.argparse.Namespace) -> int:
    """v2.9 C4: aggregate hunt-targeting signals into bug_hunt_targets.json.

    Sources (each best-effort, provenance recorded):
      1. declared high-risk modules — quality_manifest.json "high_risk_modules"
      2. CRG hub risk — .sessi-work/crg_metrics.json hub_risk_map (critical/high)
      3. mutation survivors — .methodology/mutation_survivors.json (C5 artifact)
      4. integration_coverage — latest gate result breakdown
      5. source inventory — remaining src files become standard (1-lens) targets

    Output feeds harness/ssi/prompts/hunt_bugs.md: high_risk modules get the
    3-lens deep scan, standard get 1 general lens; survivor entries tell
    hunters which functions have behavior no test asserts.
    """
    from datetime import datetime, timezone

    from core.utils.lang_patterns import iter_source_files, project_language

    project = _hc.Path(args.project).resolve()
    language = project_language(project)
    sources: dict = {}

    # 1. Declared high-risk modules (machine-readable owner declaration)
    declared: list[dict] = []
    manifest_path = project / ".methodology" / "quality_manifest.json"
    try:
        manifest = _hc.json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest.get("high_risk_modules", []):
            if isinstance(entry, str):
                declared.append({"path": entry, "risk": ""})
            elif isinstance(entry, dict) and entry.get("path"):
                declared.append({"path": entry["path"],
                                 "risk": entry.get("risk", "")})
    except (OSError, _hc.json.JSONDecodeError):
        pass
    sources["declared"] = len(declared)

    # 2. CRG hub risk map (critical/high hubs)
    crg_hubs: list[dict] = []
    crg_path = project / ".sessi-work" / "crg_metrics.json"
    try:
        crg = _hc.json.loads(crg_path.read_text(encoding="utf-8"))
        for hub in (crg.get("hub_risk_map") or {}).get("hubs", []):
            if hub.get("severity") in ("critical", "high") and hub.get("file"):
                crg_hubs.append(hub)
    except (OSError, _hc.json.JSONDecodeError):
        pass
    sources["crg_hubs"] = len(crg_hubs)

    # 3. Mutation survivors (C5 artifact)
    survivors: list[dict] = []
    surv_path = project / ".methodology" / "mutation_survivors.json"
    try:
        survivors = _hc.json.loads(
            surv_path.read_text(encoding="utf-8")
        ).get("survivors", [])
    except (OSError, _hc.json.JSONDecodeError):
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
            gdata = _hc.json.loads(gpath.read_text(encoding="utf-8"))
            dim = (gdata.get("breakdown") or {}).get("integration_coverage")
            if isinstance(dim, dict) and dim.get("score") is not None:
                integration = {"gate": gate_num, "score": dim["score"]}
                break
        except (OSError, _hc.json.JSONDecodeError):
            continue
    sources["integration_coverage"] = integration is not None

    # 5. Assemble: reasons accumulate per module path
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

    inventory: list[str] = []
    for rel_dir in ("03-development/src", "src"):
        base = project / rel_dir
        if base.is_dir():
            inventory.extend(
                str(p.relative_to(project))
                for p in iter_source_files(base, language)
            )

    high_risk = [
        {"path": p, "name": _hc.Path(p).stem, "reasons": r}
        for p, r in sorted(reasons.items())
    ]
    high_paths = set(reasons)
    standard = [
        {"path": p, "name": _hc.Path(p).stem,
         **({"survivors": survivors_by_file[p]} if p in survivors_by_file else {})}
        for p in inventory if p not in high_paths
    ]

    git_sha = ""
    try:
        git_sha = _hc.subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (_hc.subprocess.SubprocessError, OSError):
        pass

    targets = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha,
        "language": language,
        "high_risk": high_risk,
        "standard": standard,
        "mutation_survivors": survivors,
        "integration_coverage": integration,
        "sources": sources,
    }
    out_path = project / ".methodology" / "bug_hunt_targets.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_hc.json.dumps(targets, indent=2, ensure_ascii=False) + "\n",
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


def cmd_spec_coverage_check(args: _hc.argparse.Namespace) -> int:
    """Spec Coverage Check — compare TEST_SPEC.md items against actual test files.

    Validates that every named test case declared in the P2 TEST_SPEC.md artifact
    has been implemented as a real test function in tests/.
    """
    project = _hc.Path(args.project).resolve()
    threshold = getattr(args, "threshold", 80.0)
    fr_id = getattr(args, "fr_id", None)
    code, _ = _hc._run_spec_coverage_check(project, threshold, fr_id=fr_id, verbose=True)
    return code


def cmd_crg_arch_check(args: _hc.argparse.Namespace) -> int:
    """Non-interactive CRG architecture gate for CI (deterministic, no LLM).

    Builds/refreshes the graph and computes the architecture score via
    crg_independent — the same gate-blocking score used at finalize_gate, but
    runnable in CI because it needs no interactive session. Hard-fails (exit 1)
    when the score drops below --threshold, or (with --baseline) when structural
    drift vs that baseline reaches --drift-threshold. This closes the audit gap
    where CRG never ran in CI (architecture scoring was local-only).
    """
    project = _hc.Path(args.project).resolve()
    from core.harness_config import is_dim_disabled
    if is_dim_disabled("architecture", str(project)):
        print("[crg-arch-check] INFO: crg_architecture disabled in harness_config.json — skipping")
        return 0
    work_dir = project / ".sessi-work"
    try:
        from harness.crg_independent import run_independent_crg
        metrics = run_independent_crg(str(project), str(work_dir))
    except Exception as exc:  # CrgIndependentError / import → CRG is mandatory, block
        print(f"[crg-arch-check] BLOCKED: CRG architecture score unavailable: {exc}")
        return 1

    arch = metrics.get("architecture_score")
    if arch is None:
        arch = (metrics.get("community_cohesion") or {}).get("score") or 0.0
    threshold = getattr(args, "threshold", 80.0)
    print(f"[crg-arch-check] architecture_score={arch:.1f} (threshold {threshold:.0f})")
    if arch < threshold:
        print(f"[crg-arch-check] FAIL: architecture {arch:.1f} < {threshold:.0f}")
        return 1

    baseline = getattr(args, "baseline", None)
    if baseline:
        bp = _hc.Path(baseline)
        if bp.is_file():
            try:
                from harness.ssi.scripts.crg_analysis import compute_structural_drift
                _bl = _hc.json.loads(bp.read_text(encoding="utf-8"))
                drift = compute_structural_drift(_bl, metrics)
                dthr = getattr(args, "drift_threshold", 0.4)
                print(f"[crg-arch-check] drift vs {bp.name}: {drift:.2f} (threshold {dthr:.2f})")
                if drift >= dthr:
                    print(f"[crg-arch-check] FAIL: architecture regression drift {drift:.2f} >= {dthr:.2f}")
                    return 1
            except Exception as exc:
                print(f"[crg-arch-check] WARN: drift check skipped — {exc}")
        else:
            print(f"[crg-arch-check] INFO: baseline {bp} not found — drift check skipped")
    print("[crg-arch-check] OK")
    return 0


def cmd_check_test_spec_consistency(args: _hc.argparse.Namespace) -> int:
    """P2 self-consistency gate — prove TEST_SPEC.md is not unsatisfiable.

    Correctness is locked in P2: for each declared case the sub-assertion
    predicates are evaluated / length-checked against that case's own concrete
    inputs. A contradiction (e.g. `" " in "ㄏㄢˋ"` False, or 4 one-char chunks
    for a 5-char input) means no implementation could satisfy the spec — FAIL,
    so P3 never implements an unsatisfiable catalog. The engine reads ONLY
    TEST_SPEC.md; it never opens any requirements source (SRS/SAD/SPEC).
    """
    project = _hc.Path(args.project).resolve()
    spec_path = _hc.ProjectLayout(project).test_spec_path
    if not spec_path.exists():
        print("[check-test-spec-consistency] 02-architecture/TEST_SPEC.md not found — skipping.")
        return 0

    from core.quality_gate.parsers import SpecAssertionParser
    from core.quality_gate.red_assertion_check import check_test_spec_consistency

    parsed = SpecAssertionParser.parse(spec_path.read_text(encoding="utf-8"))
    fr_filter = getattr(args, "fr_id", None)
    if fr_filter:
        parsed = {k: v for k, v in parsed.items() if k == fr_filter}

    if not parsed:
        print("[check-test-spec-consistency] No Inputs + Sub-assertion tables (new "
              "schema) found — nothing to verify"
              + (f" [{fr_filter}]" if fr_filter else "") + ".")
        return 0

    total_err = total_review = 0
    for fr_id, (cases, assertions) in sorted(parsed.items()):
        for v in check_test_spec_consistency(cases, assertions):
            if v.severity == "error":
                total_err += 1
                print(f"[FAIL] {fr_id} {v.check_type}: {v.message}")
            elif v.severity == "info":
                total_review += 1
                print(f"[review] {fr_id}: {v.message}")

    if total_err:
        print(f"\n[BLOCKED] TEST_SPEC.md self-consistency: {total_err} contradiction(s) — "
              "P3 must not implement an unsatisfiable spec. Fix TEST_SPEC.md (P2).")
        return 1
    print("[check-test-spec-consistency] OK — 0 contradictions"
          + (f"; {total_review} needs-review (P2 Agent B sign-off)" if total_review else "") + ".")
    return 0


def cmd_check_test_mirrors_spec(args: _hc.argparse.Namespace) -> int:
    """P3 mirror gate — verify a RED test faithfully implements TEST_SPEC.md.

    Run AFTER the RED test is written (not before). Structure-only: no
    satisfiability, no eval of test logic. Correctness was locked in P2; this
    proves the test mirrors it. Divergence (a sub-assertion applied to a
    different case set, or a declared assertion missing) -> FAIL, so the fix is
    the test, never TEST_SPEC. Reads only TEST_SPEC.md and the test file.
    """
    project = _hc.Path(args.project).resolve()
    spec_path = _hc.ProjectLayout(project).test_spec_path
    fr_id = args.fr_id
    # Bug #26 fix: --test-file accepts nargs="+", so args.test_files is a list.
    # Iterate each file; aggregate violations across all files. The command
    # fails (exit 1) if any one file has an error-severity violation.
    test_files = [_hc.Path(f).resolve() for f in args.test_files]

    if not spec_path.exists():
        print("[check-test-mirrors-spec] 02-architecture/TEST_SPEC.md not found — skipping.")
        return 0

    from core.quality_gate.parsers import SpecAssertionParser
    from core.quality_gate.red_assertion_check import (
        check_test_mirrors_spec,
        check_test_mirrors_spec_js,
    )

    parsed = SpecAssertionParser.parse(spec_path.read_text(encoding="utf-8"))
    if fr_id not in parsed:
        print(f"[check-test-mirrors-spec] {fr_id} has no Inputs+Sub-assertion tables "
              "in TEST_SPEC.md — nothing to mirror.")
        return 0

    cases, assertions = parsed[fr_id]
    all_errs = []
    all_reviews = []
    for test_file in test_files:
        if not test_file.exists():
            print(f"[check-test-mirrors-spec] test file not found: {test_file} — skipping.")
            continue
        test_source = test_file.read_text(encoding="utf-8")
        suffix = test_file.suffix.lower()
        if suffix in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
            dialect = {"ts": "typescript", "tsx": "tsx"}.get(suffix.lstrip("."), "javascript")
            violations = check_test_mirrors_spec_js(test_source, cases, assertions, dialect)
        else:
            violations = check_test_mirrors_spec(test_source, cases, assertions)
        errs = [v for v in violations if v.severity == "error"]
        reviews = [v for v in violations if v.severity == "info"]
        for v in errs:
            print(f"[FAIL] {fr_id} ({test_file.name}) {v.check_type}: {v.message}")
        for v in reviews:
            print(f"[review] {fr_id} ({test_file.name}): {v.message}")
        all_errs.extend(errs)
        all_reviews.extend(reviews)
    if all_errs:
        print(f"\n[BLOCKED] {len(test_files)} test file(s) checked; {len(all_errs)} divergence(s) from "
              f"TEST_SPEC.md. P3 implements the spec verbatim — fix the test, not TEST_SPEC.")
        return 1
    print(f"[check-test-mirrors-spec] OK — {len(test_files)} test file(s) mirror {fr_id} in TEST_SPEC.md."
          + (f" {len(all_reviews)} item(s) need P3 reviewer sign-off." if all_reviews else ""))
    return 0


def cmd_manifest(args: _hc.argparse.Namespace) -> int:
    """Generate quality_manifest.json at P2 exit.

    Refuses to overwrite an existing manifest unless ``--force`` is passed,
    because the manifest holds accumulated Gate scores that ``plan-all`` and
    other commands depend on; shrinking it silently resets pipeline progress.
    """
    from harness.harness_bridge import HarnessBridge

    sad_resolved = _hc.Path(args.sad).resolve()
    # SAB.json is written under .methodology/ at the project root, so the project
    # root is the parent directory that *contains* .methodology/. Walking up from
    # the SAD path until we find it (or fall back to the SAD's parent) keeps the
    # contract correct regardless of where SAD.md lives (02-architecture/,
    # docs/, etc.).
    project = sad_resolved.parent
    for ancestor in [sad_resolved.parent, *sad_resolved.parents]:
        if (ancestor / ".methodology").is_dir():
            project = ancestor
            break
    # nargs="+" collects space-separated FR IDs, but users may also pass
    # comma-separated values. Split on commas to support both formats.
    fr_ids: list[str] = []
    for item in args.fr_ids:
        fr_ids.extend(fid.strip() for fid in item.split(",") if fid.strip())
    bridge = HarnessBridge()
    out = bridge.generate_quality_manifest(
        fr_ids=fr_ids,
        sad_path=args.sad,
        project_root=str(project),
        force=getattr(args, "force", False),
    )
    if out is None:
        manifest_path = project / ".methodology" / "quality_manifest.json"
        print(
            f"[PRESERVE] {manifest_path.name} already exists; "
            "use --force to regenerate."
        )
        return 0
    print(f"quality_manifest.json written → {out}")
    manifest = _hc.json.loads(out.read_text(encoding="utf-8"))
    print(f"  fr_ids        : {manifest['fr_ids']}")
    print(f"  generated_at  : phase {manifest['generated_at_phase']}")
    _hc._generate_sab_json(project)
    return 0


def cmd_generate_verification_report(args: _hc.argparse.Namespace) -> int:
    """Generate 05-verification/VERIFICATION_REPORT.md from manifest + SRS.

    Created to fix Finding #16: P5 plan's VERIFY-REPORT task said "Generate
    VERIFICATION_REPORT.md" but no harness tool produced it. The P4→P5
    handoff validator blocks on this file with no remediation path; this
    command is the canonical remediation.

    Usage:
        python3 harness_cli.py generate-verification-report --project .
    """
    from scripts.generate_verification_report import generate_verification_report

    project = _hc.Path(args.project).resolve()
    try:
        out = generate_verification_report(project)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[FAIL] generate-verification-report: {exc}", file=_hc.sys.stderr)
        return 1
    print(f"VERIFICATION_REPORT.md written → {out}")
    # Echo summary lines so the operator can see pass/fail count at a glance
    try:
        text = out.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "FRs Gate 1 PASS" in line or "Pass rate" in line:
                print(f"  {line.strip()}")
    except Exception:  # non-fatal
        pass
    return 0


def cmd_verify_agent_b_approvals(args: _hc.argparse.Namespace) -> int:
    """Verify that Agent B approval JSON files exist for all required FRs.

    Each FR must have a corresponding .methodology/agent_b_approvals/FR-XX.json
    with review_status == "APPROVE" and the required docs_embedded list.

    NOTE: .methodology/agent_b_approvals/ is committed (not gitignored).
    Do NOT use .sessi-work/ — that directory is in .gitignore and invisible to CI.

    Usage:
      python harness_cli.py verify-agent-b-approvals --phase 8 --fr-ids FR-01,FR-02 --project .
      python harness_cli.py verify-agent-b-approvals --phase 8 --project .  # reads from manifest
    """
    project = _hc.Path(args.project).resolve()
    phase = args.phase

    fr_ids_arg = getattr(args, "fr_ids", "") or ""
    fr_ids = [f.strip() for f in fr_ids_arg.split(",") if f.strip()]
    deliverable_ids = _hc._resolve_deliverable_ids(project, phase, fr_ids)

    if not deliverable_ids:
        print("[verify-agent-b] No FR IDs found — pass --fr-ids or ensure quality_manifest.json exists.")
        return 1

    passed, report = _hc._verify_agent_b_approvals_core(project, phase, deliverable_ids)
    print(report)
    return 0 if passed else 1


def cmd_write_approval(args: _hc.argparse.Namespace) -> int:
    """Deterministically persist an Agent B approval JSON to disk + verify in-process.

    Replaces the LLM-as-shell-wrapper pattern in workflow JS (persistApproval helper
    that wrapped `python3 -c "open().write()"` + a second `agent()` call for disk
    verification — same anti-pattern, double agent round-trip, no real verification).

    Architecture (Bug v22 fix, 2026-06-29): write + verify happen in a single Python
    call so the harness can guarantee the file exists with the expected content.
    The Bash invocation by the workflow tool sees a single deterministic exit code
    (0 = written + verified; 1 = write failed; 2 = verify failed).

    Usage:
      python harness_cli.py write-approval --fr-id SRS.md --json '<json>'
      echo '<json>' | python harness_cli.py write-approval --fr-id SRS.md --stdin
    """
    import json as _json

    project = _hc.Path(args.project).resolve()
    fr_id = args.fr_id
    if not fr_id:
        print("[write-approval] ERROR: --fr-id is required", file=_hc.sys.stderr)
        return 1

    # Resolve JSON payload from --json arg or stdin
    if args.stdin:
        raw = _hc.sys.stdin.read()
    else:
        raw = args.json or ""
    if not raw:
        print("[write-approval] ERROR: no JSON payload (--json or --stdin required)", file=_hc.sys.stderr)
        return 1

    # Validate JSON is parseable before any disk I/O
    try:
        payload = _json.loads(raw)
    except _json.JSONDecodeError as e:
        print(f"[write-approval] ERROR: invalid JSON payload: {e}", file=_hc.sys.stderr)
        return 1

    approvals_dir = project / ".methodology" / "agent_b_approvals"
    approval_path = approvals_dir / f"{fr_id}.json"
    try:
        approvals_dir.mkdir(parents=True, exist_ok=True)
        # Atomic write (tmp + os.replace) — same pattern as taskq NFR-03 atomic contract
        tmp_path = approval_path.with_suffix(approval_path.suffix + ".tmp")
        tmp_path.write_text(_json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        _hc.os.replace(tmp_path, approval_path)
    except OSError as e:
        print(f"[write-approval] ERROR: write failed for {approval_path}: {e}", file=_hc.sys.stderr)
        return 1

    # Deterministic in-process verify (replaces LLM-as-shell-wrapper disk check)
    if not approval_path.is_file():
        print(f"[write-approval] ERROR: verify failed — {approval_path} not on disk after write", file=_hc.sys.stderr)
        return 2
    size = approval_path.stat().st_size
    if size < 10:
        print(f"[write-approval] ERROR: verify failed — {approval_path} only {size} bytes", file=_hc.sys.stderr)
        return 2

    print(f"[write-approval] OK: {approval_path} ({size} bytes, written + verified)")
    return 0


def cmd_verify_file(args: _hc.argparse.Namespace) -> int:
    """Deterministically verify a file exists and (optionally) has parseable content.

    Replaces 18 LLM-as-shell-wrapper sites across 6 phase workflow JS files
    (ctxCheck / load-ctx-a / envReport / persistApproval verify). Single Python call
    reads the file, validates min-bytes, optionally parses JSON/YAML, and emits
    one deterministic exit code that the workflow JS regex-matches on stdout.

    Usage:
      python harness_cli.py verify-file --file path/to/ctx.json --expect json --min-bytes 50
      python harness_cli.py verify-file --file path/to/file --min-bytes 1   # any non-empty file
    """
    import json as _json

    file_path = _hc.Path(args.file)
    if not file_path.is_absolute():
        file_path = (_hc.Path(args.project).resolve() / file_path) if args.project else file_path

    expect = (args.expect or "any").lower()  # any | json | yaml | text
    min_bytes = args.min_bytes if args.min_bytes is not None else 1

    if not file_path.exists():
        print(f"[verify-file] MISSING: {file_path}", file=_hc.sys.stderr)
        return 1
    if not file_path.is_file():
        print(f"[verify-file] NOT_A_FILE: {file_path}", file=_hc.sys.stderr)
        return 1

    size = file_path.stat().st_size
    if size < min_bytes:
        print(f"[verify-file] TOO_SMALL: {file_path} ({size} bytes < {min_bytes})", file=_hc.sys.stderr)
        return 1

    if expect == "json":
        try:
            _json.loads(file_path.read_text(encoding="utf-8"))
        except (_json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            print(f"[verify-file] INVALID_JSON: {file_path}: {e}", file=_hc.sys.stderr)
            return 1
    elif expect == "yaml":
        try:
            import yaml as _yaml  # type: ignore
            _yaml.safe_load(file_path.read_text(encoding="utf-8"))
        except ImportError:
            print("[verify-file] WARN: PyYAML not installed — skipping YAML parse, treating as text", file=_hc.sys.stderr)
        except Exception as e:  # yaml.YAMLError + others
            print(f"[verify-file] INVALID_YAML: {file_path}: {e}", file=_hc.sys.stderr)
            return 1
    # expect == "any" | "text" — just existence + size check

    print(f"[verify-file] OK: {file_path} ({size} bytes, expect={expect})")
    return 0


def cmd_run_gap_analysis(args: _hc.argparse.Namespace) -> int:
    """Run M3 gap analysis: detect gaps between SPEC.md and codebase."""
    project = _hc.Path(args.project).resolve()
    spec = args.spec or "SPEC.md"

    print(f"\n{'='*60}\nrun-gap-analysis (M3)  project={project}\n{'='*60}")

    # Fail fast if the spec file is missing (explicit user invocation — not a pipeline skip)
    spec_path = project / spec
    if not spec_path.exists():
        print(f"[ERROR] Spec file not found: {spec_path}")
        return 1

    report = _hc._run_gap_analysis(project, similarity=args.similarity, spec=spec)

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


def cmd_verify_spec(args: _hc.argparse.Namespace) -> int:
    """Verify implementation complies with spec requirements (6-dimension check)."""
    from scripts.verify_spec_compliance import SpecComplianceChecker

    project = str(_hc.Path(args.project).resolve())
    print(f"\n{'='*60}\nverify-spec  project={project}\n{'='*60}")

    checker = SpecComplianceChecker(project)
    result = checker.check_all()

    print(f"\n{'─'*60}")
    print("Spec Compliance Report")
    print(f"{'─'*60}")
    print(f"  Score : {result['score']}")

    if result["passed"]:
        print("\n  PASSED:")
        for p in result["passed"]:
            print(f"    + {p}")

    if result["issues"]:
        print("\n  ISSUES:")
        for issue in result["issues"]:
            print(f"    - {issue}")
        if getattr(args, "fix", False):
            print("\n  FIX SUGGESTIONS:")
            for hint in checker.suggest_fixes(result["issues"]):
                print(f"    → {hint}")
            print("\n  [INFO] --fix shows suggestions only. Apply fixes manually.")

    return 0 if not result["issues"] else 1


def cmd_migrate_trace_overlay(args: _hc.argparse.Namespace) -> int:
    """Wrap a sentinel-less TRACEABILITY_MATRIX.md in AUTO-GEN sentinels.

    Idempotent. Re-running on an already-migrated file is a no-op. The
    migration does NOT extract manual rows into the overlay — that's a
    per-project human task; this tool only makes the file forward-compatible
    with `build_traceability.py`'s regeneration (which wipes non-sentinel
    content on subsequent runs).
    """
    from core.traceability.overlay import migrate_existing_matrix

    project = _hc.Path(args.project).resolve()
    matrix_path = project / "TRACEABILITY_MATRIX.md"
    overlay_path = project / "TRACEABILITY_MATRIX.overlay.yaml"

    result = migrate_existing_matrix(
        matrix_path, overlay_path, dry_run=args.dry_run
    )
    print(f"\nmigrate-trace-overlay  project={project}")
    print(f"  status: {result['status']}")
    if result["status"] == "wrapped":
        verb = "would wrap" if args.dry_run else "wrapped"
        print(f"  {verb} {result['matrix']} (+{result['lines_added']} sentinel lines)")
        if result["overlay_created"]:
            print(f"  created empty overlay {result['overlay']}")
    elif result["status"] == "already-migrated":
        print("  no-op: AUTO-GEN sentinels already present")
    elif result["status"] == "missing":
        print(f"  {result['matrix']} not found; nothing to migrate")
    return 0


def cmd_build_trace_attestation(args: _hc.argparse.Namespace) -> int:
    """Re-derive the matrix and write a git-anchored SHA-256 attestation."""
    from scripts.build_trace_attestation import build_attestation, write_attestation

    project = _hc.Path(args.project).resolve()
    overlay = _hc.Path(args.overlay).resolve() if args.overlay else None
    trace_dir = _hc.Path(args.trace_dir)
    attestation = build_attestation(project, overlay_path=overlay)
    if not args.write:
        # Build-only mode (matches scripts/build_trace_attestation.py --no-write).
        # Default (no flag) is write — CLI is the canonical writer; --write is a
        # no-op alias kept for plan-template compatibility (Bug #109).
        print(f"build-trace-attestation  project={project}  (--no-write, dry build)")
        print(f"  git_sha:         {attestation['git_sha']}")
        print(f"  content_sha256:  {attestation['content_sha256']}")
        return 0
    canonical, latest = write_attestation(project, attestation, trace_dir)
    print(f"\nbuild-trace-attestation  project={project}")
    print(f"  git_sha:         {attestation['git_sha']}")
    print(f"  content_sha256:  {attestation['content_sha256']}")
    print(f"  wrote canonical: {canonical}")
    print(f"  wrote latest:    {latest}  (gitignored)")
    if attestation.get("overlay_errors"):
        for err in attestation["overlay_errors"]:
            print(f"  overlay error: {err}", file=_hc.sys.stderr)
    return 0


def cmd_verify_trace(args: _hc.argparse.Namespace) -> int:
    """Verify committed attestation matches re-derived matrix.

    Exit codes (must match scripts/verify_trace_attestation.py):
      0 clean / 1 mismatch / 2 missing / 3 schema error.
    """
    from scripts.verify_trace_attestation import verify_attestation

    project = _hc.Path(args.project).resolve()
    overlay = _hc.Path(args.overlay).resolve() if args.overlay else None
    trace_dir = _hc.Path(args.trace_dir)
    code, msg = verify_attestation(project, overlay, trace_dir)
    gate_tag = f" [gate {args.gate}]" if getattr(args, "gate", None) else ""
    print(f"\nverify-trace{gate_tag}  project={project}")
    print(f"  {msg}")
    return code


def cmd_check_logic(args: _hc.argparse.Namespace) -> int:
    """Check code for logic correctness issues (output/branch/lazy-init/semantic)."""
    from scripts.spec_logic_checker import SpecLogicChecker, SemanticValidator

    project = str(_hc.Path(args.project).resolve())
    print(f"\n{'='*60}\ncheck-logic  project={project}\n{'='*60}")

    checker = SpecLogicChecker(project)
    result = checker.scan_python_files()
    checker.print_report(result)

    if args.srs and _hc.Path(args.srs).exists():
        print(f"\n{'─'*60}")
        print("Semantic Validation (SRS)")
        print(f"{'─'*60}")
        validator = SemanticValidator(args.srs)
        print(f"  Requirements: {len(validator.requirements)}")
        for fr_id, req in list(validator.requirements.items())[:5]:
            print(f"  {fr_id}: {req.get('description', '?')[:60]}...")

    return 0 if result.passed else 1


def cmd_check_constitution(args: _hc.argparse.Namespace) -> int:
    """Check constitution document quality for the current phase.

    Runs constitution postflight on the phase-specific directory so agents
    can self-check document quality during phase execution. Does NOT modify
    state.json or advance FSM — purely a diagnostic tool for iterative
    development (write → check → fix → repeat until pass).

    Usage:
      python3 harness_cli.py check-constitution --phase 1 --project .
      python3 harness_cli.py check-constitution --phase 2 --project . \\
          --file 02-architecture/adr/ADR.md
    """
    from core.quality_gate.constitution import run_constitution_check
    from core.quality_gate.constitution.runner import check_single_file
    from core.quality_gate.constitution.profile import get_profile

    project = _hc.Path(args.project).resolve()
    phase = int(args.phase)
    profile = get_profile()
    composite_threshold = profile.composite_threshold(phase)

    # ── Single-file branch (--file) ────────────────────────────────────
    file_arg = getattr(args, "file", None)
    if file_arg:
        file_path = _hc.Path(file_arg)
        if not file_path.is_absolute():
            file_path = (project / file_path).resolve()

        # Vacuous pass when the target file does not exist (or is a dir).
        if not file_path.exists():
            print(f"[SKIP] File not found: {file_path}")
            return 0
        if not file_path.is_file():
            print(f"[SKIP] Not a regular file: {file_path}")
            return 0

        print(f"\n{'='*60}")
        print(f"Constitution Self-Check — Phase {phase} (single file)")
        print(f"File: {file_path}")
        print(f"Threshold: {composite_threshold:.0f}%")
        print(f"{'='*60}")

        result = check_single_file(file_path, phase)
        return _hc._print_constitution_result(result, composite_threshold, profile, phase, file_path)

    # ── Existing directory branch (unchanged) ──────────────────────────
    _phase_dir = _hc.ProjectLayout(project).get_phase_dir(phase)
    if not _phase_dir.exists():
        print(f"[SKIP] Phase {phase} directory not found: {_phase_dir}")
        return 0

    print(f"\n{'='*60}")
    print(f"Constitution Self-Check — Phase {phase}")
    print(f"Directory: {_phase_dir}")
    print(f"Threshold: {composite_threshold:.0f}%")
    print(f"{'='*60}")

    result = run_constitution_check(
        check_type="all", docs_path=str(_phase_dir),
        current_phase=phase, check_mode="postflight",
    )

    return _hc._print_constitution_result(result, composite_threshold, profile, phase, _phase_dir)


def register(sub) -> None:
    """Wire this family's parsers onto the main subparser action."""
    # bug-hunt-targets (v2.9 C4 — Gate-3 adversarial-review targeting manifest)
    bht = sub.add_parser(
        "bug-hunt-targets",
        help="Aggregate hunt-targeting signals (declared/CRG/survivors/coverage) "
             "into .methodology/bug_hunt_targets.json",
    )
    bht.add_argument("--project", default=".", help="Project root (default: .)")
    bht.set_defaults(func=cmd_bug_hunt_targets)

    # spec-coverage-check (D4 unified — TEST_SPEC.md → tests/, single source of truth)
    scc = sub.add_parser(
        "spec-coverage-check",
        help="D4 unified: compare TEST_SPEC.md items against actual test implementations",
    )
    scc.add_argument("--project", default=".", help="Project root (default: .)")
    scc.add_argument("--threshold", type=float, default=80.0,
                     help="Minimum spec coverage percentage (default: 80.0)")
    scc.add_argument("--fr-id", default=None, dest="fr_id",
                     help="Check only a specific FR (e.g. FR-03)")
    scc.set_defaults(func=cmd_spec_coverage_check)

    # crg-arch-check (CI: non-interactive deterministic CRG architecture gate)
    cac = sub.add_parser(
        "crg-arch-check",
        help="Non-interactive CRG architecture gate (CI): independent score + drift regression",
    )
    cac.add_argument("--project", default=".", help="Project root (default: .)")
    cac.add_argument("--threshold", type=float, default=80.0,
                     help="Minimum architecture score (default: 80)")
    cac.add_argument("--baseline", default=None,
                     help="Prior crg_baseline_pN.json for drift regression check")
    cac.add_argument("--drift-threshold", type=float, default=0.4,
                     help="Maximum structural drift vs baseline (default: 0.4)")
    cac.set_defaults(func=cmd_crg_arch_check)

    # check-test-spec-consistency (P2: TEST_SPEC.md self-consistency gate)
    ctsc = sub.add_parser(
        "check-test-spec-consistency",
        help="P2: prove TEST_SPEC.md sub-assertions are self-consistent (no unsatisfiable case)",
    )
    ctsc.add_argument("--project", default=".", help="Project root (default: .)")
    ctsc.add_argument("--fr-id", dest="fr_id", default=None, help="Check only this FR (e.g. FR-03)")
    ctsc.set_defaults(func=cmd_check_test_spec_consistency)

    # check-test-mirrors-spec (P3: test faithfully implements TEST_SPEC.md)
    ctms = sub.add_parser(
        "check-test-mirrors-spec",
        help="P3: verify a RED test mirrors TEST_SPEC.md verbatim (run after the test is written)",
    )
    ctms.add_argument("--project", default=".", help="Project root (default: .)")
    ctms.add_argument("--fr-id", dest="fr_id", required=True, help="FR id (e.g. FR-01)")
    ctms.add_argument("--test-file", dest="test_files", nargs="+", required=True, help="Path(s) to the RED test file(s); accepts one or more paths to support per-FR splits like test_fr01_inputs.py + test_fr01_edge.py")
    ctms.set_defaults(func=cmd_check_test_mirrors_spec)

    # (check-test-inventory removed — deprecated since v2.6, it only
    #  delegated to spec-coverage-check. Use spec-coverage-check directly.)

    # manifest
    mf = sub.add_parser("manifest", help="Generate quality_manifest.json at P2 exit")
    mf.add_argument("--fr-ids", nargs="+", required=True, metavar="FR_ID")
    mf.add_argument("--sad",    default="02-architecture/SAD.md", help="Path to SAD.md")
    mf.add_argument("--no-git", action="store_true", dest="no_git",
                    help="Disable git commit/push after manifest generation")
    mf.add_argument("--force", action="store_true",
                    help="Overwrite an existing quality_manifest.json "
                         "(default: preserve existing manifest)")
    mf.set_defaults(func=cmd_manifest)

    # generate-verification-report  (P5 — fixes Finding #16)
    gvr = sub.add_parser(
        "generate-verification-report",
        help="Generate 05-verification/VERIFICATION_REPORT.md from manifest + SRS.md",
    )
    gvr.add_argument("--project", default=".", help="Project root (default: .)")
    gvr.set_defaults(func=cmd_generate_verification_report)

    # verify-agent-b-approvals
    vab = sub.add_parser(
        "verify-agent-b-approvals",
        help="Verify Agent B approval JSONs exist for all FRs (blocks if missing or non-APPROVE)",
    )
    vab.add_argument("--phase",   type=int, required=True, help="Current phase number")
    vab.add_argument("--project", default=".", help="Project root (default: .)")
    vab.add_argument("--fr-ids",  default="", dest="fr_ids",
                     help="Comma-separated FR IDs (default: read from quality_manifest.json)")
    vab.set_defaults(func=cmd_verify_agent_b_approvals)

    # write-approval (architectural fix for Bug v22 — replaces LLM-as-shell-wrapper persistApproval)
    wa = sub.add_parser(
        "write-approval",
        help="Deterministically persist an Agent B approval JSON to disk + verify in one call "
             "(replaces workflow JS persistApproval LLM-as-shell-wrapper; atomic write + size check, "
             "exit 0=ok 1=write-fail 2=verify-fail).",
    )
    wa.add_argument("--project", default=".", help="Project root (default: .)")
    wa.add_argument("--fr-id", required=True, dest="fr_id",
                    help="Deliverable ID (e.g. 'SRS.md'). File written to "
                         ".methodology/agent_b_approvals/<fr-id>.json")
    wa.add_argument("--json", default=None,
                    help="JSON payload as a string. Use single quotes around the JSON to escape "
                         "inner double quotes in shell.")
    wa.add_argument("--stdin", action="store_true",
                    help="Read JSON payload from stdin (alternative to --json for large payloads)")
    wa.set_defaults(func=cmd_write_approval)

    # verify-file (architectural fix — replaces 18 LLM-as-shell-wrapper verify sites in 6 phases)
    vf = sub.add_parser(
        "verify-file",
        help="Deterministically verify a file exists + meets size/parse criteria "
             "(replaces workflow JS ctxCheck / load-ctx-a / envReport / persistApproval verify). "
             "Exit 0=ok, 1=missing/invalid.",
    )
    vf.add_argument("--file", required=True, help="File path to verify (absolute, or relative to --project)")
    vf.add_argument("--project", default=".", help="Project root for relative --file paths (default: .)")
    vf.add_argument("--expect", choices=["any", "json", "yaml", "text"], default="any",
                    help="Content expectation: any (default) | json (parse) | yaml (parse) | text (any)")
    vf.add_argument("--min-bytes", type=int, default=1, dest="min_bytes",
                    help="Minimum file size in bytes (default: 1)")
    vf.set_defaults(func=cmd_verify_file)

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

    # verify-spec
    vs = sub.add_parser(
        "verify-spec",
        help="Verify implementation complies with spec requirements (6-dimension check)",
    )
    vs.add_argument("--project", default=".", help="Project root (default: .)")
    vs.add_argument("--fix", action="store_true",
                    help="Show fix suggestions for each issue (no auto-fix)")
    vs.set_defaults(func=cmd_verify_spec)

    # migrate-trace-overlay (PR 2)
    mto = sub.add_parser(
        "migrate-trace-overlay",
        help="Wrap TRACEABILITY_MATRIX.md in AUTO-GEN sentinels (one-time)",
    )
    mto.add_argument("--project", default=".", help="Target project root path")
    mto.add_argument("--dry-run", action="store_true",
                     help="Print the change without writing files")
    mto.set_defaults(func=cmd_migrate_trace_overlay)

    # build-trace-attestation (PR 3)
    bta = sub.add_parser(
        "build-trace-attestation",
        help="Re-derive matrix and write git-anchored SHA-256 attestation",
    )
    bta.add_argument("--project", required=True)
    bta.add_argument("--overlay", default=None)
    bta.add_argument("--trace-dir", default=".methodology/trace")
    bta.add_argument("--write", action="store_true", default=True,
                     help="Write attestation to .methodology/trace/ (default: True; "
                          "pass --no-write for build-only)")
    bta.add_argument("--no-write", dest="write", action="store_false",
                     help="Build matrix but do NOT write attestation files")
    bta.set_defaults(func=cmd_build_trace_attestation)

    # verify-trace (PR 3)
    vt = sub.add_parser(
        "verify-trace",
        help="Re-derive and verify committed attestation (CI/gate use)",
    )
    vt.add_argument("--project", required=True)
    vt.add_argument("--overlay", default=None)
    vt.add_argument("--gate", type=int, default=None)
    vt.add_argument("--trace-dir", default=".methodology/trace")
    vt.set_defaults(func=cmd_verify_trace)

    # check-logic
    cl = sub.add_parser(
        "check-logic",
        help="Check code for logic correctness (output/branch/lazy-init/semantic)",
    )
    cl.add_argument("--project", default=".", help="Project root (default: .)")
    cl.add_argument("--srs",     default=None, help="SRS.md path for semantic validation")
    cl.set_defaults(func=cmd_check_logic)

    cc = sub.add_parser(
        "check-constitution",
        help="Check document quality against constitution standards for a phase",
    )
    cc.add_argument("--phase",   required=True, type=int, choices=_hc.VALID_PHASES,
                    help="Phase to check (1–8)")
    cc.add_argument("--project", default=".", help="Project root (default: .)")
    cc.add_argument(
        "--file",
        default=None,
        help=(
            "Scope the check to a single file (relative to --project, or absolute). "
            "Missing file = vacuous pass (exit 0). "
            "Default (omitted): scan the whole phase directory."
        ),
    )
    cc.set_defaults(func=cmd_check_constitution)
