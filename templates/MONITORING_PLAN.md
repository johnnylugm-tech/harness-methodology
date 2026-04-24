# MONITORING_PLAN.md

> 本檔案為 On-demand Lazy Load 模板，僅在需要產生此文件時載入。
> 來自：SKILL_TEMPLATES.md §T5.2

## 監控維度

| 維度 | 指標 | 告警閾値 | 數據來源 |
|------|------|---------|---------|
| 效能 | 回應時間 | > {value} ms | {source} |
| 可靠性 | 錯誤率 | > {value}% | {source} |
| 資源 | 記憶體 | > {value} MB | {source} |
| 爆斷器 | 觸發次數 | > {N}/min | {source} |
