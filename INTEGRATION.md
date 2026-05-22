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
python scripts/generate_sab.py --project .
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
- Current phase (1-8) -> stored in `.methodology/state.json` (`current_phase` field)
- Enable block on failure? (y/n)

Installed hooks:

| Hook | Trigger | Behavior |
|---|---|---|
| `prepare-commit-msg` | `git commit` | **Blocks** if `harness_cli.py pre-commit-check --phase $PHASE` fails |
| `post-merge` | `git merge` | Warns only — runs `pre-commit-check --phase $PHASE` (non-blocking) |
| `pre-push` | `git push` | **Blocks** — runs full `run-phase --phase $PHASE` (no bypass) |

**Phase management**:
```bash
# Advance phase (updates state.json):
python harness_cli.py advance-phase --completed 2 --project .
# Check current phase:
python3 -c "import json; print(json.load(open('.methodology/state.json'))['current_phase'])"
```

> **No bypass mechanism exists for git hooks.** If `run-phase` fails before push, fix the underlying issue. Use `git commit --no-verify` only as a last resort for emergency hotfixes; CI will detect the missing sentinel on the next push audit.

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
# Manual step-by-step (recommended — run-pipeline removed in v2.5)
python harness_cli.py plan-phase --phase 1 --project .
python harness_cli.py run-phase --phase 1 --project .
python harness_cli.py plan-phase --phase 2 --project .
python harness_cli.py run-phase --phase 2 --project .
python harness_cli.py plan-phase --phase 3 --project .
python harness_cli.py run-phase --phase 3 --project .
python harness_cli.py finalize-gate --gate 2 --phase 3 --project .
# Continue for P4–P8 with same pattern
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

> **Gate 4 is a phase-exit gate.** Run Gate 4 manually at P6 exit: `python harness_cli.py run-gate --gate 4 --phase 6 --project .` followed by `python harness_cli.py finalize-gate --gate 4 --phase 6 --project .`.

> **Branch protection** — configure in GitHub repo Settings → Branches → Add rule (branch: `main`):
> - ✅ Block force pushes
> - ✅ Block deletions
> - ❌ Do NOT enable "Require a pull request" — harness uses Agent B review + direct push, incompatible with PR gating
> - ❌ Do NOT enable "Require status checks" — only gates PR merges, not direct pushes

### Option A — Submodule (recommended)

The canonical workflow is at `templates/harness_quality_gate.yml` — `init-project` deploys it directly.
The template contains these jobs (all trigger on `push: branches: [main]`):

| Job | Purpose |
|-----|---------|
| `gate-check` | Phase preflight + gate score variance + FR traceability |
| `push-milestone-enforcement` | Blocks raw `git push` for P3+ (requires `push-milestone`) |
| `p1p2-enforcement` | Blocks raw `git push` for P1/P2 (requires `push-checkpoint`) |
| `agent-b-approval-check` | Verifies Agent B APPROVE files for P3+ |
| `p8-archive-check` | Validates `.methodology-archive/` + no Phase 9 refs |

> See `templates/harness_quality_gate.yml` for the full YAML. The table above is a structural reference only — the template is the single source of truth.

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

> **Option B differences from template**: Replace `harness/harness_cli.py` → `python /opt/harness/harness_cli.py` and `harness/requirements.txt` → `/opt/harness/requirements.txt` in all jobs. `push-milestone-enforcement` and `p8-archive-check` are unchanged — they only access `.methodology/` state files.

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

> **Option C differences from template**: Replace `harness/harness_cli.py` → `python harness_cli.py` and `harness/scripts/` → `scripts/` in all jobs. `push-milestone-enforcement` and `p8-archive-check` are unchanged — they only access `.methodology/` state files.

Phase is auto-detected from `.methodology/state.json` — no GitHub Variable required. `CURRENT_PHASE` Actions variable is no longer used.

---

## 5. Environment Variables Reference

