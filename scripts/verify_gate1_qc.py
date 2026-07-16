"""Phase 3 Gate 1 verdict reader — read quality_manifest quality_complete.

Authoritative Gate 1 verifier used by phase3-implementation.js per-FR
verify step. Replaces the v2.13.2 `spawnSync` design (which the
dynamic-workflow runtime sandbox cannot service — no Node.js
`child_process.spawnSync` exposed; ReferenceError at line ~406).

Python IS available to the Bash sub-agent, so the deterministic manifest
read is moved here. The sub-agent runs this script and echoes stdout
verbatim; workflow JS regex-parses the echoed string. The LLM is a
string carrier only — it cannot influence the verdict (the bytes on
disk are what Python read, the regex matches what Python printed).

Usage:
    python verify_gate1_qc.py --fr-id FR-01 --project /abs/path/to/repo

Exit codes:
    0  — quality_complete is True (PASS)
    1  — quality_complete is False or missing (FAIL)
    2  — manifest unreadable (ERROR)

Stdout (verbatim — LLM must echo this unchanged):
    "GATE1_VERIFIED_PASS"                          on PASS
    "GATE1_VERIFIED_FAIL score=<numeric_or_None>"  on FAIL
    "GATE1_VERIFIED_ERROR <reason>"                on ERROR

Why this script lives at harness/scripts/ (not inside the workflow JS):
    Phase 3 spawnSync was removed in v2.13.3 because the dynamic-workflow
    runtime sandbox has no child_process API. The script is invoked via
    `await agent('Run EXACTLY: ' + PY + ' <script>', schema: VERDICT_SCHEMA)`
    so workflow JS can still drive the verify step without an LLM in the
    verdict loop (LLM only carries the string; workflow JS regex-matches).

    Counterfactual ("just use await agent without a script"): a free-form
    agent prompt can hallucinate pass:false (wf_53d055ce-d0b: the LLM
    reported `pass:false, reason=GATE1_VERIFIED_FAIL score=91.81` despite
    `quality_complete=True`). The v2.13.2 spawnSync design was right
    (deterministic verifier, no LLM judgement); this script preserves
    that property under the runtime constraint.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Phase 3 Gate 1 verdict reader (manifest quality_complete)."
    )
    ap.add_argument("--fr-id", required=True, help="FR id, e.g. FR-01")
    ap.add_argument("--project", required=True, help="absolute project root")
    args = ap.parse_args()

    manifest_path = Path(args.project) / ".methodology" / "quality_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"GATE1_VERIFIED_ERROR manifest not found at {manifest_path}")
        return 2
    except (OSError, json.JSONDecodeError) as e:
        print(f"GATE1_VERIFIED_ERROR manifest unreadable: {type(e).__name__}: {e}")
        return 2

    gate1 = ((manifest.get("gate_results") or {}).get("gate1") or {})
    fr = gate1.get(args.fr_id) or {}
    qc = fr.get("quality_complete") is True
    score = fr.get("score")

    if qc:
        print("GATE1_VERIFIED_PASS")
        return 0
    print(f"GATE1_VERIFIED_FAIL score={score}")
    return 1


if __name__ == "__main__":
    sys.exit(main())