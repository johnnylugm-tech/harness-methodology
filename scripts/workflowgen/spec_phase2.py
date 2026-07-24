"""Phase 2 (Architecture Design) workflow assembly — Round 15 station5
extraction from the former monolithic phase_specs.py. See
scripts/workflowgen/spec_shared.py for the cross-phase _render_meta.
"""
from __future__ import annotations

from . import js_blocks as B
from .spec_shared import _render_meta

_HEADER_2 = """\
// Phase 2 — Architecture Design (faithful to .methodology/phase2_plan.md v2.12.0)
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/phase_specs.py::generate_phase2() (+ js_blocks.py for
// the blocks shared across phase workflow files). Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write --phase 2
//
// Structure: A/B document型 (same family as phase1). 3 serial deliverables
// (SAD → ADR → TEST_SPEC), each with an Agent A author / stateless Agent B
// reviewer loop (max 5 rounds, HR-12 escalation), plus SAB generation,
// constitution check, holistic peer review, push, advance.
//
// Built on workflow-playbook.md lessons:
//   - NO import/fs/process/schema: (all I/O via agent(); JSON parsed as text).
//   - Bash for harness CLI + file reads (Read tool hallucinates — §8.2).
//   - SCOPE RULES on every agent (prevent over-reach — §7.3).
//   - PY = .venv/bin/python (3.14; /usr/bin/python3 is 3.9 = unsupported).
//   - Launch via scriptPath (avoids stale name-resolver cache — §6.5).
//
// Usage:
//   Workflow({ scriptPath: '.claude/workflows/phase2-architecture.js',
//              args: { repo: '.' } })
"""

_META_PHASES_2 = [
    "Entry & Preflight", "Load Upstream", "Sub-Task 1/3 — SAD.md",
    "Sub-Task 2/3 — ADR.md", "Constitution Check — ADR",
    "Sub-Task 3/3 — TEST_SPEC.md", "SAB Generation", "Constitution Check",
    "Peer Review", "Push", "Advance", "Sync",
]

_PHASE2_MAX_ROUND_CONSTS = (
    "// HR-12: safety ceiling; observed P2 runs converge in ≤2 rounds — lower only if cost is a concern\n"
    "const MAX_B_ROUNDS = 5\n"
    "// HR-12: Phase 1/2 exit gate Peer Review must converge in 5 rounds.\n"
    "// Round-5 REJECT → escalate to human (hard return), must not silently pass.\n"
    "const MAX_PEER_ROUNDS = 5\n"
    "// v28: retry at orchestrator level, not inside one outer agent call. Single-prompt\n"
    "// write+verify via mcp__filesystem__. See persistApproval.\n"
    "const MAX_OUTER_ATTEMPTS = 3\n"
)

_PHASE2_DOCS_EMBEDDED_NOTE = 'looks for PURE basenames like "SAD.md", "ADR.md", "TEST_SPEC.md", NOT descriptive strings. Use bare basenames only.'
_PHASE2_CRITICAL_DOCS_NOTE = 'for Phase 2, `docs_embedded` MUST include ALL of: "SRS.md", "SAD.md" — regardless of which deliverable you are reviewing. The harness verifier (_REQUIRED_EMBEDDED_DOCS[2]) rejects any P2 approval missing either.'
_PHASE2_EVIDENCE_TYPE_NOTE = "real_invention=truly new requirement; over_interpretation=ambiguous canonical phrase (caps at medium); methodology_artifact=framework-side gap (always low)."


def _render_phase2_entry_preflight() -> str:
    return (
        B.render_phase_header("Entry & Preflight")
        + "log('ENTRY-CHECK + P1-ARTIFACTS + run-phase 2 + validate-handoff + CI + load-context')\n"
        + "\n"
        + "const MAX_PREFLIGHT_ATTEMPTS = 3\n"
        + "let preflightPass = false, preflightReport = ''\n"
        + "for (let attempt = 1; attempt <= MAX_PREFLIGHT_ATTEMPTS; attempt++) {\n"
        + "  log('  preflight attempt ' + attempt + '/' + MAX_PREFLIGHT_ATTEMPTS)\n"
        + "  preflightReport = await agent(\n"
        + "    'YOU ARE THE PHASE-2 PREFLIGHT ORCHESTRATOR. Run bash commands in order; report final status.\\n'\n"
        + "    + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "    + 'Steps:\\n'\n"
        + "    + '1. ENTRY-CHECK (P1 review-complete): `git -C ' + REPO + ' log --oneline --grep=\"phase1(review-complete)\" -1` OR confirm all 4 P1 files exist.\\n'\n"
        + "    + '2. P1-ARTIFACTS: `ls ' + REPO + '/01-requirements/SRS.md ' + REPO + '/01-requirements/SPEC_TRACKING.md ' + REPO + '/01-requirements/TRACEABILITY_MATRIX.md ' + REPO + '/TEST_INVENTORY.yaml`. ALL 4 must exist — if any missing, report FAIL (return to Phase 1).\\n'\n"
        + "    + '3. PREFLIGHT: `' + PY + ' ' + REPO + '/harness_cli.py run-phase --phase 2 --project ' + REPO + '`. If FAIL: fix FSM/Constitution/Drift, re-run.\\n'\n"
        + "    + '4. HANDOFF: `' + PY + ' ' + REPO + '/harness_cli.py validate-handoff --from-phase 1 --project ' + REPO + '`. Must exit 0; if exit 1, read errors, fix upstream P1 deliverable, re-run.\\n'\n"
        + "    + '5. PREFLIGHT-CI: confirm `' + REPO + '/.github/workflows/harness_quality_gate.yml` (CI workflow) + `' + REPO + '/.git/hooks/prepare-commit-msg` (git hook) both exist; confirm state.json current_phase=2. If stale: `' + PY + ' ' + REPO + '/harness_cli.py init-project --phase 2 --project ' + REPO + ' --overwrite`.\\n'\n"
        + "    + '6. LOAD-CONTEXT: `mkdir -p ' + REPO + '/.sessi-work && ' + PY + ' ' + REPO + '/harness_cli.py load-context --phase 2 --project ' + REPO + ' --json > ' + REPO + '/.sessi-work/phase2_ctx.json`.\\n\\n'\n"
        + "    + '7. READ THE LESSONS BLOCK: after step 6, Bash `cat ' + REPO + '/.sessi-work/phase2_ctx.json` and READ the `lessons` field (compact markdown, \"\" if none). DO NOT repeat those past failure modes in this preflight or any follow-up P2 work. (Direction C — past lessons injection)\\n\\n'\n"
        + "    + 'Report plain text: \"PREFLIGHT: PASS\" or \"PREFLIGHT: FAIL — <one-line reason>\".\\n\\n'\n"
        + "    + 'SCOPE RULES:\\n'\n"
        + "    + '- DO NOT write any P2 deliverable (SAD/ADR/TEST_SPEC).\\n'\n"
        + "    + '- DO NOT run advance-phase, push-checkpoint, run-gate.\\n'\n"
        + "    + '- DO NOT modify files inside harness/ (HR-17).\\n'\n"
        + "    + '- ONLY run the commands above, fix preflight issues, and report.',\n"
        + "    { label: 'preflight-' + attempt, phase: 'Entry & Preflight', agentType: 'general-purpose' },\n"
        + "  )\n"
        + "  preflightPass = typeof preflightReport === 'string' && /PREFLIGHT:\\s*PASS/.test(preflightReport)\n"
        + "  if (preflightPass) break\n"
        + "}\n"
        + "if (!preflightPass) return { error: 'Phase 2 preflight did not PASS after ' + MAX_PREFLIGHT_ATTEMPTS + ' attempts', raw: String(preflightReport ?? '').slice(-600) }\n"
    )


