# CONFIG_RECORDS.md - {專案名稱}

> 本檔案為 On-demand Lazy Load 模板，僅在需要產生此文件時載入。
> 來自：SKILL_TEMPLATES.md §T8.1

## 1. 版本資訊
- 版本：v{version}
- Git Commit：{hash}
- 發佈日期：{date}

## 2. 執行環境配置
| 環境 | 配置 |
|------|------|
| 開發 | {config} |
| 生產 | {config} |

## 3. 依賴套件清單
```
{pip freeze / npm lock output}
```

## 4. 環境變數
| 變數 | 類型 | 說明 |
|------|------|------|
| {VAR} | secret | {說明} |

## 5. 部署記錄
| 日期 | 版本 | 方式 | 執行人 |
|------|------|------|--------|
| {date} | {ver} | {method} | {name} |

## 6. 配置變更記錄
| Phase | 變更內容 | 理由 |
|-------|---------|------|
| Phase 5 | {變更} | {理由} |

## 7. 回滚 SOP
**觸發條件**：{條件}
**命令**：
```bash
{rollback commands}
```

## 8. 配置合規性確認
- [ ] Phase 7 風險緩解措施已落實
- [ ] 監控閾値已配置
- [ ] 爆斷器已啟用
