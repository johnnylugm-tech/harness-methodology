"""What Gate evidence has to contain before a dimension may be scored.

Round 80 站8. Moved out of harness/harness_bridge.py verbatim; the bodies here
are byte-identical to the ones that were there, which
tests/test_god_file_split_safety.py asserts by AST source segment.

Seven checks and the five tables they read. Each answers the same kind of
question — is what the sub-agent supplied for this dimension actually evidence?
— against a different fact: the tool output's content (`_validate_tool_content`
and the `_TOOL_*` tables), whether an infrastructure failure is being reported
as a finding (`_check_infra_fail_pollution`), whether the evidence was executed
at all (`_check_tool_evidence`), what the declared test outcome was
(`_check_tests_failed`, `_parse_skip_counts`, `_check_test_skip_ratio`) and
whether the verification target reaches the delivered system
(`_verify_system_reach_block`).

`path_escapes_root` and `_gate_dimension_names` travel with them because they
are inside this set's dependency closure; harness_bridge re-exports both, along
with everything else here, so every existing caller and test keeps its name.
The closure references nothing defined in harness_bridge and no class there, so
the import goes one way only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Round 80 站12: the five per-dimension tables these checks read now live
# in harness/gate_evidence_tables.py — data and logic change for different
# reasons. Imported here rather than referenced through the module so every
# existing name in this file, and every re-export harness_bridge makes from
# it, keeps working unchanged.
from harness.gate_evidence_tables import (  # noqa: F401  re-export, Round 80 站12
    DIMENSION_EXCLUSION_FILES,
    _INFRA_FAIL_EVIDENCE_SIGNATURES,
    _TOOL_CONTENT_PATTERNS,
    _TOOL_OUTPUT_MIN_BYTES,
    _TOOL_REQUIRED_PATTERNS,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - annotations only
    # The three signatures below name GateContext, which lives in
    # harness_bridge and imports this module. Guarded so the annotation
    # stays honest without creating a runtime cycle; `from __future__
    # import annotations` above means it is never evaluated anyway.
    from harness.harness_bridge import GateContext


def path_escapes_root(candidate: Path, root: Path) -> bool:
    """True if `candidate` resolves to a location outside `root`.

    Shared containment check for agent-controlled path fields (tool_output,
    issue_registry_path, ...) so an agent writing `../../etc/passwd` (or an
    absolute path, or a symlink to outside) into a gate result JSON can't be
    silently read. May raise OSError/RuntimeError if resolution fails (e.g.
    a symlink loop) — callers catch those themselves since the message they
    surface differs per call site.
    """
    return not candidate.resolve().is_relative_to(root.resolve())








def _validate_tool_content(
    content: str,
    tool: str | None,
    dim_name: str,
    *,
    inline: bool,
) -> list[str]:
    """S3-A: Verify that *content* looks like genuine tool output.

    Checks (in order):
      1. Minimum size (file only — inline snippets are expected to be short)
      2. Comment-header stub detection (applies to both file and inline)
      3. Tool-specific structural pattern match (applies to both)
      4. For tools whose dimension score is read out of the output: the
         quantity itself is present (Round 67 站3)
      5. For a tool whose dimension score has no framework-produced number,
         so that this file is the score's whole backing: the file says which
         tool produced it (Round 91, file only)

    Returns list of violation messages (empty = OK).
    """
    violations: list[str] = []

    # 1. Minimum size (file only)
    if not inline:
        size = len(content.encode("utf-8"))
        if size < _TOOL_OUTPUT_MIN_BYTES:
            violations.append(
                f"{dim_name}: tool_output file is too small ({size} bytes) — "
                f"likely a stub; real tool output is at least {_TOOL_OUTPUT_MIN_BYTES} bytes"
            )
            return violations  # Early exit — no point checking further

    # 2. Comment-header stub detection
    first_nonblank = next((ln for ln in content.splitlines() if ln.strip()), "")
    if first_nonblank.strip().startswith("#"):
        kind = "tool_evidence" if inline else "tool_output"
        violations.append(
            f"{dim_name}: {kind} starts with '#' comment — "
            f"this is a stub marker, not genuine tool output"
        )
        return violations  # Early exit

    # 3. Tool-specific structural pattern
    if tool and tool in _TOOL_CONTENT_PATTERNS:
        patterns = _TOOL_CONTENT_PATTERNS[tool]
        if not any(
            re.search(p, content, re.IGNORECASE | re.MULTILINE)
            for p in patterns
        ):
            kind = "tool_evidence" if inline else "tool_output"
            violations.append(
                f"{dim_name}: {kind} does not match any expected output pattern for "
                f"'{tool}' — content may not be genuine {tool} output"
            )

    # 4. The quantity the score was read from has to be in there.
    if tool and tool in _TOOL_REQUIRED_PATTERNS:
        what, required = _TOOL_REQUIRED_PATTERNS[tool]
        if not any(
            re.search(p, content, re.IGNORECASE | re.MULTILINE) for p in required
        ):
            kind = "tool_evidence" if inline else "tool_output"
            violations.append(
                f"{dim_name}: {kind} contains no {what} — no TOTAL row, no "
                f"percentage, no coverage header. The score cited against this "
                f"evidence cannot have been read out of it; re-run the tool "
                f"with coverage enabled and cite that run"
            )

    # 5. Round 91. For a tool whose dimension score has NO framework-produced
    #    number, the committed file is the score's only backing, and check 3
    #    is not enough to be that backing: it is an OR over four bare words.
    #    Measured on taskq-redo's Gate 4 — a 22893-byte
    #    `license_compliance.json` that json.loads rejects at line 1 (scancode's
    #    real JSON sandwiched between Python warnings and `Scanning done.`)
    #    passed with zero violations, because "Scan files for: licenses"
    #    contains "license". score=100.0, score_source=artifact_verified.
    #
    #    scancode is the only member today, so there is no registry for one
    #    entry (Round 35 站3's precedent, stated for mutmut in harness_bridge):
    #    `mutation_testing` has `.methodology/mutation_score.json` behind it and
    #    its tool_output is audit, not the number. license_compliance has no
    #    such artifact. Add a second member here and this becomes a table.
    #
    #    Only for files. An inline `tool_evidence` IS an excerpt by definition
    #    (see its own field description in evaluate_dimension.md) and cannot be
    #    expected to parse.
    if tool == "scancode" and not inline:
        violations.extend(_scancode_provenance_problems(content, dim_name))

    return violations


def _scancode_provenance_problems(content: str, dim_name: str) -> list[str]:
    """Does this file say scancode produced it?

    Round 91. Asks about the output's ORIGIN rather than about words in its
    text, which is the difference that matters: measured over the corpus's
    twenty committed `license_compliance` evidence files, `headers[0].tool_name`
    separates them into four groups with nothing ambiguous in between —

        9  scancode-toolkit          genuine `--json-pp` output
        8  not JSON                  stdout/stderr interleaved, or truncated
        2  JSON without headers      an agent's own summary (one is 45 bytes)
        1  JSON that is not an object

    while a content-word rule passes all twenty and a "must be valid JSON" rule
    still passes the 45-byte summary. The remediation is named in each message
    because every one of the three failures has a different one.
    """
    what = f"{dim_name}: tool_output"
    try:
        doc = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return [
            f"{what} is not parseable JSON. `scancode --json-pp -` writes JSON "
            f"to stdout and its progress lines to stderr; a file holding both "
            f"is neither. Re-run with stderr separated "
            f"(`2>/dev/null`) and commit that output — this dimension's score "
            f"has no other backing"
        ]
    if not isinstance(doc, dict):
        return [
            f"{what} parses as {type(doc).__name__}, not a scancode document. "
            f"Commit the output of `scancode --license --json-pp -` itself"
        ]
    headers = doc.get("headers")
    if not (isinstance(headers, list) and headers and isinstance(headers[0], dict)):
        return [
            f"{what} is JSON with no `headers` block, so it cannot say which "
            f"tool produced it — a hand-written summary belongs in "
            f"`tool_evidence`, and `tool_output` has to be the tool's own file"
        ]
    produced_by = headers[0].get("tool_name")
    if produced_by != "scancode-toolkit":
        return [
            f"{what} reports tool_name={produced_by!r}, not 'scancode-toolkit' "
            f"— the score for this dimension is cited against output from "
            f"something else"
        ]
    return []




def _check_infra_fail_pollution(raw: dict) -> list[str]:
    """Round 12 站3b: INFRA_FAIL ≠ quality failure.

    A zero score whose evidence carries a run-gate PRECONDITION-block
    signature is not a measurement — the tool never ran. Writing it into
    the manifest as a quality zero poisons scoring history and dispatches
    code fixers at a non-code problem. Detect and reject the result
    outright so finalize-gate FATALs with an infra diagnosis instead.

    Round N: partial-pollution carve-out. If at least ONE evaluated dimension
    produced a real (non-zero) score with non-INFRA-pollution evidence, the
    run-gate DID execute end-to-end; the other dimensions' INFRA-block
    zeros are partial pollution (one SAB-phantom dimension aborts the run
    while the rest still score normally) and the whole verdict must NOT
    be blanket-rejected. The per-dim diagnostic message still surfaces via
    the partial-pollution diagnostics list so operators see the affected
    dimensions, but `finalize-gate` proceeds with the real PASS record for
    the cleanly-evaluated dimensions. Incident: taskq-plus FR-05 P3 (2026-07)
    — GATE1 hit `[BLOCKED] run-gate` for the SAB phantom dimension while
    7/8 other dimensions evaluated normally; blanket rejection discarded
    a real Gate 1 PASS verdict and the workflow escalated to human on
    false-positive grounds.
    """
    entries: list[tuple[str, float | None, str]] = []
    breakdown = raw.get("breakdown")
    if isinstance(breakdown, dict):
        for dim, row in breakdown.items():
            if isinstance(row, dict):
                _ev = " ".join(str(row.get(k, "")) for k in ("tool_evidence", "evidence"))
                entries.append((str(dim), row.get("score"), _ev))
    for row in raw.get("dimensions", []) or []:
        if isinstance(row, dict):
            _ev = " ".join(str(row.get(k, "")) for k in ("tool_evidence", "evidence"))
            entries.append((str(row.get("name", "?")), row.get("score"), _ev))
    # Partial-pollution carve-out: at least one dimension passed cleanly
    # (non-zero score AND its evidence contains no INFRA-fail signature).
    # When present, the gate DID run end-to-end — accept the verdict and
    # surface partial-pollution info via diagnostics rather than rejecting.
    has_real_pass = any(
        (score not in (0, 0.0, None))
        and not any(sig in (evidence or "") for sig in _INFRA_FAIL_EVIDENCE_SIGNATURES)
        for _, score, evidence in entries
    )
    violations: list[str] = []
    partial_diagnostics: list[str] = []
    for dim, score, evidence in entries:
        if not evidence:
            continue
        matched = [sig for sig in _INFRA_FAIL_EVIDENCE_SIGNATURES if sig in evidence]
        if matched and (score in (0, 0.0, None)):
            msg = (
                f"dimension {dim!r}: score={score} with run-gate PRECONDITION-block "
                f"evidence ({matched[0]!r}) — this is an INFRA failure, not a quality "
                f"measurement. Do NOT dispatch code fixes for it. Fix the precondition "
                f"run-gate reported (SAB phantom/unregistered module, manifest state), "
                f"re-run run-gate until its preconditions pass, then re-evaluate."
            )
            if has_real_pass:
                # Partial pollution — surface per-dim info but accept the whole verdict.
                partial_diagnostics.append(msg)
            else:
                # Whole-gate pollution — reject so finalize-gate FATALs with infra dx.
                violations.append(msg)
    # Attach partial diagnostics as a marker suffix so callers can still surface
    # them without treating them as blockers. The first violation (if any) carries
    # the diagnostics block; if no violations, append a synthetic diagnostic-only
    # entry prefixed with "[partial-pollution]" so it's distinguishable from the
    # whole-gate rejections (operators looking at finalize-gate output).
    if partial_diagnostics and not violations:
        violations.append(
            "[partial-pollution] " + " | ".join(partial_diagnostics)
            + " — accepted (at least one dimension PASSed cleanly); fix the "
            "SAB/manifest preconditions and re-run to clear the partial-pollution marker."
        )
    return violations




def _check_tool_evidence(ctx: "GateContext", raw: dict,
                         digests: "dict | None" = None) -> list[str]:
    """S3: Verify tool execution evidence in gate result JSON.

    When *digests* is supplied, every piece of evidence that PASSES验证 is
    fingerprinted into it (Round 27 站3). The digest is taken here rather than
    later because here is the only moment the evidence is known to exist and to
    be genuine — taskq-plus's Gate 4 cites 13 tool_output paths under the
    gitignored .sessi-work/, all of them gone now, while the verdict that read
    them is committed and permanent.

    For dimensions with requires_tool_execution:true in the gate YAML config,
    the result JSON breakdown entry MUST include either:
      - tool_output: path to a file containing raw tool stdout/stderr
      - tool_evidence: inline string of tool output snippet

    Additionally (S3-A), the content of tool_output files and tool_evidence
    strings is validated for structural authenticity — stub files and comment
    placeholders are rejected.

    Returns list of violation messages (empty = all good).
    """
    import yaml as _yaml
    from pathlib import Path as _Path

    # Round 29 Station 1: use the single-source-of-truth resolver instead of
    # project_root-relative globbing.  The old path (project/harness/gate_configs)
    # was one level too high when the harness is checked out as a git submodule
    # (the actual path is project/harness/harness/gate_configs).  SSOT resolver:
    # core.quality_gate.gate_thresholds.gate_config_path() — uses __file__ so it
    # always lands on the framework's own shipped configs.
    from core.quality_gate.gate_thresholds import gate_config_path as _gcp

    # Round 30 站3: gate_num comes from GateContext, which the framework builds
    # — a value outside 1-4 is a caller-contract violation, and Round 29 caught
    # the ValueError and returned `[]`, i.e. "no evidence violations found". The
    # raise now reaches the Round 28 crash boundary, which names the caller.
    cfg_path = _gcp(ctx.gate_num)

    if not cfg_path.exists():
        # Round 29 Station 1: gate configs are framework-owned assets tracked by
        # git ls-files.  Missing → checkout is corrupt.  Return a blocking
        # violation instead of silently returning [] (which the old code did
        # and was indistinguishable from "no violations").
        return [
            f"S3 gate config not found: {cfg_path} "
            f"(gate {ctx.gate_num}). Expected framework-owned asset — "
            f"is the harness checkout intact?"
        ]

    try:
        cfg = _yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except (_yaml.YAMLError, OSError) as exc:
        return [
            f"S3 gate config unreadable: {cfg_path} ({exc})"
        ]



    violations: list[str] = []
    breakdown = raw.get("breakdown", {})

    # Round 29 Station 6: exclusion files that alter a dimension's score
    # (e.g. .gitleaksignore for secrets_scanning) must themselves be in
    # version control.  An untracked exclusion file means the score on a
    # fresh clone would be different — the denominator is in the scorer's
    # hands, not the framework's.
    #
    # Round 30 站6: fingerprinted as well as tracked. `.gitleaksignore` is
    # committed and still the score moves when a line is added to it — the file
    # being in git says nothing about which version of it produced this verdict.
    # The digest goes into evidence_digest beside the tool outputs (Round 27
    # 站3's channel), so two verdicts scored under different exemption lists are
    # distinguishable from the artifacts alone.
    _excl_pairs: "list[tuple[str, str]]" = [
        (_dim, str(_f))
        for _dim, _spec in DIMENSION_EXCLUSION_FILES.items()
        if _spec is not None
        for _f in ((_spec,) if isinstance(_spec, str) else _spec)
    ]
    for _dim_name, _excl_file in _excl_pairs:
        _excl_path = _Path(ctx.project_root) / _excl_file
        if not _excl_path.is_file():
            continue
        if digests is not None:
            from core.quality_gate.evidence_digest import digest_of_file
            digests[f"{_dim_name}::{_excl_file}"] = digest_of_file(
                _excl_path, source=f"{_excl_file} (score-altering exclusions)"
            )
        _project_root_path = _Path(ctx.project_root)
        import subprocess as _sp  # bound before the try: the except reads it
        try:
            _tracked = _sp.run(
                ["git", "ls-files", "--error-unmatch", _excl_file],
                cwd=str(_project_root_path),
                capture_output=True, text=True, timeout=10,
            )
            if _tracked.returncode != 0:
                violations.append(
                    f"S6 {_excl_file} exists but is not tracked by git — "
                    f"the {_dim_name} score depends on an exclusion file "
                    f"that is absent on a fresh clone. "
                    f"Either commit it or remove the exclusion entries."
                )
        except (OSError, _sp.SubprocessError) as _git_exc:
            # Round 30 站3: git is a HARD dependency of this framework —
            # enforcer_sha, state.json's phase_completed[].sha and every hook
            # need it. Round 29 wrote this as `except Exception` into a
            # logging.debug nobody reads, so a check that could not run was
            # indistinguishable from a check that found nothing. It still must
            # not block a gate on a provenance-adjacent failure, so it records
            # and continues — the ledger is where "we could not check" lives.
            from core.degradation_ledger import record_degradation
            record_degradation(
                str(_project_root_path), "gate:S6-exclusion-vcs",
                f"could not verify {_excl_file} is tracked by git ({_git_exc})",
                why=f"the {_dim_name} score was accepted without its exclusion "
                    f"file being checked into version control", owner="harness"
            )

    # Evidence format patterns are keyed by the RESOLVED tool id — a TS
    # project's linting evidence is eslint JSON, not ruff output.
    from harness.toolchains import (
        get_project_language,
        get_project_test_runner,
        resolve_tool_id,
    )
    _language = get_project_language(ctx.project_root)
    _test_runner = get_project_test_runner(ctx.project_root)

    for dim in cfg.get("dimensions", []):
        dim_name = dim.get("name", "")
        requires_tool = dim.get("requires_tool_execution", False)
        if not requires_tool:
            continue

        tool = dim.get("tool")
        if _language != "python" and tool:
            tool = resolve_tool_id(
                dim_name, _language, yaml_tool=tool, test_runner=_test_runner
            ) or tool
        dim_data = breakdown.get(dim_name, {})
        tool_output = dim_data.get("tool_output")
        tool_evidence = dim_data.get("tool_evidence")

        if tool_output:
            out_path = _Path(ctx.project_root) / tool_output
            # Containment check: refuse to read any tool_output that
            # resolves outside project_root. An agent writing
            # `../../etc/passwd` (or an absolute path, or a symlink to
            # outside) into the gate result JSON must not be silently
            # read by the audit cross-check.
            try:
                if path_escapes_root(out_path, _Path(ctx.project_root)):
                    violations.append(
                        f"{dim_name}: tool_output path '{tool_output}' "
                        f"escapes project root — refusing to read"
                    )
                    continue
            except (OSError, RuntimeError) as exc:
                violations.append(
                    f"{dim_name}: tool_output path '{tool_output}' "
                    f"cannot be resolved: {exc}"
                )
                continue
            if not out_path.exists():
                violations.append(
                    f"{dim_name}: tool_output path '{tool_output}' does not exist"
                )
            else:
                try:
                    content = out_path.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    violations.append(f"{dim_name}: cannot read tool_output file: {exc}")
                    continue
                _content_problems = _validate_tool_content(
                    content, tool, dim_name, inline=False
                )
                violations.extend(_content_problems)
                if digests is not None and not _content_problems:
                    from core.quality_gate.evidence_digest import digest_of_file
                    digests[dim_name] = digest_of_file(out_path, source=str(tool_output))
        elif tool_evidence:
            evidence_str = str(tool_evidence).strip()
            if len(evidence_str) < 10:
                violations.append(
                    f"{dim_name}: tool_evidence too short "
                    f"({len(evidence_str)} chars) — must be real tool output snippet"
                )
            else:
                _content_problems = _validate_tool_content(
                    evidence_str, tool, dim_name, inline=True
                )
                violations.extend(_content_problems)
                if digests is not None and not _content_problems:
                    from core.quality_gate.evidence_digest import digest_of_text
                    digests[dim_name] = digest_of_text(
                        evidence_str, source="tool_evidence (inline)"
                    )
        else:
            violations.append(
                f"{dim_name}: requires tool execution but result JSON has neither "
                f"tool_output nor tool_evidence — scores must come from actual tool runs"
            )

    return violations


#: The `details` key a red suite blocks under.
#:
#: Round 96. It used to be `tool_score_fabrication`, whose registered headline
#: reads "Claimed dimension score could not be reproduced by running the tool"
#: — which is not what happened. The agent claimed nothing; its own tests are
#: failing. `core.lessons.record_gate_block` files the block under the same
#: key, so the cross-run memory recorded the wrong defect too.
#:
#: The rule this restores is already written forty lines below, in
#: `_mutation_artifact_verdict`'s docstring: Round 35 站3 split `infra_fail`
#: out of `tool_score_fabrication` "because the outcomes carry opposite
#: instructions". `tool_score_fabrication` says do NOT re-run — the score is
#: what failed. A red suite is the opposite instruction: run it, and run it
#: the way the harness did.
#:
#: Measured on taskq-final's Phase 8: 22 rounds, 9.5 hours, twenty-two
#: identical `declared=0 measured=5` ledger rows, and the fixer dispatched
#: every time was COVERAGE-FIX.
RED_SUITE_DETAIL_KEY = "fr_tests_red"


def _check_tests_failed(
    raw: dict, fr_id: "str | None" = None,
    *, framework_run: "tuple[str, str, int] | None" = None,
) -> list[str]:
    """S4-B: Verify none of THIS FR's own tests are red.

    S4 cross-validates the coverage *percentage* — `_score_pytest(coverage=True)`
    reads `TOTAL … N%` and never looks at how many tests failed — so a gate
    could pass at 91% coverage with 5 red tests. That is what this check is for.

    Round 77 站1: it asks the run S4 just performed. S4 executes `pytest-cov`
    itself (gate1_per_fr.yaml declares `requires_tool_execution: true` for
    test_coverage) and holds the full output; until this round S4-B decided the
    same question by regex over the agent's 500-character `tool_evidence`
    excerpt forty lines later. Round 67 / Round 72's mother pattern: the
    framework computed the truth and the verdict read somewhere else.

    Three cases, and none of them treats unreadable output as clean:

    (a) the harness ran a pytest-family tool and its short summary reconciles
        with its own counts line — the verdict is the framework's, scoped by
        `test_suite_run.select_fr_outcomes` (the same predicate `fr_suite_verdict`
        uses for TDD-GREEN, so the convention has one implementation). The
        agent's `tool_evidence` does not enter into it.
    (b) the harness ran a test tool whose per-test outcomes it cannot read —
        a JS runner, or pytest output it could not reconcile.
    (c) the harness did not run the tool (the agent self-reported below
        threshold, so S4 skipped it).

    (b) and (c) keep the pre-Round-76 rule unchanged: any `N failed` in the
    agent's evidence blocks. That rule is fail-closed, and deliberately not
    replaced with the framework's own whole-suite output for JS — a JS run is
    not per-FR scoped, so blocking on it would hand JS projects the exact
    defect this round removes from Python ones. Round 77 站5 adds the agent's
    own `tests_failed` to that branch: where the framework cannot see, a
    self-declared failure is an admission, and until this round the one field
    the prompt calls REQUIRED had no reader anywhere in the tree.

    Returns list of violation messages (empty = all clear).
    """
    from core.quality_gate.fr_test_scope import (
        declared_tests_failed,
        scoped_test_failures,
    )

    scoped = scoped_test_failures(fr_id, framework_run)
    if scoped is not None:
        mine = scoped[0]
        if mine:
            return [
                f"test_coverage: {len(mine)} of {str(fr_id).strip()}'s own "
                f"test(s) FAILED in the harness's own run — gate cannot pass "
                f"while they are red: {', '.join(sorted(mine))}"
            ]
        return []

    _declared = declared_tests_failed(raw)
    if _declared:
        return [
            f"test_coverage: the result declares tests_failed={_declared} and "
            f"the harness could not measure this FR's tests itself — a gate "
            f"cannot pass on a self-reported red suite. Fix the failures, or "
            f"cite a run the harness can read."
        ]

    breakdown = raw.get("breakdown", {})
    evidence = str(breakdown.get("test_coverage", {}).get("tool_evidence", "") or "")
    if not evidence:
        return []  # S3 already blocks on missing evidence

    m = re.search(r"(\d+)\s+failed", evidence)
    if m and int(m.group(1)) > 0:
        failed = int(m.group(1))
        return [
            f"test_coverage: {failed} test(s) FAILED in tool_evidence — "
            f"gate cannot pass with failing tests. Fix all failures before re-submitting."
        ]
    return []


def _parse_skip_counts(
    raw: dict, framework_run: "tuple[str, str, int] | None" = None,
) -> "tuple[int, int] | None":
    """`(skipped, total)` from the framework's own run, else from the evidence.

    One parse, two readers: the ratio WARN below and the ledger row at the
    finalize call site. Round 46 站2 split them apart because they answer
    different questions — "is coverage computed from a subset?" has a ratio
    threshold, "did any test not run?" does not.

    Round 77 站6: when S4 ran a test tool itself, that run is the source. The
    coverage number this ratio qualifies already comes from it —
    `_score_pytest` reads `TOTAL … N%` out of the same stdout — so numerator
    and denominator now come from one execution rather than two (Round 37 /
    Round 42: the denominator travels with the number). The scope changes
    with the source: the framework's run is the whole suite, the agent's
    excerpt was its per-FR scoped run, and the ledger row records which one
    it read.

    It also removes a way the row could vanish. Round 76 told the agent to put
    the FAILED lines in `tool_evidence` "before the summary line", inside a
    field the same prompt caps at 500 characters; measured, that evicts
    `N passed / N skipped` entirely and this function returns None — so the
    `gate:test-skips` row disappeared for exactly the FRs that had failing
    tests. Round 77 站3 removed the instruction; this removes the dependency.
    """
    from core.quality_gate.fr_test_scope import readable_run_output

    evidence = readable_run_output(framework_run)
    if not evidence:
        breakdown = raw.get("breakdown", {})
        evidence = str(
            breakdown.get("test_coverage", {}).get("tool_evidence", "") or "")
    if not evidence:
        return None
    passed_m = re.search(r"(\d+)\s+passed", evidence)
    skipped_m = re.search(r"(\d+)\s+skipped", evidence)
    if not (passed_m and skipped_m):
        return None
    passed = int(passed_m.group(1))
    skipped = int(skipped_m.group(1))
    total = passed + skipped
    return (skipped, total) if total else None


def _check_test_skip_ratio(
    raw: dict, threshold: float = 0.10,
    framework_run: "tuple[str, str, int] | None" = None,
) -> str | None:
    """W1: Warn when a high fraction of tests are skipped.

    Skipped tests contribute 0 coverage lines.  A skip ratio above *threshold*
    (default 10 %) means coverage is computed from a subset of the suite and
    may miss infrastructure code paths (e.g. DB schema, async sessions).

    This is a **WARN** (not BLOCK) — some projects legitimately skip tests
    that require real external services.

    Scope note (Round 46 站2): this is a statement about *coverage*, and about
    coverage it is honest. It is NOT the enforcer for "a requirement's own
    test did not run" — that is `compute_trace_dimension`'s absent-witness
    rule, which blocks through the traceability dimension. taskq-advance's
    17 skips are 6.25 % of its suite and never tripped this warning, while
    three of its NFRs had guards skipping themselves. Two questions, two
    mechanisms; do not make this one carry the other's weight.

    Returns a warning string, or ``None`` if the skip ratio is within threshold.
    """
    counts = _parse_skip_counts(raw, framework_run)
    if counts is None:
        return None
    skipped, total = counts

    skip_ratio = skipped / total
    if skip_ratio > threshold:
        return (
            f"[WARN] {skipped} of {total} tests ({skip_ratio:.0%}) are SKIPPED — "
            f"skipped tests contribute 0 coverage lines. Coverage score reflects only "
            f"non-skipped tests. Consider mocking infrastructure to run skipped tests, "
            f"or document why the skips are architectural constraints in TODO.md."
        )
    return None


def _gate_dimension_names(ctx: "GateContext") -> frozenset[str]:
    """The dimension names this gate's config declares.

    Round 53 站5a. `ctx.config` is a GateConfig or a plain dict depending on
    the caller, and two places in `finalize_gate` already branch on that to
    get the full entries. This returns names only, and stays separate from
    those two on purpose: they are inline inside long functions and need the
    entries, so folding them together would trade one duplicated branch for a
    parameter that means "which shape do you want".
    """
    if isinstance(ctx.config, dict):
        entries = ctx.config.get("dimensions") or []
    else:
        entries = getattr(ctx.config, "dimensions", None) or []
    names: set[str] = set()
    for entry in entries:
        name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", None)
        if name:
            names.add(str(name))
    return frozenset(names)


def _verify_system_reach_block(ctx: "GateContext") -> list[str]:
    """Which replaced boundaries `make verify-system` did not execute for real.

    Round 52 站2. Round 51 站3 recorded that a dimension was scored over a
    suite which replaced a SAB high-risk module before every test in the file,
    and let the number stand with a marker. The obligation this raises is the
    one thing the framework can still ask: the project's own verification
    target — the only command it runs that the test suite did not configure —
    has to execute what the suite replaced.

    Returns [] when there is nothing outstanding AND when the reach could not
    be measured; the ledger row carries the difference. A gate must not be
    blocked by a measurement that did not happen (Round 35 站2), and it must
    not read a measurement that did not happen as a pass either — which is why
    `unmet_obligations` omits the key rather than returning [], and why this
    function branches on the status instead of on the list.

    Never raises: a report about coverage instrumentation is a worse reason to
    stop a gate than the thing it was going to report.
    """
    from core.degradation_ledger import record_degradation
    from core.quality_gate.verify_system_reach import (
        STATUS_MEASURED,
        unmet_obligations,
    )

    # Round 53 站5a: only a gate that runs `execute_verification_target` can
    # have a reach artifact, so only such a gate has this question. Measured on
    # taskq-super's full P1-P8 run: 116 `gate:verify-system-reach` rows, every
    # one "no reach artifact", and correlating each row's `ts` against
    # gate_timestamps.jsonl puts ALL 116 at Gate 1 and none at Gate 2, 3 or 4 —
    # 18.5% of that project's degradation ledger, filed under owner `harness`,
    # asking a gate a question its own config says it cannot answer.
    #
    # Not "quieten the log". Round 46 站1's rule is that abstaining is not
    # passing; a question that was never in this gate's scope was never
    # abstained from, and the gate config is the single source of what a gate's
    # scope is.
    if "execute_verification_target" not in _gate_dimension_names(ctx):
        return []

    try:
        verdict = unmet_obligations(ctx.project_root)
    except Exception as exc:  # pragma: no cover — reporting must not stop a gate
        record_degradation(
            ctx.project_root, "gate:verify-system-reach",
            "reach obligation check failed", f"{type(exc).__name__}: {exc}",
            owner="harness",
        )
        return []

    if verdict["status"] != STATUS_MEASURED:
        record_degradation(
            ctx.project_root, "gate:verify-system-reach",
            "which boundaries `make verify-system` executed is unknown",
            verdict["reason"], owner="harness",
        )
        return []

    for row in verdict.get("unmeasurable") or []:
        record_degradation(
            ctx.project_root, "gate:verify-system-reach",
            f"obligation {row['module']}.{row['attr']} cannot be evaluated",
            row["why"], owner="harness",
        )

    return [
        f"{row['module']}.{row['attr']} is replaced by an autouse fixture in "
        f"the test suite and is never executed by `make verify-system`"
        for row in verdict.get("unmet") or []
    ]


# ── mutation_testing: the score is the framework's, or the gate blocks ──
# Round 80 站13. Moved from harness_bridge byte-identical. It asks this
# file's question — is what the agent supplied for this dimension actually
# evidence? — of the one dimension the framework measures end to end
# itself, so it belongs with the other six rather than beside the gate
# loop that calls it.


# ---------------------------------------------------------------------------
def _mutation_artifact_violations(
    ctx: "GateContext", dim_name: str, agent_score: "float | None",
    threshold: float,
) -> "tuple[list[str], list[str]]":
    """S4 for mutation_testing: the score is the framework's, or the gate blocks.

    Round 31 站2. mutmut is the one tool the framework runs end-to-end itself —
    temp workdir, setup.cfg rewrite, interpreter pinning, scope from the SAB —
    and `compute_mutation_score` is where the authoritative number comes out of
    the sqlite cache. It had zero production callers. What reached a live
    Gate 2 instead was an agent-written prose file that passed content
    validation because the mutmut pattern list contains the bare word "mutmut",
    carrying a number nothing could check.

    So the framework's own artifact is the source. Returns
    ``(fabrication, unverifiable)`` — Round 35 站3 split them, because the
    outcomes carry opposite instructions and all of them used to be filed as
    `tool_score_fabrication`, whose registered remediation reads "the score,
    not the run, is what failed — do NOT re-run". For a missing artifact the
    correct action is precisely to run the command.

    * absent / unreadable / malformed → `infra_fail`, naming the command that
      writes it. Abstaining is not passing (Round 30): "we could not establish
      the score" must never resolve to "the claimed score stands".
    * present with `score: null` → `infra_fail`, carrying the reason the
      framework recorded. It ran and could not measure; nothing about the
      project's tests has been established (Round 35 站2).
    * present, and the framework's score clears the threshold → fine, whatever
      the agent wrote; the caller patches the real number into the breakdown.
    * present, framework's score BELOW threshold while the agent claimed a
      pass → `tool_score_fabrication`. That is the same rule S4 applies to
      every other tool (harness says fail, agent says pass), with the artifact
      standing in for a re-run that would cost an hour.
    """
    from core.quality_gate.mutation_enforcer import (
        MUTATION_SCORE_ARTIFACT,
        MUTATION_SCORE_PROVENANCE_KEY,
    )

    _how = (
        f"Run `python3 harness_cli.py mutation-test-score --project .` — it "
        f"runs mutmut with the framework's workdir isolation and writes "
        f"{MUTATION_SCORE_ARTIFACT}. Do not run `mutmut run` yourself."
    )
    path = Path(ctx.project_root) / MUTATION_SCORE_ARTIFACT
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw_score = data["score"]
        framework_score = None if raw_score is None else float(raw_score)
    except FileNotFoundError:
        return [], [
            f"{dim_name}: no framework-computed score — {MUTATION_SCORE_ARTIFACT} "
            f"is missing, so the recorded score is whatever the agent wrote. "
            f"{_how}"
        ]
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return [], [
            f"{dim_name}: {MUTATION_SCORE_ARTIFACT} is unreadable ({exc}) — the "
            f"score it should carry cannot be established. {_how}"
        ]

    # Round 72 站2: and whether this framework wrote it. Both writers stamp
    # `enforcer_sha` (Round 19 站3's provenance field) into the payload, and
    # every reader ignored it — so a file with the right `score` key passed as
    # "the framework's own artifact", which is what the docstring above claims
    # this function establishes.
    #
    # Measured on taskq-new's Gate 4: no `enforcer_sha`, a `generated_at` of
    # "2026-08-23T14:51:22.f+00:00" (a strftime placeholder this code cannot
    # emit) and a `note` reading "reconstructed from .mutmut-cache … 685
    # untested mutants are out-of-scope". 256/(256+99) = 72.1 cleared the
    # threshold of 70; 256/(256+99+685) is 24.6. gate4_result.json records it
    # as "framework: compute_mutation_score → …" with framework_override: true.
    #
    # Filed as `unverifiable`, the same bucket as an absent file, because it is
    # the same fact: no score the framework produced is on record. It is not
    # `tool_score_fabrication`, whose remediation reads "the score, not the
    # run, is what failed — do NOT re-run"; here re-running is exactly right.
    if MUTATION_SCORE_PROVENANCE_KEY not in data:
        return [], [
            f"{dim_name}: {MUTATION_SCORE_ARTIFACT} carries no "
            f"`{MUTATION_SCORE_PROVENANCE_KEY}`, so this framework did not "
            f"write it — the number in it is whatever its author typed, not a "
            f"measurement anything here performed. {_how}"
        ]

    # Round 31 站4: the score is only meaningful over the scope it was taken
    # on. The generator runs once at the P2→P3 handoff, so a SAB corrected
    # mid-P3 — the normal way a missing scope_layers gets noticed, since Gate 2
    # is where the cost shows up — leaves setup.cfg saying something else.
    #
    # Round 35 站3: this used to sit below the null check, so on the one
    # occasion the scope is the likeliest cause — a run that could not measure
    # — it was never evaluated. Measured on taskq-renew, where setup.cfg
    # declares no scope while the SAB names two layers, and nothing said so.
    from core.quality_gate.mutmut_scope import scope_drift
    drift = scope_drift(ctx.project_root)
    if drift:
        return [], [f"{dim_name}: mutation scope disagrees with the SAB — {drift}"]

    if framework_score is None:
        return [], [
            f"{dim_name}: the framework ran mutmut and could not measure — "
            f"{data.get('could_not_measure') or 'no reason recorded'}. Nothing "
            f"has been established about this project's tests, so do not touch "
            f"the score: repair the run. {_how}"
        ]

    if framework_score < threshold and (
        agent_score is None or agent_score >= threshold
    ):
        _claim = "N/A (agent)" if agent_score is None else f"{agent_score:.1f}"
        return [
            f"{dim_name}: framework-computed score {framework_score:.1f} is "
            f"below threshold {threshold:.0f}, but the gate result claims "
            f"{_claim}. The framework's own run is the score for this "
            f"dimension; write {framework_score:.1f} and fix the tests that "
            f"let those mutants live."
        ], []

    print(
        f"  [S4] {dim_name}: framework-computed score {framework_score:.1f} "
        f"[scope: {data.get('paths_to_mutate', '?')}, "
        f"{data.get('mutated_files', '?')} files] ✓"
    )
    return [], []