| Variable | Used by | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | SSI runner, agent_spawner | — | **Required** — Claude API key for all LLM-based gate evaluation (Gates 1–4) |
| `HERMES_REVIEWER_TARGET` | `reviewer_router.py` | — | Hermes reviewer target (e.g. `telegram:6308981865`). Used for Agent B A/B collaboration (`reviewer_router.py`) — active from P1, fallback chain Hermes→Gemini→Claude sub-agent if unset. |
| `HERMES_TIMEOUT_MS` | `reviewer_router.py` | `120000` | Hermes long-poll timeout in ms (default: 2 min) |
| `DRIFT_PROJECT_PATH` | `cron_drift_monitor.py` | cwd | Path to target project for drift analysis |
| `PYTHONPATH` | All scripts | — | Must include harness-methodology root if not using submodule |
| `SSI_ROOT` | All scripts | `harness/ssi` | Path to embedded SSI package (auto-detected from harness_cli.py location) |

> **Note**: `HERMES_REVIEWER_TARGET` requires the `mcp_tools` package to be importable at runtime. `harness/reviewer_router.py` degrades gracefully if MCP is unavailable (falls back to Gemini→Claude sub-agent for A/B reviews). Email/Slack notification channels (`drift_notifier`) are planned but not yet implemented.

---

## 5.1 `enforcement.json` Configuration Keys

`.methodology/enforcement.json` (in the **target project**) supports the following keys in addition to the standard gate thresholds:

| Key path | Type | Default | Purpose |
|---|---|---|---|
| `phase_truth.pytest_timeout_seconds` | `int` | `300` | Maximum seconds for pytest subprocess in `PhaseTruthVerifier.check_pytest()` (SG-5). Raise for large test suites; floor is 30s. |
| `hr_overrides.HR-11_phase_truth_threshold` | `int` | `90` | Override the Phase Truth gate threshold (%) for this project (SG-7). Only lower if the project has a documented waiver. |

**Example** (`.methodology/enforcement.json`):
```json
{
  "phase_truth": {
    "pytest_timeout_seconds": 600
  },
  "hr_overrides": {
    "HR-11_phase_truth_threshold": 85
  }
}
```

---

## 5.2 Migration Notes

### CV-1 — `sessions_spawn.log` canonical path (harness v2.5+)

The canonical location of the A/B session log changed from `sessions_spawn.log` (project root) to `.methodology/sessions_spawn.log`.

**If your project has an existing `sessions_spawn.log` at the root**:
```bash
mkdir -p .methodology
mv sessions_spawn.log .methodology/sessions_spawn.log
git add .methodology/sessions_spawn.log sessions_spawn.log
git commit -m "chore: migrate sessions_spawn.log to .methodology/ (CV-1)"
```

`SessionsSpawnLogger` and `PhaseTruthVerifier` now read and write exclusively from `.methodology/sessions_spawn.log`. The root-level file is no longer consulted.

### SG-11 — `session_id` required in sessions_spawn.log (harness v2.5+)

`sessions_spawn.log` entries without a `session_id` field are now counted as malformed. If your project has entries that lack `session_id`, re-run the A/B dispatch for those FRs, or manually add synthetic IDs:

```python
# One-off repair script:
import json
from pathlib import Path

log = Path(".methodology/sessions_spawn.log")
lines = log.read_text().splitlines()
repaired = []
for line in lines:
    if not line.strip():
        continue
    d = json.loads(line)
    if not d.get("session_id"):
        d["session_id"] = f"legacy-{d.get('role','?')}-{len(repaired)}"
    repaired.append(json.dumps(d))
log.write_text("\n".join(repaired) + "\n")
```

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

# 6. Advance phase — updates .methodology/state.json (single source of truth)
python harness_cli.py advance-phase --completed <current>

# 7. Generate plan for next phase
python harness_cli.py plan-phase --phase <next>
```

---

*See [SAD.md](SAD.md) for full module architecture. See [SKILL.md](SKILL.md) for HR rules and gate thresholds.*
