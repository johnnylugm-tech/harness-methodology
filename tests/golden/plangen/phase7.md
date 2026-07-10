# Phase 7 Full Execution Plan -- golden-fixture-project

> **Version**: v<VERSION> (project plan)
> **Project**: golden-fixture-project
> **Date**: <DATE>
> **Framework**: harness-methodology v<VERSION>
> **Phase**: 7 - Risk Management
> **Status**: Full version (including Phase 7 detailed tasks)

> **Hard Rules in Force (this plan)** — explicit reminders:
> - HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews. Never role-play A or B yourself.
> - HR-05: harness-methodology wins all conflicts — if a project decision contradicts SKILL.md / INIT / this plan, the harness wins.
> - HR-16: Trace dimension = `min(4a, 4b, 4c)` — ALL THREE must pass (G2/G3/G4 only): 4a = 100% over IN_PROGRESS+VERIFIED FRs, 4b = TEST_SPEC→test coverage (60/80/90% at G2/G3/G4), 4c = NFR→test coverage (60/80/90% at G2/G3/G4, NFR-99 placeholder excluded). `gate_score_overrides` is a **threshold floor (raises, not lowers)** per `sab_parser.derive_gate_score_overrides` — cannot bypass a failing trace dim. Remediation: fix code/FRs/tests to pass, accept gate block, or escalate to human. No automated override.
> - HR-17: NEVER modify files inside `harness/` — debug the framework, never hot-patch the submodule.

---

## Phase 7 Tasks: Risk Management

### Phase 7 Overview
Phase 7 identifies, tracks, and mitigates all risks introduced during development.
Each FR gets a Gate 1 risk-aware re-evaluation (CHECKPOINT). No harness run-gate — P7 cleared by Gate 4. However, advance-phase still enforces TDD-PRECHECK (gitleaks + ruff + mypy + pytest 100% + D4 spec-coverage ≥90% + mutmut mutation testing) before FSM transition.

> If risk mitigation requires code changes to any FR, run full TDD: `run-fr-step --step TDD-RED` → TDD-GREEN → TDD-IMPROVE → GATE1. Crash recovery (`resume-fr-phase`) auto-detects code changes and switches from GATE1-DELTA to full TDD when needed.

> **Crash Recovery**: `python3 harness_cli.py resume-fr-phase --phase 7 --project .`
> prints the next pending step. Each `run-fr-step` auto-pushes to GitHub on completion.
> Per-FR GATE1-DELTA auto-pushes on completion; when code-change triggers full TDD, TDD-RED → GREEN → IMPROVE → GATE1 each push immediately (idempotent on re-run).
> At milestones, `HANDOVER.md` is written with phase/FR/status summary.

> **Checkpoint Index**:
> - CHECKPOINT-1: Gate 1 / FR-01 *(auto-push via run-fr-step)*
> - CHECKPOINT-2: Gate 1 / FR-02 *(auto-push via run-fr-step)*
> - MILESTONE: P7 exit push (risk register complete) → **HANDOVER.md**

### Entry Gate Verification

- **[ENTRY-CHECK]** Gate 4 PASS:
  Proof: .methodology/quality_manifest.json records Gate 4 PASS from P6.
  If NOT confirmed: verify Phase 6 Gate PASS is recorded in quality_manifest.json and confirm all intervening phases (P7–P6) completed their tasks.

### Pre-Phase Preflight

- **[PREFLIGHT]** Run phase hooks (FSM, Kill-Switch, Drift):
  ```bash
  python3 harness_cli.py run-phase --phase 7 --project .
  ```
  If FAILED: fix FSM/Drift issues. There is no gate bypass flag.
  Re-run `run-phase` after each fix. Max 3 attempts.
  After 3 FAIL: escalate to human — provide last `run-phase --phase 7` full output.
  Human fix → re-run `run-phase --phase 7 --project .` → PASS required before continuing.
  **Reliability lint fix** (P4+ blocking — if `preflight_reliability_lint` reports findings):
  Fix flagged patterns before continuing: `subprocess.run/Popen` without `timeout=`,
  `tempfile.mkstemp` outside try/finally, `os.path.exists` before open/unlink (TOCTOU),
  `time.sleep` inside async def. Re-run `run-phase` after each fix.
  **Config liveness fix** (P4+ blocking — if `preflight_config_liveness` reports orphans):
  Env keys read in code but absent from `.env.example`/`docker-compose*.yml`/`deployment/`.
  Add the key to the declaration source (or fix the typo). Re-run `run-phase` after each fix.
  **Attestation fix** (P5+ — if ASPICE Traceability preflight shows `attestation: missing` or `mismatch`):
  ```bash
  python3 harness_cli.py build-trace-attestation --project . --write
  git add .methodology/trace/attestation.json
  git commit -m 'trace: regenerate attestation'
  ```
  Re-run `run-phase` to confirm `Attestation: clean` before continuing.

