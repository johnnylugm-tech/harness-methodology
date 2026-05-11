# Phase 2 — Architecture Design (P2 SOP)

<!-- Role A: ARCHITECT | Role B: TECH_LEAD -->
<!-- Input: SRS.md (from P1) -->
<!-- Output: SAD.md + ADR.md + quality_manifest.json -->
<!-- Exit: Human¹ peer review — no automated gate -->

## P2 前提

P2 將 SRS.md 的 FR 轉換為架構設計。SAD.md 定義模組、依賴、技術選擇、品質目標。
ADR.md 記錄關鍵架構決策及其 rationale。`quality_manifest.json` 是 P3+ 門禁的機器可讀基準。

---

## Step 1 — Architecture Design（Agent A: ARCHITECT）

### 1.1 從 SRS.md 提取 FR 清單

```python
# 解析 SRS.md → 取得 FR 清單 + acceptance criteria
import re, json
srs = Path("docs/SRS.md").read_text()
fr_ids = re.findall(r"### (FR-\d+):", srs)
# → ["FR-01", "FR-02", ...]
```

### 1.2 設計模組架構

基於 SAD template (`templates/SAD.md`)，定義：
- Module 劃分（每個 module 對應一組 FR）
- 模組間的依賴關係（單向、無循環）
- External interface（每個 module 的公開 API）
- Logical constraints（架構不變量）

### 1.3 寫入 SAB Block（machine-readable）

```json
{
  "version": "1.0",
  "phase": 2,
  "layers": [...],
  "dependencies": {...},
  "quality_targets": {
    "max_complexity": 15,
    "min_coverage": 80,
    "max_coupling": 0.3
  }
}
```

> SAB 用於 P3+ 的 Drift Detection — 實作偏離架構時觸發警告。

---

## Step 2 — ADR（Architecture Decision Records）

針對每個關鍵架構決策，寫入 `docs/adr/ADR-001-{title}.md`：

```markdown
# ADR-001: {Decision Title}

## Status
Proposed | Accepted | Deprecated | Superseded

## Context
{What is the issue motivating this decision?}

## Decision
{What is the change we're proposing?}

## Consequences
{What becomes easier / harder because of this change?}
```

至少產出 1 篇 ADR（技術棧選擇）。複雜專案建議 2-3 篇。

---

## Step 3 — quality_manifest.json 生成

從 SAD.md 的 SAB block 生成 manifest：

```bash
python harness_cli.py manifest --fr-ids FR-01 FR-02 ... --sad docs/SAD.md
# Output: .methodology/quality_manifest.json
```

或直接呼叫：

```python
from harness.harness_bridge import HarnessBridge
HarnessBridge().generate_quality_manifest(
    fr_ids=["FR-01", "FR-02"],  # from SRS.md
    sad_path="docs/SAD.md"
)
```

---

## Step 4 — Agent B Review（TECH_LEAD）

Agent B 審查重點：
1. Module 劃分是否合理（高內聚、低耦合）
2. FR ↔ Module 對應是否完整（每個 FR 都有歸屬 module，無孤兒 FR）
3. 依賴關係是否單向無循環（DAG check）
4. ADR rationale 是否充分（技術選擇有客觀理由）
5. SAD 與 SRS 是否一致（no contradiction）
6. SAB block JSON 是否有效且完整

---

## P2 Exit Checklist

- [ ] SAD.md 已生成（含 module 設計 + logical constraints + SAB block）
- [ ] ADR.md（≥1 篇）寫入 `docs/adr/`
- [ ] `quality_manifest.json` 已生成於 `.methodology/`
- [ ] Agent B 審查通過（review_status: APPROVE）
- [ ] `sessions_spawn.log` 有 A/B 各 1 筆記錄（HR-10）
- [ ] `python scripts/list-modules.py --validate` 通過
- [ ] HANDOVER.md 已寫入（P2 摘要 + 下一步 P3 提示）

---

## P2 → P3 交接

```bash
git add docs/SAD.md docs/adr/ .methodology/quality_manifest.json \
        .methodology/sessions_spawn.log HANDOVER.md
git commit -m "feat(P2): SAD.md + ADR complete — {N} modules, {M} FRs traced"
python harness_cli.py plan-phase --phase 3 --repo .
```

---

## Agent A Dispatch Template (P2)

Orchestrator: copy this when spawning Agent A for P2.

```
[TASK]
Phase: 2 — Architecture Design
Role: ARCHITECT
FR-ID: n/a (per-phase task)
Deliverable: SAD.md (based on templates/SAD.md) + ADR.md (≥1)

SRS requirements:
> {paste relevant FR sections from docs/SRS.md — embed, not file path}

SAD template structure:
> {paste templates/SAD.md outline — embed, not file path}

Constraints:
- No circular dependencies between modules
- Each FR must map to exactly one module
- Architecture must follow SAD template structure
- At least 1 ADR for technology stack decisions

Expected output:
- SAD.md with module design, dependencies, SAB block, quality targets
- docs/adr/ADR-001-{title}.md
- JSON: {"status": "success", "files": ["docs/SAD.md", "docs/adr/ADR-001-*.md"],
         "confidence": N, "citations": [...], "summary": "..."}
```

## Agent B Dispatch Template (P2)

Orchestrator: copy this when spawning Agent B for P2.

```
[TASK]
Phase: 2 — Architecture Design
Role: TECH_LEAD — review SAD.md + ADR.md
FR-ID: n/a (per-phase task)

SRS (for cross-reference):
> {paste full SRS.md content — embed, not file path}

SAD to review:
> {paste full SAD.md content — embed, not file path. Agent B is stateless (§0.5)}

ADR to review:
> {paste ADR content — embed, not file path}

Review criteria:
1. Module cohesion: each module has a single clear responsibility?
2. FR coverage: every FR in SRS maps to a module in SAD? (no orphans)
3. Dependency DAG: no circular dependencies? (draw the graph)
4. ADR rationale: technology choices justified with objective criteria?
5. SRS-SAD consistency: no contradictions between spec and design?
6. SAB block: valid JSON, layers and dependencies correctly specified?

Expected output:
- JSON: {"status": "success", "review_status": "APPROVE|REJECT",
         "confidence": N, "violations": [...], "citations": [...],
         "summary": "..."}
```
