"""No dimension's threshold may be waived (Round 21 → Round 38).

Round 21 built this module to fix an *ordering* mistake: ``_collect_da_waivers``
(cli/gate_cmds.py) decided both "is this waiver permitted?" and "is this waiver
needed?" at gate-prerequisite time, which is *before* ``finalize_gate`` runs the
framework's own independent CRG pass. The only dimension a waiver has ever
targeted — ``architecture`` — is CRG-only: the agent does not score it and
writes JSON ``null``, so the necessity check compared the gate's threshold
against a value that did not exist yet.

Round 38 removed the question rather than re-timing it, on two measurements.

**The waiver reached one enforcer out of three.** ``cmd_crg_arch_check`` — which
CI runs on every push from phase 3 (job "CRG Architecture Gate (P3+)") and which
the workflow JS ANDs into ``gate{N}Pass`` — has no waiver logic at all. So a
granted waiver produced a local PASS and a red build, and the gate loop then
spent its three rounds on a remedy that could not satisfy the check the same
framework was running. The framework's prescribed fix could not clear the
framework's own gate.

**The one waiver ever granted rested on a premise the measurement invented.**
taskq-renew's ``gate4_result.json`` carried ``da_waiver: {"architecture": true}``
with evidence naming the communities ``storage-load-sub1`` and ``sub2``. Those
exist only in the truncated 11-of-47-file graph Round 37 diagnosed; the correct
47-file graph has no such communities at all. (taskq's P6, the case Round 21 was
written for, was the same shape from the other side: its waiver claimed CRG
scored the tree 0 while the framework's own run scored it 100.0.)

What replaces it is calibration, not exemption: ``crg_excludes`` and
``crg_cohesion_healthy`` in ``.methodology/harness_config.json``. That file is
committed, so **every** enforcer reads it — which is the whole difference. A
waiver is visible to one judge; a calibration is visible to all of them. And a
genuinely oversized community (taskq-renew's ``storage-parser``, 97 members) is
deliberately not calibratable: that one is a finding, not an artifact.

Requests are still detected and still refused, with a message pointing at
calibration — see ``cli/gate_cmds.py::_collect_da_waivers``. Silently ignoring
an agent-written ``da_waiver`` would be worse than rejecting it.
"""

from __future__ import annotations

__all__ = ["CRG_ONLY_DIMENSIONS", "WAIVABLE_DIMENSIONS"]

# Dimensions scored by the framework's own independent CRG run
# (harness/crg_independent.py), never by the agent. Still live: the CRG override
# path in harness/harness_bridge.py uses it to decide which dimension it owns.
CRG_ONLY_DIMENSIONS: frozenset[str] = frozenset({"architecture"})

# Dimensions a DA waiver may target. Empty, and kept as a named empty set rather
# than deleted, because `_collect_da_waivers` still checks membership to refuse
# a request — and because a future reader asking "can anything be waived?"
# deserves this docstring rather than silence.
WAIVABLE_DIMENSIONS: frozenset[str] = frozenset()
