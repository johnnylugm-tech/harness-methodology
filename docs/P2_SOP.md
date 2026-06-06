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

### 1.3 寫入 SAB Block（machine-readable — BINDING CONTRACT）

> **CONTRACT**：block 格式由 `core/quality_gate/sab_parser.py:render_canonical_sab_template()` 定義。
> 請勿手寫 YAML — 貼上 canonical 範本後替換 EXAMPLE 值。
> Commit 前必須驗證：
> ```bash
> python3 scripts/generate_sab.py --validate --project .
> ```

以下是 canonical 格式（從 `render_canonical_sab_template()` 取得）：

```yaml
sab:
  version: "1.0"
  created_at: "{YYYY-MM-DD}"
  phase: 2  # 必須是 int，不可加引號
  project: "{project_name}"

  layers:
    - name: api
      modules: ["app.api.webhooks"]
      allowed_dependencies: ["service"]

  allowed_dependencies:
    - from: api
      to: service

  quality_targets:
    max_complexity: 15
    min_coverage: 80
    max_coupling: 0.3

  nfr_dimension_mapping: {}  # OPTIONAL — 自動從 nfr_traceability.type 衍生

  nfr_traceability:
    NFR-01:
      type: performance       # 8 個合法 type（見下方）
      target: "p95 < 200ms"  # ">=N" 或 "≥N" 會 raise gate floor
      module: app.processing.pipeline
    NFR-02:
      type: security
      target: "reject unsigned reqs"
      module: app.security.signature
    NFR-03:
      type: reliability
      target: "health check < 500ms"
      module: app.infrastructure.health
    NFR-04:
      type: maintainability
      target: "CC <= 10, zero lint"
      module: cross-cutting
    NFR-05:
      type: testability
      target: "assertion quality >= 70"
      module: tests
    NFR-06:
      type: deployability    # advisory — 無 gate 評分
      target: "compose up within 60s"
      module: docker-compose.yml
    NFR-07:
      type: scalability      # advisory
      target: "horizontal scale to 10 nodes"
      module: infra.k8s
    NFR-08:
      type: usability        # advisory
      target: "first-time user task < 5 min"
      module: docs.quickstart

  advisory_only: []  # AUTO-FILLED — 勿手填
  gate_score_overrides: {}  # AUTO-DERIVED — 勿手填

  fr_module_traceability:
    FR-01: "app.models"
    FR-02: "app.api.webhooks"

  architecture_constraints:
    - "no_circular_dependencies"

  high_risk_modules:
    - "app.api.webhooks"
```

> **NFR type 完整清單（8 個）**：
> - Enforceable（有 gate 評分工具，raise dimension floor）：
>   `performance` / `security` / `maintainability` / `reliability` / `testability`
> - Advisory（無評分工具，自動加入 `advisory_only`，不進 gate）：
>   `deployability` / `scalability` / `usability`
>
> `nfr_dimension_mapping` 不需填寫 — harness 從 `nfr_traceability.type` 自動衍生。
> `advisory_only` 和 `gate_score_overrides` 由 parser 計算 — 不需手填。

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
6. SAB block 已通過 `python3 scripts/generate_sab.py --validate --project .`（exit 0）
7. SAB block `phase` 是 int（非字串）、NFR type 均為合法的 8 個值之一

**CRG Architecture Design Verification（通用規則，不限語言/框架）：**

```
8. SUBDIRECTORY BOUNDARY — 檢查是否用子目錄控制 CRG community 邊界
   PASS: 已分層（2+ level deep），無 flat src/ 問題
   WARN: 部分 flat（>5 files 在同層無子目錄）
   REJECT: 完全扁平（單目錄 10+ files），CRG Leiden 會自由拆分出低分 community

9. HUB COVERAGE — 檢查每個 directory 的跨檔內部呼叫
   PASS: 每個 ≥2 files 目錄有 hub module（utils/common/helper）+ 多數 sibling 引用；
         每個 function body 都呼叫 hub function（不限於 module-level）；
         若 ≥4 個 sibling files，hub 有 ≥2 個 function 供呼叫
   WARN: 部分孤立檔案（不被任何 sibling import，只貢獻 external edges）；
         僅有 module-level 呼叫但 function body 中無呼叫
   REJECT: 任何目錄完全無 cross-file import

10. ENTRY POINT PLACEMENT — CLI/main/app.py 不能孤立在 root
    PASS: 在目錄內且該目錄有 strong hub 可補償 external edges
    REJECT: entry point 在 project root（src/cli.py, src/main.py 等無 siblings）

11. ESTIMATED SOURCE COMMUNITIES — 預估 CRG community 數量
    PASS: 3-6 個 source directories 是安全區
    WARN: 7-8 個（P3 Gate 需注意，可能需要合併）
    REJECT: 9+ 個（部分 community 必低於 0.3，gap 太大）

12. ISOLATED NODE RISK — 檢查是否有一檔目錄且該檔 import 大量外部
    PASS: 結構合理，無此風險
    REJECT: 有 1-file directory 且該檔案預期 import 大量外部套件（pure external edge dilution → cohesion near 0）
```

