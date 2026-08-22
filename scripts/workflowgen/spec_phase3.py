"""Phase 3 (Implementation) workflow assembly — Round 15 station4 extraction
from the former monolithic phase_specs.py (largest single-phase family). See
scripts/workflowgen/spec_shared.py for the cross-phase _render_meta.
"""
from __future__ import annotations

from . import js_blocks as B
from . import spec_shared as S
from .spec_shared import _render_meta

_HEADER_3 = """\
// Phase 3 — Implementation (faithful to .methodology/phase3_plan.md v2.12.0)
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/phase_specs.py::generate_phase3() (+ js_blocks.py for
// the blocks shared across phase workflow files). Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write --phase 3
//
// Structure: FR-loop型 + Gate 2 exit. Script holds the per-FR loop (playbook
// "plan as code"): load fr_ids via an agent, then for each FR dispatch a
// narrow agent that runs the TDD chain (RED→MIRROR→GREEN→IMPROVE→GATE1).
// Milestone pushes are script-driven (≥1/3 → p3-mid; all done → p3-pre-gate2).
// Gate 2 is one orchestrator agent (run-gate → eval → finalize → D4 60%).
//
// Playbook lessons: NO import/fs/process, Bash for all harness CLI,
// SCOPE RULES per agent, PY = .venv/bin/python, scriptPath launch.
// v4 (2026-07-02): gate verdicts use FLAT schema: (playbook §5.2 rev) — the
// v2 blanket schema ban was itself a workaround that forced every gate onto
// regex-over-LLM-prose, the root cause of bugs #126/#134/#135/#136 and the
// ENV_CHECK_RC paraphrase false-negative. Flat 2-3 field schemas on bash-proxy
// agents are runtime-validated (AJV + 2 retries); complex nested schemas on
// heavy-cognition agents remain forbidden (that was the real v2 lesson).
"""

_META_PHASES_3 = [
    "Entry & Preflight", "Env Check", "Load FRs",
    "Per-FR TDD", "Milestones", "Gate 2", "Preview Next-Phase", "Advance", "Sync",
]


def _render_phase3_entry_preflight() -> str:
    """phase3's Entry & Preflight has a 5th step (P2-ARTIFACTS) that no other
    migrated phase has — it's the only phase transitioning FROM Phase 2's
    architecture artifacts specifically. render_entry_preflight()'s 4-step
    template is already shared/golden-locked by phase4/5/7/8; forcing a 5th
    conditional step into it for one outlier is the wrong abstraction, so
    this stays a phase3-specific verbatim renderer."""
    return (
        B.render_phase_header("Entry & Preflight")
        + "log('ENTRY-CHECK + P2-ARTIFACTS + run-phase 3 + validate-handoff + CI')\n"
        + "const preflightReport = await agent(\n"
        + "  'YOU ARE THE PHASE-3 PREFLIGHT ORCHESTRATOR. Run bash in order; report.\\n'\n"
        + "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "  + 'Steps:\\n'\n"
        + "  + '1. ENTRY-CHECK: `git -C ' + REPO + ' log --oneline --grep=\"phase2(review-complete)\" -1` OR confirm P2 artifacts exist.\\n'\n"
        + "  + '2. P2-ARTIFACTS: `ls ' + REPO + '/02-architecture/SAD.md ' + REPO + '/02-architecture/adr/ADR.md ' + REPO + '/02-architecture/TEST_SPEC.md ' + REPO + '/.methodology/quality_manifest.json ' + REPO + '/.methodology/SAB.json`. ALL must exist (else FAIL → return to Phase 2).\\n'\n"
        + "  + '3. PREFLIGHT: `' + PY + ' ' + REPO + '/harness_cli.py run-phase --phase 3 --project ' + REPO + '`. FAIL → fix FSM/Constitution/Drift, re-run (max 3).\\n'\n"
        + "  + '4. HANDOFF: `' + PY + ' ' + REPO + '/harness_cli.py validate-handoff --from-phase 2 --project ' + REPO + '`. Must exit 0.\\n'\n"
        + "  + '5. PREFLIGHT-CI: (a) confirm `' + REPO + '/.github/workflows/harness_quality_gate.yml` (CI workflow) + `' + REPO + '/.git/hooks/prepare-commit-msg` (git hook) both exist; if either is missing, run `init-project --phase 3 --project ' + REPO + ' --overwrite` and re-check. (b) Confirm state.json current_phase=3. If current_phase != 3: FAIL — init-project never changes an existing FSM state. If lower, the Phase 2 workflow must complete the enforced transition with `advance-phase --completed 2 --project ' + REPO + '`; if higher, run the workflow matching current_phase. Do NOT run advance-phase from this preflight.\\n\\n'\n"
        + "  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if ALL 5 steps succeeded; reason = one-line summary (on FAIL: which step + verbatim error tail).\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n- DO NOT implement any FR or run TDD steps.\\n- DO NOT run advance-phase/push-milestone/run-gate.\\n- DO NOT modify harness/.\\n- ONLY preflight commands + fixes.',\n"
        + "  { label: 'preflight', phase: 'Entry & Preflight', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(preflightReport && preflightReport.pass === true)) {\n"
        + "  return halt('preflight', { error: 'Phase 3 preflight did not PASS', reason: preflightReport ? String(preflightReport.reason ?? '').slice(-600) : 'agent returned null (skipped or terminal API error)' })\n"
        + "}\n"
    )


