# CONFIG_RECORDS.md - {Project Name}

> On-demand Lazy Load template. Load only when this document needs to be generated.
> Source: SKILL_TEMPLATES.md SS T8.1

## 1. Version Info
- Version: v{version}
- Git Commit: {hash}
- Release Date: {date}

## 2. Runtime Configuration
| Environment | Config |
|-------------|--------|
| Development | {config} |
| Production | {config} |

## 3. Dependencies
```
{pip freeze / npm lock output}
```

## 4. Environment Variables
| Variable | Type | Description |
|----------|------|-------------|
| {VAR} | secret | {description} |

## 5. Deployment Records
| Date | Version | Method | Executor |
|------|---------|--------|----------|
| {date} | {ver} | {method} | {name} |

## 6. Config Change Log
| Phase | Change | Reason |
|-------|--------|--------|
| Phase 5 | {change} | {reason} |

## 7. Rollback SOP
**Trigger condition**: {condition}
**Commands**:
```bash
{rollback commands}
```

## 8. Config Compliance
- [ ] Phase 7 risk mitigations implemented
- [ ] Monitoring thresholds configured
- [ ] Circuit breaker enabled