- **[V2.9.1-B.1-HANDOFF]** Cross-deliverable dependency check (P6 → P7) — v2.9.1 B.1. **Must PASS** before any Phase 7 work begins:
  ```bash
  python3 harness_cli.py validate-handoff --from-phase 6 --project .
  ```
  > Verifies P6 deliverables are present and well-formed (e.g. P1 TEST_INVENTORY.yaml non-empty + covers all FRs; P2 TEST_SPEC.md has parseable named test cases; P3 all FRs have per-FR Gate 1 sentinels; P4 TEST_RESULTS.md non-trivial; P5 VERIFICATION_REPORT.md non-trivial; P6 06-quality/QUALITY_REPORT.md + RELEASE_NOTES.md + FINAL_SIGN_OFF.md + .methodology/quality_manifest.json gate_results.gate4.quality_complete=true; P7 07-risk/RISK_REGISTER.md + RISK_MITIGATION_PLANS.md + RISK_STATUS_REPORT.md).
  > If exit 1: read the error list, fix the upstream deliverable, re-run until exit 0. Do NOT proceed with Phase 7 work on a BLOCKED handoff.

- **[PREFLIGHT-CI]** Confirm CI wiring unchanged (should be set since P1):
  1. `.github/workflows/harness_quality_gate.yml` exists
  2. Git hooks installed (`ls .git/hooks/prepare-commit-msg`)
  3. harness importable (submodule, PYTHONPATH, or vendored `quality_gate/`)
  4. Phase 7 confirmed in `.methodology/state.json` (`advance-phase` already run)
  > If stale: run `python3 harness_cli.py init-project --phase 7 --project . --overwrite`

### Risk Categories
- Technical risks
- Schedule risks
- Resource risks
- External risks

### FR Risk Evaluation (2 total)

#### FR-01: Risk Assessment
- Review open issues from previous gates for FR-01
- Check `deferred_fixes.md` for FR-01 entries
- Confirm no new defects introduced

**Gate 1 Re-evaluation — FR-01** (carry-forward · sub-agent dispatch):
- **[ORCH-GATE1-DELTA]** Dispatch GATE1-DELTA evaluator sub-agent:
  ```bash
  python3 harness_cli.py run-fr-step --phase 7 --fr-id FR-01 \
    --step GATE1-DELTA --project .
  ```
  → Code-change detection: git diff FR-01 files since last Gate 1 PASS
  → No changes → skip (idempotent — safe to re-run)
  → Changes detected → full GATE1 re-evaluation (3 dims: linting/type_safety/test_coverage)
  → GitHub push: ✅ auto-done by run-fr-step
  → GATE1 FAIL: auto-dispatches CODE-FIX sub-agent → retries (max 3 rounds)
  → exit 2 = BLOCKED: human intervention required before continuing
  → Human fix → re-run `run-fr-step --step GATE1-DELTA --fr-id FR-01` → exit 0 required before continuing.

- **[ORCH-POST]** After GATE1-DELTA PASS — orchestrator runs directly:
  ```bash
  python3 harness_cli.py spec-coverage-check --project . --threshold 40.0 --fr-id FR-01
  python3 harness/scripts/generate_sab.py --project .
  # Note: if SAB.json exists, append --overwrite to regenerate
  ```

#### FR-02: Risk Assessment
- Review open issues from previous gates for FR-02
- Check `deferred_fixes.md` for FR-02 entries
- Confirm no new defects introduced

