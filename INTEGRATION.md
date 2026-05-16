# Integration Guide — harness-methodology

> **Scope**: How to maintain the framework itself, and how to wire it into a target development project.
> For gate embedding and SSI evaluation model, see [`docs/HARNESS_INTEGRATION.md`](docs/HARNESS_INTEGRATION.md).
>
> **Last verified**: 2026-05-12 &nbsp;|&nbsp; **Synced with**: SAD.md v2.4

---

## 1. Two-Context Model

```
harness-methodology (this repo)          Your Target Project (any repo)
──────────────────────────────────       ─────────────────────────────────
Framework source + CI self-tests         Your code + harness installed as dep
.github/workflows/harness_ci.yml  ←→    .github/workflows/harness_quality_gate.yml
scripts/ (tools to run elsewhere)  →     .git/hooks/ (installed via setup.sh)
                                    →    scripts/cron_drift_monitor.py (pointed at project)
```

**Rule**: Never mix the two. harness-methodology's CI tests the framework. Your project's CI runs the framework against your code.

---

## 2. Context A — Framework Self-Maintenance (this repo)

### 2.1 GitHub Actions: `.github/workflows/harness_ci.yml`

Triggers on push/PR to `main`.

| Job | What it does |
|---|---|
| `framework-self-tests` | `ruff check .` → `pytest tests/ -v --tb=short` |

Both steps are blocking — lint or test failure blocks the PR.

### 2.2 Release Scripts

```bash
# Run pre-release validation
python scripts/list-modules.py --validate
python scripts/validate_cross_refs.py

# Run full test suite
python -m pytest tests/ -v --tb=short

# Tag and push (CI takes over — see .github/workflows/release.yml)
git tag v2.4.0
git push origin v2.4.0

# Regenerate machine-readable SAD block
python scripts/generate_sab.py --sad SAD.md --output .methodology/sab.json
```

---

## 3. Context B — Target Project Integration

### 3.1 Step 1: Make harness Importable in Target Repo

The git hooks and CI workflow call `harness_cli.py` — the `core/` and `harness/` packages must be importable. Three options:

**Option A — Git submodule (recommended for teams)**
```bash
cd your-project
git submodule add https://github.com/johnnylugm-tech/harness-methodology harness
pip install -r harness/requirements.txt
```
Entry point: `harness/harness_cli.py`. Python path: `harness/` root on `sys.path`.

> **`requirements.txt` contents** (repo-bundled, not generated): `pyyaml>=6.0` (gate config loading — required), `pytest>=7.0` (framework self-tests). The `anthropic` SDK is **not** included — it is installed as part of `software_self_improvement`. If `requirements.txt` is missing (e.g. Option C copy forgot to include it), `pip install -r ... || true` in CI will silently succeed, and `run-gate` will later fail with `ModuleNotFoundError: No module named 'yaml'`.

**Option B — Direct clone alongside project**
```bash
git clone https://github.com/johnnylugm-tech/harness-methodology /opt/harness
export PYTHONPATH="/opt/harness:$PYTHONPATH"
```
Entry point: `/opt/harness/harness_cli.py`.

**Option C — Copy `core/` and `harness/` into project**
```bash
cp -r harness-methodology/core your-project/core
cp -r harness-methodology/harness your-project/harness
cp -r harness-methodology/scripts your-project/scripts
cp harness-methodology/harness_cli.py your-project/
```
Simplest for single-developer setups; requires manual updates when framework changes.

> **Why copy `scripts/`**: CI Option C YAML runs `python scripts/check_fr_full.py` for FR traceability checks. `check_fr_full.py` lives in the repo's root `scripts/` directory — it is **not** inside `harness/`. Without copying `scripts/`, the CI step silently skips (masked by `continue-on-error: true`) and FR traceability is never verified.

### 3.2 Step 2: Install Git Hooks

Run from the **target project** root (not from harness-methodology):

```bash
bash /path/to/harness-methodology/scripts/setup-git-hooks.sh
```

Interactive prompts:
- Current phase (1-8) -> stored in `git config quality.phase`
- Enable block on failure? (y/n)

