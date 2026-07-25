"""Round 12 站2c — dispatch-determinism registry for the 8 workflow files.

Every `agent()` dispatch in .claude/workflows/*.js is classified by WHAT
THE LLM CONTRIBUTES and by WHERE THE VERDICT COMES FROM. The registry is
the machine-checked SSOT; docs/WORKFLOW_ALIGNMENT_AUDIT.md renders the
human-readable section from the same data (same pattern as Round 11's
RUNTIME_ONLY registry).

Classes (`cls`):
  carrier  — the agent runs a FIXED command and transports its output;
             the LLM contributes no judgment. These cannot be "sunk"
             further: the dynamic-workflow runtime has no direct exec
             API (playbook §4 — no child_process/fs/process), so a Bash
             sub-agent is the only way for workflow JS to reach a
             deterministic tool. The carrier IS the sunk form.
  judgment — the LLM's output is the actual work product (writing docs,
             fixing code, scoring dimensions, reviewing).
  mixed    — fixed command skeleton + the agent reacts to tool output
             (fix-on-BLOCKED loops, retry orchestration).

Verdict sources (`verdict`), strongest to weakest:
  js-regex   — workflow JS regex/startsWith on a CANONICAL string printed
               by a deterministic tool (Python print → same bytes).
               Hallucination requires rewriting echoed stdout.
  schema     — AJV-validated StructuredOutput transcription of tool
               output. Hallucination = mistyping a field (Bug #122 class).
  text-token — workflow JS regex on the LLM's OWN prose (e.g.
               /SYNC:\\s*PASS/). Weakest: the LLM can simply write the
               token. Every text-token row is a hardening candidate,
               listed in the audit doc.
  none       — fire-and-report; no gate on the response.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.workflow_audit.extract import extract_js_agent_labels  # noqa: E402

PHASE_FILES = sorted((REPO / ".claude" / "workflows").glob("phase*.js"))

# (label-literal regex, cls, verdict, note) — first match wins.
DISPATCH_REGISTRY: list[tuple[str, str, str, str]] = [
    # ── carriers: fixed command + canonical-string / schema transport ──
    (r"^resolve-repo$", "carrier", "js-regex",
     "fixed walk-up one-liner; JS regex-parses REPO=<abs path>"),
    (r"^env-check$", "carrier", "schema",
     "run-env-check && finalize-env-check; RC= line transcribed to rc"),
    (r"^ctx-check-$", "carrier", "schema",
     "python json.load probe prints FILE_OK_<size>; pass transcribed"),
    (r"^ctx-regen-$", "carrier", "none",
     "load-context regen; next ctx-check round is the gate"),
    (r"^load-ctx-a$", "carrier", "schema",
     "python prints fr_ids JSON; transcribed into CTX_SCHEMA"),
    (r"^gate1-verify-$", "carrier", "js-regex",
     "verify_gate1_qc.py canonical stdout; 站2a: verdict from echoed "
     "stdout ONLY (schema pass ignored — wf_53d055ce-d0b class closed)"),
    (r"^(gate2|gate3|gate4)-verify-r$", "carrier", "schema",
     "state.json last_gate_ok (>= this gate, proves finalize-gate's Phase "
     "Truth check passed — manifest quality_complete alone is set before "
     "that check runs and never reverts) + D4 rc transcribed "
     "(GATE_VERIFY_SCHEMA)"),
    (r"^advance-verify-r$", "carrier", "schema",
     "state.json current_phase printed as JSON, transcribed (PHASE_SCHEMA)"),
    (r"^p8-verify-r$", "carrier", "schema",
     "git log --grep=P8 presence transcribed (VERDICT_SCHEMA)"),
    (r"^aci-verify$", "carrier", "schema",
     "check-artifact-consistency rc transcription"),
    (r"^aci-post-sab$", "carrier", "text-token",
     "includes('OK') on echoed [check-artifact-consistency] OK line — "
     "canonical-ish but matched in the LLM's own reply text"),
    (r"^sbr-$", "carrier", "js-regex",
     "structured_b_review.py writes JSON; agent cats it; extractLastJson"),
    (r"^(persist-|write-approval-)$", "carrier", "js-regex",
     "write-approval CLI; /\\[write-approval\\]\\s*OK/ on echoed stdout"),
    (r"^loadpy-$", "carrier", "js-regex",
     "read-file CLI + cat; length/--expect-prefix anchors validated in JS"),
    (r"^legal-artifacts$", "carrier", "js-regex",
     "print-legal-artifacts CLI output echoed verbatim"),
    (r"^gate1-precheck$", "carrier", "schema",
     "already-passed FR list transcribed (FR_LIST_SCHEMA)"),
    (r"^(gate2|gate3|gate4)-precheck$", "carrier", "schema",
     "state.json last_gate >= this gate transcribed (VERDICT_SCHEMA); skips "
     "the round loop only when finalize-gate already fully finalized this "
     "gate (Phase Truth included), not merely SSI-scored"),
    # ── judgment: the LLM output IS the work product ──
    (r"^a-$", "judgment", "none",
     "A agents author deliverable content (SRS/SAD/ADR/TEST_SPEC…)"),
    (r"^b-$", "judgment", "js-regex",
     "B review JSON extracted+validated via structured_b_review CLI"),
    (r"^(peer-b-r|peer-review-r)$", "judgment", "js-regex",
     "holistic peer review; balanced-JSON parse + field validation"),
    (r"^peer-fix-r$", "judgment", "none",
     "fixer applies peer-review gaps; next review round is the gate"),
    (r"^tdd-$", "judgment", "js-regex",
     "drives run-fr-step TDD chain; gate1-verify carrier is the verdict"),
    (r"^delta-$", "judgment", "js-regex",
     "GATE1-DELTA orchestration (backgrounded); gate1-verify is the verdict"),
    (r"^delta-fastpath$", "judgment", "schema",
     "classification of FRs into pass/fail lists (DELTA_FAST_SCHEMA); "
     "misclassification self-corrects — fails fall into the full loop"),
    (r"^(gate2-r|gate3-r|gate4-r)$", "judgment", "text-token",
     "gate orchestrator scores dims inline + fixes; its prose is "
     "narrative only — the -verify-r carrier is authoritative"),
    (r"^(test-plan|coverage|bug-hunt|config-docs|archive|release-docs|"
     r"risk-docs|verification-docs|sab-generation|deferred-fixes|"
     r"cleanup-r)$", "judgment", "text-token",
     "doc/artifact authoring steps; SAB/ACI/etc. verdicts come from "
     "follow-up carrier or CLI checks where they exist"),
    # ── mixed: fixed command skeleton + fix-on-output loops ──
    (r"^(preflight|preflight-|preflight-a)$", "mixed", "text-token",
     "runs fixed command menu, fixes what run-phase reports; P3-8 use "
     "VERDICT_SCHEMA (schema), P1/P2 use /PREFLIGHT:\\s*PASS/ prose"),
    (r"^(constitution-|constitution-adr)$", "mixed", "text-token",
     "check-constitution + content fixes; /CONSTITUTION:\\s*PASS/ prose"),
    (r"^forward-ref-check$", "mixed", "text-token",
     "check-artifact-consistency forward_refs + fixes; /FWDREF:\\s*PASS/"),
    (r"^(advance|advance-r)$", "mixed", "text-token",
     "advance-phase + fix-on-BLOCKED; advance-verify-r carrier is the "
     "authoritative gate — prose token is narrative"),
    (r"^(push-|final-push-r)$", "mixed", "text-token",
     "push-milestone/checkpoint + fix-on-BLOCKED; p8-verify/git-log "
     "carriers authoritative where present"),
    (r"^milestone-", "mixed", "text-token",
     "push-milestone variants; GUARD step makes re-runs idempotent"),
    (r"^(sync|sync-retry|sync-push|sync-handover-note)$", "mixed", "text-token",
     "git push origin main; /SYNC:\\s*PASS/ prose — hardening candidate "
     "(a git-log proxy carrier would be canonical)"),
    (r"^orch-post-$", "mixed", "none",
     "spec-coverage-check + amend-sab fire-and-report (40% advisory floor)"),
    (r"^artifacts-commit$", "carrier", "none",
     "single fixed git add+commit command (|| true); 'nothing to commit' "
     "is a valid outcome, so no verdict gate by design"),
    (r"^tag-advance-r$", "mixed", "text-token",
     "phase6 tag + advance-phase + fix-on-BLOCKED; advance-verify-r "
     "carrier is the authoritative gate"),
]

_COMPILED = [(re.compile(pat), cls, verdict, note)
             for pat, cls, verdict, note in DISPATCH_REGISTRY]


def classify(label: str) -> tuple[str, str, str] | None:
    for rx, cls, verdict, note in _COMPILED:
        if rx.search(label):
            return cls, verdict, note
    return None


def _all_labels() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for f in PHASE_FILES:
        for lb in extract_js_agent_labels(f.read_text(encoding="utf-8")):
            # dynamic doc-name labels in phase1's peer loop (e.g.
            # '01-requirements/SRS.md (APPROVED)') are loadpy cache keys
            # reused as labels — classify them with the loadpy carrier.
            key = "loadpy-" if lb.endswith("(APPROVED)") else lb
            out.setdefault(key, set()).add(f.name)
    return out


def test_every_dispatch_label_is_classified():
    unclassified = {lb: sorted(files) for lb, files in _all_labels().items()
                    if classify(lb) is None}
    assert not unclassified, (
        "agent() labels missing from DISPATCH_REGISTRY (new dispatch added "
        f"without classifying its determinism): {unclassified}"
    )


def test_no_orphan_registry_patterns():
    labels = list(_all_labels())
    orphans = [rx.pattern for rx, *_ in _COMPILED
               if not any(rx.search(lb) for lb in labels)]
    assert not orphans, (
        f"registry patterns matching no live label (stale entries): {orphans}"
    )


def test_carriers_do_not_gate_on_llm_prose():
    """A carrier's whole point is that the LLM contributes no judgment —
    its verdict must come from a canonical string or schema transcription,
    with the two documented grandfathered exceptions."""
    grandfathered = {r"^aci-post-sab$"}
    offenders = [rx.pattern for rx, cls, verdict, _ in _COMPILED
                 if cls == "carrier" and verdict == "text-token"
                 and rx.pattern not in grandfathered]
    assert not offenders, (
        f"carrier dispatches gating on LLM prose (harden or reclassify): {offenders}"
    )
