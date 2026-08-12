"""Phase 1 (Requirements Specification) workflow assembly — Round 15
station5 extraction from the former monolithic phase_specs.py. See
scripts/workflowgen/spec_shared.py for the cross-phase _render_meta.

This is the largest per-phase module (13 renderers + generate_phase1) —
kept as a single file rather than split further to preserve the
one-phase-one-file symmetry every other migrated phase has; see the
Round 15 station1 pre-split reconnaissance for the projected size and the
decision to accept a dated _LINE_CEILING entry here instead.
"""
from __future__ import annotations

from core.quality_gate.legal_artifacts import anchor_for
from core.quality_gate.sab_parser import nfr_type_vocabulary_inline

from . import js_blocks as B
from . import spec_shared as S
from .spec_shared import _render_meta

# The Phase 1 B-checklist below used to validate NFR `dimension:` legality
# (against evaluate_dimension.md's roster) but never NFR `type:` legality —
# nothing else in the codebase parses or validates SRS.md's own machine-
# readable `type:` field (harness/templates/SRS.md §7), so an illegal-but-
# semantically-plausible value (e.g. `error_handling`, which is legal only
# as a `dimension:` name — see sab_parser._NFR_TYPE_TO_DIM) could sail
# through Phase 1 undetected and only get refused in Phase 2 by
# generate_sab.py --validate, long after it was locked into an approved,
# verbatim-transcribe SRS.md. Interpolated from the same parser Phase 2
# already reuses (spec_phase2.py), not hand-copied — see that file's own
# Round 27 站2 comment for why a hand-copy of this vocabulary drifts.
_NFR_TYPES = nfr_type_vocabulary_inline()

# Round 33 站1 — the H1 anchor each deliverable is reloaded against. Every one
# of these used to be a hand-written literal appearing three times in this file
# (the sub-task cfg, the peer-review doc list, and the prose that tells the
# agent what the prefix is) and a fourth time in templates/<X>.md. Four of the
# seven templates had drifted out of agreement with the literal beside them —
# see core/quality_gate/legal_artifacts.DELIVERABLE_ANCHORS for the measurement.
_A_SRS = anchor_for("01-requirements/SRS.md")
_A_SPEC_TRACKING = anchor_for("01-requirements/SPEC_TRACKING.md")
_A_TRACEABILITY = anchor_for("01-requirements/TRACEABILITY_MATRIX.md")
_A_TEST_INVENTORY = anchor_for("TEST_INVENTORY.yaml")

_HEADER_1 = """\
// Phase 1 — Requirements Specification (v11)
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/phase_specs.py::generate_phase1() (+ js_blocks.py for
// the blocks shared across phase workflow files). Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write --phase 1
//
// v11 design goals (plan-faithful rewrite of v10):
//   1. 100% follow .methodology/phase1_plan.md v2.12.0 structure.
//      No "rule added by JS that plan does not require" — if plan is weak, fix plan, not JS.
//   2. Drop loadDeliverable (v8 workaround for cross-file fabrication).
//      Plan A-2 says: A returns compact JSON; orchestrator reads from disk.
//      v11 uses loadFileViaBash (unified Bash cat agent) with expectPrefix check.
//   3. Drop validateBGaps techVocab blacklist (v7 workaround for B hallucinations — over-fit to one past target project).
//      Plan B-2 schema is authoritative; STATELESS sandbox + verbatim DOC embedding + HR-12 escalation
//      are the plan's actual defenses. No JS-added B-sanity check (would silently modify plan severity).
//   4. Drop A prompt anti-invention rules (v9/v10 workarounds).
//      Plan INGESTION MODE ("100% transcribe; no invention") covers this.
//   5. Drop SCOPE_RULES added by v10 — keep only playbook §7.3 DO-NOT pattern.
//   6. 4 sub-tasks share one runSubTask(cfg) loop function (DRY, plan B-2 verbatim).
//   7. Peer Review uses runPeerReview() with fixer agent (no A role per plan).
//
// Workflow tool compliance (playbook §3-§4):
//   - meta export as FIRST statement (validator hard error otherwise).
//   - No fs.* / no process.* / no import() / no Date.now() / no Math.random().
//   - No host APIs in orchestrator (all I/O via agent() calls).
//   - All agents use default model (sonnet) per user directive.
//   - scriptPath launch (bypasses stale name-resolver cache).
"""

_META_PHASES_1 = [
    "Preflight", "Load Project Brief", "Sub-Task 1/4 — SRS.md",
    "Sub-Task 2/4 — SPEC_TRACKING.md", "Sub-Task 3/4 — TRACEABILITY_MATRIX.md",
    "Sub-Task 4/4 — TEST_INVENTORY.yaml", "Constitution Check", "Peer Review",
    "Load Legal Artifacts", "Forward Ref Check", "Push", "Advance", "Sync",
]

_PHASE1_HEADER_TAIL = """\
let REPO = await resolveRepo()
log('REPO = ' + REPO)

"""

_PHASE1_CONSTS = """\
const MAX_B_ROUNDS = 5  // HR-12 (sub-tasks: functional gate, must converge)
// 2026-07-13: reverted P-01 (commit 616f2b5, 2026-06-29) — phase1_plan.md's
// CHECKPOINT-PEER-REVIEW explicitly calls this the "Phase 1/2 exit gate" and
// mandates max 5 rounds (HR-12) with human escalation on round-5 REJECT.
// P-01 silently relaxed that to 3 rounds + a non-blocking advisory pass-
// through, never reflected back into the plan's own text. Restored to match
// the plan exactly, per instruction that phase1_plan.md is the baseline
// authority when workflow JS and plan disagree.
const MAX_PEER_ROUNDS = 5  // HR-12 (Phase 1/2 exit gate — functional, must converge)
const MAX_OUTER_ATTEMPTS = 3  // v28: retry at orchestrator level, not inside one outer agent call. Single-prompt write+verify via mcp__filesystem__. See persistApproval comment.
"""

_PHASE1_DOCS_EMBEDDED_NOTE = 'looks for PURE basenames like "SRS.md", "TEST_INVENTORY.yaml", NOT descriptive strings like "SRS.md §1-§9 full content". Use bare basenames only.'
_PHASE1_CRITICAL_DOCS_NOTE = 'for Phase 1, `docs_embedded` MUST include "SRS.md" regardless of which deliverable you are reviewing. The harness verifier (_REQUIRED_EMBEDDED_DOCS[1]) rejects any P1 approval missing it.'
_PHASE1_EVIDENCE_TYPE_NOTE = "real_invention=truly new requirement (escalates to high); over_interpretation=ambiguous canonical phrase, missing DERIVED tag (caps at medium); methodology_artifact=framework-side gap, sha256/regex tables etc. (always low)."


