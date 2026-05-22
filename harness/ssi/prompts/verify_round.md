# Verify Round Protocol

Cross-check all dimension scores after improvements. Detect regressions and cap unsupported claims.

---

## Step 1: Run Deterministic Verification

Reads per-dimension scores from `round_<n>/scores/*.json` and writes
`round_<n>/verified.json` with capped/regression-adjusted result.

```bash
python3 scripts/verify.py .sessi-work/round_<n> <repo_path>
```

Read the output (stdout + `.sessi-work/round_<n>/verified.json`):
- `verification.capped[]` — dimensions where claims were capped
- `verification.regressions[]` — dimensions that got worse
- `verified: true/false` — overall pass/fail

**Use `verified.json` for all downstream steps — it contains the capped/regression-adjusted scores.**

### Step 1a (CRG): Structural Drift Verification

Verify structural drift across this round. Uses `crg_metrics.json` which
`compute_metrics()` now writes with a real `structural_drift` value (computed
against the previous round's metrics snapshot).

```bash
# Read the drift value computed by crg_analysis.py metrics
DRIFT=$(python3 -c "
import json, sys
m = json.loads(open('.sessi-work/crg_metrics.json').read())
print(m.get('structural_drift', 0.0))
" 2>/dev/null || echo "0.0")
echo "Per-round structural drift: ${DRIFT}"
```

Two classes of regression to escalate:

- **Architectural drift** — `structural_drift` > 0.4: treat as regression
  and trigger Step 3's revert protocol. 0.2–0.4: log a warning.
- **Test gap expansion** — `test_gaps` count grew this round. Each new gap
  adds an `open` issue in the registry (dimension = `test_coverage`,
  severity = `medium`).

---

## Step 2: Handle Capped Dimensions

For each entry in `capped[]`:

```
IF cap occurred (claim > EVIDENCE_THRESHOLD without diff evidence):
  → Accept the capped score (lower value)
  → Log: "Score capped from {claim} to {capped_to}: insufficient evidence"
  → Do NOT re-run improvements for this dimension this round
```

The capped score is the correct score for this round.

---

## Step 3: Handle Regressions

For each entry in `regressions[]`:

```
dimension_name: { before: X, after: Y, delta: -Z }

Actions:
1. Identify which fix caused the regression (git log --oneline -5)
2. IF fix is identifiable AND revert is safe:
   git revert <commit_hash> --no-edit
   **Re-run dimension tool and compare score to pre-regression value:**
   ```bash
   BEFORE=<pre-regression score from verifications.regressions[].before>
   # Re-run the dimension tool and parse its score (Step 1 formula from evaluate_dimension.md).
   # Then read the freshly-written score file:
   AFTER=$(python3 -c "
   import json
   d = json.loads(open('.sessi-work/round_<n>/scores/<dimension>.json').read())
   print(d.get('score', 0))
   " 2>/dev/null || echo '0')
   if [ "${AFTER}" -lt "$((BEFORE - 2))" ]; then
     echo "WARN: revert did not restore score: ${BEFORE} → ${AFTER}"
     python3 scripts/issue_tracker.py add .sessi-work/issue_registry.json \
       <dimension> medium /tmp/revert_finding.json
   fi
   ```
3. IF regression is acceptable trade-off (e.g., security fix breaks a flaky test):
   Document in .sessi-work/round_<n>/deferred_fixes.md
   Keep regression, flag for human review
4. Re-run verify.py to refresh verified.json with post-revert scores:
   ```bash
   python3 scripts/verify.py .sessi-work/round_<n> <repo_path>
   ```
```

---

## Step 4: LLM Cross-Check (Tier 3 only)

For each **Tier 3** dimension (architecture, readability, error_handling, documentation, performance):

Perform a brief sanity check:
- Does the claimed improvement match what was actually changed?
- Any obvious regression not caught by tools?

This is a lightweight 1-paragraph check per dimension, not a full re-evaluation.
Use Claude native (no Gemini for this step — judgment required).

---

## Step 5: Final Round Score

After verification and any reverts:

```bash
python3 scripts/score.py .sessi-work/round_<n> config.json \
  .sessi-work/issue_registry.json > .sessi-work/round_<n>/final_score.json
```

Check `meets_target`:
- `true` → trigger early-stop check in SKILL.md Step 3e
- `false` → continue to next round (if rounds remaining)

---

## Output Files

```
.sessi-work/round_<n>/
├── scores/
│   └── <dimension>.json   ← per-dimension raw scores (input to verify.py)
├── verified.json          ← verified scores with caps/regressions (use this)
├── final_score.json       ← post-verification overall score (with registry)
└── deferred_fixes.md      ← items requiring human attention
```
