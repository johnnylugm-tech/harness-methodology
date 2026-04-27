# Integration Guide — harness-methodology

> **Scope**: How to maintain the framework itself, and how to wire it into a target development project.

---

## 1. Two-Context Model

```
harness-methodology (this repo)          Your Target Project (any repo)
──────────────────────────────────       ─────────────────────────────────
Framework source + CI self-tests         Your code + harness installed as dep
.github/workflows/harness_ci.yml  ←→    .github/workflows/your_ci.yml
scripts/ (tools to run elsewhere)  →     .git/hooks/ (installed via setup.sh)
                                    →    scripts/cron_drift_monitor.py (pointed at project)
```

**Rule**: Never mix the two. harness-methodology's CI tests the framework. Your project's CI runs the framework against your code.

---

## 2. Context A — Framework Self-Maintenance (this repo)

### 2.1 GitHub Actions: `.github/workflows/harness_ci.yml`

Triggers on push/PR to `main`.

| Job | What it does | Pass threshold |
|---|---|---|
| `mutation-testing-median3` | Runs `mutmut` 3×, takes median mutation score | ≥ 70 |
| `gate-unit-tests` | `pytest tests/` — framework unit tests | non-blocking (`\|\| true`) |

> ⚠️ `gate-unit-tests` is currently non-blocking. Promote to blocking once test coverage is stable.

### 2.2 Release Scripts

```bash
# Bump version in relevant files
python scripts/bump_version.py --part minor

# Create GitHub release with notes
bash scripts/create_release.sh v2.1

# Regenerate machine-readable SAD block
python scripts/generate_sab.py --sad SAD.md --output .methodology/sab.json
```

---

## 3. Context B — Target Project Integration

### 3.1 Step 1: Make `quality_gate/` Available in Target Repo

The git hooks and analysis scripts call `quality_gate.cli` — this module must be importable from the target project root. Three options:

**Option A — Git submodule (recommended for teams)**
```bash
cd your-project
git submodule add https://github.com/johnnylugm-tech/harness-methodology harness
pip install -r harness/requirements.txt
```
Hooks will resolve `quality_gate` via `harness/quality_gate/`.

**Option B — Direct clone alongside project**
```bash
git clone https://github.com/johnnylugm-tech/harness-methodology /opt/harness
export PYTHONPATH="/opt/harness:$PYTHONPATH"
```

**Option C — Copy `quality_gate/` into project**
```bash
cp -r harness-methodology/quality_gate your-project/quality_gate
```
Simplest for single-developer setups; requires manual updates.

### 3.2 Step 2: Install Git Hooks

Run from the **target project** root (not from harness-methodology):

```bash
bash /path/to/harness-methodology/scripts/setup-git-hooks.sh
```

Interactive prompts:
- Current phase (1–8) → stored in `git config quality.phase`
- Enable block on failure? (y/n)

Installed hooks:

| Hook | Trigger | Behavior |
|---|---|---|
| `prepare-commit-msg` | `git commit` | **Blocks** if `quality check-phase $PHASE --block` fails |
| `post-merge` | `git merge` | Warns only (non-blocking) |
| `pre-push` | `git push` | **Blocks** unless last commit message contains `STAGE_PASS` |

**Phase management**:
```bash
git config quality.phase 3        # Move to Phase 3
git config quality.phase          # Check current phase
```

**Emergency bypass** (use sparingly):
```bash
git commit -m "fix: hotfix [STAGE_PASS] emergency"   # pre-push skips check
git commit --no-verify                                # skips all hooks (audit trail broken)
```

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

**Notification channels** (configure in `cron_drift_monitor.py` or env):
```python
# Log only (default)
DriftMonitor(project_path=..., feedback_store=store)

# Email alerts
from quality_gate.drift_notifier import DriftNotifier, EmailChannel
notifier = DriftNotifier(channels=[EmailChannel(smtp_host=..., from_addr=..., to_addrs=[...])])

# Slack
from quality_gate.drift_notifier import DriftNotifier, SlackChannel
notifier = DriftNotifier(channels=[SlackChannel(webhook_url="https://hooks.slack.com/...")])
```

### 3.4 Step 4 (Optional): On-Demand Analysis Scripts

Run these from your target project root with `PYTHONPATH` pointing to harness:

| Script | When to run | Command |
|---|---|---|
| `check_fr_full.py` | Phase exit audit | `python harness/scripts/check_fr_full.py --phase 3` |
| `check_fr_quality.py` | FR completeness check | `python harness/scripts/check_fr_quality.py` |
| `generate_fr_mapping.py` | Build FR→code trace matrix | `python harness/scripts/generate_fr_mapping.py` |
| `generate_full_plan.py` | Generate phase plan doc | `python harness/scripts/generate_full_plan.py --phase 3` |
| `phase_auditor.py` | Deep phase completeness audit | `python harness/scripts/phase_auditor.py --phase 3` |
| `spec_logic_checker.py` | Validate spec consistency | `python harness/scripts/spec_logic_checker.py` |
| `verify_spec_compliance.py` | ASPICE compliance check | `python harness/scripts/verify_spec_compliance.py` |
| `verify_path_consistency.py` | Check all file references valid | `python harness/scripts/verify_path_consistency.py` |
| `state_monitor.py` | Inspect `.methodology/state.json` | `python harness/scripts/state_monitor.py` |
| `dev_log_checker.py` | Validate dev log entries | `python harness/scripts/dev_log_checker.py` |

---

## 4. Target Project CI (Recommended GitHub Actions)

Add this to your project's `.github/workflows/harness_gate.yml`:

```yaml
name: Harness Quality Gate

on:
  pull_request:
    branches: [main]

jobs:
  gate-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: true   # if using submodule option

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install harness dependencies
        run: |
          pip install pyyaml
          pip install -r harness/requirements.txt || true

      - name: Run Quality Gate (current phase)
        env:
          PHASE: ${{ vars.CURRENT_PHASE || '3' }}
        run: |
          python -m quality_gate.cli quality check-phase $PHASE --block

      - name: FR Traceability Check
        run: python harness/scripts/check_fr_full.py --phase ${{ vars.CURRENT_PHASE || '3' }}
        continue-on-error: true   # advisory only until FR coverage is complete
```

Set `vars.CURRENT_PHASE` in GitHub repo → Settings → Variables.

---

## 5. Environment Variables Reference

| Variable | Used by | Default | Purpose |
|---|---|---|---|
| `HERMES_REVIEWER_TARGET` | `harness_bridge.py`, `reviewer_router.py` | — | External reviewer target (e.g. `telegram:CHAT_ID`) |
| `HERMES_TIMEOUT_MS` | `reviewer_router.py` | `120000` | Global Hermes call timeout (ms) |
| `DRIFT_PROJECT_PATH` | `cron_drift_monitor.py` | cwd | Path to target project for drift analysis |
| `PYTHONPATH` | All scripts | — | Must include harness-methodology root if not submodule |
| `ANTHROPIC_API_KEY` | SSI runner, agent_spawner | — | Required for LLM-based gate evaluation |

---

## 6. Phase Transition Checklist

When moving to next phase in target project:

```bash
# 1. Verify current phase gate passes
python harness_cli.py run-gate --gate <N> --phase <current>

# 2. Update local git config
git config quality.phase <next>

# 3. Generate plan for next phase
python harness_cli.py plan-phase --phase <next>

# 4. (Optional) Run full audit
python harness/scripts/phase_auditor.py --phase <current>
```

---

*See [SAD.md](SAD.md) for full module architecture. See [SKILL.md](SKILL.md) for HR rules and gate thresholds.*