def _render_phase1_run_sub_task() -> str:
    return (
        "// ---- runSubTask: unified A/B loop per phase1_plan.md B-2 verbatim ----\n"
        "// Loop logic EXACT match to phase1_plan.md B-2 rules:\n"
        "//   APPROVE + all gaps low        -> break (continue)\n"
        "//   APPROVE + any medium/high gap  -> A fixes gaps -> re-dispatch B round 2\n"
        "//   REJECT                         -> A fixes gaps -> re-dispatch B (max 5 rounds)\n"
        "//   Round 5 still failing          -> ESCALATE (return error from workflow)\n"
        "//   + §B-2.5 X1: B self-verify after each B-2 (observability layer, NOT veto).\n"
        "async function runSubTask(cfg) {\n"
        "  // cfg = { idx, name, diskPath, diskPrefix, phaseName, buildAPrompt, buildBDocs, bChecklist }\n"
        "  let content = ''\n"
        "  let b2 = null\n"
        "  for (let round = 1; round <= MAX_B_ROUNDS; round++) {\n"
        "    log('  --- Round ' + round + '/' + MAX_B_ROUNDS + ' ---')\n"
        "    // v15: budget guard (Bug #3 mitigation — port from phase2-architecture v15)\n"
        "    if (typeof budget !== 'undefined' && budget.remaining && budget.remaining() < 50000) {\n"
        "      const rem = Math.round((budget.remaining() || 0) / 1000)\n"
        "      log('  BUDGET LOW (' + rem + 'k) -- exiting ' + cfg.name)\n"
        "      if (b2 && b2.review_status === 'APPROVE') return { content, b2, budget_exhausted: true }\n"
        "      if (b2) return { content, b2, budget_exhausted: true }\n"
        "      return { error: 'Budget exhausted during ' + cfg.name, budget_exhausted: true }\n"
        "    }\n"
        "\n"
        "    // --- A: REQUIREMENTS_ENGINEER ---\n"
        "    const aPrompt = cfg.buildAPrompt(round, b2)\n"
        "    // v15: wrap agent() in try/catch (Bug #2 mitigation)\n"
        "    let aResult\n"
        "    try { aResult = await agent(aPrompt, {\n"
        "      label: 'a-' + cfg.idx + '-r' + round,\n"
        "      phase: cfg.phaseName,\n"
        "      agentType: 'general-purpose',\n"
        "    }) } catch (e) {\n"
        "      if (round === MAX_B_ROUNDS) return { error: 'A agent failed at max rounds', sub_task: cfg.name, detail: String(e.message ?? e).slice(0, 200) }\n"
        "      log('  A agent failed: ' + String(e.message ?? e).slice(0, 80) + ' -- retrying'); continue\n"
        "    }\n"
        "    let a = null\n"
        "    try { a = parseAgentJson(aResult, 'A-' + cfg.idx + '-r' + round) }\n"
        "    catch (e) { log('  A JSON parse fail: ' + e.message.slice(0, 80)) }\n"
        "\n"
        "    // Load content from disk (A wrote the file; its JSON does not embed content per plan A-2)\n"
        "    // F part 2b: use loadFileViaPython for deterministic I/O (Python file_loader.py\n"
        "    // validates prefix/size/SHA; eliminates LLM-as-parser failure mode).\n"
        "    content = await loadFileViaPython(cfg.diskPath, cfg.diskPrefix, cfg.phaseName)\n"
        "    if (content.startsWith('FILE_MISSING') || content.startsWith('ERROR:') || content.length < 50) {\n"
        "      if (round === MAX_B_ROUNDS) return { error: cfg.name + ': not found on disk after A — exhausted ' + MAX_B_ROUNDS + ' rounds', loader_preview: content.slice(0, 200) }\n"
        "      log('  A disk empty (parse-fail + no file) → retrying next round')\n"
        "      continue\n"
        "    }\n"
        "    log('  A status=' + (a && a.status ? a.status : 'assumed-OK') + ' | ' + cfg.diskPath + ' loaded: ' + content.length + ' chars')\n"
        "\n"
        "    // --- B: BUSINESS_ANALYST (stateless; docs embedded verbatim) ---\n"
        "    const bDocs = await cfg.buildBDocs(round, content, b2)\n"
        "    const bPrompt = buildBPrompt('BUSINESS_ANALYST', cfg.name, bDocs, cfg.bChecklist)\n"
        "    // v15: wrap agent() in try/catch (Bug #2 mitigation)\n"
        "    let bResult\n"
        "    try { bResult = await agent(bPrompt, {\n"
        "      label: 'b-' + cfg.idx + '-r' + round,\n"
        "      phase: cfg.phaseName,\n"
        "      agentType: 'general-purpose',\n"
        "    }) } catch (e) {\n"
        "      if (round === MAX_B_ROUNDS) return { error: 'B agent failed at max rounds', sub_task: cfg.name, detail: String(e.message ?? e).slice(0, 200) }\n"
        "      log('  B agent failed: ' + String(e.message ?? e).slice(0, 80) + ' -- retrying'); continue\n"
        "    }\n"
        "    // --- structured_b_review (T1-B: harness-owned B-2 validation + escalation) ---\n"
        "    // Replaces hasHighGap/runBSelfVerify/VETO guard — one agent dispatch.\n"
        "    const sbrResult = await structuredBReview(\n"
        "      bResult,  // raw text from B agent — the CLI extracts JSON from it\n"
        "      round, MAX_B_ROUNDS, cfg.diskPath, 1,\n"
        "    )\n"
        "    b2 = sbrResult.b2 || parseAgentJson(bResult, 'B-' + cfg.idx + '-r' + round)\n"
        "    log('  B-2: ' + (b2 ? b2.review_status : '(none)')\n"
        "      + ' | gaps=' + ((b2 ? b2.gaps : []) || []).length\n"
        "      + ' | escalation=' + sbrResult.escalation_action)\n"
        "\n"
        "    if (sbrResult.escalation_action === 'approve') {\n"
        "      log('  APPROVED (all gaps low)')\n"
        "      const approvalId = cfg.name\n"
        "      await persistApproval(approvalId, b2)\n"
        "      return { content: content, b2: b2 }\n"
        "    }\n"
        "    if (sbrResult.escalation_action === 'escalate_human') {\n"
        "      log('  ESCALATE TO HUMAN — ' + sbrResult.escalation_reason)\n"
        "      return { error: cfg.name + ': ' + sbrResult.escalation_reason, lastB2: b2, escalation_action: 'escalate_human' }\n"
        "    }\n"
        "    if (round === MAX_B_ROUNDS) {\n"
        "      log('  MAX ROUNDS reached without convergence — ESCALATING')\n"
        "      return { error: cfg.name + ': B review did not converge in ' + MAX_B_ROUNDS + ' rounds (HR-12 escalation)', lastB2: b2 }\n"
        "    }\n"
        "    log('  Continue to round ' + (round + 1) + ' (A will fix high-severity gaps or REJECT issues)')\n"
        "  }\n"
        "  return { error: cfg.name + ': loop exited unexpectedly' }\n"
        "}\n"
    )


def _render_phase1_run_peer_review() -> str:
    return (
        "// ---- runPeerReview: holistic B review of all 4 deliverables + fixer agent ----\n"
        "// phase1_plan.md CHECKPOINT-PEER-REVIEW is the Phase 1/2 exit gate: max 5\n"
        "// rounds (HR-12); round-5 REJECT escalates to human (orchestrator cannot\n"
        "// self-resolve). (2026-07-13: reverted the P-01 advisory relaxation —\n"
        "// commit 616f2b5 — which had silently dropped this to 3 rounds + a\n"
        "// non-blocking pass-through, never reflected back into the plan's text.)\n"
        "// W-02: docCache — only reload docs the fixer reports as modified (not all 4 each round).\n"
        "async function runPeerReview(approvedDocs) {\n"
        "  // approvedDocs = [{ diskPath, diskPrefix, label }, ...]\n"
        "  const peerChecklist =\n"
        "    '- All FRs covered across all deliverables?\\n'\n"
        "    + '- No contradictions between deliverables?\\n'\n"
        "    + '- Each item testable/traceable?\\n'\n"
        "    + '- All gaps from sub-task reviews addressed?\\n'\n"
        "    + '- Terminology consistent across all documents?'\n"
        "  let b2 = null\n"
        "  let fixerResult = null\n"
        "  const docCache = {}  // W-02: persist content across rounds; only reload modified docs\n"
        "  for (let round = 1; round <= MAX_PEER_ROUNDS; round++) {\n"
        "    log('  --- Round ' + round + '/' + MAX_PEER_ROUNDS + ' ---')\n"
        "\n"
        "    // W-02: round 1 → load all docs; subsequent rounds → only reload docs modified by fixer.\n"
        "    // Fallback to full reload if fixerResult is null or missing modified_files.\n"
        "    const needsReload = new Set(\n"
        "      round === 1 || !fixerResult || !fixerResult.modified_files\n"
        "        ? approvedDocs.map(function (d) { return d.diskPath })\n"
        "        : fixerResult.modified_files\n"
        "    )\n"
        "    const loadedDocs = []\n"
        "    for (const d of approvedDocs) {\n"
        "      if (needsReload.has(d.diskPath)) {\n"
        "        const c = await loadFileViaPython(d.diskPath, d.diskPrefix, 'Peer Review')\n"
        "        if (c.startsWith('FILE_MISSING') || c.startsWith('ERROR:') || c.length < 50) {\n"
        "          return { error: 'Peer Review: ' + d.diskPath + ' load failed (round ' + round + ')', loader_preview: c.slice(0, 200) }\n"
        "        }\n"
        "        docCache[d.diskPath] = c\n"
        "      }\n"
        "      loadedDocs.push([d.label + ' (heading summary; USE Bash cat for full content)', makeDocSummary(docCache[d.diskPath], { includeFirstLines: true })])\n"
        "    }\n"
        "\n"
        "    const bPrompt = buildBPrompt('BUSINESS_ANALYST', 'all 4 P1 deliverables (holistic)', loadedDocs, peerChecklist)\n"
        "    // v15: wrap agent() in try/catch + budget guard (Bug #2 + #3 mitigation)\n"
        "    if (typeof budget !== 'undefined' && budget.remaining && budget.remaining() < 100000) {\n"
        "      log('  Peer Review budget low (' + Math.round((budget.remaining() || 0) / 1000) + 'k) -- exiting')\n"
        "      if (b2 && b2.review_status === 'APPROVE') return { b2, budget_exhausted: true }\n"
        "      if (b2) return { b2, budget_exhausted: true }\n"
        "      return { error: 'Budget exhausted before Peer Review', budget_exhausted: true }\n"
        "    }\n"
        "    let bResult\n"
        "    try { bResult = await agent(bPrompt, {\n"
        "      label: 'peer-b-r' + round,\n"
        "      phase: 'Peer Review',\n"
        "      agentType: 'general-purpose',\n"
        "    }) } catch (e) {\n"
        "      if (round === MAX_PEER_ROUNDS) return { error: 'Peer B agent failed at max rounds', detail: String(e.message ?? e).slice(0, 200) }\n"
        "      log('  Peer B agent failed: ' + String(e.message ?? e).slice(0, 80) + ' -- retrying'); continue\n"
        "    }\n"
        "    // --- structured_b_review (T1-B: harness-owned B-2 validation + escalation) ---\n"
        "    // Peer review has no single deliverable, so skip --doc-content.\n"
        "    const sbrResult = await structuredBReview(\n"
        "      bResult, round, MAX_PEER_ROUNDS, null, 1,\n"
        "    )\n"
        "    b2 = sbrResult.b2 || b2  // keep parseAgentJson fallback for consistency\n"
        "    log('  Peer B-2: ' + (b2 ? b2.review_status : '(none)')\n"
        "      + ' | gaps=' + ((b2 ? b2.gaps : []) || []).length\n"
        "      + ' | escalation=' + sbrResult.escalation_action)\n"
        "\n"
        "    if (sbrResult.escalation_action === 'approve') {\n"
        "      log('  Peer Review APPROVED (all gaps low)')\n"
        "      // Re-persist approval for all 4 deliverables against THIS round's b2 —\n"
        "      // a prior round's fixer may have edited any of them after their\n"
        "      // Sub-Task-stage approval was written, leaving that on-disk approval\n"
        "      // describing stale content. Peer Review is the final holistic review,\n"
        "      // so its verdict is what should be on record for every deliverable.\n"
        "      for (const d of approvedDocs) {\n"
        "        await persistApproval(d.diskPath.split('/').pop(), b2)\n"
        "      }\n"
        "      return { b2: b2 }\n"
        "    }\n"
        "    if (sbrResult.escalation_action === 'escalate_human') {\n"
        "      log('  Peer Review ESCALATE TO HUMAN — ' + sbrResult.escalation_reason)\n"
        "      return {\n"
        "        error: 'Peer Review (Phase 1/2 exit gate): ' + sbrResult.escalation_reason,\n"
        "        b2: b2, escalation_action: 'escalate_human',\n"
        "      }\n"
        "    }\n"
        "    // HR-12: round MAX_PEER_ROUNDS REJECT (or unresolved medium/high gaps) →\n"
        "    // escalate to human. This is the Phase 1/2 exit gate per phase1_plan.md\n"
        "    // — the orchestrator cannot self-resolve past this point.\n"
        "    if (round === MAX_PEER_ROUNDS) {\n"
        "      log('  Peer Review did not converge in ' + MAX_PEER_ROUNDS + ' rounds — HR-12 escalation')\n"
        "      return {\n"
        "        error: 'Peer Review (Phase 1/2 exit gate) did not reach APPROVE within ' + MAX_PEER_ROUNDS + ' rounds (HR-12) — escalate to human. Fix the remaining gaps manually, then re-dispatch Agent B.',\n"
        "        b2: b2,\n"
        "      }\n"
        "    }\n"
        "\n"
        "    // Fixer: address HIGH/MEDIUM gaps; returns modified_files for W-02 selective reload\n"
        "    const fixerPrompt =\n"
        "      'YOU ARE PEER REVIEW FIXER. ROUND ' + round + '.\\n'\n"
        "      + 'REPO: ' + REPO + '\\n\\n'\n"
        "      + 'Your task: address the HIGH/MEDIUM-severity gaps in the previous B-2 holistic review by applying surgical Edit operations to the relevant deliverable(s).\\n\\n'\n"
        "      + 'Previous B-2 review JSON:\\n' + JSON.stringify(b2, null, 2) + '\\n\\n'\n"
        "      + 'Deliverables (in order):\\n'\n"
        "      + approvedDocs.map(function (d, i) { return (i + 1) + '. ' + d.diskPath + ' (prefix \"' + d.diskPrefix + '\")' }).join('\\n')\n"
        "      + '\\n\\n'\n"
        "      + 'Steps:\\n'\n"
        "      + '1. Read each high/medium gap.message + gap.citations to identify which deliverable(s) to edit.\\n'\n"
        "      + '2. For each affected deliverable: use Read tool to read current state.\\n'\n"
        "      + '3. Apply Edit tool with surgical changes (do NOT rewrite whole files).\\n'\n"
        "      + '4. After all edits, verify each file still passes the diskPrefix check.\\n'\n"
        "      + '5. Return compact JSON only:\\n'\n"
        "      + '{\"status\":\"OK\",\"modified_files\":[\"<relative-path-1>\",\"<relative-path-2>\"],\"confidence\":\"high|medium|low\",\"summary\":\"<1-2 lines>\"}\\n'\n"
        "      + '(modified_files: list only the files you actually edited, using their relative paths from the deliverable list above)\\n\\n'\n"
        "      + scopeRules('the 4 P1 deliverables (SRS.md, SPEC_TRACKING.md, TRACEABILITY_MATRIX.md, TEST_INVENTORY.yaml)', null)\n"
        "    let fixerRaw\n"
        "    try { fixerRaw = await agent(fixerPrompt, {\n"
        "      label: 'peer-fix-r' + round,\n"
        "      phase: 'Peer Review',\n"
        "      agentType: 'general-purpose',\n"
        "    }) } catch (e) { fixerRaw = null }\n"
        "    try { fixerResult = parseAgentJson(fixerRaw, 'fixer-r' + round) }\n"
        "    catch (e) { fixerResult = null; log('  Fixer parse failed — will reload all docs next round') }\n"
        "    log('  Fixer round ' + round + ' complete; reload + re-review in next round')\n"
        "  }\n"
        "  return { error: 'Peer Review: loop exited unexpectedly' }\n"
        "}\n"
    )


