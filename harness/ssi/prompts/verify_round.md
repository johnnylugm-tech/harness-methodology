# Verify Round Protocol

Cross-check all dimension scores after improvements. Detect regressions and cap unsupported claims.

---

## Step 1: Run Deterministic Verification

```bash
python3 scripts/verify.py \
  .sessi-work/round_<n>/result.json \
  .sessi-work/round_<n> \
  <repo_path>
```

Read the output:
- `verification.capped[]` — dimensions where claims were capped
- `verification.regressions[]` — dimensions that got worse
- `verified: true/false` — overall pass/fail

**Use `verified.json` for all downstream steps. Never use raw `result.json`.**

### Step 1a (CRG, if available): Structural Verification

Refresh the graph and measure structural drift across the whole round.
This catches regressions that dimension tools cannot see:

```bash
# Incremental refresh (seconds — only re-parses changed files)
code-review-graph update --repo <repo_path>

# Blast radius of ALL fixes made this round (cumulative, not per-fix).
# Uses a different base than the per-fix `risky` check:
#   risky . HEAD  →  uncommitted diff vs HEAD        (per-fix, pre-commit)
#   blast  <repo> <tag>  →  current state vs round tag  (per-round, post-commit)

# Round 1: no previous round tag exists — count commits made this session
COMMITS_THIS_ROUND=$(git -C <repo_path> log --oneline round-0..HEAD 2>/dev/null \
  | wc -l || git -C <repo_path> log --oneline | wc -l)
BASE_REF="HEAD~${COMMITS_THIS_ROUND}"

# Round N>1: previous round tag exists
# (check with: git tag -l "round-<n-1>" — if output is non-empty, tag exists)
if git -C <repo_path> tag -l "round-<n-1>" | grep -q .; then
  BASE_REF="round-<n-1>"
fi

python3 scripts/crg_integration.py blast <repo_path> "${BASE_REF}" \
> .sessi-work/round_<n>/crg_blast_radius.json
```

Two classes of regression to escalate:

- **Architectural drift** — `risk_score` jumped > 0.2 between rounds, OR
  new hub nodes appeared. Log a warning in `verified.json`; if drift > 0.4,
  treat as regression and trigger Step 3's revert protocol.
- **Test gap expansion** — `test_gaps` count grew this round. Each new gap
  adds an `open` issue in the registry (dimension = `test_coverage`,
  severity = `medium`).

If CRG is not installed this step is skipped silently.

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
   Re-run dimension tool to confirm revert worked
3. IF regression is acceptable trade-off (e.g., security fix breaks a flaky test):
   Document in .sessi-work/round_<n>/deferred_fixes.md
   Keep regression, flag for human review
4. Update verified.json with post-revert scores
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
python3 scripts/score.py .sessi-work/round_<n> config.json > .sessi-work/round_<n>/final_score.json
```

Check `meets_target`:
- `true` → trigger early-stop check in SKILL.md Step 3e
- `false` → continue to next round (if rounds remaining)

---

## Output Files

```
.sessi-work/round_<n>/
├── result.json          ← raw (do not use downstream)
├── verified.json        ← verified scores (use this)
├── final_score.json     ← post-verification overall score
└── deferred_fixes.md    ← items requiring human attention
```
