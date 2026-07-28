"""Phase 6 (Quality Assurance) workflow assembly — Round 15 station3
extraction from the former monolithic phase_specs.py. See
scripts/workflowgen/spec_shared.py for the cross-phase _render_meta.
"""
from __future__ import annotations

from . import js_blocks as B
from .spec_shared import _render_meta

_HEADER_6 = """\
// Phase 6 — Quality Assurance (faithful to .methodology/phase6_plan.md v2.12.0)
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/phase_specs.py::generate_phase6() (+ js_blocks.py for
// the blocks shared across phase workflow files). Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write --phase 6
//
// Structure: NO FR loop. Gate 4 (14 dims, tool-scored + artifact-backed DA
// challenge for Tier 3 dims) PLUS Agent B peer review of the QA deliverables
// (both required to exit). Then release notes + final sign-off + git tag + advance.
//
// Playbook lessons: NO import/fs/process, Bash CLI, SCOPE RULES,
// PY = .venv/bin/python, scriptPath launch.
// v4 (2026-07-02): gate verdicts use FLAT schema: (playbook §5.2 rev) — regex
// over LLM prose was the root cause of the #126/#134/#135/#136/ENV_CHECK_RC
// bug class. Heavy orchestrators keep prose narrative; verdicts come from
// schema proxy agents reading harness artifacts (manifest qc, state.json).
// EXCEPTION kept as prose+parser: Peer Review's complex nested verdicts JSON
// (the original v2 schema failure case — heavy-cognition agent + big schema).
"""

_META_PHASES_6 = [
    "Entry & Preflight", "Gate 4", "Release Docs",
    "Peer Review", "Tag & Advance", "Sync",
]

_GATE4_STEPS = [
    "1. G4a: `' + PY + ' ' + REPO + '/harness_cli.py run-gate --gate 4 --phase 6 --project ' + REPO + '` (CRG recon runs inside). Read the printed prompt.",
    (
        "2. A3 DA CHALLENGE (artifact-backed — finalize-gate validates this BEFORE scoring): for EACH Tier 3 dim (architecture, readability, error_handling, documentation, performance), dispatch a Claude sub-agent (you have the Agent tool) with a CHALLENGER persona that critiques the design/score, then record its critique + your defence. Dispatch each challenger SYNCHRONOUSLY — call the Agent tool and wait for its return before the next; do NOT run challengers in the background and busy-poll with `sleep`/`cat *.output` (that blows the per-agent wall-clock budget and stalls the round). Write into .sessi-work/gate4_result.json:\\n"
        "   \"devil_advocate\": {\"architecture\":true,\"readability\":true,\"error_handling\":true,\"documentation\":true,\"performance\":true},\\n"
        "   \"devil_advocate_evidence\": {\"<dim>\": {\"challenger_model\":\"claude\",\"challenge\":\"<≥120 chars actual critique>\",\"response\":\"<≥120 chars defence>\"}, ...}.\\n"
        "   A bare boolean is NOT accepted. If architecture/error_handling score 0 due to Orchestrator hub-and-spoke: also add \"da_waiver\": {\"architecture\": true} (requires the matching evidence artifact)."
    ),
    (
        "3. G4b: Evaluate all 14 dims inline per ' + REPO + '/harness/harness/ssi/prompts/evaluate_dimension.md → .sessi-work/gate4_result.json.\\n"
        "   Dims: linting(90) type_safety(85) test_coverage(80) security(80) secrets_scanning(100) license_compliance(100) architecture(80) readability(80) error_handling(80) documentation(75) performance(75) integration_coverage(75) test_assertion_quality(70).\\n"
        "   NOTE: mutation_testing is disabled by default via .methodology/harness_config.json (mutation_testing=false). If enabled, the harness auto-includes it and re-normalises the composite score.\\n"
        "   FRAMEWORK-OWNED (do NOT self-score): traceability + architecture (CRG override). Fix failing dims at ROOT CAUSE in code."
    ),
    "4. D4: `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 90.0`. FAIL → add tests, re-run. Runs BEFORE G4c so any fix here is captured by the G4c commit (Round 26: a D4 fix landing AFTER finalize-gate committed had no downstream commit step and was left uncommitted).",
    (
        "5. G4c: `' + PY + ' ' + REPO + '/harness_cli.py finalize-gate --gate 4 --phase 6 --project ' + REPO + '` (writes QUALITY_REPORT.md + HANDOVER.md + pushes on PASS; also the commit point for any code/test fixes from steps 3-4 above).\\n"
    ),
]