def _render_phase3_load_frs() -> str:
    """phase3's Load FRs computes fr_titles SERVER-SIDE (Python, forwarded via
    CTX_SCHEMA's fr_titles field) — a different, WORKING mechanism from the
    shared render_load_frs(include_fr_titles=True), whose client-side
    `Array.isArray(ctx.fr_details)` check is always false (fr_details is a
    dict keyed by FR id, per cli/project_cmds.py:666 — confirmed pre-existing
    in phase5/7/8's original files too, not introduced by this migration).
    Forcing phase3 onto the shared (broken) mechanism would be a real
    regression, so this stays its own renderer. Also appends the Gate 1
    pre-check (identify already-PASSed FRs to skip on resume) that no other
    migrated phase has — phase3 is the only full-TDD-chain phase where
    "already done" is a meaningful per-FR skip condition.
    """
    return (
        B.render_phase_header("Load FRs")
        + "log('load-context --phase 3 → fr_ids (script holds the loop)')\n"
        + "// v15: retry loop — agent() wrapped (Bug #2); v4: schema transport, no prose parsing\n"
        + "// v2.13.1: hardened against agent hallucination — verify .sessi-work/phase3_ctx.json\n"
        + "// actually exists and contains non-empty fr_ids before accepting (Bug #122).\n"
        + "// Fix D (2026-07-18): this file also carries the `lessons` field (recall_lessons()\n"
        + "// snapshot from .methodology/lessons/*.md, injected into each per-FR TDD prompt\n"
        + "// below). .sessi-work/ is gitignored, so a `git reset --hard` back to a phase-3\n"
        + "// entry commit never clears a stale phase3_ctx.json from a PRIOR (pre-fix) run —\n"
        + "// the old \"exists and parses\" check alone happily reused it, leaking a dead run's\n"
        + "// failure lessons into a fresh one and anchoring the TDD sub-agent on a\n"
        + "// already-fixed problem. Force a fresh load-context on the first attempt of every\n"
        + "// workflow invocation; the \"reuse if valid\" fallback still applies on attempts\n"
        + "// 2/3, which only run if regeneration itself failed (Bug #122's original intent).\n"
        + "let ctx = null\n"
        + "const ctxFile = REPO + '/.sessi-work/phase3_ctx.json'\n"
        + "// Round 22 站3: the separate ctx-check dispatch is gone. On attempt 1 its\n"
        + "// verdict was never even read — Fix D's `attempt === 1 ||` short-circuits\n"
        + "// ahead of it, so the probe ran, answered, and was discarded. On attempts\n"
        + "// 2/3 it ran `json.load(ctxFile)` to prove the file parses, which is the\n"
        + "// same thing the read below does and fails on. Both cases it distinguished\n"
        + "// (missing vs. unparseable) had identical handling: regenerate.\n"
        + "let needRegen = true  // Fix D: attempt 1 ALWAYS regenerates\n"
        + "for (let attempt = 1; attempt <= 3; attempt++) {\n"
        + "  if (needRegen) {\n"
        + "    log('  ' + (attempt === 1 ? 'forcing fresh load-context (attempt 1 — avoid stale lessons)' : 'ctx unreadable (attempt ' + attempt + ') — regenerating'))\n"
        + "    const ctxRegenCmd = `${PY} ${REPO}/harness_cli.py load-context --phase 3 --project ${REPO} --json > ${ctxFile} && ${PY} -c \"import json,os; json.load(open('${ctxFile}')); print('REGEN_OK_'+str(os.path.getsize('${ctxFile}')))\"`\n"
        + "    try {\n"
        + "      await agent(\n"
        + "        `You MUST use the Bash tool. Run exactly:\\n${ctxRegenCmd}\\nReturn the raw stdout as your final message.`,\n"
        + "        { label: 'ctx-regen-' + attempt, phase: 'Load FRs', agentType: 'general-purpose' },\n"
        + "      )\n"
        + "    } catch (e) { log('  ctx-regen agent failed: ' + String(e.message ?? e).slice(0, 80)) }\n"
        + "  }\n"
        + "  needRegen = true\n"
        + "\n"
        + "  try {\n"
        + "    // J1 fix (2026-06-29): forward fr_titles too. load-context emits fr_details as a\n"
        + "    // DICT keyed by FR id ({\"FR-01\":{\"title\":...}}). The previous parse only forwarded\n"
        + "    // fr_details_keys (no titles), and the consumer (frTitle below) read it as an Array\n"
        + "    // — so titles silently never populated. Emit an {id:title} map the consumer uses\n"
        + "    // directly.\n"
        + "    const ctxParseCmd = `${PY} -c \"import json; d=json.load(open('${ctxFile}')); fd=d.get('fr_details') or {}; print(json.dumps({'fr_ids':d.get('fr_ids',[]),'fr_count':len(d.get('fr_ids',[])),'fr_titles':{k:(v.get('title','') if isinstance(v,dict) else '') for k,v in fd.items()}}))\"`\n"
        + "    const ctxResult = await agent(\n"
        + "      `You MUST use the Bash tool. Run exactly:\\n${ctxParseCmd}\\nThe command FAILS (nonzero exit, Python traceback) when the file is missing or not valid JSON — report that verbatim rather than inventing values. On success stdout is a single JSON line: report via the StructuredOutput tool fr_ids, fr_count, fr_titles = the EXACT values from that line (transcribe, do not recompute).`,\n"
        + "      { label: 'load-ctx-a' + attempt, phase: 'Load FRs', agentType: 'general-purpose', schema: CTX_SCHEMA },\n"
        + "    )\n"
        + "    if (ctxResult && Array.isArray(ctxResult.fr_ids) && ctxResult.fr_ids.length > 0) {\n"
        + "      ctx = ctxResult\n"
        + "      log('  load-ctx OK (schema-validated, ' + ctx.fr_ids.length + ' FRs)')\n"
        + "      break\n"
        + "    }\n"
        + "    log('  load-ctx returned no fr_ids (attempt ' + attempt + '): keys=' + Object.keys(ctxResult ?? {}).join(',') + ' — regenerating ctx file')\n"
        + "  } catch (e) { log('  load-ctx agent failed: ' + String(e.message ?? e).slice(0, 80) + ' — regenerating ctx file') }\n"
        + "}\n"
        + "if (!ctx) return halt('load-frs', { error: 'Load FRs: ctx failed after 3 attempts', ctxFile })\n"
        + "let frIds = Array.isArray(ctx.fr_ids) ? ctx.fr_ids : []\n"
        + "if (!frIds.length) return halt('load-frs', { error: 'Load FRs: no fr_ids found in ctx', ctxKeys: Object.keys(ctx) })\n"
        + "// J1: fr_titles is the {id:title} map emitted by ctxParseCmd above.\n"
        + "const frTitle = (ctx.fr_titles && typeof ctx.fr_titles === 'object') ? ctx.fr_titles : {}\n"
        + "log('  fr_ids = ' + JSON.stringify(frIds))\n"
        + "\n"
        + "// Gate 1 pre-check: identify FRs that ALREADY passed Gate 1 (skip TDD on resume/re-run).\n"
        + "// AUTHORITATIVE source = quality_manifest.gate_results.gate1[fr].quality_complete, which\n"
        + "// harness_bridge writes on EVERY finalize-gate (pass OR fail). NOT the g1_p3_*.flag\n"
        + "// sentinel: that flag is written by run-gate (it only proves run-gate executed), so a\n"
        + "// finalize-gate that raised GateBlockedError on a failing dimension still leaves the\n"
        + "// sentinel behind — using it as a PASS signal misreports blocked FRs as done.\n"
        + "const precheckCmd = PY + ' -c \"import json; g=(json.load(open(\\'' + REPO + '/.methodology/quality_manifest.json\\')).get(\\'gate_results\\',{}) or {}).get(\\'gate1\\',{}) or {}; print(chr(10).join(fr for fr,v in g.items() if isinstance(v,dict) and v.get(\\'quality_complete\\') is True))\"'\n"
        + "const precheckResult = await agent(\n"
        + "  'Run EXACTLY this command via the Bash tool (stdout is a newline-separated list of FR ids, possibly empty):\\n`' + precheckCmd + '`\\n'\n"
        + "  + 'Then report via the StructuredOutput tool: fr_ids_done = the EXACT FR ids from stdout as an array (empty array if stdout is empty).',\n"
        + "  { label: 'gate1-precheck', phase: 'Load FRs', agentType: 'general-purpose', schema: FR_LIST_SCHEMA }\n"
        + ")\n"
        + "const alreadyDone = new Set()\n"
        + "for (const id of (precheckResult && Array.isArray(precheckResult.fr_ids_done) ? precheckResult.fr_ids_done : [])) {\n"
        + "  if (/^FR-\\d+$/.test(String(id).trim())) alreadyDone.add(String(id).trim())\n"
        + "}\n"
        + "if (alreadyDone.size > 0) log('  sentinel pre-check: Gate 1 (Phase 3) already PASS for ' + [...alreadyDone].join(', ') + ' — skipping TDD agents')\n"
    )