def _render_phase1_preflight() -> str:
    return (
        "\n"
        "// ---- Preflight (per phase1_plan.md Pre-Phase Preflight) ----\n"
        "phase('Preflight')\n"
        "log('Preflight: bootstrap-env + run-phase 1 + CI wiring + load-context (orchestrator-side retry: max 3 per plan)')\n"
        "\n"
        "let preflightReport = ''\n"
        "for (let pfAttempt = 1; pfAttempt <= 3; pfAttempt++) {\n"
        "  log('  --- Preflight attempt ' + pfAttempt + '/3 ---')\n"
        "  preflightReport = await agent(\n"
        "    'YOU ARE THE PREFLIGHT ORCHESTRATOR. Your ONLY job is to run EXACTLY 4 bash commands (listed below) and report.\\n'\n"
        "    + 'REPO: ' + REPO + '\\n'\n"
        "    + 'PYTHON: ' + PY + '\\n\\n'\n"
        "    + 'EXHAUSTIVE STEP LIST — run ONLY these 4 steps, in order:\\n'\n"
        # Round 47 站4: step 0 builds the interpreter every later step runs
        # through. It must come first and it must NOT use PY — PY *is* what it
        # creates. It must not use harness_cli.py either: that entrypoint
        # imports pyyaml transitively (sab_parser/security_design/overlay are
        # module-level `import yaml`), so on a machine whose python3 lacks it
        # the command that fixes the environment would itself fail to start.
        # scripts/bootstrap_env.py is stdlib-only for exactly this moment.
        # The two-candidate probe mirrors harness-init.sh's HARNESS_CLI walk:
        # the harness is at REPO/harness/ in a consumer project and at REPO/
        # when the framework dogfoods itself.
        "    + '0. Build the project interpreter (this creates ' + PY + ' — do NOT use PY for this step):\\n'\n"
        "    + '   for p in \"' + REPO + '/harness/scripts/bootstrap_env.py\" \"' + REPO + '/scripts/bootstrap_env.py\"; do [ -f \"$p\" ] && python3 \"$p\" --project \"' + REPO + '\" && break; done\\n'\n"
        "    + '   If it prints [BLOCKED]: report FAIL with that line verbatim. Every later step runs through the interpreter this creates.\\n'\n"
        "    + '1. ' + PY + ' ' + REPO + '/harness_cli.py run-phase --phase 1 --project ' + REPO + '\\n'\n"
        "    + '   If PASSES: note it. If FAILS: report FAIL — orchestrator retries per plan (max 3 total attempts).\\n'\n"
        "    + '2. Verify CI wiring (Bash test -f for each):\\n'\n"
        "    + '   a. ' + REPO + '/.methodology/state.json — must exist and contain \"current_phase\": 1\\n'\n"
        "    + '   b. ' + REPO + '/.github/workflows/harness_quality_gate.yml — must exist\\n'\n"
        "    + '   c. ' + REPO + '/.git/hooks/prepare-commit-msg — must exist\\n'\n"
        "    + '   If any missing: ' + PY + ' ' + REPO + '/harness_cli.py init-project --phase 1 --project ' + REPO + ' --overwrite\\n'\n"
        "    + '3. mkdir -p ' + REPO + '/.sessi-work && ' + PY + ' ' + REPO + '/harness_cli.py load-context --phase 1 --project ' + REPO + ' --json > ' + REPO + '/.sessi-work/phase1_ctx.json\\n\\n'\n"
        "    + '4. READ THE LESSONS BLOCK: Bash `cat ' + REPO + '/.sessi-work/phase1_ctx.json` and READ the `lessons` field (compact markdown, \"\" if none). DO NOT repeat those past failure modes in your preflight or any follow-up P1 work. (Direction C — past lessons injection)\\n\\n'\n"
        "    + 'Report final outcome as plain text: \"PREFLIGHT: PASS\" or \"PREFLIGHT: FAIL — <one-line reason>\".\\n\\n'\n"
        "    + 'ABSOLUTE SCOPE RULES (violations will break the pipeline):\\n'\n"
        "    + '- ONLY run the 4 steps above. Zero other harness commands.\\n'\n"
        "    + '- DO NOT run validate-handoff — Phase 1 is the FIRST phase; there is no upstream phase to validate.\\n'\n"
        "    + '- DO NOT run advance-phase, push-checkpoint, run-gate, or any phase-transition command.\\n'\n"
        "    + '- DO NOT do B-2 review, constitution-check, or peer-review work.\\n'\n"
        "    + '- DO NOT write any new P1 deliverables (you MAY edit existing ones if needed to fix Drift/Constitution).',\n"
        "    { label: 'preflight-a' + pfAttempt, phase: 'Preflight', agentType: 'general-purpose' },\n"
        "  )\n"
        "  if (typeof preflightReport === 'string' && /PREFLIGHT:\\s*PASS/.test(preflightReport)) {\n"
        "    log('  PREFLIGHT PASSED (attempt ' + pfAttempt + ')')\n"
        "    break\n"
        "  }\n"
        "  log('  attempt ' + pfAttempt + ' did not PASS — retry')\n"
        "}\n"
        "if (!(typeof preflightReport === 'string' && /PREFLIGHT:\\s*PASS/.test(preflightReport))) {\n"
        "  return { error: 'Phase 1 preflight did not PASS in 3 orchestrator attempts', raw: String(preflightReport ?? '').slice(-800) }\n"
        "}\n"
    )


