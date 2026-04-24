# DEPLOYMENT.md - 部署指南

## 部署前檢查清單

- [ ] 所有 Constitution 檢查通過
- [ ] 所有測試通過
- [ ] SPEC_TRACKING.md 所有項目 ✅
- [ ] TRACABILITY_MATRIX.md 所有項目 ✅

## 部署方式

### Docker 部署

```bash
# 建構映像
docker build -t myapp:latest .

# 執行容器
docker run -d -p 8000:8000 myapp:latest
```

### 系統服務（systemd）

```ini
[Unit]
Description=MyApp
After=network.target

[Service]
Type=simple
User=app
WorkingDirectory=/opt/myapp
ExecStart=/usr/bin/python3 -m src.cli
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## 環境變數

| 變數 | 說明 | 預設値 |
|------|------|--------|
| LOG_LEVEL | 日誌等級 | INFO |
| MAX_WORKERS | 最大工作線程 | 4 |