_GATE4_SCOPE_RULES = (
    "- DO NOT generate RELEASE_NOTES/FINAL_SIGN_OFF (next phase) or run advance-phase / git tag.\\n"
    "- DO NOT edit gate4_result.json scores to fake them — fix code (DA evidence is the only hand-authored part).\\n"
    "- DO NOT hand-write or rewrite 06-quality/QUALITY_REPORT.md — finalize-gate is its sole author and now renders DA-waiver dimensions correctly (raw score + PASS (DA-waiver)); a hand-edited copy only creates an uncommitted second source.\\n"
    "- DO NOT run scripts/build_traceability.py directly against the project root, and DO NOT hand-author TRACEABILITY_MATRIX.overlay.yaml overrides — the canonical matrix is 01-requirements/TRACEABILITY_MATRIX.md, auto-refreshed by advance-phase; a root-level copy or a hand-written overlay only creates an untracked duplicate with no effect on this gate.\\n"
    "- DO NOT modify harness/ (HR-17).\\n"
    "- ONLY run-gate/DA-challenge/eval/finalize/spec-coverage + code fixes."
)


def _render_phase6_entry_preflight() -> str:
    return (
        B.render_phase_header("Entry & Preflight")
        + "log('ENTRY-CHECK Gate3 + P5 artifacts + D4-precheck 90% + run-phase 6 + handoff + CI')\n"
        + "const preflightReport = await agent(\n"
        + "  'YOU ARE THE PHASE-6 PREFLIGHT ORCHESTRATOR. Run bash in order; report.\\n'\n"
        + "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "  + 'Steps:\\n'\n"
        + "  + '1. ENTRY-CHECK: run EXACTLY this bash command to verify Gate 3 status (do NOT rely on reading the file yourself — use the command output):\\n`' + PY + ' -c \"import json; m=json.load(open(\\'' + REPO + '/.methodology/quality_manifest.json\\')); g3=(m.get(\\'gate_results\\',{}) or {}).get(\\'gate3\\',{}) or {}; print(\\'GATE_VERIFIED\\' if isinstance(g3,dict) and g3.get(\\'quality_complete\\') is True else \\'GATE_MISSING\\')\"`\\nIf GATE_MISSING → FAIL (return to Phase 4).\\n'\n"
        + "  + '2. D4-PRECHECK: `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 90.0`. Gate 4 blocks at 90% — if below, ADD missing test implementations NOW. Do NOT proceed until this passes.\\n'\n"
        + "  + '3. PREFLIGHT: `' + PY + ' ' + REPO + '/harness_cli.py run-phase --phase 6 --project ' + REPO + '`. FAIL → fix, re-run (max 3). Also fix if reported: reliability lint (subprocess timeout / mkstemp / TOCTOU / sleep-in-async), config liveness (env keys absent from .env.example), attestation missing/mismatch (build-trace-attestation --write + commit; re-run until \"Attestation: clean\").\\n'\n"
        + "  + '4. HANDOFF: `' + PY + ' ' + REPO + '/harness_cli.py validate-handoff --from-phase 5 --project ' + REPO + '`. Must exit 0.\\n'\n"
        + "  + '5. PREFLIGHT-CI: confirm `' + REPO + '/.github/workflows/harness_quality_gate.yml` (CI workflow) + `' + REPO + '/.git/hooks/prepare-commit-msg` (git hook) both exist; confirm state.json current_phase=6. If stale: `init-project --phase 6 --project ' + REPO + ' --overwrite`.\\n'\n"
        + "  + '6. PHASE-CONTEXT (load-context): `mkdir -p ' + REPO + '/.sessi-work && ' + PY + ' ' + REPO + '/harness_cli.py load-context --phase 6 --project ' + REPO + ' --json > ' + REPO + '/.sessi-work/phase6_ctx.json`.\\n\\n'\n"
        + "  + '7. READ THE LESSONS BLOCK (advisory, not a gate): Bash `cat ' + REPO + '/.sessi-work/phase6_ctx.json` and READ the `lessons` field (compact markdown, \"\" if none). DO NOT repeat those past failure modes in this preflight or any follow-up P6 work. (Direction C — past lessons injection)\\n\\n'\n"
        + "  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if ALL 6 must-succeed steps succeeded; step 7 is read-only advisory. reason = one-line summary (on FAIL: which step + verbatim error tail).\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n- DO NOT run run-gate / generate release docs / peer review.\\n- DO NOT run advance-phase / git tag.\\n- DO NOT modify harness/.\\n- ONLY preflight commands + load-context + spec-coverage fixes.',\n"
        + "  { label: 'preflight', phase: 'Entry & Preflight', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(preflightReport && preflightReport.pass === true)) {\n"
        + "  return { error: 'Phase 6 preflight did not PASS', reason: preflightReport ? String(preflightReport.reason ?? '').slice(-600) : 'agent returned null (skipped or terminal API error)' }\n"
        + "}\n"
    )