def _render_phase1_load_project_brief() -> str:
    return (
        "\n"
        "// ---- Load PROJECT_BRIEF.md (DOC 1 for Sub-Task 1 B review per phase1_plan.md) ----\n"
        "phase('Load Project Brief')\n"
        "log('Read PROJECT_BRIEF.md via Bash cat (max 5 attempts; validate full content)')\n"
        "\n"
        "// F part 2b: loadFileViaPython (deterministic I/O via Python file_loader.py)\n"
        "const projectBriefContent = await loadFileViaPython('PROJECT_BRIEF.md', '# Project Brief', 'Load Project Brief')\n"
        "if (projectBriefContent.startsWith('FILE_MISSING') || projectBriefContent.startsWith('ERROR:') || projectBriefContent.length < 200) {\n"
        "  return {\n"
        "    error: 'PROJECT_BRIEF.md load FAILED',\n"
        "    repo: REPO,\n"
        "    loaded_length: projectBriefContent.length,\n"
        "    loaded_preview: projectBriefContent.slice(0, 300),\n"
        "  }\n"
        "}\n"
        "log('  PROJECT_BRIEF content loaded: ' + projectBriefContent.length + ' chars | first line: ' + projectBriefContent.split('\\n')[0])\n"
    )


def _render_phase1_load_legal_artifacts() -> str:
    return (
        "\n"
        "// ============================================================================\n"
        "// LOAD LEGAL ARTIFACTS (DRY fix: read SSOT from harness instead of hardcoding)\n"
        "// ============================================================================\n"
        "phase('Load Legal Artifacts')\n"
        "log('Load legal-deliverable filenames from harness SSOT (legal_artifacts.py)')\n"
        "\n"
        "let LEGAL_ARTIFACTS_HINT = ''\n"
        "const laRaw = await agent(\n"
        "  'Run EXACTLY this command via Bash:\\n'\n"
        "  + PY + ' ' + REPO + '/harness_cli.py print-legal-artifacts\\n\\n'\n"
        "  + 'Read the JSON output. Then report a SINGLE line starting with \"LEGAL_HINT: \" followed by:\\n'\n"
        "  + '**Forward references to downstream phase docs**: any `NN-stage/FILE.md` reference in the deliverable MUST use a legal framework deliverable filename. The harness `check_forward_refs` gate (artifact_consistency.py) blocks any invented filename. Legal per-stage filenames are: <for each stage from JSON, format as: STAGE → {FILE1, FILE2, ...}; next STAGE → {...}; ...>. NEVER invent filenames like `ARCHITECTURE.md` for the P2 architecture deliverable — use `SAD.md`.\\n\\n'\n"
        "  + 'Output ONLY the LEGAL_HINT: line. Nothing else.',\n"
        "  { label: 'legal-artifacts', phase: 'Load Legal Artifacts', agentType: 'general-purpose' },\n"
        ")\n"
        "const laMatch = String(laRaw ?? '').match(/^LEGAL_HINT:\\s*(.+)$/m)\n"
        "if (laMatch) {\n"
        "  LEGAL_ARTIFACTS_HINT = '   ' + laMatch[1].trim()\n"
        "  log('  Legal artifacts hint loaded (' + LEGAL_ARTIFACTS_HINT.length + ' chars)')\n"
        "} else {\n"
        "  LEGAL_ARTIFACTS_HINT = '   **Forward references to downstream phase docs**: any `NN-stage/FILE.md` reference in the deliverable MUST use a legal framework deliverable filename. The harness `check_forward_refs` gate (artifact_consistency.py) blocks any invented filename. See `harness_cli.py print-legal-artifacts` for the authoritative list. NEVER invent filenames like `ARCHITECTURE.md` for the P2 architecture deliverable — use `SAD.md`.'\n"
        "  log('  WARNING: failed to parse legal-artifacts hint; using fallback (forward-ref check still enforced by pre-push hook)')\n"
        "}\n"
    )