def _render_phase2_load_upstream() -> str:
    return (
        B.render_phase_header("Load Upstream")
        + "log('cat SRS.md + harness templates for embedding into stateless Agent B prompts')\n"
        + "const srsContent = await loadFileViaPython('01-requirements/SRS.md', '# Software Requirements Specification', 'Load Upstream')\n"
        + "if (srsContent.startsWith('ERROR:') || srsContent.length < 50) {\n"
        + "  return { error: 'Failed to load SRS.md for upstream context', loaded_preview: srsContent.slice(0, 200) }\n"
        + "}\n"
        + "log('  SRS.md loaded: ' + srsContent.length + ' chars')\n"
        + "const sadTemplateContent = await loadFileViaPython('harness/templates/SAD.md', '#', 'Load Upstream')\n"
        + "log('  harness/templates/SAD.md loaded: ' + sadTemplateContent.length + ' chars')\n"
        + "const adrTemplateContent = await loadFileViaPython('harness/templates/ADR.md', '#', 'Load Upstream')\n"
        + "log('  harness/templates/ADR.md loaded: ' + adrTemplateContent.length + ' chars')\n"
    )


def _render_phase2_subtask1_sad() -> str:
    return (
        B.render_phase_header("Sub-Task 1/3 — SAD.md")
        + "log('abLoop: SAD authoring (ARCHITECT A + TECH_LEAD B; max 5 rounds; HR-12 escalate)')\n"
        + "const sad = await abLoop({\n"
        + "  phaseName: 'Sub-Task 1/3 — SAD.md', key: 'sad', deliverable: 'SAD.md', diskPath: '02-architecture/SAD.md', diskPrefix: '# Software Architecture Document',\n"
        + "  buildAPrompt: (round, prevB2) =>\n"
        + "    'YOU ARE ARCHITECT (Agent A for Sub-Task 1/3 SAD.md). ROUND ' + round + '.\\n'\n"
        + "    + 'REPO: ' + REPO + '\\nYour SINGLE deliverable: ' + REPO + '/02-architecture/SAD.md\\n\\n'\n"
        + "    + '**REQUIRED H1 (must include \"Software Architecture Document\")**: the file MUST start with `# Software Architecture Document (SAD) — \\`<project>\\`` (or any H1 line containing the phrase \"Software Architecture Document\"). The orchestrator loader validates this H1 anchor via startswith — a non-conforming first line fails the load step.\\n\\n'\n"
        + "    + 'Steps:\\n'\n"
        + "    + '1. Self-check (Bash): `test -f ' + REPO + '/02-architecture/SAD.md`. If EXISTS, Read it (current state).\\n'\n"
        + "    + '2. Author Software Architecture Document. REQUIRED:\\n'\n"
        + "    + '   - §1 Overview. §2 Module design: every FR (enumerate from SPEC.md ### FR-XX: headings) maps to ≥1 module; follow SPEC.md §6 directory structure (read SPEC §6 for the project-specific module tree — do not assume a fixed module set). ≤15 files/dir, no god-module.\\n'\n"
        + "    + '   - §3 Interfaces & data flows (consistent diagrams). §4 NFR handling (latency/security/cost per all NFRs enumerated from SPEC.md ### NFR-XX: headings).\\n'\n"
        + "    + '   - §5 SAB block placeholder: include the literal marker `<!-- SAB:START -->` (real YAML filled in SAB Generation phase later).\\n'\n"
        + "    + '   - §6 Security Design (STRIDE-lite Threat Model): Write the SEC block into SAD.md §6 using the canonical template (do NOT hand-write the YAML — paste from canonical template via `python3 -c \"from core.quality_gate.security_design import render_canonical_security_template; print(render_canonical_security_template())\"` then replace EXAMPLE values with real project values). Must include literal marker `<!-- SEC:START -->` + boundaries + threats + verified_by, OR an honest `applicability: none` + ≥20-char justification. `applicability: none` is a fully valid declaration for projects with no real attack surface.\\n'\n"
        + "    + '   - No circular dependencies.\\n'\n"
        + "    + '3. Re-read file (Read) for FINAL state. Create dir ' + REPO + '/02-architecture if missing (Write tool).\\n'\n"
        + "    + (round > 1 ? '4. Apply HIGH-severity gap fixes from previous B-2 (DOC below) via Edit (surgical, do NOT rewrite whole file).\\n' : '')\n"
        + "    + 'Return ONLY this compact JSON — do NOT embed file content (content is read from disk separately):\\n'\n"
        + "    + '{\"status\":\"OK\",\"files\":[\"02-architecture/SAD.md\"],\"confidence\":\"high|medium|low\",\"citations\":[\"SRS.md FR-01\",\"...\"],\"summary\":\"<1-2 lines>\"}\\n\\n'\n"
        + "    + 'SCOPE RULES:\\n- DO NOT write ADR.md or TEST_SPEC.md.\\n- DO NOT run phase-transition / quality-gate / generate_sab commands.\\n- DO NOT modify harness/ (HR-17).\\n- ONLY author SAD.md and return JSON.'\n"
        + "    + (round > 1 && prevB2 ? '\\n\\n=== [DOC: Previous B-2 review JSON — SAD.md] ===\\n' + JSON.stringify(prevB2, null, 2) : ''),\n"
        + "  buildBDocs: (content) => [\n"
        + "    ['DOC 1: 01-requirements/SRS.md (APPROVED — heading summary; USE Bash to Read full content if needed)', makeDocSummary(srsContent, { includeFirstLines: true })],\n"
        + "    ['DOC 2: draft 02-architecture/SAD.md (full content — this IS the deliverable under review)', content],\n"
        + "    ['DOC 3: harness/templates/SAD.md §2.1 — Directory Structure Design Principles (heading summary)', makeDocSummary(sadTemplateContent)],\n"
        + "  ],\n"
        + "  checklist:\n"
        + "    '- Every FR maps to ≥1 module?\\n- NFRs addressed (latency/security/cost)?\\n- No circular dependencies?\\n- Data flow diagrams consistent?\\n'\n"
        + "    + '- SAB block present in §5 (<!-- SAB:START --> marker exists)?\\n- `phase` is a bare int (not quoted string)? e.g. `phase: 2` not `phase: \"2\"`\\n- All NFR `type` values from legal values (performance/security/maintainability/reliability/testability/deployability/scalability/usability)?\\n'\n"
        + "    + '- Directory structure follows CRG cohesion principles (SAD.md §2.1)? See embedded DOC 3\\n- ≤15 files/dir, no god-module, no flat dump?\\n'\n"
        + "    + '- SEC block complete in §6 (<!-- SEC:START --> marker exists; boundaries + threats + verified_by, or an honest applicability: none + justification)?\\n- Each threat\\'s `verified_by` is a single test name (no comma-separated list) — split into a separate T-NN entry per additional test?',\n"
        + "})\n"
        + "if (!sad.ok) return sad\n"
        + "let sadContent = sad.content, sadB2 = sad.b2\n"
    )


