# 專案結構模板

> Spec Kit 風格的標準化專案結構

---

## 標準目錄結構

```
project/
├── 00-summary/             # 摘要
├── 01-requirements/        # Phase 1: 需求分析 (SRS.md)
├── 02-architecture/       # Phase 2: 架構設計 (SAD.md)
├── 03-development/src/     # Phase 3: 代碼實作
├── 03-development/tests/   # Phase 3: 測試
├── 04-testing/            # Phase 4: 測試驗證
├── 05-deployment/         # Phase 5: 部署
├── 06-maintenance/        # Phase 6+: 維運
└── .methodology/          # Framework 狀態追蹤
```

> ⚠️ 注意：Phase 編號與目錄前綴對齊（Phase N → 0N-xxx/）

---

## 01-requirements 模板

### SRS.md

```markdown
# 需求規格

## 專案名稱
[名稱]

## 功能需求

### FR-001: [功能名稱]
- **優先級**: P0 / P1 / P2
- **驗收標準**: [可測試的標準]
```

---

## 02-architecture 模板

### SAD.md

```markdown
# 架構設計

## 系統概覽
[系統架構圖]

## 元件設計

### Component: [名稱]
- **職責**: [職責描述]
```

---

## 03-development 模板

### src/

```
src/
├── FR-01/                  # FR-01 模組
│   ├── __init__.py
│   ├── main.py
│   └── test_fr01.py
└── FR-02/                  # FR-02 模組
```

---

## 04-testing 模板

### tests/

```
tests/
├── test_fr01.py
├── test_fr02.py
└── integration/
```

---

## 05-deployment 模板

### DOCKERFILE / deployment/

```dockerfile
FROM python:3.11
COPY src/ /app/src/
```

---

## 06-maintenance 模板

### 維運文件結構

```
06-maintenance/
├── MONITORING_PLAN.md
└── RUNBOOK.md
```

---

*模板版本: 1.0.0*