def _render_phase6_release_docs() -> str:
    return (
        B.render_phase_header("Release Docs")
        + "log('Generate RELEASE_NOTES.md + FINAL_SIGN_OFF.md (reference Gate 4 score + provenance)')\n"
        + "const releaseReport = await agent(\n"
        + "  'YOU ARE THE P6 RELEASE AUTHOR. Generate the release deliverables (after Gate 4 PASS).\\n'\n"
        + "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "  + 'Steps:\\n'\n"
        + "  + '1. G4e RELEASE_NOTES: write ' + REPO + '/RELEASE_NOTES.md (project root). Summarise changes since Gate 3. Include: version, date, FR list, Gate 4 composite score (read from .methodology/quality_manifest.json — persistent SoT, per phase6_plan.md v2.12.0), known limitations. Reference 06-quality/QUALITY_REPORT.md (auto-generated by G4c).\\n'\n"
        + "  + '2. G4f FINAL_SIGN_OFF: write ' + REPO + '/FINAL_SIGN_OFF.md (project root). Include: project name, completion date, Gate 4 composite score, sign-off statement. MUST reference 05-verification/VERIFICATION_REPORT.md (verification provenance) and 05-verification/BASELINE.md (P5 system baseline).\\n\\n'\n"
        + "  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if both docs were written with the required references; reason = one-line summary.\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n- DO NOT run advance-phase / git tag / peer review dispatch.\\n- DO NOT modify harness/.\\n- DO NOT re-run Gate 4.\\n- ONLY generate RELEASE_NOTES.md + FINAL_SIGN_OFF.md.',\n"
        + "  { label: 'release-docs', phase: 'Release Docs', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(releaseReport && releaseReport.pass === true)) {\n"
        + "  return { error: 'Phase 6 release docs did not PASS', reason: releaseReport ? String(releaseReport.reason ?? '').slice(-500) : 'agent returned null' }\n"
        + "}\n"
    )