def _render_phase2_subtask2_adr() -> str:
    return (
        B.render_phase_header("Sub-Task 2/3 — ADR.md")
        + "log('abLoop: ADR authoring (extract decisions from APPROVED SAD.md; downstream ADR-Constitution gate)')\n"
        + "const adr = await abLoop({\n"
        + "  phaseName: 'Sub-Task 2/3 — ADR.md', key: 'adr', deliverable: 'ADR.md', diskPath: '02-architecture/adr/ADR.md', diskPrefix: '# Architecture Decision Records',\n"
        + "  buildAPrompt: (round, prevB2) =>\n"
        + "    'YOU ARE ARCHITECT (Agent A for Sub-Task 2/3 ADR.md). ROUND ' + round + '.\\n'\n"
        + "    + 'REPO: ' + REPO + '\\nYour SINGLE deliverable: ' + REPO + '/02-architecture/adr/ADR.md\\n\\n'\n"
        + "    + '**REQUIRED H1 (must include \"Architecture Decision Records\")**: the file MUST start with `# Architecture Decision Records (ADR) — \\`<project>\\`` (or any H1 line containing the phrase \"Architecture Decision Records\"). Individual decisions go under `## ADR-NNN: <title>` sub-headings beneath this H1. The orchestrator loader validates this H1 anchor via startswith — a non-conforming first line fails the load step.\\n\\n'\n"
        + "    + 'Steps:\\n'\n"
        + "    + '1. Self-check (Bash): `test -f ' + REPO + '/02-architecture/adr/ADR.md`. If EXISTS, Read it.\\n'\n"
        + "    + '2. Extract key architecture decisions from SAD.md (read ' + REPO + '/02-architecture/SAD.md). Write individual ADR entries. EACH ADR: context, decision, consequences, alternatives considered. Cover tech stack (Python stdlib-only — read the actual Python version from .venv/bin/python --version), patterns (ThreadPoolExecutor, atomic write, circuit breaker), interfaces. Remove any `<!-- harness:template-stub -->` markers.\\n'\n"
        + "    + '3. Create dir ' + REPO + '/02-architecture/adr if missing. Re-read for FINAL state.\\n'\n"
        + "    + (round > 1 ? '4. Apply HIGH-severity gap fixes from previous B-2 via Edit (surgical).\\n' : '')\n"
        + "    + 'Return ONLY this compact JSON — do NOT embed file content (content is read from disk separately):\\n'\n"
        + "    + '{\"status\":\"OK\",\"files\":[\"02-architecture/adr/ADR.md\"],\"confidence\":\"high|medium|low\",\"citations\":[\"...\"],\"summary\":\"...\"}\\n\\n'\n"
        + "    + 'SCOPE RULES:\\n- DO NOT write SAD.md or TEST_SPEC.md.\\n- DO NOT run phase-transition / quality-gate commands.\\n- ONLY author ADR.md.'\n"
        + "    + (round > 1 && prevB2 ? '\\n\\n=== [DOC: Previous B-2 review JSON — ADR.md] ===\\n' + JSON.stringify(prevB2, null, 2) : ''),\n"
        + "  buildBDocs: (content) => [\n"
        + "    ['DOC 1: Previous Sub-Task B-2 review JSON — SAD.md (gaps-only; reason stripped)', JSON.stringify(safePrevB2(sadB2), null, 2)],\n"
        + "    ['DOC 2: 01-requirements/SRS.md (APPROVED — heading summary; USE Bash to Read full content if needed)', makeDocSummary(srsContent, { includeFirstLines: true })],\n"
        + "    ['DOC 3: 02-architecture/SAD.md (APPROVED — heading summary; USE Bash to Read full content if needed)', makeDocSummary(sadContent, { includeFirstLines: true })],\n"
        + "    ['DOC 4: draft 02-architecture/adr/ADR.md (full content — this IS the deliverable under review)', content],\n"
        + "    ['DOC 5: harness/templates/ADR.md (template format — heading summary)', makeDocSummary(adrTemplateContent)],\n"
        + "  ],\n"
        + "  checklist:\n"
        + "    '- Upstream SAD review caveats addressed?\\n- All major decisions documented (tech stack, patterns, interfaces)?\\n'\n"
        + "    + '- Each ADR has clear context, decision, consequences?\\n- Alternatives considered documented?\\n- Decision aligns with SAD.md architecture?\\n'\n"
        + "    + '- ADR format matches harness/templates/ADR.md (template format)? See embedded DOC 5',\n"
        + "})\n"
        + "if (!adr.ok) return adr\n"
        + "let adrContent = adr.content, adrB2 = adr.b2\n"
    )


def _render_phase2_constitution_check_adr() -> str:
    return (
        "\n"
        "// ---- Constitution Check — ADR (single-file, per phase2_plan.md CONSTITUTION-CHECK-ADR) ----\n"
        "phase('Constitution Check — ADR')\n"
        "log('check-constitution --file ADR.md + check-artifact-consistency (catches stub/low-density AND NFR→ADR coverage gaps before TEST_SPEC/Push depend on it)')\n"
        "const adrConstReport = await agent(\n"
        "  'YOU ARE THE ADR CONSTITUTION CHECKER. Run bash, fix if needed, report.\\n'\n"
        "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        "  + 'Command: `' + PY + ' ' + REPO + '/harness_cli.py check-constitution --phase 2 --project ' + REPO + ' --file 02-architecture/adr/ADR.md`\\n'\n"
        "  + '- PASS → proceed to the next command below.\\n'\n"
        "  + '- FAIL → the output lists `missing: <keywords>` on each sub-threshold dimension. Add substantive content covering those exact terms (e.g. a traceability table linking each decision to the SRS FR-IDs and specification it satisfies), remove any template-stub markers, re-run until PASS. Do NOT keyword-stuff — fold the terms into real decision context.\\n'\n"
        "  + '- File missing ([SKIP] exit 0) → report \"ADR-CONSTITUTION: FAIL — ADR.md missing\" (escalate).\\n\\n'\n"
        "  + 'After check-constitution PASSes, ALSO run: `' + PY + ' ' + REPO + '/harness_cli.py check-artifact-consistency --project ' + REPO + '`\\n'\n"
        "  + '- PASS → report \"ADR-CONSTITUTION: PASS\".\\n'\n"
        "  + '- FAIL on nfr_not_traced → the output names the missing NFR-ID. Read the corresponding SRS.md NFR section, then add a genuine traceability-table row for it (a real owning decision, or — if the NFR is cross-cutting with no single owning decision — a short honest ADR entry saying so). Do NOT invent test file paths, benchmark designs, gate numbers, or phase-mechanics that are not already documented elsewhere in this project (SRS.md / SAD.md / SPEC.md) — cite only what those files actually say. Re-run both commands until both PASS.\\n'\n"
        "  + '- FAIL on illegal_forward_ref → remove/correct the invented filename reference. Re-run both commands until both PASS.\\n\\n'\n"
        "  + 'SCOPE RULES:\\n- DO NOT touch SAD/TEST_SPEC.\\n- DO NOT run phase-transition commands.\\n- ONLY check-constitution + check-artifact-consistency on ADR.md and fix it.',\n"
        "  { label: 'constitution-adr', phase: 'Constitution Check — ADR', agentType: 'general-purpose' },\n"
        ")\n"
        "if (!(typeof adrConstReport === 'string' && /ADR-CONSTITUTION:\\s*PASS/.test(adrConstReport))) {\n"
        "  return { error: 'ADR constitution check did not PASS', raw: String(adrConstReport ?? '').slice(-500) }\n"
        "}\n"
        "// Structural gate (2026-07-10 fix): don't just trust the agent's self-report — the\n"
        "// original bug was discovered because a P2-produced ADR.md silently lacked NFR-01\n"
        "// coverage all the way until the Sync-phase git push (after Push+Advance already\n"
        "// succeeded). Verify check-artifact-consistency independently here so a false\n"
        "// \"PASS\" claim can't slip through to Push/Advance.\n"
        "{\n"
        "  const aciVerify = await agent(\n"
        "    'Run: `' + PY + ' ' + REPO + '/harness_cli.py check-artifact-consistency --project ' + REPO + '`\\n'\n"
        "    + 'Report ONLY: \"ACI: PASS\" if exit code 0, else \"ACI: FAIL — <first FAIL line>\".',\n"
        "    { label: 'aci-verify', phase: 'Constitution Check — ADR', agentType: 'general-purpose' },\n"
        "  )\n"
        "  if (!(typeof aciVerify === 'string' && /ACI:\\s*PASS/.test(aciVerify))) {\n"
        "    return { error: 'check-artifact-consistency did not PASS after ADR constitution check', raw: String(aciVerify ?? '').slice(-500) }\n"
        "  }\n"
        "}\n"
    )