REJECT_IF (CRG):
- source directories > 8 → REJECT
- entry point 孤立在 project root → REJECT
- flat 單目錄 10+ files 無子目錄 → REJECT
- 任何 directory 完全無 cross-file internal edges 可能性 → REJECT
- 任一 sibling 檔的 function body 皆無 hub 呼叫（僅 module-level）→ REJECT（edge count 不足以 offset external edges）

---

## P2 Exit Checklist

- [ ] SAD.md 已生成（含 module 設計 + logical constraints + SAB block）
- [ ] ADR.md（≥1 篇）寫入 `docs/adr/`
- [ ] ADR.md 內 `<!-- harness:template-stub -->` sentinel 已移除（stub 表示 Agent A 從未填寫）
- [ ] 跑過 `python3 harness_cli.py check-constitution --phase 2 --project . --file 02-architecture/adr/ADR.md` 且 PASS（mid-loop 檢查）
- [ ] 跑過 end-of-phase `check-constitution --phase 2 --project .` 且 PASS（最終防線）
- [ ] `quality_manifest.json` 已生成於 `.methodology/`
- [ ] Agent B 審查通過（review_status: APPROVE）
- [ ] `sessions_spawn.log` 有 A/B 各 1 筆記錄（HR-10）
- [ ] `python scripts/list-modules.py --validate` 通過
- [ ] HANDOVER.md 已寫入（P2 摘要 + 下一步 P3 提示）
- [ ] HR-12 A/B Iteration Limit enforced (Max 5 rounds per FR)

---

## P2 → P3 交接

```bash
git add docs/SAD.md docs/adr/ .methodology/quality_manifest.json \
        .methodology/sessions_spawn.log HANDOVER.md
git commit -m "feat(P2): SAD.md + ADR complete — {N} modules, {M} FRs traced"
# Next phase plan pre-generated by plan-all at project init — verify it exists:
ls .methodology/phase3_plan.md 2>/dev/null || python harness_cli.py plan-all --project .
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
6. SAB block passed `python3 scripts/generate_sab.py --validate --project .` (exit 0)?
7. SAB block `phase` is int (not quoted string)? All NFR `type` values from 8 legal values (performance/security/maintainability/reliability/testability/deployability/scalability/usability)?

**CRG criteria (5 universal rules):**
8. SUBDIRECTORY BOUNDARY: subdirectories used to control CRG community boundaries? PASS=2+ level, WARN=>5 files flat, REJECT=10+ files flat
9. HUB COVERAGE: each ≥2-file dir has a hub module + every sibling's function bodies call it + ≥2 hub functions if ≥4 siblings? PASS=ok, WARN=orphan files or only module-level calls, REJECT=no cross-file import at all
10. ENTRY POINT PLACEMENT: entry points in a dir with strong hub? PASS=yes, REJECT=project root
11. ESTIMATED SOURCE COMMUNITIES: safe zone? PASS=3-6, WARN=7-8, REJECT=9+
12. ISOLATED NODE RISK: any 1-file dir with heavy external imports? PASS=no, REJECT=yes (pure external edge dilution)

REJECT_IF (CRG):
- source directories > 8 → REJECT
- entry point isolated at project root → REJECT
- flat single directory 10+ files without subdirectories → REJECT
- any directory with zero cross-file internal edges possibility → REJECT
- any sibling file has hub calls only at module-level, not in function bodies → REJECT (edge count insufficient to offset external edges)

Expected output:
- JSON: {"status": "success", "review_status": "APPROVE|REJECT",
         "confidence": N, "violations": [...], "citations": [...],
         "summary": "..."}
```