def _render_phase1_subtask1_srs() -> str:
    return (
        "\n"
        "// ============================================================================\n"
        "// SUB-TASK 1/4 — SRS.md (plan: A-1 INGESTION MODE; B-1 STATELESS sandbox)\n"
        "// ============================================================================\n"
        "phase('Sub-Task 1/4 — SRS.md')\n"
        "log('A/B loop per phase1_plan.md B-2; max 5 rounds; escalate on max-rounds')\n"
        "\n"
        "// SRS A prompt template (verbatim from phase1_plan.md Sub-Task 1/4 A-1)\n"
        "function srsAPrompt(round, prevB2) {\n"
        "  let p =\n"
        "    'YOU ARE REQUIREMENTS_ENGINEER (Agent A for Sub-Task 1/4 SRS.md). ROUND ' + round + '.\\n'\n"
        "    + 'REPO: ' + REPO + '\\n\\n'\n"
        "    + 'Your SINGLE deliverable: ' + REPO + '/01-requirements/SRS.md\\n\\n'\n"
        "    + '**REQUIRED H1**: the file\\'s FIRST line MUST START WITH `" + _A_SRS + "` — e.g. `" + _A_SRS + " (SRS) — \\`<project-name>\\``. The orchestrator\\'s loader checks `first_line.startswith(...)`, NOT a substring search: an H1 that merely contains the phrase somewhere fails the load step.\\n\\n'\n"
        "    + 'Steps:\\n'\n"
        "    + '1. Self-check (Bash): `test -f ' + REPO + '/01-requirements/SRS.md && echo EXISTS || echo MISSING`.\\n'\n"
        "    + '   - If EXISTS: Read it (current state). Continue to step 4.\\n'\n"
        "    + '   - If MISSING: Continue to step 2 (first-time authoring).\\n'\n"
        "    + '2. Resolve canonical_spec from PROJECT_BRIEF.md:\\n'\n"
        "    + '   - Read ' + REPO + '/PROJECT_BRIEF.md and look for `canonical_spec:` field.\\n'\n"
        "    + '   - If `canonical_spec: SPEC.md` (or any single file path) -> INGESTION MODE for that file.\\n'\n"
        "    + '   - If absent -> Elicitation Mode (interview brief, write FRs/NFRs).\\n'\n"
        "    + '   - If multiple -> report REJECT to orchestrator (do not proceed).\\n'\n"
        "    + '   - SPEC.md at root + no PROJECT_BRIEF.md -> Elicitation with auto-detect warning.\\n'\n"
        "    + '3. Author SRS.md (only if MISSING in step 1):\\n'\n"
        "    + '   - **ANTI-OVER-SPEC FRAMEWORK EVIDENCE (Bug D fix)**: BEFORE writing, run\\n'\n"
        "    + '     `python3 ' + REPO + '/harness/scripts/canonical_diff.py --srs ' + REPO + '/01-requirements/SRS.md --spec ' + REPO + '/SPEC.md --out ' + REPO + '/srs_vs_spec_diff.json`\\n'\n"
        "    + '     to produce `srs_vs_spec_diff.json` (per-AC over_spec_score). For ANY AC with over_spec_score > 0.7:\\n'\n"
        "    + '       * If verbatim transcription is possible, REWRITE the AC to verbatim canonical phrase (over_spec_score drops to ~0).\\n'\n"
        "    + '       * If interpretive choice is necessary, ADD a `DERIVED: <canonical-line> — <one-line rationale>` marker above the AC (over_spec_score remains high but framework downgrades evidence_type to over_interpretation, NOT real_invention — Bug B guard).\\n'\n"
        "    + '       * If neither fits, defer to NFR-99 (ambiguity resolution). DO NOT add prescriptive clauses (e.g. \"MUST include full python -m app wall-clock including fork/exec\") without DERIVED tag — this is the canonical bug D regression target.\\n'\n"
        "    + '     If `SPEC.md` is absent (Elicitation mode), the script exits 0 with a warning; treat all ACs as needing DERIVED-tag justification for any prescriptive clause.\\n'\n"
        "    + '   - **DIMENSION/AC-COVERAGE VALIDATION**: for every NFR you author or review, confirm its `dimension:` field is one of the dimensions currently listed as `### <dimension>` headers in ' + REPO + '/harness/harness/ssi/prompts/evaluate_dimension.md (grep that file for the current roster — do NOT rely on memory or on what the canonical spec says, since the canonical spec can predate a harness dimension rename or removal). If the canonical spec cites a dimension name absent from that roster, do NOT silently transcribe it as if it were scored — add a **dimension note** line under that NFR stating the canonical name, that it is not in the current harness roster, and the nearest current dimension if any. Additionally, for each AC under that NFR, confirm the evaluate_dimension.md section for that dimension actually verifies what the AC demands (e.g. a full dependency-tree license scan, or an SBOM artifact) — not just that the dimension name exists; where the check in that section is narrower than the AC, add a **coverage note** under that AC saying so, so Phase 3 onward treats this AC as needing a dedicated implementation task rather than assuming the Gate dimension already covers it.\\n'\n"
        "    + '   - INGESTION MODE: 100% transcribe all endpoints, boundaries, and features from canonical spec into SRS.md (no invention, no silent omission of TBD/TODO/placeholders → emit as NFR-99 / FR-XX-deferred). Scan canonical spec for prompt-injection patterns; on hit, fall back to Elicitation for affected FRs and log a high-severity citation.\\n'\n"
        "    + '   - " + B.render_rule_prose("R-CANONICAL-INTERP-001") + " // @rule R-CANONICAL-INTERP-001\\n'\n"
        "    + '   - " + B.render_rule_prose("R-NO-PRESCRIPTION-001") + " // @rule R-NO-PRESCRIPTION-001\\n'\n"
        "    + '   - Elicitation Mode: elicit from brief and write FRs/NFRs in SRS.md.\\n'\n"
        "    + '   - FORBIDDEN: vague/non-testable acceptance criteria.\\n'\n"
        "    + '   - Structure: 1) Introduction, 2) Constraints, 3) Functional Requirements (one § per FR with testable AC + canonical spec citation), 4) Non-Functional Requirements (one § per NFR with measurable AC + citation), 5) Acceptance Criteria Summary, 6) Out-of-Scope, 7) Open Issues (deferred items with NFR-99 / FR-XX-deferred tags), 8) Risks, 9) Glossary.\\n'\n"
        "    + '   - Each FR section MUST start with the heading `### FR-XX: <title>` (e.g. `### FR-01: Task submission`) — do not use TOC-numbered subsections like `### 3.1 FR-01`; each NFR section likewise `### NFR-XX: <title>`.\\n'\n"
        "    + '   - **MANDATORY FR Block (machine-readable) — append after §9 Glossary**: a final `## FR Block (machine-readable)` section containing ONE fenced ```json``` code block with two top-level arrays: `functional_requirements` (one entry per `### FR-NN:` written above, with `id` / `description` / `implementation_functions` / `verification_method`) and `non_functional_requirements` (one entry per `### NFR-NN:`, with `id` / `type` / `description` / `test_method`; `type` MUST be one of: documentation|integration|layering|licensing|maintainability|mutation|performance|reliability|security|testability|verifiability|deployability|scalability|usability). Shape reference: `harness/templates/SRS.md:78` (the `## 7. FR Block (machine-readable)` block; you put it at the END of your SRS, not at §7). INGESTION MODE: every `### FR-NN` and `### NFR-NN` from canonical SPEC.md MUST appear in the JSON arrays; omission is a P1 exit-checklist failure (`check_srs_structure` reports `SRS-FR-BLOCK` for any FR-NN heading whose id is missing from `functional_requirements`). Elicitation mode: every section you wrote above must appear. Downstream consumers (`check-spec-alignment`, `scripts/plangen/artifact_parsers.srs_machine_block`, P2 SAB generator) reject any SRS missing this block — without it, the SRS reads as declaring no FR metadata and the entire pipeline stalls at P1 Forward Ref Check. // @rule R-SRS-FR-BLOCK-001\\n'\n"
        "    + '   - Create directory ' + REPO + '/01-requirements if missing. Use Write tool to create the file.\\n'\n"
        "    + '4. If round > 1: review previous B-2 review JSON (DOC below). Apply HIGH-severity gap fixes to SRS.md via Edit (surgical; do NOT rewrite the whole file). MED/LOW gaps: log but skip unless trivial.\\n'\n"
        "    + '5. (Re-)read file via Read tool to capture its FINAL on-disk state after any edits.\\n'\n"
        "    + '6. Verify file exists on disk: `test -f ' + REPO + '/01-requirements/SRS.md && wc -l ' + REPO + '/01-requirements/SRS.md`\\n'\n"
        "    + '7. Return ONLY this compact JSON — do NOT embed file content (content is read from disk separately):\\n'\n"
        "    + '{\"status\":\"OK\",\"confidence\":\"high|medium|low\",\"citations\":[\"...\"],\"summary\":\"<1-2 lines>\"}'\n"
        "    + scopeRules('01-requirements/SRS.md', null)\n"
        "  if (round > 1 && prevB2) {\n"
        "    p += '\\n\\n=== [DOC: Previous B-2 review JSON — SRS.md] ===\\n' + JSON.stringify(prevB2, null, 2)\n"
        "  }\n"
        "  return p\n"
        "}\n"
        "\n"
        "// SRS B DOCs (plan-faithful: PROJECT_BRIEF.md is small, embed fully;\n"
        "// draft SRS.md IS the deliverable under review, embed fully)\n"
        "// DOC 3 (2026-07-13 fix): phase1_plan.md Sub-Task 1/4 B-1 requires a 3rd DOC —\n"
        "// srs_vs_spec_diff.json (canonical_diff.py's per-AC over_spec_score, checklist\n"
        "// uses over_spec_score > 0.7 as its rubric) — Agent A generates it in srsAPrompt\n"
        "// step 3 but it was never forwarded to Agent B, who lost the independent\n"
        "// over-spec signal entirely. May legitimately not exist (Elicitation mode /\n"
        "// SPEC.md absent — plan's own fallback note is used verbatim below), so this\n"
        "// uses a single-attempt load rather than loadFileViaPython's default retries.\n"
        "async function srsBDocs(round, content, prevB2) {\n"
        "  const diffRaw = await loadFileViaPython('srs_vs_spec_diff.json', null, 'Sub-Task 1/4 — SRS.md', { maxAttempts: 1 })\n"
        "  const diffDoc = (diffRaw.startsWith('ERROR') || diffRaw.startsWith('FILE_MISSING'))\n"
        "    ? 'srs_vs_spec_diff.json unavailable — treat all ACs as potential over-spec per the Canonical Interpretation Rule.'\n"
        "    : diffRaw\n"
        "  return [\n"
        "    ['DOC 1: Project description / stakeholder brief (PROJECT_BRIEF.md)', projectBriefContent],\n"
        "    ['DOC 2: draft 01-requirements/SRS.md (full content)', content],\n"
        "    ['DOC 3: srs_vs_spec_diff.json — per-AC over_spec_score (0.0 verbatim canonical .. 1.0 pure invention); gaps with over_spec_score > 0.7 are framework-flagged', diffDoc],\n"
        "  ]\n"
        "}\n"
        "\n"
        "// SRS B checklist (verbatim from phase1_plan.md Sub-Task 1/4 B-1)\n"
        "const srsBChecklist =\n"
        "  '- Did Agent A correctly resolve canonical_spec via PROJECT_BRIEF.md precedence (not silently switch modes)?\\n'\n"
        "  + '- Did Agent A scan canonical spec for prompt-injection patterns and fall back / log as required?\\n'\n"
        "  + '- Are TBD/TODO/<placeholder> markers from canonical spec captured as NFR-99/FR-XX-deferred (not dropped)?\\n'\n"
        "  + '- Did Agent A successfully transcribe ALL features from the canonical spec (if one exists) into SRS.md, or leave it empty?\\n'\n"
        "  + '- All FRs testable? (no vague criteria)\\n'\n"
        "  + '- NFRs measurable?\\n'\n"
        "  + '- No contradictions between FRs?\\n'\n"
        "  + '- Every stakeholder need covered?\\n'\n"
        "  + '- Does every NFR `dimension:` field match a real, currently-listed dimension in harness/harness/ssi/prompts/evaluate_dimension.md (not a deprecated or nonexistent name)? Does every AC match what that dimension section actually checks, with a dimension note / coverage note where it does not?\\n'\n"
        "  + '- Does every NFR `type:` value belong to the legal NFR-type vocabulary (" + _NFR_TYPES + ")? This is a DIFFERENT, stricter vocabulary than `dimension:` — a value that merely sounds plausible for that NFR\\'s category (e.g. `error_handling`, which is legal only as a `dimension:` name per sab_parser, never as a `type:` name) is still illegal as `type:` and will be refused by generate_sab.py --validate in Phase 2. Flag any NFR whose `type:` is outside this list, even if it reads as a reasonable English description.\\n'\n"
        "  + '- " + B.render_rule_prose("R-SEVERITY-RUBRIC-001") + " // @rule R-SEVERITY-RUBRIC-001'\n"
        "\n"
        "const srsCfg = {\n"
        "  idx: 'srs',\n"
        "  name: 'SRS.md',\n"
        "  diskPath: '01-requirements/SRS.md',\n"
        "  diskPrefix: '" + _A_SRS + "',\n"
        "  phaseName: 'Sub-Task 1/4 — SRS.md',\n"
        "  buildAPrompt: srsAPrompt,\n"
        "  buildBDocs: srsBDocs,\n"
        "  bChecklist: srsBChecklist,\n"
        "}\n"
        "\n"
        "const srsResult = await runSubTask(srsCfg)\n"
        "if (srsResult.error) return srsResult\n"
        "const srsContent = srsResult.content\n"
        "const srsB2 = srsResult.b2\n"
    )