def _render_phase2_subtask3_test_spec() -> str:
    return (
        B.render_phase_header("Sub-Task 3/3 — TEST_SPEC.md")
        + "log('abLoop: TEST_SPEC authoring (per-FR test catalog; v2.9.1 B.3 table-row shape; check-test-spec-consistency)')\n"
        + "const testSpec = await abLoop({\n"
        + "  phaseName: 'Sub-Task 3/3 — TEST_SPEC.md', key: 'test-spec', deliverable: 'TEST_SPEC.md', diskPath: '02-architecture/TEST_SPEC.md', diskPrefix: '# TEST_SPEC.md',\n"
        + "  buildAPrompt: (round, prevB2) =>\n"
        + "    'YOU ARE ARCHITECT (Agent A for Sub-Task 3/3 TEST_SPEC.md). ROUND ' + round + '.\\n'\n"
        + "    + 'REPO: ' + REPO + '\\nYour SINGLE deliverable: ' + REPO + '/02-architecture/TEST_SPEC.md\\n\\n'\n"
        + "    + '**REQUIRED H1 (must include \"TEST_SPEC\")**: the file MUST start with `# TEST_SPEC.md — <subtitle>` (or any H1 line containing \"TEST_SPEC\"). Per-FR catalogs go under `### FR-XX:` headers beneath this H1. The orchestrator loader validates this H1 anchor via startswith — a non-conforming first line fails the load step.\\n\\n'\n"
        + "    + 'Steps:\\n'\n"
        + "    + '1. Self-check (Bash): `test -f ' + REPO + '/02-architecture/TEST_SPEC.md`. If EXISTS, Read it.\\n'\n"
        + "    + '2. Generate Test Specification Catalog. CRITICAL shape (v2.9.1 B.3): each FR is a `### FR-XX: ...` header FOLLOWED BY TABLE ROWS (a prose-only doc FAILS the D4 spec-coverage parser).\\n'\n"
        + "    + '   - Per FR (enumerate from SPEC.md ### FR-XX: headings — do not assume a fixed FR count): assign Classification (API_ENDPOINT|DATA_ENTITY|ALGORITHM|STATE_MACHINE|INTEGRATION|SECURITY_CONTROL|INFRASTRUCTURE). ≥1 named test case (happy_path + validation mandatory). Preserve TEST_INVENTORY.yaml names where specified.\\n'\n"
        + "    + '   - Apply 8-Question Protocol per FR. Concrete Inputs in TRUE form (key=\"value\", NOT pytest-id underscore form). Sub-assertions table per FR (rule_id + predicate + applies_to).\\n'\n"
        + "    + '   - **TEST_SPEC shape rules (v2.13.0 — covers FR-05 P3 2026-07-16 lesson) — MANDATORY, checked by check-test-spec-consistency:**\\n'\n"
        + "    + '     1. **Multi-scenario cases (1 case → N scenarios)**: when one case row enumerates N distinct expected behaviors (e.g. 5 exit codes, 3 status transitions), DO NOT collapse into a single Inputs row. Use N sub-rows, each with its own Inputs set + Expected column. One test function per sub-row.\\n'\n"
        + "    + '     2. **Stateful isolation cases**: when a case exercises shared mutable state across sub-cases (breaker.json, store.json, cache.json), explicitly declare `state_mode: shared | isolate_per_case | isolate_per_test` in the Inputs row. Tests must match the declared mode (e.g. `isolate_per_test` requires function-scoped fixtures, NOT module-scope).\\n'\n"
        + "    + '     3. **Subprocess / cross-process cases**: when a case spawns subprocesses (NFR Integration N-series), explicitly declare `subprocess_mode: in_process | out_of_process` and `shared_TASKQ_HOME: bool` in the Inputs row. Tests must propagate `PYTHONPATH` to child env if `out_of_process` (pytest `pythonpath` config does NOT inherit).\\n'\n"
        + "    + '     4. **Sub-assertion predicate naming**: `predicate` column MUST NOT use Python stdlib top-level module names as the LHS identifier: `json`, `os`, `sys`, `time`, `subprocess`, `pathlib`, `asyncio`, `typing`, `logging`, `path`, `file`, `id`, `type`. If the prose AC literally uses such a word, rewrite the predicate using a domain-specific synonym (`json_flag`, `os_name`, `path_str`, etc.) and note the rename in the `rule_id` comment. Same check applies to class names (`dict`, `list`, `set`, `tuple`, `str`, `int`, `bool`, `bytes`).\\n'\n"
        + "    + '     5. **Spec ambiguity protocol**: when SRS.md AC prose + Inputs column seem inconsistent (e.g. AC says \"5 of which 3 done\" but Inputs lists 5 identical commands), DO NOT invent impossible assertions. Declare `precondition: <how to construct the scenario>` explicitly in the Inputs row, OR mark the case `skip_reason: spec_gap_resolved_in_p3`. check-test-spec-consistency will reject ambiguous cases that lack one of these.\\n'\n"
        + "    + '     6. **NFR Layering & Parameterization**: Read 01-requirements/TRACEABILITY_MATRIX.md and TEST_INVENTORY.yaml via Bash. For NFRs where TEST_SPEC.md is the verifier (Integration-level, e.g., NP-06, NP-07), you MUST define concrete `Inputs` (e.g., fault types, loop counts) and `Sub-assertions`. For all other NFRs (Unit/Static), isolate them in a `Deferred to Downstream Phases` table with columns: #, NFR, Test Function, Layer, Title.\\n'\n"
        + "    + '   - Step 1b Architecture-Risk Triggers: scan SAD modules — shared mutable state (store.py) → force NP-13; external process (executor.py subprocess) → force NP-15; cache (cache.py) → force NP-07. Forced cases tagged SAD: in tests/integration/.\\n'\n"
        + "    + '   - **Direction B (Properties)**: If an FR has algebraic invariants (round-trip / idempotence / commutativity / invariant preservation), declare a `**Properties**` table for it: columns `property_id | invariant | applies_to` (+ optional `generator_strategy` / `shrinks_to`). The column name MUST be exactly `invariant` — the check-property-spec parser looks for a header containing that word; `property_statement` or any other name is NOT recognised and silently skips the check for that FR. Skip for FRs without clean algebraic invariants (do NOT force).\\n'\n"
        + "    + '     **Invariant syntax — MANDATORY**: the `invariant` cell MUST be valid Python-expression syntax (e.g. `decode(encode(source)) == source`), never prose. A symbolic variable not bound to any declared case (e.g. `sig_fn(x) == sig_fn(x)`) is fine — it degrades to non-blocking needs_review, not an error. A natural-language sentence describing the property in words FAILS TO PARSE and hard-blocks P2 with `malformed_predicate`. Keep any explanation OUTSIDE the table (a note below it), never inside the invariant cell itself.\\n'\n"
        + "    + '   - NFR Pattern Activation table + cross-cutting section + Summary table (counts per type).\\n'\n"
        + "    + '3. Run self-consistency: `' + PY + ' ' + REPO + '/harness_cli.py check-test-spec-consistency --project ' + REPO + '`. Fix until it passes.\\n'\n"
        + "    + '3b. If you declared any `**Properties**` table (Direction B), also run: `' + PY + ' ' + REPO + '/harness_cli.py check-property-spec --project ' + REPO + ' --no-require-execution`. Fix until it passes — property test EXECUTION is not required until P4, but the invariant text itself must already be syntactically valid Python.\\n'\n"
        + "    + '4. Re-read for FINAL state.\\n'\n"
        + "    + (round > 1 ? '5. Apply HIGH-severity gap fixes from previous B-2 via Edit (surgical).\\n' : '')\n"
        + "    + 'Return ONLY this compact JSON — do NOT embed file content (content is read from disk separately):\\n'\n"
        + "    + '{\"status\":\"OK\",\"files\":[\"02-architecture/TEST_SPEC.md\"],\"confidence\":\"high|medium|low\",\"citations\":[\"...\"],\"summary\":\"...\"}\\n\\n'\n"
        + "    + 'SCOPE RULES:\\n- DO NOT write SAD/ADR.\\n- DO NOT run phase-transition / run-gate commands.\\n- DO NOT modify harness/.\\n- ONLY author TEST_SPEC.md (check-test-spec-consistency is allowed).'\n"
        + "    + (round > 1 && prevB2 ? '\\n\\n=== [DOC: Previous B-2 review JSON — TEST_SPEC.md] ===\\n' + JSON.stringify(prevB2, null, 2) : ''),\n"
        + "  buildBDocs: (content) => [\n"
        + "    ['DOC 1: Previous Sub-Task B-2 review JSON — ADR.md (gaps-only; reason stripped)', JSON.stringify(safePrevB2(adrB2), null, 2)],\n"
        + "    ['DOC 2: 01-requirements/SRS.md (APPROVED — heading summary; USE Bash to Read full content if needed)', makeDocSummary(srsContent, { includeFirstLines: true })],\n"
        + "    ['DOC 3: 02-architecture/SAD.md (APPROVED — heading summary; USE Bash to Read full content if needed)', makeDocSummary(sadContent, { includeFirstLines: true })],\n"
        + "    ['DOC 4: 02-architecture/adr/ADR.md (APPROVED — heading summary; USE Bash to Read full content if needed)', makeDocSummary(adrContent)],\n"
        + "    ['DOC 5: draft 02-architecture/TEST_SPEC.md (full content — this IS the deliverable under review)', content],\n"
        + "  ],\n"
        + "  checklist:\n"
        + "    '- Upstream ADR review caveats addressed?\\n- Every FR has ≥1 named test case (happy_path + validation mandatory)?\\n'\n"
        + "    + '- 8-Question Protocol applied per FR?\\n- Classification assigned per FR?\\n- NFR Pattern Activation table filled?\\n'\n"
        + "    + '- Architecture-risk triggers applied (NP-13/NP-15/NP-07 forced where SAD warrants)?\\n'\n"
        + "    + '- Concrete Inputs in TRUE form (key=\"value\"), not pytest-id form?\\n- Sub-assertions table per FR (rule_id + predicate + applies_to)?\\n'\n"
        + "    + '- Each `### FR-XX:` header followed by TABLE ROWS (not prose-only)?\\n- Summary table populated with counts per type?\\n'\n"
        + "    + '- Self-consistency gate passes? (`check-test-spec-consistency`)?\\n- Direction B property gate passes? (python3 harness_cli.py check-property-spec --project . --no-require-execution)\\n- Cross-cutting NFRs validated? (Integration-level NFRs MUST have concrete Inputs/Sub-assertions; Unit/Static NFRs MUST be moved to a Deferred table)?\\n'\n"
        + "    + '- All upstream deliverables consistent with each other? No contradictory decisions?',\n"
        + "})\n"
        + "if (!testSpec.ok) return testSpec\n"
        + "let testSpecContent = testSpec.content\n"
    )


