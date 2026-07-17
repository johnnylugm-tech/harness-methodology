"""Renderers shared across every `generate_phaseN()` in the `spec_phase*`
modules (Round 15 station1 — extracted from the former monolithic
phase_specs.py). `_render_meta` is the only genuinely cross-phase renderer;
grep-verified call counts confirmed every other former phase_specs.py
renderer/constant is referenced exactly once and stayed with its owning
phase module — see docs/PROPOSAL_ADJUDICATIONS.md's Round 15 entry and the
station1 commit message for the verification method.
"""
from __future__ import annotations


def _render_meta(*, name: str, description: str, phases: list[str]) -> str:
    lines = ["export const meta = {", f"  name: '{name}',"]
    lines.append(f"  description: '{description}',")
    lines.append("  phases: [")
    lines.extend(f"    {{ title: '{t}' }}," for t in phases)
    lines.append("  ],")
    lines.append("}")
    return "\n".join(lines) + "\n"