def _render_per_fr_tdd() -> str:
    """phase3's Per-FR TDD is the ONLY full-implementation chain (RED→MIRROR
    →GREEN→IMPROVE→amend-sab→GATE1→ORCH-POST) — no other migrated phase runs
    TDD-RED/GREEN/IMPROVE at all (they only re-evaluate via GATE1-DELTA), so
    this has no shared counterpart. Includes the mid-loop p3-mid milestone
    push (fires once at the historical, textually-inconsistent "≥1/3" wording
    despite the actual threshold formula computing 50% — Math.max(1,
    Math.floor(n/2)) — a pre-existing mismatch between comment/log text and
    code preserved verbatim, not corrected by this migration) and the
    [FATAL] structurally-broken-dispatch abort path unique to this phase.
    """
    return (
        B.render_phase_header("Per-FR TDD")
        + "const gate1Pass = []\n"
        + "const gate1Fail = []\n"
        + "let p3MidPushed = false\n"
        + "const p3MidThreshold = Math.max(1, Math.floor(frIds.length / 2))  // PUSH ③ trigger: ≥50% FRs Gate 1 PASS (phase3_plan.md, harness push_cmds.py)\n"
        + "for (const frId of frIds) {\n"
        + "  if (alreadyDone.has(frId)) {\n"
        + "    log('  ' + frId + ' — sentinel exists, Gate 1 PASS (skip TDD)')\n"
        + "    gate1Pass.push(frId)\n"
        + "  } else {\n"
        + "        log('  === ' + frId + ' (' + (frTitle[frId] || '') + ') — TDD chain ===')\n"
        + "    const frNum = frId.match(/\\d+/)[0].padStart(2, '0')\n"
        + "    let passed = false\n"
        + "    for (let frAttempt = 1; frAttempt <= 2; frAttempt++) {\n"
        + "    if (frAttempt > 1) log('  ' + frId + ' — Gate 1 FAILED on attempt 1, retrying this FR once before moving on (dispatch prompt is resume-aware: re-checks git log, skips already-landed RED/MIRROR/GREEN/IMPROVE commits)')\n"
        + "    const frReport = await agent(\n"
        + "      'YOU ARE THE IMPLEMENTER for ' + frId + ' (' + (frTitle[frId] || '') + '). Run the full TDD chain for THIS ONE FR.\\n'\n"
        + "      + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "      + 'FIRST — this dispatch may be a retry of a prior attempt on ' + frId + ' that was interrupted mid-chain (this is common; do not assume you are starting from zero). Run `git -C ' + REPO + ' log --oneline -8` and check for existing commits matching RED (`test(RED):`) / GREEN (`feat(' + frId + '):`) / MIRROR (`test(' + frId + '):`) / IMPROVE (`refactor(' + frId + '):`) for this FR. Skip any step below whose commit already exists — jump straight to the first step that is missing. `run-fr-step` itself is idempotent (it skips a step whose commit already landed), so this only saves you the time of re-deciding what to do; it never causes a step to be silently skipped that actually still needs doing.\\n\\n'\n"
        + "      + 'Direction C (past lessons): Bash `cat ' + REPO + '/.sessi-work/phase3_ctx.json` and READ the `lessons` field (compact markdown, \"\" if none). DO NOT repeat those past failure modes in this FR\\'s TDD chain (implementation / tests / GATE1 fixes).\\n\\n'\n"
        + "      + 'Run these harness steps IN ORDER (each is a bash command; read its output before the next):\\n'\n"
        + "      + 'IMPORTANT for steps 1/3/4 below (TDD-RED/TDD-GREEN/TDD-IMPROVE): these usually finish well under 120s — run them as plain synchronous Bash commands, do NOT pre-emptively background them. But they occasionally exceed the Bash tool\\'s own 120s default and get auto-backgrounded (\"...moved to the background (ID: <id>)... Output is being written to: <path>... You will be notified when it completes.\"). If that happens: do NOT call the Monitor tool on it — Monitor\\'s async notification will not arrive within this single dispatch, and you will be left waiting with nothing to report. Instead recover synchronously: run `sleep 30 && tail -100 <path>` (repeat, cap 20 polls / ~10min).\\n'\n"
        + "      + '   A poll that shows NO NEW content since the previous poll means the command is still running normally — this is the expected steady state while a subprocess/test-suite/coverage run works, NOT a sign of a problem. Sleep 30 and tail again; do NOT investigate `ps`, do NOT hunt for PIDs, do NOT run any other diagnostic command while waiting — you were never given a PID for this command (unlike GATE1\\'s step 6 below, which captures its own via `nohup ... & echo $!`), so any PID found by other means belongs to something else and inspecting it wastes turns without telling you anything.\\n'\n"
        + "      + '   Stop polling ONLY when: (a) the tail output shows the command has returned control (an explicit PASS/FAIL/error the CLI itself prints, or a new `git log`-visible commit for this step), or (b) you reach the 20-poll cap — report \"' + frId + ' <step-name>: TIMEOUT\" (not FAIL, not silence) and move on, exactly as GATE1\\'s own timeout contract in step 6 does.\\n'\n"
        + "      + '1. TDD-RED:    `' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step TDD-RED --project ' + REPO + ' --srs 01-requirements/SRS.md`\\n'\n"
        + "  + '   AFTER RED writes the test file: open `tests/test_fr' + frNum + '.py` and ensure EVERY NFR associated with ' + frId + ' in the traceability table (TRACEABILITY_MATRIX.md §5 is the canonical listing) has a `# NFR-XX` comment on at least one test function. Without these annotations, `compute_trace_dimension` 4c = 0% and Gate 2 blocks (HR-16). Use `grep -n \"# NFR-\" tests/test_fr' + frNum + '.py` and check against the NFR list for ' + frId + ' — document every association.\\n'\n"
        + "      + '2. MIRROR:     `' + PY + ' ' + REPO + '/harness_cli.py check-test-mirrors-spec --fr-id ' + frId + ' --test-file tests/test_fr' + frNum + '.py --project ' + REPO + '`\\n'\n"
        + "      + '   On MIRROR FAIL: fix the TEST to match TEST_SPEC.md — do NOT edit TEST_SPEC.md (correctness was locked in Phase 2; P3 only implements). Re-run.\\n'\n"
        + "      + '3. TDD-GREEN:  `' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step TDD-GREEN --project ' + REPO + ' --srs 01-requirements/SRS.md`\\n'\n"
        + "      + '4. TDD-IMPROVE:`' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step TDD-IMPROVE --project ' + REPO + '`\\n'\n"
        + "      + '   Coverage-filling tests that exercise ANOTHER FR\\'s not-yet-implemented stub (the `_err(f\"\\'<name>\\' is not yet implemented...\")` pattern) MUST NOT assert on that stub\\'s specific message text — it is temporary and will be replaced when the owning FR lands, breaking your test. Either skip that branch (it will be covered when the owning FR implements it and re-runs GATE1/DELTA) or assert only an invariant guaranteed stable across the stub-to-real transition (e.g. non-zero exit code), never the stub\\'s literal text.\\n'\n"
        + "      + '5. amend-sab (proactive, BEFORE GATE1): `' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step amend-sab --project ' + REPO + '` (first-class dispatch, idempotent, deterministic — does NOT spawn a sub-agent). If new modules are registered to .methodology/SAB.json: commit them (`git -C ' + REPO + ' add .methodology/SAB.json && git -C ' + REPO + ' commit -m \"amend: register SAB modules (' + frId + ')\"`) before proceeding to GATE1. This FR\\'s GREEN/IMPROVE steps may have added modules GATE1\\'s Architecture Amendment Protocol would otherwise BLOCK on — registering them now avoids a wasted GATE1 round.\\n'\n"
        + "      + '6. GATE1 — long-running (harness runs up to 3 internal CODE-FIX rounds, each up to ~600s: can silently block ~2400s worst case). Run it BACKGROUNDED — do NOT invoke it as a plain synchronous command:\\n'\n"
        + "      + '   GATE1 invocation procedure (a/b/c):\\n'\n"
        + "      + '   a. Launch: `nohup ' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step GATE1 --project ' + REPO + ' > /tmp/gate1_' + frId + '.log 2>&1 & echo $!` — note the printed PID.\\n'\n"
        + "      + '   b. Poll: every 30s run `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Repeat until DONE (cap 40 polls / ~20min, comfortably above the ~2400s worst case). Still RUNNING past the cap → `kill <PID>` (reaps the whole tree), report \"' + frId + ' GATE1: TIMEOUT\" (not FAIL).\\n'\n"
        + "      + '   c. Once DONE: `cat /tmp/gate1_' + frId + '.log` for the full output — identical to what a synchronous run would have printed. Parse PASS/FAIL from it exactly as before.\\n'\n"
        + "      + '   Gate 1 per-dimension thresholds are printed in the log itself (dynamic — read from quality_manifest gate_score_overrides, do not assume fixed numbers).\\n'\n"
        + "      + '   - PASS → done.\\n'\n"
        + "      + '   - FAIL → fix failing dims (ruff check . --fix; add tests for coverage; fix pyright errors), repeat the GATE1 invocation procedure (a/b/c). Max 3 rounds.\\n'\n"
        + "      + '   - Still failing after 3 → report FAIL.\\n'\n"
        + "      + '   - Structurally-broken dispatch [FATAL]: if the log contains \"[FATAL]\" (e.g. \"claude.ai connectors are disabled\" or any other structurally-broken-dispatch signature), STOP IMMEDIATELY — do NOT unset/modify any environment variables yourself, do NOT retry the GATE1 invocation procedure. The message\\'s suggested fix is for a human operator to run OUTSIDE this session, not something you can act on. Report \"' + frId + ' GATE1: FAIL — structurally broken dispatch environment, escalate to human (see [FATAL] message)\" and stop this FR\\'s TDD chain.\\n'\n"
        + "      + '   - Harness bug [HARNESS-BUG]: if the log contains \"[HARNESS-BUG]\", harness-methodology itself crashed — this is NOT a problem with your code or tests. STOP IMMEDIATELY, do NOT retry, do NOT modify any project code to work around it. Report \"' + frId + ' GATE1: FAIL — harness-methodology bug detected, escalate to human (see [HARNESS-BUG] message and its crash bundle path)\" and stop this FR\\'s TDD chain.\\n'\n"
        + "      + '   - Architecture Amendment Protocol [BLOCKED]: if the log contains \"Unregistered modules detected: {…}\", this means step 5 amend-sab did not run (or its dispatch failed before commit). Verify .methodology/SAB.json is committed; if not, run `' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step amend-sab --project ' + REPO + '` followed by the `git add ... && git commit` lines manually, then repeat the GATE1 invocation procedure (a/b/c). Max 1 amend round per FR.\\n'\n"
        + "      + '   run-fr-step auto-pushes on completion (idempotent). Crash recovery: `resume-fr-phase --phase 3 --project ' + REPO + '`.\\n'\n"
        + "      + '7. ORCH-POST (after GATE1 PASS, per phase3_plan.md [ORCH-POST]):\\n'\n"
        + "      + '   a. `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 40.0 --fr-id ' + frId + '` (per-FR D4 ≥40%). FAIL → add the missing test implementations for ' + frId + ', re-run.\\n'\n"
        + "      + '   b. SAB.json is kept in sync by amend-sab (step 5 above). Do NOT run generate_sab.py --overwrite here.\\n'\n"
        + "      + '      (generate_sab.py --overwrite rebuilds SAB purely from SAD.md §5, which was locked in Phase 2\\n'\n"
        + "      + '       and may not reflect modules added during Phase 3 implementation. Only run generate_sab.py --overwrite\\n'\n"
        + "      + '       manually AFTER updating SAD.md §5 to include all Phase 3 modules.)\\n\\n'\n"
        + "      + 'Implement the module per SPEC.md (read ' + REPO + '/SPEC.md for ' + frId + ') + SAD.md module mapping. Write source under the package directory layout your project uses: if `03-development/src/<package>/` is a FLAT PACKAGE (one `<module>.py` per file, e.g. `03-development/src/<package>/<module>.py`), write `src/<package>/<module>.py`; if it is MODULE-PER-DIR (one `<module>/__init__.py` per directory), write `src/<package>/<module>/__init__.py`. The init-project directory scaffold shows which layout your project uses. Do NOT place this FR\\'s implementation inside a file another FR already owns or a shared/global file (e.g. `cli.py`) used by multiple FRs — each FR\\'s logic belongs in its own module per SAD.md/quality_manifest.json\\'s fr_module_traceability mapping. Tests for ' + frId + ' MUST be placed at the path(s) declared in TEST_SPEC.md §FR-' + frNum + ' (test file list) — TEST_SPEC.md is the canonical source of truth for test placement. If TEST_SPEC lists multiple test files (e.g. unit + integration variants), you MUST create all of them; pass `--test-file <path1> --test-file <path2> ...` to MIRROR and related tooling. The legacy single-file convention (`tests/test_fr' + frNum + '.py` only) is no longer required when TEST_SPEC specifies otherwise. Docstrings must include [' + frId + '] reference (NFR-05).\\n\\n'\n"
        + "      + 'Report final line: \"' + frId + ' GATE1: PASS\" or \"' + frId + ' GATE1: FAIL — <reason>\".\\n\\n'\n"
        + "      + 'SCOPE RULES:\\n- DO NOT implement any FR OTHER than ' + frId + '.\\n- DO NOT run run-gate (Gate 2), advance-phase, or push-milestone.\\n- DO NOT edit .methodology/quality_manifest.json or .sessi-work/gate1_result.json to fake/reset scores — fix the underlying code/tests instead.\\n- DO NOT modify harness/ (HR-17).\\n- ONLY the 7 steps above for ' + frId + ' (amend-sab in step 5, spec-coverage-check in step 7a is allowed).',\n"
        + "      { label: 'tdd-' + frId, phase: 'Per-FR TDD', agentType: 'general-purpose' },\n"
        + "    )\n"
        + "    // L1: distinguish a session/rate-limit block (null/empty agent return) from a real\n"
        + "    // Gate 1 FAIL — mirror the Gate 2 detection (below). Without this, a rate-limit mid-\n"
        + "    // TDD is misreported as a code-quality Gate 1 failure. Sentinel GUARD skips completed\n"
        + "    // FRs on resume, so aborting here is safe.\n"
        + S.render_session_block_guard(
            'frReport', '', 3,
            step_js='frId',
            extra_fields='fr_id: frId, gate1Pass',
            message="Agent hit session/rate limit during ' + frId + ' TDD. "
                    "Resume after quota reset — sentinel GUARD will skip completed FRs.",
            indent='    ',
        )
        + B.render_terminal_abort_detectors(phase=3, indent="    ", step="GATE1")
        + "    // AUTHORITATIVE Gate 1 verdict: read the harness quality_manifest (bridge writes\n"
        + "    // gate_results.gate1[fr].quality_complete on every finalize-gate, pass OR fail) —\n"
        + "    // NOT the sub-agent's self-reported \"GATE1: PASS\" string. A sub-agent can report\n"
        + "    // PASS from its own gate1_result.json overall score even when finalize-gate raised\n"
        + "    // GateBlockedError (e.g. spec-coverage short, or a dimension below threshold), which\n"
        + "    // silently advances a FR that the harness actually blocked. Verify against the\n"
        + "    // harness's own record so a blocked gate is never counted as passed.\n"
        + "    // v2.13.3 (P3 2026-07-16 follow-up to v2.13.2): the v2.13.2 design used\n"
        + "    // `spawnSync` to invoke a deterministic Python verifier synchronously\n"
        + "    // from the workflow. The intent was right (avoid LLM-hallucinated\n"
        + "    // verify verdicts — see wf_53d055ce-d0b where the LLM reported\n"
        + "    // `pass:false, reason=GATE1_VERIFIED_FAIL score=91.81` despite\n"
        + "    // `quality_complete=True`), but the dynamic-workflow runtime sandbox\n"
        + "    // does not expose Node.js `child_process.spawnSync` — the call\n"
        + "    // ReferenceErrors at this site (`spawnSync is not defined`).\n"
        + "    //\n"
        + "    // The deterministic manifest read is moved to a stand-alone script\n"
        + "    // (`harness/scripts/verify_gate1_qc.py`) which the Bash sub-agent\n"
        + "    // invokes. The LLM is now a string carrier: the prompt requires it to\n"
        + "    // echo the literal Python stdout (Python's print is deterministic —\n"
        + "    // same input, same bytes), and workflow JS regex-parses the echoed\n"
        + "    // string to derive `passed`. The LLM's own `pass` field is ignored.\n"
        + "    // Same AUTHORITATIVE manifest read (the whole point of this verify\n"
        + "    // step) is preserved; only the execution substrate changes.\n"
        + "    const verifyResult = await agent(\n"
        + "      'You MUST use the Bash tool. Run EXACTLY this single command (single line):\\n'\n"
        + "      + PY + ' ' + REPO + '/harness/scripts/verify_gate1_qc.py --fr-id ' + frId + ' --project ' + REPO + '\\n'\n"
        + "      + 'Then report via the StructuredOutput tool: pass = true ONLY if the FIRST line of stdout is exactly \"GATE1_VERIFIED_PASS\"; reason = the verbatim stdout (do NOT paraphrase, summarize, or prepend commentary).',\n"
        + "      { label: 'gate1-verify-' + frId, phase: 'Per-FR TDD', agentType: 'general-purpose', schema: VERDICT_SCHEMA }\n"
        + "    )\n"
        + "    const verifyOut = String((verifyResult && verifyResult.reason) || '').trim()\n"
        + "    // Round 12 站2a: verdict from the deterministic stdout ONLY — the AND\n"
        + "    // on verifyResult.pass contradicted the comment above (v2.13.3 shipped\n"
        + "    // \"the LLM's pass field is ignored\" in prose while the code still let a\n"
        + "    // hallucinated pass:false veto a PASS manifest, the exact\n"
        + "    // wf_53d055ce-d0b incident this step exists to prevent).\n"
        + "    passed = verifyOut.startsWith('GATE1_VERIFIED_PASS')\n"
        + "    if (passed) break\n"
        + "    }\n"
        + "    if (passed) { gate1Pass.push(frId); log('  ' + frId + ' Gate 1 PASS (' + gate1Pass.length + '/' + frIds.length + ') [harness-verified]') }\n"
        + "    else { gate1Fail.push(frId); log('  ' + frId + ' Gate 1 FAIL [harness manifest qc != true; sub-agent self-report ignored]') }\n"
        + "  }\n"
        + "\n"
        + "  // PUSH ③ p3-mid — fire once when ≥1/3 FRs have Gate 1 PASS (but not yet all done).\n"
        + "  if (!p3MidPushed && gate1Pass.length >= p3MidThreshold && gate1Pass.length < frIds.length) {\n"
        + "    p3MidPushed = true\n"
        + "    log('  ≥1/3 FRs Gate 1 PASS (' + gate1Pass.length + '/' + frIds.length + ') — pushing p3-mid milestone')\n"
        + "    await agent(\n"
        + "      'YOU ARE THE P3 MID-MILESTONE PUSHER (≥1/3 FRs Gate 1 PASS).\\n'\n"
        + "      + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "      + '0. GUARD: `git -C ' + REPO + ' log --oneline --grep=\"P3-mid)\" -1`. If a p3-mid commit already exists, report \"MILESTONE: PASS (already pushed)\" and stop — do NOT push again.\\n'\n"
        + "      + '1. Command: `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p3-mid --project ' + REPO\n"
        + "      + ' --fr-done ' + gate1Pass.length + ' --fr-total ' + frIds.length + ' --fr-ids ' + gate1Pass.join(',') + '`\\n'\n"
        + "      + '   Writes HANDOVER.md + commits + pushes. If a hook blocks, reword commit to start with `chore(harness):` (NOT --no-verify), retry.\\n\\n'\n"
        + "      + 'Report: \"MILESTONE: PASS|FAIL — <details>\".\\n\\n'\n"
        + "      + 'SCOPE RULES:\\n- DO NOT run run-gate / advance-phase / implement FRs.\\n- ONLY push-milestone p3-mid.',\n"
        + "      { label: 'milestone-p3-mid', phase: 'Per-FR TDD', agentType: 'general-purpose' },\n"
        + "    )\n"
        + "  }\n"
        + "}\n"
        + "if (gate1Fail.length) {\n"
        + "  return halt('gate1', { error: 'Phase 3: Gate 1 FAILED for FR(s): ' + gate1Fail.join(', ') + ' (escalate — fix code/tests, resume-fr-phase)', gate1Pass, gate1Fail })\n"
        + "}\n"
    )