def _render_phase2_sab_generation() -> str:
    return (
        B.render_phase_header("SAB Generation")
        + "log('SAB-WRITE (canonical template into SAD §5) + SAB-VALIDATE + SAB-GENERATE')\n"
        + "const sabReport = await agent(\n"
        + "  'YOU ARE THE SAB GENERATOR. Write the SAB YAML block into SAD.md §5, validate, generate SAB.json.\\n'\n"
        + "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "  + 'Steps:\\n'\n"
        + "  + '1. SAB-WRITE: Edit ' + REPO + '/02-architecture/SAD.md §5 — replace the `<!-- SAB:START -->` placeholder with a real `sab:` YAML block. CONTRACT (parsed by sab_parser.py):\\n'\n"
        + "  + '   - `phase: 2` MUST be a bare int (NOT \"2\").\\n'\n"
        + "  + '   - layers + allowed_dependencies reflect SAD §2 module design (api/service/store style).\\n'\n"
        + "  + '   - nfr_traceability: one entry per NFR enumerated from SPEC.md (parse `### NFR-XX:` headings — do not assume a fixed NFR count) with a `type` from the 8 legal values (performance/security/maintainability/reliability/testability/deployability/scalability/usability) + measurable `target` + `module`.\\n'\n"
        + "  + '   - fr_module_traceability: one entry per FR enumerated from SPEC.md (parse `### FR-XX:` headings) pointing to a REAL module from SAD §2.\\n'\n"
        + "  + '   - quality_targets (max_complexity/min_coverage/max_coupling), architecture_constraints (no_circular_dependencies), high_risk_modules. Leave advisory_only/gate_score_overrides/nfr_dimension_mapping empty ({} or []).\\n'\n"
        + "  + '2. SAB-VALIDATE: `' + PY + ' ' + REPO + '/harness/scripts/generate_sab.py --validate --project ' + REPO + '`. Must exit 0. Fix unknown NFR type / phase-as-string until PASS.\\n'\n"
        + "  + '3. SAB-GENERATE: `' + PY + ' ' + REPO + '/harness/scripts/generate_sab.py --project ' + REPO + '` (add --overwrite if SAB.json exists). Produces .methodology/SAB.json.\\n\\n'\n"
        + "  + 'Report plain text: \"SAB: PASS\" or \"SAB: FAIL — <reason>\".\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n- DO NOT modify harness/ source (running harness/scripts/generate_sab.py is allowed, editing it is NOT — HR-17).\\n- DO NOT run advance-phase / push / run-gate.\\n- ONLY edit SAD.md §5 SAB block + run generate_sab.py validate/generate.',\n"
        + "  { label: 'sab-generation', phase: 'SAB Generation', agentType: 'general-purpose' },\n"
        + ")\n"
        + "if (!(typeof sabReport === 'string' && /SAB:\\s*PASS/.test(sabReport))) {\n"
        + "  return { error: 'SAB generation did not PASS', raw: String(sabReport ?? '').slice(-500) }\n"
        + "}\n"
    )


