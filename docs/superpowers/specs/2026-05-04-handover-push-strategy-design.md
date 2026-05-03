# Design: Handover-Aware Multi-Push Git Strategy

**Date**: 2026-05-04  
**Status**: Approved  
**Approach**: B — `HandoverGenerator` data-driven template

---

## Motivation

Long Claude sessions accumulate context that degrades performance. By aligning
`/compact` with meaningful git checkpoints, each session ends at a stable,
resumable node. State persists on GitHub, not in local memory.

## Push Point Map (10 total)

| # | Checkpoint ID  | Phase | Trigger                              | New? |
|---|----------------|-------|--------------------------------------|------|
| ① | `P1-exit`      | P1    | P1 SRS/SAD draft complete            | ✅   |
| ② | `P2-exit`      | P2    | `quality_manifest.json` generated    | —    |
| ③ | `P3-mid`       | P3    | FR Gate1 PASS ≥ 50% of total FRs    | ✅   |
| ④ | `P3-pre-ssi`   | P3    | All FRs done, SSI not yet executed   | ✅   |
| ⑤ | `P3-gate2`     | P3    | Gate 2 PASS (SSI score ≥ 75)        | —    |
| ⑥ | `P4-gate3`     | P4    | Gate 3 PASS (score ≥ 80)            | —    |
| ⑦ | `P5-baseline`  | P5    | `BASELINE.md` committed              | —    |
| ⑧ | `P6-gate4`     | P6    | Gate 4 APPROVE (score ≥ 85) + tag   | —    |
| ⑨ | `P7-exit`      | P7    | Risk register complete               | ✅   |
| ⑩ | `P8-exit`      | P8    | Config records complete              | ✅   |

## Architecture

### `HandoverGenerator` (`harness/handover_generator.py`)

Single-responsibility: render + write `HANDOVER.md` at project root.

```
HandoverGenerator(project: Path)
  └── write(checkpoint_id, phase, task_background, current_status,
            next_steps, notes, extra) → Path
        └── _render(...) → str
```

- `checkpoint_id`: e.g. `P3-pre-ssi-20260504`
- `current_status`: runtime string (scores, FR counts, etc.)
- `next_steps`: ordered list of actions for next session
- `notes`: warnings; default includes "100% follow SKILL.md"
- `extra`: optional k/v dict appended as "附加資訊" section

### `GitStrategy` (`harness/git_strategy.py`)

**New methods**: `commit_and_push_p1`, `commit_and_push_p3_mid`,
`commit_and_push_p3_pre_ssi`, `commit_and_push_p7`, `commit_and_push_p8`

**Updated methods**: all existing push methods gain `_write_handover()` call
before `_commit_and_push()`.

**Internal helper**: `_write_handover(checkpoint_id, phase, status, steps, notes, background, extra)`
— builds `HandoverGenerator`, writes, returns path.

### `HANDOVER.md` format

```markdown
# Harness Methodology — Session Handover
**Checkpoint**: `P3-pre-ssi-20260504`
**Phase**: P3 — Implementation
**Generated**: 2026-05-04T10:30:00Z

> ⚠️  開始下一個工作階段前，請先執行 `/compact` …

---
## 任務背景
## 目前執行狀況
## 接下來的工作
## 注意事項
## 附加資訊   ← optional
---
*由 HandoverGenerator 自動生成。下次 push 時此檔案將被覆寫。*
```

## Default Notes (all checkpoints)

```python
_DEFAULT_NOTES = [
    "100% follow SKILL.md",
    "Do NOT commit .sessi-work/ or .methodology/ runtime artifacts",
    "Git failures are warnings — they never block the pipeline",
]
```

## Key Constraints

- `HANDOVER.md` is NOT in `.gitignore`; it is committed and overwritten each push
- All push methods are **non-blocking** — git failures are warnings
- `HandoverGenerator` has no external dependencies (stdlib only)

## Files Changed

| File | Change |
|------|--------|
| `harness/handover_generator.py` | New |
| `harness/git_strategy.py` | Add 5 methods + `_write_handover` helper + update 6 existing |
| `tests/test_handover_generator.py` | New |
| `docs/superpowers/specs/2026-05-04-handover-push-strategy-design.md` | New (this file) |