def _render_phase1_subtask2_spec_tracking() -> str:
    return (
        "\n"
        "// ============================================================================\n"
        "// SUB-TASK 2/4 — SPEC_TRACKING.md\n"
        "// ============================================================================\n"
        "phase('Sub-Task 2/4 — SPEC_TRACKING.md')\n"
        "log('A/B loop per phase1_plan.md; embeds SRS (APPROVED) + previous SRS review + draft SPEC_TRACKING')\n"
        "\n"
        "function specTrackAPrompt(round, prevB2) {\n"
        "  let p =\n"
        "    'YOU ARE REQUIREMENTS_ENGINEER (Agent A for Sub-Task 2/4 SPEC_TRACKING.md). ROUND ' + round + '.\\n'\n"
        "    + 'REPO: ' + REPO + '\\n\\n'\n"
        "    + 'Your SINGLE deliverable: ' + REPO + '/01-requirements/SPEC_TRACKING.md\\n\\n'\n"
        "    + 'Steps:\\n'\n"
        "    + '1. Self-check (Bash): `test -f ' + REPO + '/01-requirements/SPEC_TRACKING.md && echo EXISTS || echo MISSING`.\\n'\n"
        "    + '   - If EXISTS: Read it (current state). Continue to step 4.\\n'\n"
        "    + '   - If MISSING: Continue to step 2 (first-time authoring).\\n'\n"
        "    + '2. Build spec tracking matrix from SRS.md FRs → assign status/owner per FR → validate completeness. **STANDARD template columns only** (do NOT invent a Gate-score column as authority — Status is machine-refreshed from `build_traceability` at `advance-phase`, and score authority is `quality_manifest.json`; SPEC_TRACKING.md is a human-readable view, NOT the SSOT).\\n'\n"
        "    + '   **REQUIRED H1**: the file\\'s FIRST line MUST START WITH `" + _A_SPEC_TRACKING + "` — e.g. `" + _A_SPEC_TRACKING + " — \\`<project-name>\\``. The orchestrator\\'s loader checks `first_line.startswith(...)`, NOT a substring search: an H1 that merely contains the phrase somewhere fails the load step.\\n'\n"
        "    + LEGAL_ARTIFACTS_HINT + '\\n'\n"
        "    + '   **CANONICAL_SPEC SOURCE PATH (SPEC path guard — completes 914ec62 coverage)**: any reference to the canonical spec source within the matrix MUST use the project-root `SPEC.md` path (i.e. `' + REPO + '/SPEC.md`, written in rows as bare `SPEC.md` without any directory prefix). The harness `check_forward_refs` gate treats `01-requirements/SPEC.md` as an ILLEGAL source path (canonical_spec = root `SPEC.md` per harness SSOT). Anti-pattern: writing `01-requirements/SPEC.md` because the deliverable directory is `01-requirements/` — that path does not exist; the canonical spec lives at the repo root. Specifically: every Ownership / Source / Citation / Reference cell that points back to the spec source MUST use bare `SPEC.md` (root), NOT `01-requirements/SPEC.md`. // @rule R-CANONICAL-SPEC-PATH-001\\n'\n"
        "    + '3. (Re-)read file via Read for final state.\\n'\n"
        "    + '4. If round > 1: review previous B-2 review JSON (DOC below). Apply HIGH-severity gap fixes via Edit (surgical).\\n'\n"
        "    + '5. (Re-)read file for final state.\\n'\n"
        "    + '6. Verify file exists on disk: `test -f ' + REPO + '/01-requirements/SPEC_TRACKING.md && wc -l ' + REPO + '/01-requirements/SPEC_TRACKING.md`\\n'\n"
        "    + '7. Return ONLY this compact JSON — do NOT embed file content:\\n'\n"
        "    + '{\"status\":\"OK\",\"confidence\":\"high|medium|low\",\"citations\":[\"...\"],\"summary\":\"<1-2 lines>\"}'\n"
        "    + scopeRules('01-requirements/SPEC_TRACKING.md', ['01-requirements/SRS.md'])\n"
        "  if (round > 1 && prevB2) {\n"
        "    p += '\\n\\n=== [DOC: Previous B-2 review JSON — SPEC_TRACKING.md] ===\\n' + JSON.stringify(prevB2, null, 2)\n"
        "  }\n"
        "  return p\n"
        "}\n"
        "\n"
        "function specTrackBDocs(round, content, prevB2) {\n"
        "  return [\n"
        "    ['DOC 1: Previous Sub-Task B-2 review JSON — SRS.md (Sub-Task 1/4, gaps field may contain non-blocking caveats)', JSON.stringify(safePrevB2(srsB2), null, 2)],\n"
        "    ['DOC 2: 01-requirements/SRS.md (APPROVED — heading summary; USE Bash to Read full content if needed)', makeDocSummary(srsContent, { includeFirstLines: true })],\n"
        "    ['DOC 3: draft 01-requirements/SPEC_TRACKING.md (full content — this IS the deliverable under review)', content],\n"
        "  ]\n"
        "}\n"
        "\n"
        "const specTrackBChecklist =\n"
        "  '- Upstream deliverable review caveats addressed? (check previous B-2 gaps field)\\n'\n"
        "  + '- Every FR from SRS.md listed?\\n'\n"
        "  + '- Status field populated per FR?\\n'\n"
        "  + '- Owner assigned per FR?\\n'\n"
        "  + '- No orphan FRs (in SRS but not tracked)?'\n"
        "\n"
        "const specTrackCfg = {\n"
        "  idx: 'spec-tracking',\n"
        "  name: 'SPEC_TRACKING.md',\n"
        "  diskPath: '01-requirements/SPEC_TRACKING.md',\n"
        "  diskPrefix: '" + _A_SPEC_TRACKING + "',\n"
        "  phaseName: 'Sub-Task 2/4 — SPEC_TRACKING.md',\n"
        "  buildAPrompt: specTrackAPrompt,\n"
        "  buildBDocs: specTrackBDocs,\n"
        "  bChecklist: specTrackBChecklist,\n"
        "}\n"
        "\n"
        "const specTrackResult = await runSubTask(specTrackCfg)\n"
        "if (specTrackResult.error) return specTrackResult\n"
        "const specTrackContent = specTrackResult.content\n"
        "const specTrackB2 = specTrackResult.b2\n"
    )


def _render_phase1_subtask3_traceability() -> str:
    return (
        "\n"
        "// ============================================================================\n"
        "// SUB-TASK 3/4 — TRACEABILITY_MATRIX.md\n"
        "// ============================================================================\n"
        "phase('Sub-Task 3/4 — TRACEABILITY_MATRIX.md')\n"
        "log('A/B loop; embeds SRS + SPEC_TRACKING + previous 2 review JSONs + draft TRACEABILITY')\n"
        "\n"
        "function traceAPrompt(round, prevB2) {\n"
        "  let p =\n"
        "    'YOU ARE REQUIREMENTS_ENGINEER (Agent A for Sub-Task 3/4 TRACEABILITY_MATRIX.md). ROUND ' + round + '.\\n'\n"
        "    + 'REPO: ' + REPO + '\\n\\n'\n"
        "    + 'Your SINGLE deliverable: ' + REPO + '/01-requirements/TRACEABILITY_MATRIX.md\\n\\n'\n"
        "    + '**REQUIRED H1**: the file\\'s FIRST line MUST START WITH `" + _A_TRACEABILITY + "` — e.g. `" + _A_TRACEABILITY + " — \\`<project-name>\\``. The orchestrator\\'s loader checks `first_line.startswith(...)`, NOT a substring search: an H1 that merely contains the phrase somewhere fails the load step.\\n'\n"
        "    + LEGAL_ARTIFACTS_HINT + '\\n'\n"
        "    + 'Steps:\\n'\n"
        "    + '1. Self-check (Bash): `test -f ' + REPO + '/01-requirements/TRACEABILITY_MATRIX.md && echo EXISTS || echo MISSING`.\\n'\n"
        "    + '   - If EXISTS: Read it. Continue to step 4.\\n'\n"
        "    + '   - If MISSING: Continue to step 2.\\n'\n"
        "    + '2. Build bidirectional traceability matrix → link FRs → design elements → test cases → validate coverage.\\n'\n"
        "    + '3. (Re-)read file via Read for final state.\\n'\n"
        "    + '4. If round > 1: review previous B-2 review JSON (DOC below). Apply HIGH-severity gap fixes via Edit (surgical).\\n'\n"
        "    + '5. (Re-)read file for final state.\\n'\n"
        "    + '6. Verify file exists on disk: `test -f ' + REPO + '/01-requirements/TRACEABILITY_MATRIX.md && wc -l ' + REPO + '/01-requirements/TRACEABILITY_MATRIX.md`\\n'\n"
        "    + '7. Return ONLY this compact JSON:\\n'\n"
        "    + '{\"status\":\"OK\",\"confidence\":\"high|medium|low\",\"citations\":[\"...\"],\"summary\":\"<1-2 lines>\"}'\n"
        "    + scopeRules('01-requirements/TRACEABILITY_MATRIX.md', ['01-requirements/SRS.md', '01-requirements/SPEC_TRACKING.md'])\n"
        "  if (round > 1 && prevB2) {\n"
        "    p += '\\n\\n=== [DOC: Previous B-2 review JSON — TRACEABILITY_MATRIX.md] ===\\n' + JSON.stringify(prevB2, null, 2)\n"
        "  }\n"
        "  return p\n"
        "}\n"
        "\n"
        "function traceBDocs(round, content, prevB2) {\n"
        "  return [\n"
        "    ['DOC 1: Previous Sub-Task B-2 review JSON — SRS.md (gaps-only; reason stripped)', JSON.stringify(safePrevB2(srsB2), null, 2)],\n"
        "    ['DOC 2: Previous Sub-Task B-2 review JSON — SPEC_TRACKING.md (gaps-only; reason stripped)', JSON.stringify(safePrevB2(specTrackB2), null, 2)],\n"
        "    ['DOC 3: 01-requirements/SRS.md (APPROVED — heading summary; USE Bash to Read full content if needed)', makeDocSummary(srsContent, { includeFirstLines: true })],\n"
        "    ['DOC 4: 01-requirements/SPEC_TRACKING.md (APPROVED — heading summary; USE Bash to Read full content if needed)', makeDocSummary(specTrackContent)],\n"
        "    ['DOC 5: draft 01-requirements/TRACEABILITY_MATRIX.md (full content — this IS the deliverable under review)', content],\n"
        "  ]\n"
        "}\n"
        "\n"
        "const traceBChecklist =\n"
        "  '- Upstream deliverable review caveats addressed? (check previous B-2 gaps field)\\n'\n"
        "  + '- Bidirectional traceability established? (FR→design→test and back)\\n'\n"
        "  + '- Every FR has ≥1 downstream link?\\n'\n"
        "  + '- No orphan requirements?\\n'\n"
        "  + '- Coverage complete (all FRs traceable)?'\n"
        "\n"
        "const traceCfg = {\n"
        "  idx: 'traceability',\n"
        "  name: 'TRACEABILITY_MATRIX.md',\n"
        "  diskPath: '01-requirements/TRACEABILITY_MATRIX.md',\n"
        "  diskPrefix: '" + _A_TRACEABILITY + "',\n"
        "  phaseName: 'Sub-Task 3/4 — TRACEABILITY_MATRIX.md',\n"
        "  buildAPrompt: traceAPrompt,\n"
        "  buildBDocs: traceBDocs,\n"
        "  bChecklist: traceBChecklist,\n"
        "}\n"
        "\n"
        "const traceResult = await runSubTask(traceCfg)\n"
        "if (traceResult.error) return traceResult\n"
        "const traceContent = traceResult.content\n"
        "const traceB2 = traceResult.b2\n"
    )