Installed hooks:

| Hook | Trigger | Behavior |
|---|---|---|
| `prepare-commit-msg` | `git commit` | **Blocks** if `harness_cli.py pre-commit-check --phase $PHASE` fails |
| `post-merge` | `git merge` | Warns only — runs `pre-commit-check --phase $PHASE` (non-blocking) |
| `pre-push` | `git push` | **Blocks** — runs full `run-phase --phase $PHASE` (no bypass) |

**Phase management**:
```bash
git config quality.phase 3        # Move to Phase 3
git config quality.phase          # Check current phase
```

> **No bypass mechanism exists for git hooks.** If `run-phase` fails before push, fix the underlying issue. Use `git commit --no-verify` only as a last resort for emergency hotfixes; CI will still block the PR.

### 3.3 Step 3 (Optional): Drift Monitor — Continuous Architecture Watch

The drift monitor detects structural divergence between code and spec artifacts hourly.

**Manual test first**:
```bash
DRIFT_PROJECT_PATH=/path/to/your-project \
  python /path/to/harness-methodology/scripts/cron_drift_monitor.py
```

**Install crontab**:
```bash
# Edit crontab
crontab -e

# Add (replace paths):
0 * * * * DRIFT_PROJECT_PATH=/your/project \
  /your/venv/bin/python \
  /path/to/harness/scripts/cron_drift_monitor.py \
  >> /your/project/logs/drift_monitor.log 2>&1
```

> **Note**: Email/Slack notification channels (`drift_notifier`, `EmailChannel`, `SlackChannel`) are planned but not yet implemented. Currently log-only.

### 3.4 Step 4 (Optional): On-Demand Analysis Scripts

Run from target project root with harness on `PYTHONPATH` (or via submodule).

| Task | CLI command (preferred) | Direct script |
|---|---|---|
| Phase exit audit | `python harness_cli.py audit-phase --phase 3 --repo owner/repo` | `python harness/scripts/phase_auditor.py --phase 3` |
| FR completeness check | — | `python harness/scripts/check_fr_full.py --phase 3` |
| FR quality check | — | `python harness/scripts/check_fr_quality.py` |
| FR -> code trace matrix | — | `python harness/scripts/generate_fr_mapping.py` |
| Generate phase plan | `python harness_cli.py plan-phase --phase 3` | `python harness/scripts/generate_full_plan.py --phase 3` |
| Spec compliance (ASPICE) | `python harness_cli.py verify-spec` | `python harness/scripts/verify_spec_compliance.py` |
| Logic correctness check | `python harness_cli.py check-logic --srs SRS.md` | `python harness/scripts/spec_logic_checker.py` |
| M3 gap analysis | `python harness_cli.py run-gap-analysis` | — |
| Path consistency | — | `python harness/scripts/verify_path_consistency.py` |
| State inspection | `python harness_cli.py status` | `python harness/scripts/state_monitor.py` |
| Dev log check | — | `python harness/scripts/dev_log_checker.py` |

**Full pipeline execution**:
```bash
python harness_cli.py run-pipeline --phase-from 1 --phase-to 8 --project .
```

**One-shot project initialization** (automates Steps 1-4 above):
```bash
python harness_cli.py init-project --project /path/to/target --phase 3
```

---

## 4. Target Project CI (Recommended GitHub Actions)

> **The YAML below targets Option A (submodule).** For Option B (global clone) and Option C (copy), see the variant blocks at the end of this section. Running `harness_cli.py init-project` auto-generates the correct YAML for your install option.
>
> **CI scope — structural enforcement only**: CI runs `run-phase` (FSM / constitution / drift / traceability) and gate score variance check. Gate score evaluation (LLM-based, `run-gate` → Claude → `finalize-gate`) requires an interactive Claude session and is **always local**.
>
> **Single source of truth**: The YAML below is kept in sync with `templates/harness_quality_gate.yml` and `_harness_workflow_template()` in `harness_cli.py` (used by `init-project`).