def _render_phase3_milestones() -> str:
    return (
        B.render_phase_header("Milestones")
        + "log('All ' + frIds.length + ' FRs Gate 1 PASS — push p3-pre-gate2 (last stable snapshot before Gate 2)')\n"
        + "const preGate2Report = await agent(\n"
        + "  'YOU ARE THE P3 MILESTONE PUSHER. Push the pre-Gate-2 milestone.\\n'\n"
        + "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "  + '0. GUARD: `git -C ' + REPO + ' log --oneline --grep=\"P3-pre-gate2)\" -1`. If a p3-pre-gate2 commit already exists, report \"MILESTONE: PASS (already pushed)\" and stop.\\n'\n"
        + "  + '1. Command: `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p3-pre-gate2 --project ' + REPO + ' --fr-ids ' + gate1Pass.join(',') + '`\\n'\n"
        + "  + '   Writes HANDOVER.md + commits + pushes. If a hook blocks, reword commit to start with `chore(harness):` (NOT --no-verify), retry.\\n\\n'\n"
        + "  + 'Verdict: report via the StructuredOutput tool — pass=true if the milestone commit exists or was pushed; reason = one-line detail.\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n- DO NOT run run-gate or advance-phase.\\n- ONLY push-milestone p3-pre-gate2.',\n"
        + "  { label: 'milestone-pre-gate2', phase: 'Milestones', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(preGate2Report && preGate2Report.pass === true)) {\n"
        + "  log('  WARNING: p3-pre-gate2 milestone push did not confirm PASS — continuing to Gate 2 (milestone is a snapshot, not a hard gate)')\n"
        + "}\n"
    )