def _render_phase6_peer_review() -> str:
    return (
        B.render_phase_header("Peer Review")
        + "log('Agent B reviews 4 deliverables; workflow writes 4 approval JSON via persistApproval (Class C)')\n"
        + "\n"
        + "// v22-era 4 deliverables advanced-phase expects (harness_cli.py:_PHASE_DELIVERABLES[6]).\n"
        + "const peerDeliverables = ['QUALITY_REPORT.md', 'RELEASE_NOTES.md', 'FINAL_SIGN_OFF.md', 'quality_manifest']\n"
        + "\n"
        + "let peerVerdict = null\n"
        + "for (let attempt = 1; attempt <= MAX_OUTER_ATTEMPTS_PEER; attempt++) {\n"
        + "  const peerReport = await agent(\n"
        + "    'YOU ARE AGENT B (TECH_LEAD reviewer) for the Phase 6 Gate 4 deliverables (HR-01).\\n'\n"
        + "    + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "    + 'Steps:\\n'\n"
        + "    + '1. Review 06-quality/QUALITY_REPORT.md, RELEASE_NOTES.md, FINAL_SIGN_OFF.md (read them via Bash cat for exact content).\\n'\n"
        + "    + '2. Cross-check .methodology/quality_manifest.json Gate 4 scoring logic. Reference 05-verification/VERIFICATION_REPORT.md and 05-verification/BASELINE.md for historical traceability.\\n'\n"
        + "    + '3. If any deliverable warrants REJECT or has medium/high gaps: fix the deliverable (or escalate), then re-review.\\n\\n'\n"
        + "    + 'Output ONLY a single JSON object (no other text, no markdown fences) in your final message:\\n'\n"
        + "    + '{\"verdicts\": [\\n'\n"
        + "    + '  {\"deliverable\":\"QUALITY_REPORT.md\",\"review_status\":\"APPROVE\",\"reason\":\"<concise>\",\"citations\":[\"file:line\"],\"docs_embedded\":[\"QUALITY_REPORT.md\",\"RELEASE_NOTES.md\",\"FINAL_SIGN_OFF.md\",\"VERIFICATION_REPORT.md\"],\"gaps\":[]},\\n'\n"
        + "    + '  {\"deliverable\":\"RELEASE_NOTES.md\",\"review_status\":\"APPROVE\",\"reason\":\"<concise>\",\"citations\":[\"file:line\"],\"docs_embedded\":[\"QUALITY_REPORT.md\",\"RELEASE_NOTES.md\",\"FINAL_SIGN_OFF.md\",\"VERIFICATION_REPORT.md\"],\"gaps\":[]},\\n'\n"
        + "    + '  {\"deliverable\":\"FINAL_SIGN_OFF.md\",\"review_status\":\"APPROVE\",\"reason\":\"<concise>\",\"citations\":[\"file:line\"],\"docs_embedded\":[\"QUALITY_REPORT.md\",\"RELEASE_NOTES.md\",\"FINAL_SIGN_OFF.md\",\"VERIFICATION_REPORT.md\"],\"gaps\":[]},\\n'\n"
        + "    + '  {\"deliverable\":\"quality_manifest\",\"review_status\":\"APPROVE\",\"reason\":\"<concise>\",\"citations\":[\"file:line\"],\"docs_embedded\":[\"QUALITY_REPORT.md\",\"RELEASE_NOTES.md\",\"FINAL_SIGN_OFF.md\",\"VERIFICATION_REPORT.md\"],\"gaps\":[]}\\n'\n"
        + "    + ']}\\n'\n"
        + "    + 'CRITICAL: \"docs_embedded\" must list ALL 4 required embedded docs (QUALITY_REPORT.md, RELEASE_NOTES.md, FINAL_SIGN_OFF.md, VERIFICATION_REPORT.md) — NOT just the deliverable being reviewed. The harness _verify_agent_b_approvals_core checks every verdict includes every required doc (Bug v26 basename-match contract).\\n'\n"
        + "    + 'Each \"reason\" must be ≥100 chars of substantive justification (not \"APPROVE\" or one-word). Each \"gaps\" array is empty when review_status is APPROVE. Each \"citations\" must include ≥1 file:line you actually cat-ed.\\n\\n'\n"
        + "    + 'SCOPE RULES:\\n- DO NOT run advance-phase / git tag / run-gate.\\n- DO NOT modify harness/ (HR-17).\\n- DO NOT write any files (workflow writes approval JSON; you only review content).',\n"
        + "    { label: 'peer-review-r' + attempt, phase: 'Peer Review', agentType: 'general-purpose' },\n"
        + "  )\n"
        + "  // parseAgentJson lives at top of file (same pattern as phase1+phase2)\n"
        + "  try {\n"
        + "    const parsed = parseAgentJson(peerReport, 'PeerB-r' + attempt)\n"
        + "    if (!parsed || !Array.isArray(parsed.verdicts) || parsed.verdicts.length !== peerDeliverables.length) {\n"
        + "      throw new Error('verdicts[] missing or wrong length (expected ' + peerDeliverables.length + ')')\n"
        + "    }\n"
        + "    // Sanity: each verdict must be for one of our 4 deliverables\n"
        + "    for (const v of parsed.verdicts) {\n"
        + "      if (!peerDeliverables.includes(v.deliverable)) {\n"
        + "        throw new Error('unknown deliverable in verdict: ' + v.deliverable)\n"
        + "      }\n"
        + "      if (!v.reason || String(v.reason).trim().length < 100) {\n"
        + "        throw new Error('verdict for ' + v.deliverable + ' has reason < 100 chars')\n"
        + "      }\n"
        + "      if (!Array.isArray(v.citations) || v.citations.length < 1) {\n"
        + "        throw new Error('verdict for ' + v.deliverable + ' has empty citations[] — agent_b_approvals.py hard-blocks this at advance-phase')\n"
        + "      }\n"
        + "    }\n"
        + "    peerVerdict = parsed\n"
        + "    log('  peer review verdict parsed (round ' + attempt + '/' + MAX_OUTER_ATTEMPTS_PEER + ')')\n"
        + "    break\n"
        + "  } catch (e) {\n"
        + "    log('  Peer B parse failed: ' + String(e.message ?? e).slice(0, 120) + ' — retrying')\n"
        + "    if (attempt === MAX_OUTER_ATTEMPTS_PEER) {\n"
        + "      return { error: 'Peer B parse failed after ' + MAX_OUTER_ATTEMPTS_PEER + ' rounds', detail: String(e.message ?? e).slice(0, 400) }\n"
        + "    }\n"
        + "  }\n"
        + "}\n"
        + "if (!peerVerdict) {\n"
        + "  return { error: 'Peer B did not produce valid verdict' }\n"
        + "}\n"
        + "\n"
        + "// T1-B: check whether ALL verdicts are APPROVE (no REJECT, no medium/high gaps).\n"
        + "// Previously the workflow wrote all 4 approvals unconditionally regardless of\n"
        + "// review_status — a REJECT verdict would be committed to disk with no escalation.\n"
        + "const allApproved = peerVerdict.verdicts.every(function (v) {\n"
        + "  if (v.review_status !== 'APPROVE') return false\n"
        + "  return !(v.gaps || []).some(function (g) { return g.severity === 'medium' || g.severity === 'high' })\n"
        + "})\n"
        + "if (!allApproved) {\n"
        + "  return { error: 'HR-08: Phase 6 Peer Review had REJECT or unresolved medium/high gaps — escalate to human (previously this was silently ignored; T1-B adds the check)', peerVerdict: peerVerdict }\n"
        + "}\n"
        + "\n"
        + "// Workflow writes 4 approval JSON files via persistApproval (Class C).\n"
        + "// This avoids the v33b-class double-encode bug where a sub-agent emitting a\n"
        + "// JSON string-of-string was accepted by `size >= 10 bytes` verify but later\n"
        + "// failed at advance-phase _verify_agent_b_approvals_core (data.get on str).\n"
        + "for (const v of peerVerdict.verdicts) {\n"
        + "  await persistApproval(v.deliverable, v)\n"
        + "}\n"
    )


