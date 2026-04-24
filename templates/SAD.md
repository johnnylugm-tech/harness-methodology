# SAD - {專案名稱}

> 本檔案為 On-demand Lazy Load 模板，僅在需要產生此文件時載入。
> 來自：SKILL_TEMPLATES.md §T2.1

## 1. 架構概述
{架構高層描述}

## 2. 模組設計

### 2.1 {模組名稱}

| 屬性 | 値 |
|------|-----|
| 職責 | {職責} |
| 對外介面 | {API} |
| 依賴 | {依賴模組} |

#### 邏輯約束
- {約圇1}
- {約圇2}

## 3. 錯誤處理
| 等級 | 處理策略 |
|------|---------|
| L1 | 立即返回 |
| L2 | 重試 3 次 |
| L3 | 降級處理 |

## 4. 技術選型
| 技術 | 理由 |
|------|------|
| {技術} | {理由} |

---

## 5. SAB Block（機器可讀格式）

<!-- SAB:START -->
```json
{
  "version": "1.0",
  "created_at": "{YYYY-MM-DD}",
  "phase": 2,
  "project": "{專案名}",
  "layers": [
    {
      "name": "{layer_name}",
      "modules": ["FR-XX", "..."],
      "allowed_dependencies": ["{other_layer}"]
    }
  ],
  "dependencies": {
    "{layer_A}": ["{layer_B}"],
    "{layer_B}": ["{layer_C}"]
  },
  "quality_targets": {
    "max_complexity": 15,
    "min_coverage": 80,
    "max_coupling": 0.3
  }
}
```
<!-- SAB:END -->

**說明**：請填寫上方的 JSON 結構，這用於後續 Drift Detection。