def _render_phase2_constitution_check() -> str:
    return (
        B.render_phase_header("Constitution Check")
        + "log('check-constitution --phase 2 until PASS (max 5 attempts)')\n"
        + "let constPass = false, constReport = ''\n"
        + "for (let attempt = 1; attempt <= 5; attempt++) {\n"
        + "  log('  attempt ' + attempt + '/5')\n"
        + "  constReport = await agent(\n"
        + "    'YOU ARE THE PHASE-2 CONSTITUTION CHECKER. Run bash, fix, report.\\n'\n"
        + "    + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "    + 'Command: `' + PY + ' ' + REPO + '/harness_cli.py check-constitution --phase 2 --project ' + REPO + '`\\n'\n"
        + "    + 'If PASS: report \"CONSTITUTION: PASS\". If FAIL: the output lists `missing: <keywords>` on each sub-threshold dimension — surgically fold those exact terms into the relevant P2 doc as real content (e.g. a traceability table to SRS FR-IDs), do NOT remove content or keyword-stuff, re-run until PASS.\\n\\n'\n"
        + "    + 'SCOPE RULES:\\n- DO NOT run advance-phase/push/run-gate.\\n- ONLY check-constitution + edit P2 deliverables to fix.',\n"
        + "    { label: 'constitution-' + attempt, phase: 'Constitution Check', agentType: 'general-purpose' },\n"
        + "  )\n"
        + "  constPass = typeof constReport === 'string' && /CONSTITUTION:\\s*PASS/.test(constReport)\n"
        + "  if (constPass) break\n"
        + "}\n"
        + "if (!constPass) return { error: 'Phase 2 constitution check FAIL after 5 attempts', raw: String(constReport ?? '').slice(-500) }\n"
        + "\n"
        + "// T1-B audit fix: re-run check-artifact-consistency AFTER SAB Generation.\n"
        + "// The ADR constitution check ran aci against forward-refs only (SAB didn't exist\n"
        + "// yet). Now that SAB is generated, run the full aci to catch SAB-dependent\n"
        + "// issues (SEC threat owner_module must exist in SAB, NFR targets vs SAB\n"
        + "// quality_targets, etc.) — this is the SEC-VALIDATE step phase2_plan.md places\n"
        + "// AFTER SAB Generation.\n"
        + "log('check-artifact-consistency (post-SAB SEC-VALIDATE)')\n"
        + "const aciPostSab = await agent(\n"
        + "  'Run: `' + PY + ' ' + REPO + '/harness_cli.py check-artifact-consistency --project ' + REPO + '`\\n'\n"
        + "  + 'Return the verbatim exit code line: \"[check-artifact-consistency] OK\" or \"[BLOCKED] ...\".',\n"
        + "  { label: 'aci-post-sab', phase: 'Constitution Check', agentType: 'general-purpose' },\n"
        + ")\n"
        + "if (typeof aciPostSab !== 'string' || !aciPostSab.includes('OK')) {\n"
        + "  return { error: 'check-artifact-consistency (post-SAB SEC-VALIDATE) FAIL', raw: String(aciPostSab ?? '').slice(-500) }\n"
        + "}\n"
    )


