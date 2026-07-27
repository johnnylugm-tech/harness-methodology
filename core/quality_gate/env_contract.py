"""Round 20 站1 — the environment contract: classification once, verification always.

env-check answers two questions of different natures, and until this station it
answered both with one LLM pass, every run:

  classification  Does this project RUN without FOO exported? Decided by what
                  the project's docs say about FOO — a documented default, a
                  test/dev-only opt-in flag, or neither.
  verification    Is FOO exported right now? Decided by the environment, and
                  fully computable (core/quality_gate/env_verify.py).

Round 24 (37adc43) recorded the cost of conflating them: the same documented env
var, against the same unchanged project state, was classified optional_missing
(ready=true) in one workflow run and required+present:false (ready=false, a
false FAIL) in another. Nothing had changed except the sampling of an LLM.

Three layers of checking existed and none of them could catch it, because all
three read the same field: the agent self-reports `ready`, run-env-check exits
on `ready`, and the workflow JS cross-checks... `ready`. `_verify_env_check_claims`
re-verifies only `present: true` claims, so BOTH shapes of that bug (a var
demoted to optional_missing, or promoted to required+present:false) sat in its
blind spot by construction.

The split here: the classification is stored in a versioned
`.methodology/env_contract.json` — reviewable, correctable, diffable — and is
only re-derived when the documents it was derived FROM change (content
fingerprint, the same device Round 18 站3 used for attestations). `ready` is then
COMPUTED from that contract against the live environment on every run, by
env_verify's probes. No LLM output decides it.

A note on what固化 does and does not fix: a wrong classification now persists
until the source docs change. That is the intended trade — a persistent wrong
answer is visible in a diff and can be corrected by hand, whereas a randomly
flipping one is neither. Same reasoning as SAB.json and fr_module_traceability,
which are also judgements captured once rather than re-derived per run.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "CONTRACT_RELPATH",
    "SCHEMA_VERSION",
    "contract_path",
    "compute_source_fingerprint",
    "load_contract",
    "write_contract",
    "contract_is_current",
    "derive_contract_from_result",
    "evaluate_contract",
]

CONTRACT_RELPATH = ".methodology/env_contract.json"
SCHEMA_VERSION = 1

# Classification buckets for env vars. Only `mandatory` gates readiness.
#
# These mirror the CLASSIFICATION RULE in HarnessBridge's EnvCheckContext
# .evaluation_prompt(), minus its first clause ("exported in current shell?"),
# which is environment state and therefore verification, not classification.
# Folding that clause into the stored classification would put a live
# measurement into a cached artifact — the exact mixing this module exists to
# undo.
MANDATORY = "mandatory"        # no default, not a dev flag — absence breaks the project
HAS_DEFAULT = "has_default"    # documented default value; absence is fine
DEV_OPT_IN = "dev_opt_in"      # test/dev-only opt-in; absence is the INTENDED state
ENV_BUCKETS = (MANDATORY, HAS_DEFAULT, DEV_OPT_IN)


def contract_path(project: "str | Path") -> Path:
    return Path(project) / CONTRACT_RELPATH


def compute_source_fingerprint(
    sad_excerpt: str, srs_excerpt: str, docker_compose_excerpt: str = ""
) -> str:
    """sha256 over every document the classification is derived from.

    These are exactly the three excerpts EnvCheckContext.evaluation_prompt()
    puts in front of the sub-agent. They are deterministic (whole file, or a
    truncation at a fixed character count), so the same project state yields the
    same fingerprint — the property that makes "re-classify only when the source
    changed" well-defined.

    If a future change adds a fourth document to that prompt, it MUST be added
    here too, or classification will silently go stale against it.
    tests/test_env_contract.py::test_fingerprint_covers_every_prompt_document
    pins the two lists together.
    """
    h = hashlib.sha256()
    for part in (sad_excerpt or "", srs_excerpt or "", docker_compose_excerpt or ""):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")  # unambiguous separator: "ab"+"c" must not equal "a"+"bc"
    return h.hexdigest()


def load_contract(project: "str | Path") -> "dict[str, Any] | None":
    """Read the contract, or None when absent/unreadable/malformed.

    Never raises: a missing or corrupt contract must route to re-classification,
    never crash a gate.
    """
    path = contract_path(project)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_contract(project: "str | Path", contract: "dict[str, Any]") -> Path:
    path = contract_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(contract, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def contract_is_current(contract: "dict[str, Any] | None", fingerprint: str) -> bool:
    """Whether a stored classification still describes the current documents."""
    if not contract:
        return False
    if contract.get("schema_version") != SCHEMA_VERSION:
        return False
    return bool(fingerprint) and contract.get("source_sha256") == fingerprint


def derive_contract_from_result(
    result: "dict[str, Any]", fingerprint: str, enforcer_sha: str = ""
) -> "dict[str, Any]":
    """Distil the CLASSIFICATION out of an agent-written env_check_result.json.

    Takes only the naming/classification facts and drops every measurement:
    `present` flags, `ready`, and `checked_at` all describe one moment's
    environment and would be stale the instant they were stored.

    Mapping from the result schema:
      env_vars.required[].name   -> mandatory   (agent found no default/dev-flag
                                                 exemption; it is required)
      env_vars.optional_missing  -> has_default (the prompt's trusted-by-design
                                                 bucket, which also absorbs
                                                 dev-opt-in flags — see the note)
      cli_tools.required[].name  -> cli_tools
      infra_services             -> infra_services (agent-reported, unprobeable)

    Note on has_default vs dev_opt_in: the result schema has one field
    (optional_missing) for both, so a distilled contract cannot tell them apart.
    Both mean "absence is acceptable", which is all readiness needs. The
    distinction is kept in ENV_BUCKETS because a hand-edited contract SHOULD be
    able to record which it is — that is a human-legibility win, not a
    behavioural one.
    """
    env_vars = result.get("env_vars") or {}
    required = [
        str(v["name"])
        for v in (env_vars.get("required") or [])
        if isinstance(v, dict) and v.get("name")
    ]
    optional = [str(n) for n in (env_vars.get("optional_missing") or []) if n]
    tools = [
        str(t["name"])
        for t in ((result.get("cli_tools") or {}).get("required") or [])
        if isinstance(t, dict) and t.get("name")
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "source_sha256": fingerprint,
        "classified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "enforcer_sha": enforcer_sha,
        "env_vars": {
            MANDATORY: sorted(set(required)),
            HAS_DEFAULT: sorted(set(optional)),
            DEV_OPT_IN: [],
        },
        "cli_tools": sorted(set(tools)),
        "infra_services": result.get("infra_services") or {},
    }


def evaluate_contract(
    contract: "dict[str, Any]", project: "str | Path"
) -> "dict[str, Any]":
    """Measure the live environment against a stored classification.

    Returns an env_check_result.json-shaped dict whose `ready` is COMPUTED:

        ready = every mandatory env var is exported
            AND every required CLI tool resolves

    has_default / dev_opt_in vars are probed and reported for visibility but
    never gate readiness — their absence is the documented, intended state.
    infra_services are carried through unprobed (the framework cannot reliably
    reach a DB or docker daemon from here), matching the pre-existing trust
    boundary in _verify_env_check_claims.
    """
    from core.quality_gate.env_verify import probe_cli_tools, probe_env_var

    env_section = contract.get("env_vars") or {}
    mandatory = [str(n) for n in (env_section.get(MANDATORY) or [])]
    optional = [
        str(n)
        for bucket in (HAS_DEFAULT, DEV_OPT_IN)
        for n in (env_section.get(bucket) or [])
    ]
    tools = [str(t) for t in (contract.get("cli_tools") or [])]

    tool_results = probe_cli_tools(tools, Path(project))
    env_present = {n: probe_env_var(n) for n in mandatory}
    tool_present = {t: bool(tool_results.get(t, False)) for t in tools}
    required_env = [{"name": n, "present": p} for n, p in env_present.items()]
    missing_optional = [n for n in optional if not probe_env_var(n)]
    required_tools = [{"name": t, "present": p} for t, p in tool_present.items()]

    ready = all(env_present.values()) and all(tool_present.values())
    unmet: list[str] = [n for n, p in env_present.items() if not p]
    unmet += [t for t, p in tool_present.items() if not p]
    return {
        "ready": ready,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "env_vars": {"required": required_env, "optional_missing": missing_optional},
        "cli_tools": {"required": required_tools},
        "infra_services": contract.get("infra_services") or {},
        "summary": (
            "environment ready (verified against .methodology/env_contract.json)"
            if ready
            else "missing required item(s): " + ", ".join(unmet)
        ),
        "verified_from_contract": True,
    }
