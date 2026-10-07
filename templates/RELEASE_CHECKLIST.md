# RELEASE_CHECKLIST — {project_name}

> Release **candidate** at commit `{git_hash}`, rendered by the framework on {release_date}
> from its own records. The first table states what the framework measured; the
> PENDING-HUMAN items are not verified by the framework, and this document does not
> claim the release was approved or deployed.

## Framework-verified

| Item | State | Source |
|------|-------|--------|
| Phases P1–P7 completed | {phases_completed} | `.methodology/state.json` phase_completed |
| Gate 4 | {gate4_verdict} | `.methodology/gate4_result.json` |
| Confirmed critical/high findings | {open_blocking} | `.methodology/bug_hunt_report.json` |
| CI green on the release commit | enforced at the P8 exit: advance-phase refuses Phase 8 until push-milestone p8 has landed on a green build (exit 51) | `.methodology/state.json` last_milestone_head.p8 |
| Residual risks | listed and dispositioned by the project, not judged here | `07-risk/RISK_REGISTER.md` |

## PENDING-HUMAN — not verified by the framework

- [ ] Final sign-off approved by a named person
- [ ] Production environment provisioned
- [ ] Rollback plan exercised