def _render_phase3_sync() -> str:
    """phase3's Sync, on the shared renderer with its own terminal branch.

    Round 43 站3 deleted the second Sync implementation this file used to
    carry. It differed from `render_sync_verified` in two ways: it retried
    once, and on a second failure it wrote a "Sync Blocked" section into
    HANDOVER.md and returned a structured MANUAL_REQUIRED result instead of a
    bare error.

    The retry was the defect — it re-sent an IDENTICAL prompt under a comment
    about transient network blips, at a pre-push hook that runs the full phase
    preflight and therefore rejects on deterministic project content. That is
    Round 41 站3's "stop buying the same failure twice" in the one step of the
    pipeline that had no authority to fix anything. The shared renderer now
    retries WITH that authority.

    The terminal branch is genuinely phase-3-specific and is passed in: state
    .json is already authoritative for Phase 4 (Advance PASS'd above), so the
    handover commit is stranded on local rather than the phase being wrong,
    and a human needs the blocker list where the next session will read it.
    Never auto `--no-verify` (HR-17).
    """
    return B.render_sync_verified(on_blocked=(
        "  // Do NOT auto `--no-verify` (HR-17 forbids bypassing the gate without a\n"
        "  // human decision). Surface the blocker instead of terminating with a bare\n"
        "  // error: state.json current_phase is already authoritative for Phase 4\n"
        "  // (Advance PASS'd above), the handover commit just hasn't reached origin\n"
        "  // yet — a human resolves the printed blocker(s) and pushes manually.\n"
        "  const blockers = String(syncReport ?? '').slice(-600)\n"
        "  await agent(\n"
        "    'Append this section to the END of ' + REPO + '/HANDOVER.md (append — do not overwrite '\n"
        "    + 'existing content; create the file only if it truly does not exist):\\n\\n'\n"
        "    + '## Sync Blocked — manual push required\\n\\n'\n"
        "    + 'The Phase 3 advance handover commit landed locally but `git push origin main` '\n"
        "    + 'did not pass the pre-push hook after ' + SYNC_MAX_ATTEMPTS + ' attempts, "
        "the last of which was allowed to fix what the hook named:\\n\\n'\n"
        "    + '```\\n' + blockers + '\\n```\\n\\n'\n"
        "    + 'Resolve the blocker(s) above, then run `git push origin main` manually. '\n"
        "    + 'Do NOT use `--no-verify` without explicit human sign-off.\\n',\n"
        "    { label: 'sync-handover-note', phase: 'Sync', agentType: 'general-purpose' },\n"
        "  )\n"
        "  log('Phase 3 workflow ends with Sync unresolved — see HANDOVER.md \"Sync Blocked\" section.')\n"
        "  return {\n"
        "    phase: 3,\n"
        "    fr_count: frIds.length,\n"
        "    gate1_pass: gate1Pass,\n"
        "    gate2_status: gate2Pass ? 'PASS' : 'unknown',\n"
        "    advance_status: 'PASS',\n"
        "    sync_status: 'MANUAL_REQUIRED',\n"
        "    blockers,\n"
        "    artifacts: ['03-development/src/', 'tests/', '.methodology/gate2_result.json', 'HANDOVER.md'],\n"
        "    notes: 'Phase 3 complete (Advance PASS) but the handover commit could not be auto-pushed — "
        "see HANDOVER.md \"Sync Blocked\" section for the pre-push blocker list.',\n"
        "  }\n"
    ))


