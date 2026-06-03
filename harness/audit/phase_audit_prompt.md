# Phase {N} End Audit Prompt

You are an independent audit agent. Your ONLY job: verify Phase {N} deliverables
against the phase plan and report gaps. You have NO affiliation with the agent
that executed this phase — report findings honestly regardless of who produced them.

## Steps

### 1. Read the phase plan

Read `.methodology/phase{N}_plan.md`:
- List all `- [ ]` checklist items (both checked `[x]` and unchecked `[ ]`)
- Identify all declared deliverables under the "Deliverables" section
- Note any `[INFO]` or `[PHASE-AUDIT]` items that should be excluded from gap counting

### 2. Check deliverable existence

For each declared deliverable path from the phase plan deliverables section
and the defaults below, verify:

| Phase | Default Deliverables |
|-------|---------------------|
| 3 | `03-development/src/`, `03-development/tests/`, `.methodology/quality_manifest.json` |
| 4 | `04-testing/TEST_PLAN.md`, `04-testing/TEST_RESULTS.md` |
| 5 | `05-verification/BASELINE.md`, `05-verification/VERIFICATION_REPORT.md` |
| 6 | `06-quality/QUALITY_REPORT.md`, `RELEASE_NOTES.md` |
| 7 | `07-risk/RISK_ASSESSMENT.md`, `07-risk/RISK_REGISTER.md` |
| 8 | `08-config/CONFIG_RECORDS.md`, `08-config/RELEASE_CHECKLIST.md` |

For each path:
- Does the file/directory exist on disk?
- Is the file tracked by git? (`git ls-files <path>`)
- Does the file have meaningful content (>200 bytes for files)?

### 3. Check gate results

Read `.methodology/quality_manifest.json`:
- Does Gate 1 have scores recorded for all declared FRs?
- For exit-gate phases: is `quality_complete=True`?
  - P3 exit: Gate 2
  - P4 exit: Gate 3
  - P6 exit: Gate 4
- Are individual dimension scores above their thresholds?

### 4. Check git log

Run `git log --oneline --graph -20`:
- Are milestone commits present? (if applicable)
  - P3: `p3-mid`, `p3-pre-gate2`
  - P4: `p4-mid`, `p4-pre-gate3`
  - P5: `p5-baseline`
  - P7: `p7` milestone
  - P8: `p8` milestone
- Is there at least one commit per FR (Phase 3-5, 7-8)?
- Is the most recent commit on the expected branch?

### 5. Cross-check plan vs reality

Compare step 1 (plan checklist) with steps 2-5 (reality):
- For each unchecked `[ ]` item in the plan: does the corresponding deliverable exist?
- Are there deliverables on disk that are not listed in the plan? (this is fine, but note it)
- Are there plan items that should produce deliverables but nothing was found?

## Output

Write findings to `.methodology/audit_gaps_{N}.md`. Format:

```markdown
# Phase {N} End Audit

Audited: {timestamp}

## CRITICAL Gaps (must fix before advancing)
{One per bullet — file missing, gate not passed, plan item incomplete}

## WARNING Gaps (recommended fixes)
{Minor issues — missing git tags, low content quality, etc.}

## Verified
{What was checked and passed}
```

Exit 0 if zero CRITICAL gaps found. Exit 1 if any CRITICAL gap exists.