**Gate 1 Re-evaluation — FR-02** (carry-forward · sub-agent dispatch):
- **[ORCH-GATE1-DELTA]** Dispatch GATE1-DELTA evaluator sub-agent:
  ```bash
  python3 harness_cli.py run-fr-step --phase 7 --fr-id FR-02 \
    --step GATE1-DELTA --project .
  ```
  → Code-change detection: git diff FR-02 files since last Gate 1 PASS
  → No changes → skip (idempotent — safe to re-run)
  → Changes detected → full GATE1 re-evaluation (3 dims: linting/type_safety/test_coverage)
  → GitHub push: ✅ auto-done by run-fr-step
  → GATE1 FAIL: auto-dispatches CODE-FIX sub-agent → retries (max 3 rounds)
  → exit 2 = BLOCKED: human intervention required before continuing
  → Human fix → re-run `run-fr-step --step GATE1-DELTA --fr-id FR-02` → exit 0 required before continuing.

- **[ORCH-POST]** After GATE1-DELTA PASS — orchestrator runs directly:
  ```bash
  python3 harness_cli.py spec-coverage-check --project . --threshold 40.0 --fr-id FR-02
  python3 harness/scripts/generate_sab.py --project .
  # Note: if SAB.json exists, append --overwrite to regenerate
  ```

### P7 Milestone Push (10-Push Strategy ⑨)

- **PUSH ⑨ — P7 exit** (after risk register is complete):
  ```bash
  python3 harness_cli.py push-milestone --type p7 --project .
  ```
  > Writes HANDOVER.md + commits + pushes.

### Phase 7 Deliverables
- `07-risk/RISK_REGISTER.md` - Risk register
- `07-risk/RISK_MITIGATION_PLANS.md` - Mitigation plans
- `07-risk/RISK_STATUS_REPORT.md` - Risk status report
- [x] `.methodology/sessions_spawn.log` — auto-populated by AgentSpawner (non-blocking debug trail)
- Gate 1 PASS for every FR

### Phase 7 → Phase 8: Configuration Management

- Generate Phase 8 plan:
  ```bash
  python3 harness_cli.py plan-phase --phase 8 --project . \
    --output .methodology/phase8_plan.md
  ```
- **[PHASE-TRUTH]** Phase Truth ≥ 90% (HR-11) — verified by advance-phase
  > **FAIL** → check `phase_truth_verifier` output in `.sessi-work/`
  >   → identify which phase link or gate artifact failed
  >   → fix artifacts → re-run `advance-phase`
  >   → If 3 consecutive failures: escalate to human with `phase_truth_verifier` log

- **[TDD-PRECHECK]** Verify TDD checks pass — advance-phase enforces:
  - diagnostic script check: orphan diagnostic scripts (e.g. `_diag_xxx.py`) at repo root will BLOCK (exit 21)
  - secrets scanning: `gitleaks detect --source .` (exit 20) — whole-repo, runs before linting
  - linting: `ruff check .` (exit 18) — fix violations before advancing
  - type safety: `python3 -m mypy . --ignore-missing-imports` (exit 19)
    > Note: advance-phase uses mypy; Gate scoring uses pyright. Both must pass.
  - `pytest --tb=short -q --cov=03-development/src --cov-fail-under=100` (exit 9)
  - `python3 harness_cli.py spec-coverage-check --project . --threshold 90.0` (exit 10, D4 unified v2.6)
  > For genuinely untestable lines add: `# pragma: no cover` (requires justification comment).

- Advance FSM to Phase 8 (writes new HANDOVER.md + local commit):
  ```bash
  python3 harness_cli.py advance-phase --completed 7 --project .
  ```
  > **Note**: `advance-phase` will automatically check for harness submodule drift.
  > If it prints a warning that you are behind `origin/main`, it is non-blocking and for your information only.

  > **Auto-trigger on P7→P8 advance** (harness commits `4738542` + `51bd4a8`):
  > When `advance-phase --completed 7` runs, the framework automatically invokes
  > `scripts/phase8_doc_gen.py --project .` which deterministically generates
  > `08-config/CONFIG_RECORDS.md` + `08-config/RELEASE_CHECKLIST.md` from
  > `state.json` + `quality_manifest.json` + `git describe`. The generated files
  > are added to the advance-phase auto-commit so P8 starts from a real baseline.
  > If the generator fails, advance still returns 0 and prints an actionable
  > warning — re-run manually with `python3 harness/scripts/phase8_doc_gen.py --project .`.

- Confirm `HANDOVER.md` reflects Phase 8 entry (`P8-entry` checkpoint, correct plan path)
- Open `phase8_plan.md` and follow from the top.
- If session crashes during Phase 8: read `HANDOVER.md` or run `generate-next-plan`