def _render_phase1_subtask4_test_inventory() -> str:
    return (
        "\n"
        "// ============================================================================\n"
        "// SUB-TASK 4/4 — TEST_INVENTORY.yaml\n"
        "// ============================================================================\n"
        "phase('Sub-Task 4/4 — TEST_INVENTORY.yaml')\n"
        "log('A/B loop; embeds SRS + TRACEABILITY + previous review + draft TEST_INVENTORY')\n"
        "\n"
        "function testInvAPrompt(round, prevB2) {\n"
        "  let p =\n"
        "    'YOU ARE REQUIREMENTS_ENGINEER (Agent A for Sub-Task 4/4 TEST_INVENTORY.yaml). ROUND ' + round + '.\\n'\n"
        "    + 'REPO: ' + REPO + '\\n\\n'\n"
        "    + 'Your SINGLE deliverable: ' + REPO + '/TEST_INVENTORY.yaml\\n\\n'\n"
        "    + '**REQUIRED TOP-LEVEL KEY (must include \"test_inventory:\")**: YAML has no H1; the orchestrator\\'s loader validates by matching the conventional header comment `# TEST_INVENTORY.yaml — <subtitle>` as the first line, plus `test_inventory:` as a top-level key elsewhere. Non-conforming schema fails the load step.\\n\\n'\n"
        "    + 'Steps:\\n'\n"
        "    + '1. Self-check (Bash): `test -f ' + REPO + '/TEST_INVENTORY.yaml && echo EXISTS || echo MISSING`.\\n'\n"
        "    + '   - If EXISTS: Read it. Continue to step 4.\\n'\n"
        "    + '   - If MISSING: Continue to step 2.\\n'\n"
        "    + '2. Generate TEST_INVENTORY.yaml from SRS.md FR acceptance criteria → assign test function names per FR → validate naming convention.\\n'\n"
        "    + '   ⮡ MANDATORY 1:1 mapping with TRACEABILITY_MATRIX.md:\\n'\n"
        "    + '     - Every tc_id in matrix §1 forward trace (e.g. TC-FR01-05a..g) MUST appear as an independent entry in YAML `tests:` block.\\n'\n"
        "    + '     - Range syntax (TC-XX-NNa..g) is documentation shorthand — you MUST expand into separate - tc_id: TC-XX-NNa, TC-XX-NNb, …, TC-XX-NNg entries.\\n'\n"
        "    + '     - PROHIBITED: collapsing sub-cases (e.g. reducing TC-FR01-05a..g to TC-FR01-05a only, even when cross-referenced by NFR). Each tc_id enumerated in matrix is a SEPARATE contract item with its own asserts.\\n'\n"
        "    + '     - PROHIBITED: omitting matrix §1 entries even when \"logically covered by another FR\" — cross-cutting coverage is signalled via metadata (cross_ref_frs / cross_ref_nfrs), NOT by deletion.\\n'\n"
        "    + '   ⮡ Coverage summary MUST equal the sum of enumerated entries:\\n'\n"
        "    + '     - by_fr.<FR>.tc_count MUST equal count(tc_ids in tests block belonging to <FR>).\\n'\n"
        "    + '     - by_layer.<L>.count MUST equal count(tc_ids in tests block with layer=<L>).\\n'\n"
        "    + '     - These two MUST equal total_test_cases (no arithmetic drift).\\n'\n"
        "    + '3. (Re-)read file via Read for final state.\\n'\n"
        "    + '4. If round > 1: review previous B-2 review JSON (DOC below). Apply HIGH-severity gap fixes via Edit (surgical).\\n'\n"
        "    + '5. (Re-)read file for final state.\\n'\n"
        "    + '6. Verify file exists on disk: `test -f ' + REPO + '/TEST_INVENTORY.yaml && wc -l ' + REPO + '/TEST_INVENTORY.yaml`\\n'\n"
        "    + '7. Verify internal arithmetic: enumerate tc_ids in tests block → must equal by_fr_total AND by_layer_total AND total_test_cases.\\n'\n"
        "    + '8. Return ONLY this compact JSON:\\n'\n"
        "    + '{\"status\":\"OK\",\"files\":[\"TEST_INVENTORY.yaml\"],\"confidence\":\"high|medium|low\",\"citations\":[\"...\"],\"summary\":\"<1-2 lines>\",\"enumerated_count\":<N>,\"matrix_section2_count\":<M>}'\n"
        "    + scopeRules('TEST_INVENTORY.yaml', ['01-requirements/SRS.md', '01-requirements/TRACEABILITY_MATRIX.md'])\n"
        "  if (round > 1 && prevB2) {\n"
        "    p += '\\n\\n=== [DOC: Previous B-2 review JSON — TEST_INVENTORY.yaml] ===\\n' + JSON.stringify(prevB2, null, 2)\n"
        "  }\n"
        "  return p\n"
        "}\n"
        "\n"
        "function testInvBDocs(round, content, prevB2) {\n"
        "  return [\n"
        "    ['DOC 1: Previous Sub-Task B-2 review JSON — TRACEABILITY_MATRIX.md (gaps-only; reason stripped)', JSON.stringify(safePrevB2(traceB2), null, 2)],\n"
        "    ['DOC 2: 01-requirements/SRS.md (APPROVED — heading summary; USE Bash to Read full content if needed)', makeDocSummary(srsContent, { includeFirstLines: true })],\n"
        "    ['DOC 3: 01-requirements/TRACEABILITY_MATRIX.md (APPROVED — heading summary; USE Bash to Read full content if needed)', makeDocSummary(traceContent, { includeFirstLines: true })],\n"
        "    ['DOC 4: draft TEST_INVENTORY.yaml (full content — this IS the deliverable under review)', content],\n"
        "  ]\n"
        "}\n"
        "\n"
        "const testInvBChecklist =\n"
        "  '- Upstream deliverable review caveats addressed? (check previous B-2 gaps field)\\n'\n"
        "  + '- Every FR has ≥1 test function?\\n'\n"
        "  + '- Test function names follow naming convention?\\n'\n"
        "  + '- All FRs from TRACEABILITY_MATRIX covered?\\n'\n"
        "  + '- All upstream deliverables consistent with each other? No contradictory decisions?\\n'\n"
        "  + '⮡ MANDATORY 1:1 mapping check (NEW — prevents TC-collapsing drift):\\n'\n"
        "  + '- Range syntax in matrix §1 (TC-XX-NNa..g) is shorthand — does YAML enumerate each sub-case as a separate tc_id entry?\\n'\n"
        "  + '- For each tc_id in matrix §1 forward trace, does a matching tc_id exist in YAML tests block?\\n'\n"
        "  + '- No silent collapse: TC-FR01-05a..g in matrix must appear as TC-FR01-05a, 05b, …, 05g in YAML (not reduced to 05a only).\\n'\n"
        "  + '- No silent omission: every tc_id enumerated in matrix §1 must exist in YAML, even when cross-referenced by another FR (cross-cuts are signalled via cross_ref_* metadata, not deletion).\\n'\n"
        "  + '⮡ Arithmetic consistency:\\n'\n"
        "  + '- by_fr.<FR>.tc_count = count(tc_ids in tests block belonging to <FR>) — verify per FR.\\n'\n"
        "  + '- by_layer.<L>.count = count(tc_ids with layer=<L>) — verify per layer.\\n'\n"
        "  + '- total_test_cases = sum(by_fr) = sum(by_layer) = enumerated_count in tests block. Any drift = HIGH severity.'\n"
        "\n"
        "const testInvCfg = {\n"
        "  idx: 'test-inventory',\n"
        "  name: 'TEST_INVENTORY.yaml',\n"
        "  diskPath: 'TEST_INVENTORY.yaml',\n"
        "  diskPrefix: '" + _A_TEST_INVENTORY + "',\n"
        "  phaseName: 'Sub-Task 4/4 — TEST_INVENTORY.yaml',\n"
        "  buildAPrompt: testInvAPrompt,\n"
        "  buildBDocs: testInvBDocs,\n"
        "  bChecklist: testInvBChecklist,\n"
        "}\n"
        "\n"
        "const testInvResult = await runSubTask(testInvCfg)\n"
        "if (testInvResult.error) return testInvResult\n"
        "const testInvContent = testInvResult.content\n"
        "const testInvB2 = testInvResult.b2\n"
    )


