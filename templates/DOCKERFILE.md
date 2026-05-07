# Dockerfile Template

> Docker configuration based on best practices

## Usage

```bash
# Copy to project root
cp templates/DOCKERFILE.md Dockerfile

# Edit as needed
vim Dockerfile
```

## Standard Python Dockerfile

```dockerfile
FROM python:3.10-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src/ ./src/

# Default command
CMD ["python", "-m", "src.cli"]
```

## Multi-Stage Build (recommended)

```dockerfile
# Build stage
FROM python:3.10 AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --user -r requirements.txt

# Runtime stage
FROM python:3.10-slim
WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY src/ ./src/
ENV PATH=/root/.local:$PATH
CMD ["python", "-m", "src.cli"]
```