> **Gate 4 is a local-only gate.** It requires a human Hermes APPROVE within a 2-minute window — CI runners are headless and will always time out. The workflow auto-skips the preflight step when `.methodology/state.json` reports phase 6. Run Gate 4 manually at P6 exit: `python harness_cli.py run-gate --gate 4 --phase 6 --project .`

> **Branch protection required** — configure in GitHub repo Settings → Branches → Add rule (branch: `main`):
> - ✅ Require a pull request before merging
> - ✅ Require status checks to pass → Required check: `gate-check`
> - ✅ **Do not allow bypassing the above settings** (includes admins)
> - ✅ Block force pushes

### Option A — Submodule (recommended)

```yaml
name: Harness Quality Gate

on:
  push:
    branches: ['**']        # All branch pushes — catches worktree branches before merge
  pull_request:
    branches: [main]

jobs:
  gate-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: true

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install harness dependencies
        run: |
          pip install -r harness/requirements.txt || true
          pip install pyyaml 2>/dev/null || true

      - name: Auto-detect phase from state.json
        id: phase
        run: |
          PHASE=$(python3 -c "
          import json, sys
          try:
              d = json.load(open('.methodology/state.json'))
              print(d.get('current_phase', d.get('phase', 1)))
          except Exception:
              print(1)
          " 2>/dev/null || echo "1")
          echo "PHASE=$PHASE" >> $GITHUB_OUTPUT
          echo "Detected phase: $PHASE"

      - name: Run Phase Preflight (FSM / drift / constitution)
        if: steps.phase.outputs.PHASE != '6'
        env:
          PHASE: ${{ steps.phase.outputs.PHASE }}
        run: python harness/harness_cli.py run-phase --phase $PHASE --project .

      - name: Gate Score Variance Check
        env:
          PHASE: ${{ steps.phase.outputs.PHASE }}
        run: |
          python3 - <<'EOF'
          import glob, sys, os
          try:
              import yaml
          except ImportError:
              print("pyyaml not available — skipping"); sys.exit(0)
          phase = int(os.environ.get("PHASE", "1"))
          logs = glob.glob(f".methodology/decision_logs/**/GATE_{phase}_*.yaml", recursive=True)
          scores = []
          for lf in logs:
              try:
                  d = yaml.safe_load(open(lf))
                  s = (d or {}).get("scores", {}).get("gate_score")
                  if s is not None: scores.append(float(s))
              except Exception: pass
          if len(scores) > 2 and len(set(scores)) == 1:
              print(f"FAIL: All {len(scores)} gate scores identical ({scores[0]}) — fabrication detected")
              sys.exit(1)
          print(f"OK: Gate score variance passed ({len(scores)} entries)")
          EOF

      - name: FR Traceability Check
        env:
          PHASE: ${{ steps.phase.outputs.PHASE }}
        run: python harness/scripts/check_fr_full.py --phase $PHASE
        continue-on-error: true

  push-milestone-enforcement:
    name: Enforce push-milestone (no --no-verify bypass)
    runs-on: ubuntu-latest
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
      - name: Check last_milestone_command in state.json
        run: |
          STATE_FILE=".methodology/state.json"
          if [ ! -f "$STATE_FILE" ]; then
            echo "INFO: No state.json — project not yet initialized, skipping."
            exit 0
          fi
          PHASE=$(python3 -c "import json; d=json.load(open('$STATE_FILE')); print(d.get('current_phase', 0))")
          if [ "$PHASE" -lt 3 ]; then
            echo "INFO: Phase $PHASE < 3 — push-milestone not yet required."
            exit 0
          fi
          CMD=$(python3 -c "import json; d=json.load(open('$STATE_FILE')); print(d.get('last_milestone_command', ''))")
          if [ -z "$CMD" ]; then
            echo "ERROR: state.json has no last_milestone_command field."
            echo "Direct git push detected. Use instead:"
            echo "  python harness/harness_cli.py push-milestone --type <type> --project ."
            exit 1
          fi
          echo "OK: last_milestone_command = $CMD"

  agent-b-approval-check:
    name: Agent B Approval Verification
    runs-on: ubuntu-latest
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: true
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install harness dependencies
        run: pip install -r harness/requirements.txt || true
      - name: Detect phase
        id: phase
        run: |
          PHASE=$(python3 -c "
          import json
          try:
              d = json.load(open('.methodology/state.json'))
              print(d.get('current_phase', 0))
          except Exception:
              print(0)
          " 2>/dev/null || echo "0")
          echo "PHASE=$PHASE" >> $GITHUB_OUTPUT
      - name: Verify Agent B approvals (P3+)
        if: steps.phase.outputs.PHASE >= 3
        run: |
          python harness/harness_cli.py verify-agent-b-approvals \
            --phase ${{ steps.phase.outputs.PHASE }} \
            --project . || {
              echo ""
              echo "Agent B review files missing or non-APPROVE."
              echo "Each FR needs .methodology/agent_b_approvals/FR-XX.json"
              echo "with review_status=APPROVE and docs_embedded=[SRS.md, SAD.md]"
              exit 1
            }

  p8-archive-check:
    name: P8 Archive & HANDOVER Validation
    runs-on: ubuntu-latest
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
      - name: Detect if P8 milestone was pushed
        id: p8
        run: |
          STATE_FILE=".methodology/state.json"
          IS_P8="false"
          if [ -f "$STATE_FILE" ]; then
            CMD=$(python3 -c "import json; d=json.load(open('$STATE_FILE')); print(d.get('last_milestone_command',''))")
            if echo "$CMD" | grep -q "p8"; then IS_P8="true"; fi
          fi
          echo "IS_P8=$IS_P8" >> $GITHUB_OUTPUT
      - name: Validate .methodology-archive exists (P8 only)
        if: steps.p8.outputs.IS_P8 == 'true'
        run: |
          if [ ! -d ".methodology-archive" ]; then
            echo "ERROR: .methodology-archive/ does not exist. Archive phase artifacts before P8 push."
            exit 1
          fi
          echo "OK: .methodology-archive/ exists."
      - name: Validate HANDOVER.md has no Phase 9 references (P8 only)
        if: steps.p8.outputs.IS_P8 == 'true'
        run: |
          if [ -f "HANDOVER.md" ]; then
            if grep -qi "phase 9\|phase9\|phase9_plan" HANDOVER.md; then
              echo "ERROR: HANDOVER.md references non-existent Phase 9."
              grep -ni "phase 9\|phase9" HANDOVER.md || true
              exit 1
            fi
            echo "OK: HANDOVER.md has no Phase 9 references."
          fi
```

