# MONITORING_PLAN.md

> On-demand Lazy Load template.
> Source: SKILL_TEMPLATES.md SS T5.2

## Monitoring Dimensions

| Dimension | Metric | Alert Threshold | Data Source |
|-----------|--------|-----------------|-------------|
| Performance | Response Time | > {value} ms | {source} |
| Reliability | Error Rate | > {value}% | {source} |
| Resources | Memory | > {value} MB | {source} |
| Circuit Breaker | Trigger Count | > {N}/min | {source} |
