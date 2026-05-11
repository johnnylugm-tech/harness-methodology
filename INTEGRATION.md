# Integration Guide — harness-methodology

> **Scope**: How to maintain the framework itself, and how to wire it into a target development project.
> For gate embedding and SSI evaluation model, see [`docs/HARNESS_INTEGRATION.md`](docs/HARNESS_INTEGRATION.md).
>
> **Last verified**: 2026-05-06 &nbsp;|&nbsp; **Synced with**: SAD.md v2.3

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
cp harness-methodology/harness_cli.py your-project/
```
Simplest for single-developer setups; requires manual updates when framework changes.

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
| `prepare-commit-msg` | `git commit` | **Blocks** if `harness_cli.py run-gate --phase $PHASE` fails |
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

Add this to your project's `.github/workflows/harness_quality_gate.yml` (or run `harness_cli.py init-project` to auto-generate):

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
          python harness/harness_cli.py run-gate --phase $PHASE

      - name: FR Traceability Check
        run: python harness/scripts/check_fr_full.py --phase ${{ vars.CURRENT_PHASE || '3' }}
        continue-on-error: true   # advisory only until FR coverage is complete
```

Set `vars.CURRENT_PHASE` in GitHub repo -> Settings -> Variables.

---

## 5. Environment Variables Reference

| Variable | Used by | Default | Purpose |
|---|---|---|---|
| `DRIFT_PROJECT_PATH` | `cron_drift_monitor.py` | cwd | Path to target project for drift analysis |
| `PYTHONPATH` | All scripts | — | Must include harness-methodology root if not using submodule |
| `ANTHROPIC_API_KEY` | SSI runner, agent_spawner | — | Required for LLM-based gate evaluation |

> **Note**: `HERMES_REVIEWER_TARGET` and `HERMES_TIMEOUT_MS` are reserved for future Hermes bridge integration (`harness_bridge.py`, `reviewer_router.py` — not yet implemented).

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

# 6. Update local git config
git config quality.phase <next>

# 7. Generate plan for next phase
python harness_cli.py plan-phase --phase <next>
```

---

*See [SAD.md](SAD.md) for full module architecture. See [SKILL.md](SKILL.md) for HR rules and gate thresholds.*