### Option B — Global clone

```yaml
      - name: Install harness
        run: |
          git clone --depth 1 https://github.com/johnnylugm-tech/harness-methodology /opt/harness
          pip install -r /opt/harness/requirements.txt || true
          pip install pyyaml 2>/dev/null || true

      - name: Auto-detect phase from state.json
        id: phase
        run: |
          PHASE=$(python3 -c "
          import json, sys
          try:
              d = json.load(open('.methodology/state.json'))
              print(d.get('current_phase', d.get('phase', 1)))
          except Exception:
              print(1)
          " 2>/dev/null || echo "1")
          echo "PHASE=$PHASE" >> $GITHUB_OUTPUT

      - name: Run Quality Gate (current phase)
        if: steps.phase.outputs.PHASE != '6'
        env:
          PHASE: ${{ steps.phase.outputs.PHASE }}
          PYTHONPATH: /opt/harness
        run: python /opt/harness/harness_cli.py run-phase --phase $PHASE --project .

      - name: FR Traceability Check
        env:
          PYTHONPATH: /opt/harness
          PHASE: ${{ steps.phase.outputs.PHASE }}
        run: python /opt/harness/scripts/check_fr_full.py --phase $PHASE
        continue-on-error: true
```

> **Enforcement jobs for Option B**: Same 3 jobs as Option A but replace `harness/harness_cli.py` with `python /opt/harness/harness_cli.py` in `agent-b-approval-check`. `push-milestone-enforcement` and `p8-archive-check` are identical — they only access `.methodology/` state files.

### Option C — Copy into project