# See spec_phase4._D4_THRESHOLD_P4 — spec-coverage-check's floor is not a gate
# dimension, so the gate config has nothing to read; named once per phase.
_D4_THRESHOLD_P3 = 60.0

_GATE2_STEPS = [
    "1. G2a: `' + PY + ' ' + REPO + '/harness_cli.py run-gate --gate 2 --phase 3 --project ' + REPO + '` — read the printed evaluation prompt.",
    (
        "2. G2b: Evaluate ALL Gate 2 dimensions inline per ' + REPO + '/harness/harness/ssi/prompts/evaluate_dimension.md. Write ' + REPO + '/.sessi-work/gate2_result.json.\\n"
        "   Dims: use the exact `dimensions:` list G2a just printed (it is computed from gate2_p3_exit.yaml, filtered by enabled feature flags — always current, do NOT hand-copy a dim list here).\\n"
        f"{S.render_framework_owned_note(2)}"
        "   For any failing dim: fix the ROOT CAUSE in code (ruff/pyright/add tests/bandit/mutation), re-run the tool, update the score. (No auto-fix engine.)"
    ),
    (
        "3. G2c — run BACKGROUNDED (finalize-gate\\'s own git push triggers the local pre-push hook, plus CRG refresh: bounded on this project today, but a single opaque Bash call with no visible output until it returns is exactly the shape the 180s stall watchdog kills — same class of risk as GATE1, same fix):\\n"
        "   a. Launch: `nohup ' + PY + ' ' + REPO + '/harness_cli.py finalize-gate --gate 2 --phase 3 --project ' + REPO + ' > /tmp/gate2_finalize_r' + round + '.log 2>&1 & echo $!` — note the printed PID.\\n"
        "   b. Poll: every 15s run `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Repeat until DONE (cap 40 polls / ~10min). Still RUNNING past the cap → `kill <PID>` (reaps the whole tree), report \"GATE2: TIMEOUT\".\\n"
        "   c. Once DONE: `cat /tmp/gate2_finalize_r' + round + '.log` for the full output — identical to what a synchronous run would have printed.\\n"
    ),
    f"4. D4: `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold {_D4_THRESHOLD_P3}`. FAIL → add missing test implementations, re-run.",
    "5. CRG-ARCH: `BASELINE=\"\"; [ -f ' + REPO + '/.methodology/crg_baseline_p4.json ] && BASELINE=\"--baseline ' + REPO + '/.methodology/crg_baseline_p4.json\"; ' + PY + ' ' + REPO + '/harness_cli.py crg-arch-check --project ' + REPO + ' $BASELINE`. CI enforces this as an absolute floor on every push from Phase 3 onward, independent of the Gate 2/3/4 composite score — a low architecture sub-score can still let the composite pass, but this check will not. FAIL → the crg-arch-check output lists the low-cohesion communities / oversized functions; fix the underlying architecture issue, re-run.",
]

