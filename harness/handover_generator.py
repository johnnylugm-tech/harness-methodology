"""
harness/handover_generator.py — Checkpoint handover document generator.

Writes HANDOVER.md at the project root before each gate-aligned push so that
a new Claude session can read the file from GitHub and continue without any
context loss.  Call ``HandoverGenerator.write()`` before every push.

Example::

    gen = HandoverGenerator(Path("/path/to/project"))
    gen.write(
        checkpoint_id="P3-pre-ssi-20260504",
        phase=3,
        task_background="Implementing harness-methodology FR-001..FR-012.",
        current_status="12/12 FRs Gate 1 PASS. SSI not yet executed.",
        next_steps=[
            "Run SSI 3 rounds (Gate 2 target ≥75)",
            "Fix any Gate 2 failures between rounds",
        ],
        notes=["100% follow SKILL.md"],
    )
"""
from __future__ import annotations

import json
import subprocess  # nosec B404
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PHASE_NAMES: dict[int, str] = {
    1: "Spec & Discovery",
    2: "Architecture & Design",
    3: "Implementation",
    4: "Testing",
    5: "Review Baseline",
    6: "Full Review / Gate 4",
    7: "Risk Register",
    8: "Config & Records",
}

#: Default notes prepended to every handover document.
DEFAULT_NOTES: list[str] = [
    "100% follow SKILL.md",
    "Do NOT commit `.sessi-work/` or `.methodology/` runtime artifacts",
    "Git failures are warnings — they never block the pipeline",
]

_COMPACT_NOTICE = (
    "> ⚠️  **開始下一個工作階段前，請先執行 `/compact` 壓縮上下文**，"
    "再從「接下來的工作」繼續。"
)