```yaml
      # harness_cli.py and harness/ are already in repo root — no extra install step

      - name: Auto-detect phase from state.json
        id: phase
        run: |
          PHASE=$(python3 -c "
          import json, sys
          try:
              d = json.load(open('.methodology/state.json'))
              print(d.get('current_phase', d.get('phase', 1)))
          except Exception:
              print(1)
          " 2>/dev/null || echo "1")
          echo "PHASE=$PHASE" >> $GITHUB_OUTPUT

      - name: Run Quality Gate (current phase)
        if: steps.phase.outputs.PHASE != '6'
        env:
          PHASE: ${{ steps.phase.outputs.PHASE }}
        run: python harness_cli.py run-phase --phase $PHASE --project .

      - name: FR Traceability Check
        env:
          PHASE: ${{ steps.phase.outputs.PHASE }}
        run: python scripts/check_fr_full.py --phase $PHASE
        continue-on-error: true
```

> **Enforcement jobs for Option C**: Same 3 jobs as Option A but replace `harness/harness_cli.py` with `python harness_cli.py` (project root) in `agent-b-approval-check`. `push-milestone-enforcement` and `p8-archive-check` are identical to Option A.

Phase is auto-detected from `.methodology/state.json` — no GitHub Variable required. `CURRENT_PHASE` Actions variable is no longer used.

---

## 5. Environment Variables Reference

| Variable | Used by | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | SSI runner, agent_spawner | — | **Required** — Claude API key for all LLM-based gate evaluation (Gates 1–4) |
| `HERMES_REVIEWER_TARGET` | `harness_bridge.py`, `reviewer_router.py` | — | Hermes reviewer target (e.g. `telegram:6308981865`). **Two uses**: (1) Agent B A/B collaboration (`reviewer_router.py`) — from P1, fallback chain Hermes→Gemini→Claude sub-agent if unset; (2) Gate 4 human APPROVE (`harness_bridge.py`) — P6 exit only, strictly required, no fallback. Set from project start. |
| `HERMES_TIMEOUT_MS` | `harness_bridge.py`, `reviewer_router.py` | `120000` | Hermes long-poll timeout in ms (default: 2 min) |
| `DRIFT_PROJECT_PATH` | `cron_drift_monitor.py` | cwd | Path to target project for drift analysis |
| `PYTHONPATH` | All scripts | — | Must include harness-methodology root if not using submodule |
| `SSI_ROOT` | All scripts | `harness/ssi` | Path to embedded SSI package (auto-detected from harness_cli.py location) |

> **Note**: `HERMES_REVIEWER_TARGET` requires the `mcp_tools` package to be importable at runtime. `harness/reviewer_router.py` degrades gracefully if MCP is unavailable (falls back to Gemini→Claude sub-agent for A/B reviews). Gate 4 (`harness_bridge.py`) will block rather than crash if the target is unreachable. Email/Slack notification channels (`drift_notifier`) are planned but not yet implemented.

---

## 6. Phase Transition Checklist

When moving to the next phase in a target project:

```bash
# 1. Run preflight checks (includes CI readiness)
python harness_cli.py run-phase --phase <current> --project .

# 2. Verify current phase gate passes
python harness_cli.py run-gate --gate <N> --phase <current>

# 3. (P3+) Run M3 gap analysis
python harness_cli.py run-gap-analysis --project .

# 4. (Optional) Run full 8-dimension audit
python harness_cli.py audit-phase --phase <current> --repo owner/repo

# 5. (Optional) Verify spec compliance + logic
python harness_cli.py verify-spec --project .
python harness_cli.py check-logic --project .

# 6. Advance phase — updates quality.phase + GitHub CURRENT_PHASE atomically
python harness_cli.py advance-phase --completed <current>
# advance-phase does: git config quality.phase, gh variable set CURRENT_PHASE,
# and .methodology/state.json — all in one call.
# If gh CLI is unavailable, it prints the manual fallback command.

# 7. Generate plan for next phase
python harness_cli.py plan-phase --phase <next>
```

---

*See [SAD.md](SAD.md) for full module architecture. See [SKILL.md](SKILL.md) for HR rules and gate thresholds.*