_GATE2_SCOPE_RULES = (
    "- DO NOT run advance-phase or push-milestone p3-post-gate2 (next phase does that).\\n"
    "- DO NOT edit .sessi-work/gate2_result.json to fake scores — fix the code.\\n"
    "- DO NOT modify harness/ (HR-17).\\n"
    "- ONLY run-gate/eval/finalize/spec-coverage/crg-arch-check + code fixes."
)

_PHASE3_ADVANCE_STEP_OVERRIDE = (
    "advance-phase — run BACKGROUNDED (internally runs `ruff check .` + `mypy .` + `pytest --cov-fail-under=100` over the WHOLE project as sequential subprocess calls inside one opaque Bash call; harmless today at this project\\'s size (~25s measured) but this cost only grows as more FRs/tests land, and a single opaque long Bash call is exactly what the 180s stall watchdog kills — same class of risk as GATE1, same fix):\\n"
    "   a. Launch: `nohup ' + PY + ' ' + REPO + '/harness_cli.py advance-phase --completed 3 --project ' + REPO + ' > /tmp/advance_r' + round + '.log 2>&1 & echo $!` — note the printed PID.\\n"
    "   b. Poll: every 15s run `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Repeat until DONE (cap 40 polls / ~10min). Still RUNNING past the cap → `kill <PID>` (reaps the whole tree), report \"ADVANCE: TIMEOUT\".\\n"
    "   c. Once DONE: `cat /tmp/advance_r' + round + '.log` for the full output — identical to what a synchronous run would have printed.\\n"
    "   advance-phase independently re-verifies EVERYTHING before it will advance (lint, types, coverage, document quality, reliability lint, architecture drift, Phase Truth, and more) — its own output tells you exactly what is missing. If it prints \"[BLOCKED] ...\", that message IS the fix instruction: read it verbatim and do exactly what it says (it often includes the precise command to run), then repeat the advance-phase backgrounded procedure (a/b/c). Do NOT guess what might be wrong — trust only what advance-phase itself reports.\\n"
    "   advance-phase is safe to re-run: it re-checks and re-reports without side effects until every check passes, so iterate within this round as many times as needed."
)