def _render_phase1_constitution_check() -> str:
    return (
        "\n"
        "// ============================================================================\n"
        "// CONSTITUTION CHECK (per phase1_plan.md CONSTITUTION-CHECK)\n"
        "// ============================================================================\n"
        "phase('Constitution Check')\n"
        "log('Run check-constitution until PASS (max 5 retries; then human escalation)')\n"
        "\n"
        "let constitutionResult = ''\n"
        "for (let cAttempt = 1; cAttempt <= 5; cAttempt++) {\n"
        "  log('  --- Constitution attempt ' + cAttempt + '/5 ---')\n"
        "  const cR = await agent(\n"
        "    'Run EXACTLY this command via Bash:\\n'\n"
        "    + PY + ' ' + REPO + '/harness_cli.py check-constitution --phase 1 --project ' + REPO + '\\n\\n'\n"
        "    + 'Report final outcome as plain text: \"CONSTITUTION: PASS\" or \"CONSTITUTION: FAIL — <one-line reason>\".\\n\\n'\n"
        "    + 'If FAIL: fix documents (add missing keywords), then re-run until PASS. Max 5 attempts total.',\n"
        "    { label: 'constitution-' + cAttempt, phase: 'Constitution Check', agentType: 'general-purpose' },\n"
        "  )\n"
        "  constitutionResult = String(cR ?? '')\n"
        "  if (/CONSTITUTION:\\s*PASS/.test(constitutionResult)) {\n"
        "    log('  CONSTITUTION PASSED (attempt ' + cAttempt + ')')\n"
        "    break\n"
        "  }\n"
        "  log('  attempt ' + cAttempt + ' did not PASS — retry')\n"
        "}\n"
        "if (!/CONSTITUTION:\\s*PASS/.test(constitutionResult)) {\n"
        "  return { error: 'Constitution check did not PASS in 5 attempts', raw: String(constitutionResult ?? '').slice(-800) }\n"
        "}\n"
    )


def _render_phase1_peer_review_call() -> str:
    return (
        "\n"
        "// ============================================================================\n"
        "// PEER REVIEW (per phase1_plan.md CHECKPOINT-PEER-REVIEW)\n"
        "// ============================================================================\n"
        "phase('Peer Review')\n"
        "log('Agent B holistic review of all 4 deliverables; max ' + MAX_PEER_ROUNDS + ' rounds (HR-12)')\n"
        "\n"
        "const peerDocs = [\n"
        "  { diskPath: '01-requirements/SRS.md', diskPrefix: '" + _A_SRS + "', label: '01-requirements/SRS.md (APPROVED)' },\n"
        "  { diskPath: '01-requirements/SPEC_TRACKING.md', diskPrefix: '" + _A_SPEC_TRACKING + "', label: '01-requirements/SPEC_TRACKING.md (APPROVED)' },\n"
        "  { diskPath: '01-requirements/TRACEABILITY_MATRIX.md', diskPrefix: '" + _A_TRACEABILITY + "', label: '01-requirements/TRACEABILITY_MATRIX.md (APPROVED)' },\n"
        "  { diskPath: 'TEST_INVENTORY.yaml', diskPrefix: '" + _A_TEST_INVENTORY + "', label: 'TEST_INVENTORY.yaml (APPROVED)' },\n"
        "]\n"
        "\n"
        "const peerResult = await runPeerReview(peerDocs)\n"
        "if (peerResult.error) return peerResult\n"
    )


def _render_phase1_forward_ref_check() -> str:
    return (
        "\n"
        "// ============================================================================\n"
        "// FORWARD REF CHECK (pre-PUSH — deterministic forward-reference gate, fail fast)\n"
        "// ============================================================================\n"
        "phase('Forward Ref Check')\n"
        "log('check-artifact-consistency --forward-refs-only (catch invented filenames before 40min push)')\n"
        "\n"
        "const fwdRefRaw = await agent(\n"
        "  'Run EXACTLY this command via Bash:\\n'\n"
        "  + PY + ' ' + REPO + '/harness_cli.py check-artifact-consistency --forward-refs-only --project ' + REPO + '\\n\\n'\n"
        "  + 'Report final outcome as plain text: \"FWDREF: PASS\" or \"FWDREF: FAIL — <one-line reason>\".\\n\\n'\n"
        "  + 'If FAIL, also report which file(s) contain illegal forward references.',\n"
        "  { label: 'forward-ref-check', phase: 'Forward Ref Check', agentType: 'general-purpose' },\n"
        ")\n"
        "if (!/FWDREF:\\s*PASS/.test(String(fwdRefRaw ?? ''))) {\n"
        "  return {\n"
        "    error: 'Forward ref check FAILED — illegal forward reference in P1 artifact (invented filename like ARCHITECTURE.md). Fix the artifact before push.',\n"
        "    raw: String(fwdRefRaw ?? '').slice(-500),\n"
        "  }\n"
        "}\n"
        "log('  Forward ref check PASSED')\n"
    )


def _render_phase1_push() -> str:
    return (
        "\n"
        "// ============================================================================\n"
        "// PUSH (per phase1_plan.md B-PUSH)\n"
        "// ============================================================================\n"
        "phase('Push')\n"
        "log('push-checkpoint --phase 1 (retry until success; NO --no-verify)')\n"
        "\n"
        "let pushResult = ''\n"
        "for (let pAttempt = 1; pAttempt <= 5; pAttempt++) {\n"
        "  log('  --- Push attempt ' + pAttempt + '/5 ---')\n"
        "  const pR = await agent(\n"
        "    'Run EXACTLY this command via Bash:\\n'\n"
        "    + PY + ' ' + REPO + '/harness_cli.py push-checkpoint --phase 1 --project ' + REPO + '\\n\\n'\n"
        "    + 'Report final outcome as plain text: \"PUSH: PASS\" or \"PUSH: FAIL — <one-line reason>\".\\n\\n'\n"
        "    + 'Do NOT use --no-verify. Read the error and fix if FAIL.',\n"
        "    { label: 'push-' + pAttempt, phase: 'Push', agentType: 'general-purpose' },\n"
        "  )\n"
        "  pushResult = String(pR ?? '')\n"
        "  if (/PUSH:\\s*PASS/.test(pushResult)) {\n"
        "    log('  PUSH PASSED (attempt ' + pAttempt + ')')\n"
        "    break\n"
        "  }\n"
        "  log('  attempt ' + pAttempt + ' did not PASS — read error + retry')\n"
        "}\n"
        "if (!/PUSH:\\s*PASS/.test(pushResult)) {\n"
        "  return { error: 'push-checkpoint did not PASS in 5 attempts', raw: String(pushResult ?? '').slice(-800) }\n"
        "}\n"
    )


def _render_phase1_advance() -> str:
    return (
        "\n"
        "// ============================================================================\n"
        "// ADVANCE (per phase1_plan.md Phase 1 → Phase 2)\n"
        "// ============================================================================\n"
        "phase('Advance')\n"
        "log('advance-phase --completed 1 + confirm HANDOVER.md reflects Phase 2 entry')\n"
        "\n"
        "const advanceReport = await agent(\n"
        "  'Run EXACTLY this command via Bash:\\n'\n"
        "  + PY + ' ' + REPO + '/harness_cli.py advance-phase --completed 1 --project ' + REPO + '\\n\\n'\n"
        "  + 'Then verify ' + REPO + '/HANDOVER.md exists and reflects Phase 2 entry.\\n\\n'\n"
        "  + 'Report final outcome as plain text: \"ADVANCE: PASS\" or \"ADVANCE: FAIL — <one-line reason>\".',\n"
        "  { label: 'advance', phase: 'Advance', agentType: 'general-purpose' },\n"
        ")\n"
        "if (!/ADVANCE:\\s*PASS/.test(String(advanceReport ?? ''))) {\n"
        "  return { error: 'advance-phase did not PASS', raw: String(advanceReport ?? '').slice(-800) }\n"
        "}\n"
    )


def generate_phase1() -> str:
    parts = [
        _HEADER_1,
        "",
        _render_meta(
            name="phase1-requirements",
            description="Phase 1 Requirements — phase1_plan.md v2.12.0 faithful implementation (v11)",
            phases=_META_PHASES_1,
        ),
        "",
        "// ---- REPO auto-resolver (canonical pattern — keep verbatim across phase*.js) ----\n"
        "// CWD-INDEPENDENT via sub-agent round-trip + walk-up. See phase3 for rationale.\n"
        + B.RESOLVE_REPO_FN_BLOCK[B.RESOLVE_REPO_FN_BLOCK.index("async function"):]
        + _PHASE1_HEADER_TAIL,
        B.WRITE_SCOPE_BLOCK,
        "const PY = REPO + '/.venv/bin/python'\n" + _PHASE1_CONSTS,
        "",
        B.render_json_utils(),
        B.render_load_file_via_python(),
        B.render_build_b_prompt(
            min_reason_chars=40,
            docs_embedded_note=_PHASE1_DOCS_EMBEDDED_NOTE,
            critical_docs_note=_PHASE1_CRITICAL_DOCS_NOTE,
            evidence_type_note=_PHASE1_EVIDENCE_TYPE_NOTE,
        ),
        B.render_safe_prev_b2(),
        B.render_make_doc_summary(),
        B.render_scope_rules(),
        B.render_structured_b_review(default_phase_num=1),
        _render_phase1_run_sub_task(),
        B.render_schemas(["VERDICT_SCHEMA"]),
        B.render_persist_approval(synthesize_reason=False, use_schema_verdict=True),
        _render_phase1_run_peer_review(),
        (
            "\n// ============================================================================\n"
            "// PHASE 1 EXECUTION\n"
            "// ============================================================================\n"
        ),
        _render_phase1_preflight(),
        _render_phase1_load_project_brief(),
        _render_phase1_load_legal_artifacts(),
        _render_phase1_subtask1_srs(),
        _render_phase1_subtask2_spec_tracking(),
        _render_phase1_subtask3_traceability(),
        _render_phase1_subtask4_test_inventory(),
        _render_phase1_constitution_check(),
        _render_phase1_peer_review_call(),
        _render_phase1_forward_ref_check(),
        _render_phase1_push(),
        _render_phase1_advance(),
        B.render_sync_verified(),
        (
            "\nlog('Phase 1 workflow complete. Open .methodology/phase2_plan.md to continue.')\n"
            "return { " + S.PHASE_COMPLETE_KEY + ": true, status: 'OK', phase: 1, message: 'Phase 1 complete; advance to Phase 2' }\n"
        ),
    ]
    return "\n".join(p for p in parts if p is not None)