def _render_phase2_peer_review() -> str:
    return (
        B.render_phase_header("Peer Review")
        + "log('Agent B (TECH_LEAD) holistic review of all 3 P2 deliverables; max ' + MAX_PEER_ROUNDS + ' rounds (HR-12)')\n"
        + "let peerB2 = null\n"
        + "let peerReviewPassed = false\n"
        + "// W-02 (parity with phase1 runPeerReview): fixer reports which deliverables it\n"
        + "// edited; only those get reloaded next round instead of all 3 (saves ~2 loadpy\n"
        + "// agents/round). null → fall back to full reload.\n"
        + "let peerFixerResult = null\n"
        + "for (let round = 1; round <= MAX_PEER_ROUNDS; round++) {\n"
        + "  log('  --- Peer round ' + round + '/' + MAX_PEER_ROUNDS + ' ---')\n"
        + "  // v15: budget guard — gracefully exit if running low (Bug #3 mitigation)\n"
        + "  if (typeof budget !== 'undefined' && budget.remaining && budget.remaining() < 100000) {\n"
        + "    log('  Peer Review budget low (' + Math.round((budget.remaining() || 0) / 1000) + 'k remaining) — exiting gracefully')\n"
        + "    if (peerB2 && peerB2.review_status === 'APPROVE') { log('  exiting with prior APPROVE'); break }\n"
        + "    if (peerB2) return { ok: false, peerB2, budget_exhausted: true }\n"
        + "    return { error: 'Budget exhausted before Peer Review completed', budget_exhausted: true }\n"
        + "  }\n"
        + "  // v15: wrap agent() in try/catch — API errors (429/network) must not crash workflow (Bug #2)\n"
        + "  let bResult\n"
        + "  try { bResult = await agent(\n"
        + "    buildBPrompt('TECH_LEAD', 'all 3 P2 deliverables (holistic)', [\n"
        + "      ['DOC 1: 02-architecture/SAD.md (heading summary; USE Bash to Read full content if needed)', makeDocSummary(sadContent, { includeFirstLines: true })],\n"
        + "      ['DOC 2: 02-architecture/adr/ADR.md (heading summary; USE Bash to Read full content if needed)', makeDocSummary(adrContent, { includeFirstLines: true })],\n"
        + "      ['DOC 3: 02-architecture/TEST_SPEC.md (heading summary; USE Bash to Read full content if needed)', makeDocSummary(testSpecContent, { includeFirstLines: true })],\n"
        + "    ],\n"
        + "    '- All FRs covered across all deliverables?\\n- No contradictions between deliverables?\\n- Each item testable/traceable?\\n'\n"
        + "    + '- All gaps from sub-task reviews addressed?\\n- Terminology consistent across all documents?\\n'\n"
        + "    + '- SAB block layers / NFR targets semantically match SAD §2 module design?\\n'\n"
        + "    + '- Every fr_module_traceability entry points to a real SAD §2 module?\\n- NFR target fields measurable (not N/A/empty)?\\n'\n"
        + "    + '- SEC block complete in SAD.md §6 (<!-- SEC:START --> marker exists; boundaries + threats + verified_by, or an honest applicability: none + ≥20-char justification)?'),\n"
        + "    { label: 'peer-b-r' + round, phase: 'Peer Review', agentType: 'general-purpose' },\n"
        + "  ) } catch (e) {\n"
        + "    if (round === MAX_PEER_ROUNDS) {\n"
        + "      return { error: 'HR-12: Peer Review B agent failed at round ' + round + '/' + MAX_PEER_ROUNDS + ' (Phase 2 exit gate)', last: String(e.message ?? e).slice(0, 200), b2: null }\n"
        + "    }\n"
        + "    log('  Peer B agent failed: ' + String(e.message ?? e).slice(0, 80) + ' — retrying'); continue\n"
        + "  }\n"
        + "  // --- structured_b_review (T1-B: harness-owned B-2 validation + escalation) ---\n"
        + "  // Peer review spans 3 files — no single --doc-content. Pass null.\n"
        + "  const sbrResult = await structuredBReview(\n"
        + "    bResult, round, MAX_PEER_ROUNDS, null, 2,\n"
        + "  )\n"
        + "  peerB2 = sbrResult.b2 || parseAgentJson(bResult, 'PeerB-r' + round)\n"
        + "  log('  Peer B-2: ' + (peerB2 ? peerB2.review_status : '(none)')\n"
        + "    + ' | gaps=' + ((peerB2 ? peerB2.gaps : []) || []).length\n"
        + "    + ' | escalation=' + sbrResult.escalation_action)\n"
        + "\n"
        + "  if (sbrResult.escalation_action === 'approve') { log('  APPROVED'); break }\n"
        + "  if (sbrResult.escalation_action === 'escalate_human') {\n"
        + "    return { error: 'HR-12: Peer Review: ' + sbrResult.escalation_reason, b2: peerB2, escalation_action: 'escalate_human' }\n"
        + "  }\n"
        + "  // HR-12: round MAX_PEER_ROUNDS without convergence → escalate to human.\n"
        + "  if (round === MAX_PEER_ROUNDS) {\n"
        + "    return { error: 'HR-12: Peer Review did not converge in ' + round + '/' + MAX_PEER_ROUNDS + ' rounds (Phase 2 exit gate — escalate to human)', b2: peerB2 }\n"
        + "  }\n"
        + "  // Holistic gaps span multiple files → dispatch a fixer agent\n"
        + "  log('  Peer review found gaps — dispatching fixer for round ' + (round + 1))\n"
        + "  // v15: wrap fixer agent() in try/catch — fixer failures should not crash workflow (Bug #2)\n"
        + "  let peerFixerRaw = null\n"
        + "  try {\n"
        + "    peerFixerRaw = await agent(\n"
        + "      'YOU ARE ARCHITECT (holistic fixer). Fix peer-review gaps across P2 deliverables.\\n'\n"
        + "      + 'REPO: ' + REPO + '\\n\\nPeer review B-2 JSON:\\n' + JSON.stringify(peerB2, null, 2) + '\\n\\n'\n"
        + "      + 'Apply surgical Edits to whichever of 02-architecture/SAD.md, 02-architecture/adr/ADR.md, 02-architecture/TEST_SPEC.md are affected. Address all medium/high gaps.\\n\\n'\n"
        + "      + 'Return compact JSON ONLY (no prose):\\n'\n"
        + "      + '{\"status\":\"OK\",\"modified_files\":[\"02-architecture/SAD.md\"],\"summary\":\"<1-2 lines>\"}\\n'\n"
        + "      + '(modified_files: list ONLY the deliverables you actually edited, using the EXACT relative paths above: \"02-architecture/SAD.md\", \"02-architecture/adr/ADR.md\", \"02-architecture/TEST_SPEC.md\".)\\n\\n'\n"
        + "      + 'SCOPE RULES:\\n- DO NOT run phase-transition/push/run-gate.\\n- DO NOT modify harness/.\\n- ONLY edit the 3 P2 deliverables.',\n"
        + "      { label: 'peer-fix-r' + round, phase: 'Peer Review', agentType: 'general-purpose' },\n"
        + "    )\n"
        + "  } catch (e) {\n"
        + "    log('  Peer fixer agent failed: ' + String(e.message ?? e).slice(0, 80) + ' — continuing without fix')\n"
        + "  }\n"
        + "  try { peerFixerResult = parseAgentJson(peerFixerRaw, 'peer-fixer-r' + round) }\n"
        + "  catch (e) { peerFixerResult = null; log('  Peer fixer JSON parse failed — will reload all 3 docs') }\n"
        + "\n"
        + "  // W-02: reload only the deliverables the fixer reported editing (fallback: all 3).\n"
        + "  const peerModified = peerFixerResult && Array.isArray(peerFixerResult.modified_files) ? peerFixerResult.modified_files : null\n"
        + "  const peerReload = new Set(peerModified || ['02-architecture/SAD.md', '02-architecture/adr/ADR.md', '02-architecture/TEST_SPEC.md'])\n"
        + "  // O4: capture pre-reload byte counts so the log can show a real Δ. A modified_files\n"
        + "  // entry whose reloaded bytes are unchanged (Δ0) means the fixer's Edit was a no-op —\n"
        + "  // worth seeing rather than trusting the count of \"modified\" paths blindly.\n"
        + "  const preBytes = { sad: sadContent.length, adr: adrContent.length, test: testSpecContent.length }\n"
        + "  if (peerReload.has('02-architecture/SAD.md')) sadContent = await loadFileViaPython('02-architecture/SAD.md', '# Software Architecture Document', 'Peer Review')\n"
        + "  if (peerReload.has('02-architecture/adr/ADR.md')) adrContent = await loadFileViaPython('02-architecture/adr/ADR.md', '# Architecture Decision Records', 'Peer Review')\n"
        + "  if (peerReload.has('02-architecture/TEST_SPEC.md')) testSpecContent = await loadFileViaPython('02-architecture/TEST_SPEC.md', '# TEST_SPEC.md', 'Peer Review')\n"
        + "  // F2 (parity with phase1 runPeerReview 566-569): a failed reload must NOT feed an\n"
        + "  // 'ERROR:' sentinel string into next round's B summary as if it were content.\n"
        + "  for (const [lbl, c] of [['SAD.md', sadContent], ['ADR.md', adrContent], ['TEST_SPEC.md', testSpecContent]]) {\n"
        + "    if (c.startsWith('ERROR:') || c.length < 50) {\n"
        + "      return { error: 'Peer Review: ' + lbl + ' reload failed (round ' + round + ')', loader_preview: c.slice(0, 200) }\n"
        + "    }\n"
        + "  }\n"
        + "  const fmtDelta = (n) => (n >= 0 ? '+' : '') + n\n"
        + "  log('  Reloaded after fixer (' + (peerModified ? 'files=' + peerModified.join(',') : 'all 3, fixer JSON unavailable') + '): '\n"
        + "    + 'SAD=' + sadContent.length + ' Δ' + fmtDelta(sadContent.length - preBytes.sad)\n"
        + "    + ' ADR=' + adrContent.length + ' Δ' + fmtDelta(adrContent.length - preBytes.adr)\n"
        + "    + ' TEST_SPEC=' + testSpecContent.length + ' Δ' + fmtDelta(testSpecContent.length - preBytes.test))\n"
        + "}\n"
        + "if (peerReviewPassed) log('  → Peer Review PASS (APPROVE)')\n"
    )