def generate_phase3() -> str:
    parts = [
        _HEADER_3,
        "",
        _render_meta(
            name="phase3-implementation",
            description=(
                "Phase 3 Implementation — per-FR TDD (RED/GREEN/IMPROVE/GATE1) "
                "+ milestones + Gate 2 exit (phase3_plan.md v2.12.0)"
            ),
            phases=_META_PHASES_3,
        ),
        "",
        B.RESOLVE_REPO_BLOCK + B.REPO_LOG_LINE + B.BUDGET_GUARD_BLOCK,
        "",
        B.WRITE_SCOPE_BLOCK,
        "",
        B.render_schemas(["VERDICT_SCHEMA", "RC_SCHEMA", "ENV_CHECK_SCHEMA", "CTX_SCHEMA_WITH_TITLES", "FR_LIST_SCHEMA", "GATE_VERIFY_SCHEMA", "PHASE_SCHEMA"]),
        _render_phase3_entry_preflight(),
        B.render_env_check(phase=3),
        B.render_manifest_integrity_fn(phase=3),
        _render_phase3_load_frs(),
        _render_per_fr_tdd(),
        _render_phase3_milestones(),
        B.render_gate_loop(
            gate_num=2, phase=3,
            log_msg=f"Gate 2 exit ({S.render_gate_dims_summary(2)})",
            prompt_steps=_GATE2_STEPS,
            pass_line_desc=S.render_gate_pass_line(2, d4_threshold=_D4_THRESHOLD_P3),
            scope_rules=_GATE2_SCOPE_RULES,
            d4_threshold=_D4_THRESHOLD_P3,
            on_fail_error_msg="Gate 2 did not PASS in 3 rounds (HR-08; write deferred_fixes.md + escalate to human)",
            include_manifest_integrity=True,
        ),
        B.render_preview_next_phase(3),
        B.render_advance_loop(
            phase=3, next_phase=4,
            precheck_steps=[
                (
                    "GUARD + PUSH ⑤ p3-post-gate2: `git -C ' + REPO + ' log --oneline --grep=\"P3-post-gate2)\" -1`. If a commit exists, skip the push. Else: `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p3-post-gate2 --project ' + REPO + ' --fr-ids ' + gate1Pass.join(',') + '`\\n"
                    "   Pre-flight (enforced): gate2_result.json composite ≥75 + per-FR Gate 1 sentinel .sessi-work/sentinels/g1_p3_<fr>.flag exists for every FR. If BLOCKED, read the error list and fix."
                ),
            ],
            advance_step_override=_PHASE3_ADVANCE_STEP_OVERRIDE,
            scope_extra="- DO NOT re-implement FRs.\\n",
            only_extra="push-milestone p3-post-gate2 + ",
            log_msg="p3-post-gate2 milestone + advance-phase --completed 3 (TDD-PRECHECK enforced)",
        ),
        _render_phase3_sync(),
        (
            "\nlog('Phase 3 workflow complete. Open .methodology/phase4_plan.md to continue.')\n"
            "return {\n"
            + S.render_phase_complete_marker()
            +
            "  phase: 3,\n"
            "  fr_count: frIds.length,\n"
            "  gate1_pass: gate1Pass,\n"
            "  gate2_status: gate2Pass ? 'PASS' : 'unknown',\n"
            "  advance_status: 'PASS',\n"
            "  sync_status: 'PASS',\n"
            "  artifacts: ['03-development/src/', 'tests/', '.methodology/gate2_result.json', 'HANDOVER.md'],\n"
            "  notes: 'Phase 3 complete per phase3_plan.md v2.12.0. All FRs Gate 1 PASS + Gate 2 PASS. Phase 4 (Testing) ready.',\n"
            "}\n"
        ),
    ]
    return "\n".join(p for p in parts if p is not None)