def _render_phase6_tag_advance() -> str:
    return (
        B.render_phase_header("Tag & Advance")
        + "log('git tag (Gate 4 score) + advance-phase --completed 6')\n"
        + "// Round loop (2026-07-02 audit finding, ported from phase3): advance-phase\n"
        + "// enforces more independent checks than any single prompt can safely\n"
        + "// enumerate, and a static checklist goes stale the moment harness adds or\n"
        + "// changes one. advance-phase is idempotent (preflight runs before any\n"
        + "// FSM/state write), so the robust fix is an outer retry loop where the\n"
        + "// agent reads advance-phase's own [BLOCKED] output each round instead of\n"
        + "// guessing in advance. The git-tag step is separately GUARDed (step 0\n"
        + "// checks for an existing tag), so it stays safe to repeat across rounds.\n"
        + "let advancePass = false, advanceReport = ''\n"
        + "const ADVANCE_MAX_ROUNDS = 5\n"
        + "for (let round = 1; round <= ADVANCE_MAX_ROUNDS; round++) {\n"
        + "  log('  Tag & Advance round ' + round + '/' + ADVANCE_MAX_ROUNDS)\n"
        + "  // Manifest integrity: enforced by advance-phase itself since Round 22 站2\n"
        + "  // (cli/phase_cmds.py::_advance_prechecks, exit 27 with the restore command\n"
        + "  // in its [BLOCKED] message). It runs first, before any other precheck, and\n"
        + "  // on every round because advance-phase is idempotent — same guarantee the\n"
        + "  // per-round dispatch here used to buy, minus the dispatch, and now covering\n"
        + "  // the human/CI callers this loop never could.\n"
        + "  advanceReport = await agent(\n"
        + "    'YOU ARE THE PHASE-6 EXIT ORCHESTRATOR. Tag the Gate 4 release + advance to Phase 7. ROUND ' + round + '.\\n'\n"
        + "    + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "    + 'Steps:\\n'\n"
        + "    + '0. GUARD — already advanced? `PHASE=$(jq -r .current_phase ' + REPO + '/.methodology/state.json 2>/dev/null); echo \"current_phase=$PHASE\"; [ \"$PHASE\" -ge 7 ]`. Also check: `git -C ' + REPO + ' tag -l \"harness-v4-*\" | head -1`. If Phase 7 is confirmed OR tag already exists, report \"ADVANCE: PASS (already advanced)\" and stop.\\n'\n"
        + "    + '1. GIT-TAG (skip if step 0 found an existing tag): `' + PY + ' ' + REPO + '/harness_cli.py gate4-tag --project ' + REPO + '` then `git -C ' + REPO + ' push origin --tags`. gate4-tag reads composite_score from gate4_result.json (the same score finalize-gate computed and persisted), formats the tag, and creates it. Do NOT hand-build the tag command — gate4-tag is the single source of truth for tag naming and score extraction.\\n'\n"
        + "    + '2. advance-phase: `' + PY + ' ' + REPO + '/harness_cli.py advance-phase --completed 6 --project ' + REPO + '`\\n'\n"
        + "    + '   advance-phase independently re-verifies EVERYTHING before it will advance — its own output tells you exactly what is missing. If it prints \"[BLOCKED] ...\", that message IS the fix instruction: read it verbatim and do exactly what it says, then re-run this same advance-phase command. Do NOT guess what might be wrong — trust only what advance-phase itself reports. It is safe to re-run repeatedly within this round.\\n'\n"
        + "    + '3. Read ' + REPO + '/.methodology/state.json; confirm current_phase = 7 (advance-phase atomically writes state.json when complete).\\n\\n'\n"
        + "    + 'Report final line: \"ADVANCE: PASS|FAIL — <details>\". If still FAIL after exhausting this round\\'s turn, report the LAST [BLOCKED] message verbatim so the next round starts from where this one left off. PHASE_7_PLAN: ' + REPO + '/.methodology/phase7_plan.md\\n\\n'\n"
        + "    + 'SCOPE RULES:\\n- DO NOT re-do Gate 4 / release docs.\\n- DO NOT use --no-verify.\\n- DO NOT modify harness/ (HR-17).\\n- ONLY git tag + advance-phase + verify HANDOVER.md + the specific fixes advance-phase\\'s own output asked for.\\n- Any diagnostic/debug script MUST be written under .sessi-work/tmp/ (never repo root or source dirs) and self-cleaned before you exit.',\n"
        + "    { label: 'tag-advance-r' + round, phase: 'Tag & Advance', agentType: 'general-purpose' },\n"
        + "  )\n"
        + "  if (advanceReport === null || advanceReport === undefined || (typeof advanceReport === 'string' && advanceReport.length < 10)) {\n"
        + "    log('  Tag & Advance agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')\n"
        + "    return { session_limit_blocked: true, phase: 6, step: 'tag-advance', message: 'Agent hit session/rate limit during Tag & Advance. Resume after quota reset — the GUARD step skips if already advanced/tagged.' }\n"
        + "  }\n"
        + "  // AUTHORITATIVE Advance verdict: advance-phase atomically writes\n"
        + "  // state.json current_phase=7 on success. Read it via a schema proxy —\n"
        + "  // the orchestrator's prose \"ADVANCE: PASS\" is narrative only.\n"
        + "  const advVerifyCmd = PY + ' -c \"import json; print(json.dumps({\\'current_phase\\': int(json.load(open(\\'' + REPO + '/.methodology/state.json\\')).get(\\'current_phase\\') or 0)}))\"'\n"
        + "  const advV = await agent(\n"
        + "    'Run EXACTLY this command via the Bash tool (stdout is a single JSON line):\\n`' + advVerifyCmd + '`\\n'\n"
        + "    + 'Then report via the StructuredOutput tool: current_phase = the exact integer from that JSON.',\n"
        + "    { label: 'advance-verify-r' + round, phase: 'Tag & Advance', agentType: 'general-purpose', schema: PHASE_SCHEMA },\n"
        + "  )\n"
        + "  advancePass = !!(advV && advV.current_phase >= 7)\n"
        + "  if (advancePass) { log('  Tag & Advance PASS [harness-verified: state.json current_phase=' + advV.current_phase + ']'); break }\n"
        + "  log('  Tag & Advance not yet PASS [state.json current_phase=' + (advV ? advV.current_phase : '?') + '] — retry round ' + (round + 1))\n"
        + "}\n"
        + "\n"
        + "if (!advancePass) {\n"
        + "  return { error: 'Tag & Advance did not PASS in ' + ADVANCE_MAX_ROUNDS + ' rounds — check HANDOVER.md + state.json + the last [BLOCKED] message below. If Phase 7 is confirmed, resume workflow to verify.', raw: String(advanceReport ?? '').slice(-600) }\n"
        + "}\n"
    )


