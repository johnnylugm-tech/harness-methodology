"""CLAUDE.md auto-section maintenance for target projects.

Moved verbatim from harness_cli.py (絞殺者續章 S4d): builds the
harness-managed auto block between the CLAUDE_AUTO_START/END markers,
rewrites it in place, and (best-effort) LLM-cleans stale phase/gate
status from manual content. PHASE_NAMES is the short-label payload map
(deliberately shorter than core.phase_topology's official names — the
topology anchor test pins its KEY SET only).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

__all__ = [
    "CLAUDE_AUTO_START", "CLAUDE_AUTO_END", "PHASE_NAMES", "STALE_HARNESS_RE",
    "build_claude_md_auto_section", "update_claude_md", "llm_clean_stale_claude_md",
]


CLAUDE_AUTO_START = "<!-- harness:auto-start -->"
CLAUDE_AUTO_END   = "<!-- harness:auto-end -->"

PHASE_NAMES = {
    1: "Requirements", 2: "Architecture", 3: "Implementation",
    4: "Testing", 5: "Verification", 6: "Quality", 7: "Risk", 8: "Config Management",
    9: "Maintenance",
}


def build_claude_md_auto_section(project_path: Path) -> str:
    """Build the harness status markdown block from state.json + quality_manifest.json.

    Gracefully degrades: missing files → empty dicts → "Not Started" placeholders.
    """
    from datetime import datetime, timezone as _tz

    manifest: dict = {}
    state: dict = {}
    for fpath, store_key in (
        (project_path / ".methodology" / "quality_manifest.json", "manifest"),
        (project_path / ".methodology" / "state.json", "state"),
    ):
        if fpath.exists():
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
                if store_key == "manifest":
                    manifest = data
                else:
                    state = data
            except Exception:  # pylint: disable=broad-exception-caught
                pass

    current_phase = state.get("current_phase", 1)
    phase_name = PHASE_NAMES.get(current_phase, f"Phase {current_phase}")
    last_gate = state.get("last_gate", "—")
    last_fr_str = f" | Last FR: {state['last_fr']}" if state.get("last_fr") else ""
    updated = datetime.now(_tz.utc).strftime("%Y-%m-%d")

    gates = manifest.get("gate_results", {})
    fr_ids: list = manifest.get("fr_ids", [])

    # Gate progress rows (G1 shows done/total FRs; G2-G4 show numeric score)
    gate_rows: list[str] = []
    for gn in (1, 2, 3, 4):
        g = gates.get(f"gate{gn}")
        if isinstance(g, dict) and "score" in g:
            score_str = f"{g['score']:.1f}"
            status = "✅ PASS" if g.get("quality_complete") else "🔄 In Progress"
        elif isinstance(g, dict):
            # gate1: {FR-XX: {score, quality_complete, ...}}
            fr_vals = [v for v in g.values() if isinstance(v, dict) and "score" in v]
            done = sum(1 for v in fr_vals if v.get("quality_complete"))
            total = len(fr_ids) if fr_ids else len(fr_vals)
            score_str = f"{done}/{total} FRs" if total else "—"
            status = "✅ PASS" if (total and done == total) else "🔄 In Progress"
        else:
            score_str, status = "—", "⬜ Not Started"
        gate_rows.append(f"| Gate {gn} | {score_str} | {status} |")

    gate_table = "\n".join(gate_rows)

    # FR Registry rows (from gate1 results)
    gate1 = gates.get("gate1")
    fr_rows: list[str] = []
    if fr_ids:
        for fr_id in fr_ids:
            r = gate1.get(fr_id) if isinstance(gate1, dict) else None
            if isinstance(r, dict) and "score" in r:
                fr_score = f"{r['score']:.1f}"
                fr_status = "✅ COMPLETE" if r.get("quality_complete") else "🔄 In Progress"
            else:
                fr_score, fr_status = "—", "⬜ Pending"
            fr_rows.append(f"| {fr_id} | {fr_score} | {fr_status} |")
    fr_table_body = ("\n".join(fr_rows)
                     if fr_rows else "| — | — | No FRs registered yet |")

    # Optional sections (only when non-empty)
    extra_sections = ""
    arch = manifest.get("architecture_constraints", [])
    if arch:
        items = "\n".join(f"- {c}" for c in arch)
        extra_sections += f"\n### Architecture Constraints\n{items}\n"
    high_risk = manifest.get("high_risk_modules", [])
    if high_risk:
        items = "\n".join(f"- {m}" for m in high_risk)
        extra_sections += f"\n### High-Risk Modules\n{items}\n"
    nfr_map = manifest.get("nfr_dimension_mapping", {})
    if nfr_map:
        items = "\n".join(f"- {k} → {v}" for k, v in nfr_map.items())
        extra_sections += f"\n### NFR → Dimension Mapping\n{items}\n"

    return (
        f"## Harness Status _(auto-generated — do not edit this block)_\n\n"
        f"> Phase: **{current_phase} — {phase_name}**"
        f" | Last Gate: **Gate {last_gate}**{last_fr_str}"
        f" | Updated: {updated}\n\n"
        f"### Gate Progress\n"
        f"| Gate | Score / FRs | Status |\n"
        f"|------|-------------|--------|\n"
        f"{gate_table}\n\n"
        f"### FR Registry (Gate 1)\n"
        f"| FR ID | Score | Status |\n"
        f"|-------|-------|--------|\n"
        f"{fr_table_body}\n"
        f"{extra_sections}"
    )


def update_claude_md(project_path: Path) -> None:
    """Refresh the harness-managed block in project_path/CLAUDE.md (non-blocking).

    Called at: init-project, finalize-gate (pass), advance-phase.
    Replaces content between <!-- harness:auto-start/end --> markers.
    Preserves all content outside the markers (user customizations).
    Legacy CLAUDE.md without markers: auto block prepended, existing content kept.
    """
    try:
        auto = build_claude_md_auto_section(project_path)
        claude_path = project_path / "CLAUDE.md"

        if not claude_path.exists():
            claude_path.write_text(
                f"# Project: {project_path.name}\n\n"
                + CLAUDE_AUTO_START + "\n" + auto + CLAUDE_AUTO_END + "\n",
                encoding="utf-8",
            )
            return

        existing = claude_path.read_text(encoding="utf-8")
        if CLAUDE_AUTO_START in existing and CLAUDE_AUTO_END in existing:
            s = existing.index(CLAUDE_AUTO_START)
            e = existing.index(CLAUDE_AUTO_END) + len(CLAUDE_AUTO_END)
            new_content = (
                existing[:s]
                + CLAUDE_AUTO_START + "\n"
                + auto
                + CLAUDE_AUTO_END
                + existing[e:]
            )
        else:
            # Legacy CLAUDE.md: prepend auto block, keep all existing content
            new_content = (
                CLAUDE_AUTO_START + "\n"
                + auto
                + CLAUDE_AUTO_END + "\n\n"
                + existing
            )
        claude_path.write_text(new_content, encoding="utf-8")
    except Exception as _exc:  # pylint: disable=broad-exception-caught
        print(f"  [WARN] CLAUDE.md update skipped: {_exc}")


# Patterns that indicate stale harness phase/gate status in manual content.
# Deliberately narrow to avoid false-positives on architecture descriptions.
STALE_HARNESS_RE = re.compile(
    r"Current\s+state:.*Phase\s+\d"           # "Current state: Phase 7"
    r"|Working\s+in\s+Phase\s+\d"             # "Working in Phase 7+"
    r"|Gate\s+[1-4]\s+\(\d+\s+dimensions"     # "Gate 4 (14 dimensions..."
    r"|Gate\s+[1-4]\s+(?:PASS|FAIL)"          # "Gate 4 PASS"
    r"|score\s+\d+(?:\.\d+)?\s*\)",           # "score 96.5)"
    re.IGNORECASE,
)


def llm_clean_stale_claude_md(project_path: Path) -> None:
    """Remove stale harness phase/gate status text from CLAUDE.md via LLM.

    Called only on advance-phase (major milestone, acceptable 30-60s overhead).
    Pre-screens for stale patterns — skips LLM call when content is already clean.
    Non-blocking: any failure prints [WARN] and returns without modifying the file.
    """
    import shutil as _shutil
    from core.agent_spawner import _child_env as _agent_child_env
    try:
        claude_path = project_path / "CLAUDE.md"
        if not claude_path.exists():
            return

        content = claude_path.read_text(encoding="utf-8")

        # Extract content outside auto block for stale pattern detection
        if CLAUDE_AUTO_START in content and CLAUDE_AUTO_END in content:
            s = content.index(CLAUDE_AUTO_START)
            e = content.index(CLAUDE_AUTO_END) + len(CLAUDE_AUTO_END)
            outside = content[:s] + content[e:]
        else:
            outside = content

        # Pre-screen: skip LLM call if no stale harness patterns found
        if not STALE_HARNESS_RE.search(outside):
            return

        cli = _shutil.which("claude")
        if not cli:
            return  # claude CLI unavailable — skip silently

        prompt = (
            "Edit the following CLAUDE.md file. Rules:\n"
            "1. The block between <!-- harness:auto-start --> and "
            "<!-- harness:auto-end --> is auto-managed — preserve it EXACTLY as-is.\n"
            "2. Outside that block, remove or condense into a single short line "
            "any text that describes harness phase/gate status — e.g. "
            "'Current state: Phase 7', 'Gate 4 PASS (score 96.5)', "
            "'Working in Phase 7+', phase-specific task lists, "
            "gate dimension counts, completed gate result paths.\n"
            "3. Keep all architecture descriptions, commands, code blocks, "
            "and non-harness-status content exactly unchanged.\n"
            "4. Return ONLY the complete updated file content — "
            "no explanation, no markdown fencing.\n\n"
            f"File:\n{content}"
        )

        proc = subprocess.run(
            [
                cli, "-p", prompt,
                "--output-format", "text",
                "--setting-sources", "",
                "--disable-slash-commands",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--no-session-persistence",
            ],
            capture_output=True, text=True, timeout=60,
            cwd=str(project_path),
            env=_agent_child_env(),
        )

        if proc.returncode != 0:
            print(f"  [WARN] CLAUDE.md stale cleanup failed (exit {proc.returncode})")
            return

        cleaned = proc.stdout.strip()
        if not cleaned:
            print("  [WARN] CLAUDE.md stale cleanup: empty LLM output — skipping")
            return

        # Safety: auto block must survive the LLM edit intact
        if CLAUDE_AUTO_START not in cleaned or CLAUDE_AUTO_END not in cleaned:
            print("  [WARN] CLAUDE.md stale cleanup: LLM dropped auto markers — skipping")
            return

        claude_path.write_text(cleaned, encoding="utf-8")
        print("  [CLAUDE.md] Stale harness status cleaned")

    except subprocess.TimeoutExpired:
        print("  [WARN] CLAUDE.md stale cleanup timed out — skipping")
    except Exception as _exc:  # pylint: disable=broad-exception-caught
        print(f"  [WARN] CLAUDE.md stale cleanup skipped: {_exc}")
