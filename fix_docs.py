
# Fix SAD.md
with open("SAD.md", "r") as f:
    text = f.read()

replacements_sad = {
    "| **Drift Monitor cron** | `scripts/cron_drift_monitor.py` | Target project (crontab) | Hourly architecture drift detection; alert via log / email / Slack. Path via `DRIFT_PROJECT_PATH` env var |": 
    "| **Drift Monitor cron** | `scripts/cron_drift_monitor.py` | Target project (crontab) | ~~Hourly architecture drift detection; alert via log / email / Slack. Path via `DRIFT_PROJECT_PATH` env var~~ **REMOVED** (減法 T4) |",
    
    "#### `steering/integrations.py` — Integration Adapters":
    "#### `steering/integrations.py` — Integration Adapters **(REMOVED)**",
    
    "**Purpose**: Real implementations of the HR-compliance interfaces imported by `steering/integrations.py`. Eliminates the graceful-degrade no-ops; `SteeringIntegrator` is now fully operational.":
    "**Purpose**: Real implementations of the HR-compliance interfaces (note: `steering` was removed in 減法 T4).",
    
    "| `constitution/__init__.py` | — | Package marker; re-exports the three most-imported classes (`BVSRunner`, `CitationParser`, `VerificationConstitutionChecker`) for convenience. The other 5 (`ClaimVerifier`, `ClaimExtractor` (functions), `ExecutionLogger`, `InferentialSensor`, `InvariantEngine`) are reachable via the submodule path only. |":
    "| `constitution/__init__.py` | — | Package marker; re-exports the most-imported classes (`BVSRunner`, `CitationParser`) for convenience. The other 5 (`ClaimVerifier`, `ClaimExtractor` (functions), `ExecutionLogger`, `InferentialSensor`, `InvariantEngine`) are reachable via the submodule path only. |",
    
    "| `constitution/verification_constitution_checker.py` | `VerificationConstitutionChecker` | Bridges `steering/integrations.py` to `enforcement.constitution_as_code` (R001-R007); gracefully degrades to pass-through if `enforcement/` unavailable |":
    "| `constitution/verification_constitution_checker.py` | `VerificationConstitutionChecker` | ~~Bridges `steering/integrations.py` to `enforcement.constitution_as_code` (R001-R007); gracefully degrades to pass-through if `enforcement/` unavailable~~ **REMOVED** (減法 T3) |",
    
    "| `cron_drift_monitor.py` | 5KB | Hourly drift detection cron; reads `DRIFT_PROJECT_PATH` env var; alerts via log + optional Slack webhook / SMTP email (both env-var configurable) |":
    "| `cron_drift_monitor.py` | 5KB | ~~Hourly drift detection cron; reads `DRIFT_PROJECT_PATH` env var; alerts via log + optional Slack webhook / SMTP email (both env-var configurable)~~ **REMOVED** (減法 T4) |",
    
    "| `drift_crontab.example` | 780B | Example crontab configuration for `cron_drift_monitor.py` |":
    "| `drift_crontab.example` | 780B | ~~Example crontab configuration for `cron_drift_monitor.py`~~ **REMOVED** (減法 T4) |",
    
    "| **P1** | ~~`constitution/` package stub or real impl~~ | ✅ Done (v2.0.1) | `constitution/` implemented — `BVSRunner`, `CitationParser`, `VerificationConstitutionChecker` all deployed. | A |":
    "| **P1** | ~~`constitution/` package stub or real impl~~ | ✅ Done (v2.0.1) | `constitution/` implemented — `BVSRunner`, `CitationParser` deployed. (`VerificationConstitutionChecker` removed later in T3) | A |",
    
    "| `constitution.*` graceful degrade | `steering/integrations.py` | ✅ **Resolved (v2.0.1)** — `constitution/` package implemented: `BVSRunner` (HR-03 phase checks), `CitationParser` (HR-07/09), `VerificationConstitutionChecker` (bridges R001-R007). All imports now resolve; `SteeringIntegrator` fully operational. | See §3.19 |":
    "| `constitution.*` graceful degrade | `steering/integrations.py` | ✅ **Resolved (v2.0.1)** (Note: `steering` and `VerificationConstitutionChecker` later removed in T3/T4) | See §3.19 |",
    
    "| HR-12 real limiter not wired | `steering/integrations.py` | ✅ **Resolved (v2.0.2)** — `SteeringIntegrator.should_continue` property now cross-checks `HR12Resolution(max_allowed, early_stop_threshold, min_rounds_before_stop).should_stop()` against `SteeringLoop.should_continue()`. HR-12 takes priority; `VerificationConstitutionChecker.check()` called on stop. | — |":
    "| HR-12 real limiter not wired | `steering/integrations.py` | ✅ **Resolved (v2.0.2)** (Note: `steering` later removed in T4) | — |"
}

for k, v in replacements_sad.items():
    if k in text:
        text = text.replace(k, v)
    else:
        print(f"WARN: SAD.md target not found: {k[:50]}...")
with open("SAD.md", "w") as f:
    f.write(text)

# Fix manifest.yaml
with open("rules/manifest.yaml", "r") as f:
    yaml = f.read()
yaml = yaml.replace("check_methodology_consistency.py", "check_methodology_consistency.py (REMOVED)")
with open("rules/manifest.yaml", "w") as f:
    f.write(yaml)

# Fix deliverables.schema.yaml
with open("schemas/deliverables.schema.yaml", "r") as f:
    sch = f.read()
sch = sch.replace("check_methodology_consistency.py", "check_methodology_consistency.py (REMOVED)")
with open("schemas/deliverables.schema.yaml", "w") as f:
    f.write(sch)

print("Done replacing.")