def generate_phase6() -> str:
    parts = [
        _HEADER_6,
        "",
        _render_meta(
            name="phase6-quality",
            description=(
                "Phase 6 Quality — Gate 4 (14 dims + DA challenge) + Agent B peer review "
                "+ release notes/sign-off + git tag (phase6_plan.md v2.12.0)"
            ),
            phases=_META_PHASES_6,
        ),
        "",
        B.RESOLVE_REPO_BLOCK + B.REPO_LOG_LINE,
        # Round 12 站1: MAX_OUTER_ATTEMPTS declaration — the station-4 A/B
        # unification injected the shared persistApproval body (which reads
        # this file-level constant, declared in phase1/phase2's own specs)
        # into phase6 WITHOUT the declaration; phase6's pre-migration
        # writeApprovalJson used a local `const MAX = 3` instead. First sim
        # testbed run caught the ReferenceError at the first approval write
        # (after peer review already passed — the most expensive spot).
        "const MAX_OUTER_ATTEMPTS = 3\n"
        + B.render_persist_approval(
            synthesize_reason=False, use_schema_verdict=True,
            label_prefix="write-approval", phase_label="Peer Review",
        ) + "const MAX_OUTER_ATTEMPTS_PEER = 3  // peer-review dispatch retry at orchestrator level\n"
        + B.BUDGET_GUARD_BLOCK,
        B.WRITE_SCOPE_BLOCK,
        "",
        B.render_schemas(["VERDICT_SCHEMA", "GATE_VERIFY_SCHEMA", "PHASE_SCHEMA"]),
        B.render_json_utils(),
        _render_phase6_entry_preflight(),
        B.render_gate_loop(
            gate_num=4, phase=6,
            log_msg="Gate 4 full-project eval (composite ≥85, 14 dims: 12 self-scored + traceability + architecture framework-owned; mutation_testing disabled by default)",
            prompt_steps=_GATE4_STEPS,
            pass_line_desc="composite ≥85 AND all dims ≥ threshold AND DA artifacts present AND D4 ≥90%",
            scope_rules=_GATE4_SCOPE_RULES,
            d4_threshold=90.0,
            on_fail_error_msg="Gate 4 did not PASS in 3 rounds (HR-08; write deferred_fixes.md + escalate to human)",
            include_manifest_integrity=False,
            wrap_try_catch=True,
            orchestrator_desc="Phase 6 — full project quality",
            pre_gate_note="Pre-Gate: confirm all FRs merged to main + no open critical/high from Gate 3.",
            include_finalize_note=False,
        ),
        _render_phase6_release_docs(),
        _render_phase6_peer_review(),
        _render_phase6_tag_advance(),
        B.render_sync_verified(),
        (
            "\nlog('Phase 6 workflow complete. Open .methodology/phase7_plan.md to continue.')\n"
            "return {\n"
            "  phase: 6,\n"
            "  gate4_status: gate4Pass ? 'PASS' : 'unknown',\n"
            "  // Pre-existing latent bug fixed 2026-07-02: this line referenced `peerReport`,\n"
            "  // a for-block const out of scope here — the final return would have thrown\n"
            "  // ReferenceError after everything passed. peerVerdict is the in-scope truth.\n"
            "  peer_review_status: (peerVerdict && Array.isArray(peerVerdict.verdicts) && peerVerdict.verdicts.every(v => v.review_status === 'APPROVE')) ? 'APPROVE' : 'unknown',\n"
            "  advance_status: 'PASS',\n"
            "  artifacts: ['06-quality/QUALITY_REPORT.md', 'RELEASE_NOTES.md', 'FINAL_SIGN_OFF.md', '.methodology/agent_b_approvals/', '.sessi-work/gate4_result.json', '.methodology/quality_manifest.json', 'HANDOVER.md'],\n"
            "  notes: 'Phase 6 complete per phase6_plan.md v2.12.0. Gate 4 PASS + Agent B peer review APPROVE. Phase 7 (Risk Management) ready.',\n"
            "}\n"
        ),
    ]
    return "\n".join(p for p in parts if p is not None)
