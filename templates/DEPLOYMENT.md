# DEPLOYMENT.md - Deployment Guide

## Pre-Deployment Checklist

- [ ] All Constitution checks passed
- [ ] All tests passed
- [ ] SPEC_TRACKING.md all items complete
- [ ] TRACEABILITY_MATRIX.md all items complete

## Deployment Methods

### Docker

```bash
# Build image
docker build -t myapp:latest .

# Run container
docker run -d -p 8000:8000 myapp:latest
```

### systemd Service

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

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| LOG_LEVEL | Log level | INFO |
| MAX_WORKERS | Max worker threads | 4 |
