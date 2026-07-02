import os

bugs = [
    {"id": 1, "file": "harness/decision_log.py", "line": 49, "desc": "glob seq injection"},
    {"id": 2, "file": "detection/drift_detector.py", "line": 665, "desc": "dotted vs slash"},
    {"id": 3, "file": "harness/harness_bridge.py", "line": 2056, "desc": "None score TypeError"},
    {"id": 4, "file": "scripts/phase_auditor.py", "line": 506, "desc": "cache key candidates[0]"},
    {"id": 5, "file": "scripts/cron_drift_monitor.py", "line": 124, "desc": "max() severity strings"},
    {"id": 6, "file": "harness/harness_bridge.py", "line": 2278, "desc": "open_critical None pollution"},
    {"id": 7, "file": "core/quality_gate/mutation_enforcer.py", "line": 524, "desc": "subprocess without FileNotFoundError"},
    {"id": 8, "file": "core/auto_fix/__init__.py", "line": 161, "desc": "round counter assignment instead of +="},
    {"id": 9, "file": "harness/harness_bridge.py", "line": 442, "desc": "mutate caller dims"},
    {"id": 10, "file": "scripts/spec_logic_checker.py", "line": 235, "desc": "FR regex no anchor"},
    {"id": 11, "file": "harness_cli.py", "line": 6086, "desc": "gitleaks bare subprocess"},
    {"id": 12, "file": "harness_cli.py", "line": 3069, "desc": "issue_registry_path traversal"},
    {"id": 13, "file": "harness_cli.py", "line": 665, "desc": "_check_tool_evidence exception swallow"}
]

for b in bugs:
    path = b["file"]
    print(f"\n--- BUG {b['id']}: {b['desc']} ({path}:{b['line']}) ---")
    if not os.path.exists(path):
        print(f"FILE NOT FOUND: {path}")
        continue
        
    with open(path, "r") as f:
        lines = f.readlines()
        
    start = max(0, b["line"] - 8)
    end = min(len(lines), b["line"] + 8)
    
    for i in range(start, end):
        prefix = ">>" if i + 1 == b["line"] else "  "
        print(f"{prefix} {i+1:4d}: {lines[i].rstrip()}")