def _render_phase2_push() -> str:
    return (
        B.render_phase_header("Push")
        + "log('push-checkpoint --phase 2 (retry until success)')\n"
        + "let pushOk = false, pushReport = ''\n"
        + "for (let attempt = 1; attempt <= 5; attempt++) {\n"
        + "  log('  attempt ' + attempt + '/5')\n"
        + "  pushReport = await agent(\n"
        + "    'YOU ARE THE PHASE-2 PUSH ORCHESTRATOR.\\n'\n"
        + "    + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "    + 'Step 1 (Bash): `' + PY + ' ' + REPO + '/harness_cli.py push-checkpoint --phase 2 --project ' + REPO + '`\\n'\n"
        + "    + '  - If blocked by a hook error: reword commit message to start with `chore(harness):` (documented bypass; NOT --no-verify), re-run. Retry until success.\\n'\n"
        + "    + 'Step 2: Read ' + REPO + '/HANDOVER.md and confirm it exists.\\n'\n"
        + "    + 'Report: \"PUSH: PASS|FAIL — <details>\".\\n\\n'\n"
        + "    + 'SCOPE RULES:\\n- DO NOT re-do any P2 deliverable.\\n- DO NOT run advance-phase here.\\n- DO NOT use --no-verify.\\n- ONLY push + verify HANDOVER.md.',\n"
        + "    { label: 'push-' + attempt, phase: 'Push', agentType: 'general-purpose' },\n"
        + "  )\n"
        + "  pushOk = typeof pushReport === 'string' && /PUSH:\\s*PASS/.test(pushReport)\n"
        + "  if (pushOk) break\n"
        + "}\n"
        + "if (!pushOk) return { error: 'push-checkpoint --phase 2 did not succeed in 5 attempts', raw: String(pushReport ?? '').slice(-500) }\n"
    )


def _render_phase2_advance() -> str:
    return (
        B.render_phase_header("Advance")
        + "// Approval JSONs (SAD.md/ADR.md/TEST_SPEC.md) are now persisted by abLoop exit\n"
        + "// (persistApproval helper) — not here. See bc913a0 / pending P2 parity commit.\n"
        + "log('advance-phase --completed 2 + confirm HANDOVER.md reflects Phase 3 entry')\n"
        + "const advanceReport = await agent(\n"
        + "  'YOU ARE THE PHASE-2 ADVANCE ORCHESTRATOR.\\n'\n"
        + "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "  + 'Step 1 (Bash): `' + PY + ' ' + REPO + '/harness_cli.py advance-phase --completed 2 --project ' + REPO + '`\\n'\n"
        + "  + '   PHASE-TRUTH (HR-11): if advance-phase fails on Phase Truth (<90%), check phase_truth_verifier output in .sessi-work/, fix the failing phase-link/gate artifact, re-run (max 3, then escalate to human).\\n'\n"
        + "  + 'Step 2: Read ' + REPO + '/.methodology/state.json; confirm current_phase = 3 (advance-phase writes atomically).\\n'\n"
        + "  + 'Report: \"ADVANCE: PASS|FAIL — <details>\". PHASE_3_PLAN: ' + REPO + '/.methodology/phase3_plan.md\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n- DO NOT re-do P2.\\n- DO NOT modify harness/ (HR-17).\\n- ONLY advance-phase + verify HANDOVER.md.',\n"
        + "  { label: 'advance', phase: 'Advance', agentType: 'general-purpose' },\n"
        + ")\n"
        + "// F1 (parity with phase1 advance 1079-1081): advance-phase can FAIL on Phase Truth\n"
        + "// (<90%); do NOT report \"complete\" when P3 was never entered.\n"
        + "if (!/ADVANCE:\\s*PASS/.test(String(advanceReport ?? ''))) {\n"
        + "  return { error: 'advance-phase --completed 2 did not PASS', raw: String(advanceReport ?? '').slice(-600) }\n"
        + "}\n"
    )


def generate_phase2() -> str:
    parts = [
        _HEADER_2,
        "",
        _render_meta(
            name="phase2-architecture",
            description=(
                "Phase 2 Architecture — SAD/ADR/TEST_SPEC serial A/B + SAB generation "
                "+ peer review (phase2_plan.md v2.12.0)"
            ),
            phases=_META_PHASES_2,
        ),
        "",
        B.RESOLVE_REPO_BLOCK,
        _PHASE2_MAX_ROUND_CONSTS + B.REPO_LOG_LINE,
        B.WRITE_SCOPE_BLOCK,
        "",
        B.render_json_utils(),
        B.render_build_b_prompt(
            min_reason_chars=100,
            docs_embedded_note=_PHASE2_DOCS_EMBEDDED_NOTE,
            critical_docs_note=_PHASE2_CRITICAL_DOCS_NOTE,
            evidence_type_note=_PHASE2_EVIDENCE_TYPE_NOTE,
        ),
        B.render_safe_prev_b2(),
        B.render_make_doc_summary(),
        B.render_structured_b_review(default_phase_num=2),
        B.render_generic_ab_loop(b_role="TECH_LEAD", phase_num=2),
        B.render_persist_approval(synthesize_reason=True, use_schema_verdict=False),
        B.render_load_file_via_python(),
        _render_phase2_entry_preflight(),
        _render_phase2_load_upstream(),
        _render_phase2_subtask1_sad(),
        _render_phase2_subtask2_adr(),
        _render_phase2_constitution_check_adr(),
        _render_phase2_subtask3_test_spec(),
        _render_phase2_sab_generation(),
        _render_phase2_constitution_check(),
        _render_phase2_peer_review(),
        _render_phase2_push(),
        _render_phase2_advance(),
        B.render_sync_verified(),
        (
            "\nlog('Phase 2 workflow complete. Open .methodology/phase3_plan.md to continue.')\n"
            "return {\n"
            "  phase: 2,\n"
            "  peer_review_status: peerB2 ? peerB2.review_status : 'unknown',\n"
            "  push_status: pushOk ? 'PASS' : 'unknown',\n"
            "  advance_status: typeof advanceReport === 'string' && /ADVANCE:\\s*PASS/.test(advanceReport) ? 'PASS' : 'unknown',\n"
            "  artifacts: ['02-architecture/SAD.md', '02-architecture/adr/ADR.md', '02-architecture/TEST_SPEC.md', '.methodology/SAB.json', '.methodology/quality_manifest.json', 'HANDOVER.md'],\n"
            "  notes: 'Phase 2 complete per phase2_plan.md v2.12.0. Phase 3 (Implementation) ready.',\n"
            "}\n"
        ),
    ]
    return "\n".join(p for p in parts if p is not None)
