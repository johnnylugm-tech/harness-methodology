# Dockerfile 模板

> 基於最佳實踐的 Docker 配置

## 使用方式

```bash
# 複製到專案根目錄
cp .methodology/templates/DOCKERFILE.md Dockerfile

# 編輯自定義內容
vim Dockerfile
```

## 標準 Python Dockerfile

```dockerfile
FROM python:3.10-slim

WORKDIR /app

# 安裝依賴
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製源碼
COPY src/ ./src/

# 預設命令
CMD ["python", "-m", "src.cli"]
```

## 多階段建構（推薦）

```dockerfile
# 建構階段
FROM python:3.10 AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --user -r requirements.txt

# 運行階段
FROM python:3.10-slim
WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY src/ ./src/
ENV PATH=/root/.local:$PATH
CMD ["python", "-m", "src.cli"]
```