_FOOTER = (
    "---\n"
    "*由 `HandoverGenerator` 自動生成。下次 push 時此檔案將被覆寫。*\n"
)


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class HandoverGenerator:
    """
    Renders and writes ``HANDOVER.md`` to a project root directory.

    All parameters to :meth:`write` can be overridden by callers; sensible
    defaults are provided so the minimum viable call only requires the four
    mandatory fields.

    Parameters
    ----------
    project:
        Absolute path to the project root (must be a real directory).
    """

    def __init__(self, project: Path) -> None:
        self.project = project

    # ------------------------------------------------------------------
    # Git metadata helpers
    # ------------------------------------------------------------------

    def _git_remote(self) -> str:
        """Return origin remote URL, or empty string on failure."""
        try:
            result = subprocess.run(  # nosec B603 B607
                ["git", "-C", str(self.project), "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:  # pylint: disable=broad-exception-caught
            return ""

    def _git_branch(self) -> str:
        """Return current branch name, or empty string on failure."""
        try:
            result = subprocess.run(  # nosec B603 B607
                ["git", "-C", str(self.project), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:  # pylint: disable=broad-exception-caught
            return ""

    def _git_sha(self) -> str:
        """Return last commit short SHA, or empty string on failure."""
        try:
            result = subprocess.run(  # nosec B603 B607
                ["git", "-C", str(self.project), "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:  # pylint: disable=broad-exception-caught
            return ""

    def _state_snapshot(self) -> str:
        """Return condensed state.json content, or empty string if missing."""
        state_path = self.project / ".methodology" / "state.json"
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            parts = [
                f"phase={data.get('current_phase', '?')}",
                f"state={data.get('state', '?')}",
            ]
            last_gate = data.get("last_gate")
            last_fr = data.get("last_fr")
            if last_gate is not None:
                parts.append(f"last_gate={last_gate}")
            if last_fr is not None:
                parts.append(f"last_fr={last_fr}")
            return " ".join(parts)
        except Exception:  # pylint: disable=broad-exception-caught
            return ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(
        self,
        checkpoint_id: str,
        phase: int,
        task_background: str,
        current_status: str,
        next_steps: list[str],
        notes: list[str] | None = None,
        extra: dict[str, str] | None = None,
        plan_override: str | None = None,
        deliverables: list[str] | None = None,
    ) -> Path:
        """
        Render the handover document and write it to ``<project>/HANDOVER.md``.

        Parameters
        ----------
        checkpoint_id:
            Human-readable checkpoint label, e.g. ``"P3-pre-ssi-20260504"``.
        phase:
            Current pipeline phase number (1–8).
        task_background:
            1–3 sentences describing the overall task and its purpose.
        current_status:
            Dynamic description of what has been completed so far (scores,
            FR counts, gate results, etc.).
        next_steps:
            Ordered list of actions for the next session to take.
        notes:
            Extra warnings or constraints.  ``DEFAULT_NOTES`` are always
            prepended; pass ``[]`` to suppress caller-specific notes.
        extra:
            Optional key/value pairs rendered as an "附加資訊" section
            (e.g. ``{"gate2_score": "76.4", "fr_count": "12"}``).
        deliverables:
            Optional list of deliverable lines (e.g. ``["`SRS.md` ✅ (312L)", ...]``)
            rendered as a "交付物清單" section for easy handover verification.

        Returns
        -------
        Path
            Path of the written file (``<project>/HANDOVER.md``).
        """
        all_notes = list(DEFAULT_NOTES) + list(notes or [])
        plan_path = plan_override or f".methodology/phase{phase}_plan.md"
        # Gather git metadata at write-time for maximum accuracy.
        # SHA is captured pre-commit; use `git log --oneline -3` for latest.
        git_info = {
            "remote": self._git_remote(),
            "branch": self._git_branch(),
            "state": self._state_snapshot(),
            "plan": plan_path,
        }
        content = self._render(
            checkpoint_id=checkpoint_id,
            phase=phase,
            task_background=task_background,
            current_status=current_status,
            next_steps=next_steps,
            notes=all_notes,
            extra=extra or {},
            git_info=git_info,
            deliverables=list(deliverables) if deliverables else [],
        )
        path = self.project / "HANDOVER.md"
        path.write_text(content, encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _render(
        self,
        checkpoint_id: str,
        phase: int,
        task_background: str,
        current_status: str,
        next_steps: list[str],
        notes: list[str],
        extra: dict[str, str],
        git_info: dict[str, str] | None = None,
        deliverables: list[str] | None = None,
    ) -> str:
        phase_name = _PHASE_NAMES.get(phase, f"Phase {phase}")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        steps_md = "\n".join(
            f"{i + 1}. {step}" for i, step in enumerate(next_steps)
        )
        notes_md = "\n".join(f"- {note}" for note in notes) if notes else "- (none)"

        extra_section = ""
        if extra:
            rows = "\n".join(f"- **{k}**: {v}" for k, v in extra.items())
            extra_section = f"\n## 附加資訊\n\n{rows}\n"

        deliverables_section = ""
        if deliverables:
            items_md = "\n".join(f"- {d}" for d in deliverables)
            deliverables_section = f"\n## 交付物清單\n\n{items_md}\n\n"

        # Git recovery block — critical for new session clone + resume
        gi = git_info or {}
        remote = gi.get("remote", "")
        branch = gi.get("branch", "")
        state = gi.get("state", "")
        plan = gi.get("plan", "")

        # Derive bare repo name for the cd command
        _repo_name = (remote.rstrip("/").split("/")[-1].removesuffix(".git")
                      if remote else "project")

        # Extract real HERMES value from extra dict if present so the startup
        # snippet shows the actual target instead of a generic <value> placeholder.
        _hermes_entry = extra.get("HERMES_REVIEWER_TARGET", "")
        if _hermes_entry.startswith("✅ set (") and _hermes_entry.endswith(")"):
            _hermes_val = _hermes_entry[len("✅ set ("):-1]
        else:
            _hermes_val = "<value>"   # not set or unknown format

        # Three-step startup sequence — visible to a new session immediately
        _next_phase = phase + 1
        resume_section = (
            f"## ▶ 立即開始（三步）\n\n"
            f"```bash\n"
            f"# 1. Clone (if working directory cleared)\n"
            f"git clone --recurse-submodules {remote or '<repo-url>'} && cd {_repo_name}\n"
            f"\n"
            f"# 2. Set required env vars\n"
            f"export HERMES_REVIEWER_TARGET={_hermes_val}\n"
            f"\n"
            f"# 3. Read plan and start Phase {_next_phase}\n"
            f"cat {plan or f'.methodology/phase{_next_phase}_plan.md'}\n"
            f"# Follow SKILL.md §0.1 Phase {_next_phase} entry check, then execute\n"
            f"```\n"
        )

        git_section = (
            f"## 快速接手指令（詳細）\n\n"
            f"```bash\n"
            f"# Clone (--recurse-submodules required for harness submodule)\n"
            f"git clone --recurse-submodules {remote or '<repo-url>'} /tmp/{_repo_name} "
            f"&& cd /tmp/{_repo_name}\n"
            f"\n"
            f"# Confirm latest commits\n"
            f"git log --oneline -3\n"
            f"\n"
            f"# Confirm FSM state\n"
            f"cat .methodology/state.json   "
            f"# expected: {state or 'phase=? state=?'}\n"
            f"\n"
            f"# Read active plan\n"
            f"cat {plan or '.methodology/phaseN_plan.md'}\n"
            f"```\n\n"
            f"| 欄位 | 值 |\n"
            f"|------|----|\n"
            f"| Remote | `{remote or '(unknown)'}` |\n"
            f"| Branch | `{branch or '(unknown)'}` |\n"
            f"| State | `{state or '(unknown)'}` |\n"
            f"| Plan | `{plan or '(unknown)'}` |\n"
        )

        return (
            f"# Harness Methodology — Session Handover\n\n"
            f"**Checkpoint**: `{checkpoint_id}`  \n"
            f"**Phase**: P{phase} — {phase_name}  \n"
            f"**Generated**: {ts}\n\n"
            f"{_COMPACT_NOTICE}\n\n"
            f"---\n\n"
            f"{resume_section}\n"
            f"---\n\n"
            f"{git_section}\n"
            f"---\n\n"
            f"## 任務背景\n\n"
            f"{task_background}\n\n"
            f"{deliverables_section}"
            f"## 目前執行狀況\n\n"
            f"{current_status}\n\n"
            f"## 接下來的工作\n\n"
            f"{steps_md}\n\n"
            f"## 注意事項\n\n"
            f"{notes_md}\n"
            f"{extra_section}\n"
            f"{_FOOTER}"
        )
