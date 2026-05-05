# Final Report Protocol

Generate the end-of-run Quality Improvement Report. The goal is **full transparency**:
every issue found must appear somewhere in the report with its resolution and evidence.

This step runs **after** the last round completes (either by hitting `quality_complete`,
max_rounds, or saturation).

---

## Step 1: Generate the Structured Report

The deterministic portion is auto-generated — do not hand-write these sections:

```bash
python3 scripts/report_gen.py \
  <repo_path> \
  .sessi-work \
  .sessi-work/issue_registry.json \
  <score_gate> \
  .sessi-work/final_report.md
```

Output sections 1–7 are mandatory and rendered from:
- `issue_registry.json` (issues + resolutions + commit SHAs)
- `.sessi-work/round_*/verified.json` or `final_score.json` (trajectory)
- `git log` (commit evidence, file change enrichment)

**Do not edit sections 1–7 by hand.** If a fact is wrong, fix the source data
(registry or round file) and re-run.

---

## Step 2: Add Narrative Sections (Claude native)

The generated Markdown covers **what happened**. Claude adds narrative for
**why it matters** — up to three short sections appended to the generated file:

### Section 8: Root-Cause Themes (2–4 bullets)

Read the "Issues Fixed" table. Group fixes by underlying cause, not by dimension.
Surface the 2–4 themes that dominated this run.

> Example:
> - Missing input validation at API boundaries caused 4 of 7 security findings
> - Bare `except:` idiom from legacy code drove 3 error_handling issues

### Section 9: Remaining Risk (only if accepted_risks is non-empty)

Summarize the accepted risks by theme. Explain what a future iteration should
re-examine when context changes (e.g. new compliance requirement, new scale).

### Section 10: Recommendation Rationale (1 paragraph)

Explain the state-machine decision from `report_gen.py`:
- `pass` — all issues fully resolved
- `pass-with-risks` — all critical/high/medium fixed; accepted risks remain with reasons
- `partial` — open medium+ issues remain (max_rounds hit before completion)
- `fail` — regression or baseline drop detected

One paragraph. Cite the counts (e.g. "5 fixed, 2 wontfix, 0 open medium+").

---

## Step 3: Verify Traceability

Every `fixed` issue in the registry MUST have either `commit_sha` or `files_changed`.
If not, the report will show `—` and traceability is broken.

```bash
python3 -c "
import json
r = json.load(open('.sessi-work/issue_registry.json'))
for i in r['issues']:
    if i['status'] == 'fixed' and not (i.get('commit_sha') or i.get('files_changed')):
        print(f'MISSING TRACEABILITY: {i[\"id\"]} {i[\"dimension\"]} {i[\"message\"]}')
"
```

If output is non-empty, go back and update the registry with `issue_tracker.py fix`
passing the correct `commit_sha` before shipping the report.

---

## Step 4: Anti-Hallucination Guards

The report is **fact-bound to the registry and git log**. Do not:
- Invent commit SHAs
- Claim files were changed that git does not show
- Change the recommendation from what `report_gen.py` computed
- Silently omit any `accepted_risks` or `open` issue
- Re-classify severity in the narrative (it must match the registry)

The narrative sections (8–10) reference the auto-generated sections by ID. They
add interpretation, not facts.

---

## Output

`.sessi-work/final_report.md` — shown to the user, committed to the repo as the
canonical record of this quality-improvement run.
