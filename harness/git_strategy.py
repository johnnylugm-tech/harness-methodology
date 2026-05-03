"""
harness/git_strategy.py — Gate-aligned Git commit + push strategy.

5-Push Gate-Aligned Strategy
─────────────────────────────
  PUSH ① — P2 exit: quality_manifest.json created (human checkpoint)
  PUSH ② — Gate 2 PASS (P3 exit, score ≥75): all FRs implemented
  PUSH ③ — Gate 3 PASS (P4 exit, score ≥80): full test suite
  PUSH ④ — P5 BASELINE.md (lightweight; auto-committed when present)
  PUSH ⑤ — Gate 4 APPROVE (P6 full, score ≥85) + git tag gate4-YYYYMMDD-scoreXX
  PUSH ⑥ — P7+P8 completion: risk register + config records

Local commit policy (never pushes)
────────────────────────────────────
  COMMIT per FR — each Gate 1 PASS triggers a local commit (no push).
  This preserves per-FR traceability without creating CI noise.

Disable
────────
  Pass `--no-git` to any harness_cli.py command, or set
  `HARNESS_NO_GIT=1` in the environment.

Git failures are warnings — they never block the pipeline.
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# Harness runtime artifacts that pollute git history
_GITIGNORE_ENTRIES: list[str] = [
    ".sessi-work/",
    ".methodology/last_block.md",
    ".methodology/steering_history.json",
]

_TAG_PREFIX = "gate4"


class GitStrategy:
    """
    Gate-aligned Git strategy injected at gate pass / phase completion points.

    All public methods are no-ops when ``enabled=False`` and log-only warnings
    when git operations fail — the pipeline is never blocked by git errors.
    """

    def __init__(self, project: Path, enabled: bool = True, push: bool = True):
        """
        Args:
            project: Absolute path to the project root (must be a git repo).
            enabled: False disables all operations (--no-git flag).
            push:    False commits but never pushes (--no-push flag, future use).
        """
        self.project = project
        self.enabled = enabled and not bool(os.environ.get("HARNESS_NO_GIT"))
        self.push = push

    # ── Public interface ─────────────────────────────────────────────────────

    def ensure_gitignore(self) -> None:
        """Add harness runtime artifacts to .gitignore if not already present."""
        if not self.enabled:
            return
        gi = self.project / ".gitignore"
        existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
        additions = [e for e in _GITIGNORE_ENTRIES if e not in existing]
        if not additions:
            return
        with gi.open("a", encoding="utf-8") as f:
            f.write("\n# harness-methodology runtime (auto-added by git_strategy)\n")
            f.write("\n".join(additions) + "\n")
        print(f"  [git] .gitignore updated: {additions}")

    def commit_fr_gate1(self, fr_id: str, score: float, phase: int) -> bool:
        """
        Local commit after FR Gate 1 PASS.  **Never pushes.**

        Returns True if commit succeeded or if there was nothing to commit.
        """
        if not self.enabled:
            return True
        msg = f"feat({fr_id}): Gate1 PASS — score={score:.1f} [phase={phase}]"
        return self._commit(msg)

    def commit_and_push_p2(self, fr_ids: list[str]) -> bool:
        """
        Commit + push at P2 exit (quality_manifest.json generated).  PUSH ①
        """
        if not self.enabled:
            return True
        fr_list = ",".join(fr_ids[:5])
        if len(fr_ids) > 5:
            fr_list += f",…+{len(fr_ids) - 5}"
        msg = f"docs(P2): finalize SRS + SAD; generate quality manifest [fr_ids={fr_list}]"
        return self._commit_and_push(msg)

    def commit_and_push_gate(
        self, gate_num: int, phase: int, score: float, n_frs: int = 0
    ) -> bool:
        """
        Commit + push after Gate 2/3/4 PASS. Tags HEAD at Gate 4.  PUSH ②③⑤

        Args:
            gate_num: 2, 3, or 4.
            phase:    Current pipeline phase (used only for Gate 4 label fallback).
            score:    Composite gate score.
            n_frs:    FR count (Gate 2 suffix only).
        """
        if not self.enabled:
            return True
        label = {2: "feat", 3: "test", 4: "release"}.get(gate_num, "chore")
        phase_label = {2: "P3", 3: "P4", 4: "P6"}.get(gate_num, f"P{phase}")
        suffix = {
            2: f"— {n_frs} FR(s) implemented" if n_frs else "",
            3: "— full test suite",
            4: "— pipeline complete",
        }.get(gate_num, "")
        msg = f"{label}({phase_label}): Gate{gate_num} PASS score={score:.1f} {suffix}".rstrip()
        ok = self._commit_and_push(msg)
        if ok and gate_num == 4:
            self._tag_release(score)
        return ok

    def commit_and_push_final(self, phases: list[int]) -> bool:
        """
        Commit + push at P7+P8 completion.  PUSH ⑥

        Args:
            phases: List of completed late phases, e.g. [7, 8] or [7].
        """
        if not self.enabled:
            return True
        phase_str = "+".join(f"P{p}" for p in sorted(phases))
        msg = f"docs({phase_str}): risk register + config records"
        return self._commit_and_push(msg)

    # ── Private helpers ──────────────────────────────────────────────────────

    def _has_changes(self) -> bool:
        """Return True if there are any staged or unstaged changes."""
        r = self._run_git("status", "--porcelain")
        return bool(r.stdout.strip())

    def _commit(self, message: str) -> bool:
        """Stage all changes and commit. Returns True on success or nothing-to-commit."""
        if not self._has_changes():
            print("  [git] nothing to commit — skip")
            return True
        r1 = self._run_git("add", "-A")
        if r1.returncode != 0:
            print(f"  [git WARN] git add failed: {r1.stderr[:200]}")
            return False
        r2 = self._run_git("commit", "-m", message)
        if r2.returncode != 0:
            print(f"  [git WARN] git commit failed: {r2.stderr[:200]}")
            return False
        sha = self._run_git("rev-parse", "--short", "HEAD").stdout.strip()
        print(f"  [git] committed {sha}: {message[:72]}")
        return True

    def _commit_and_push(self, message: str) -> bool:
        if not self._commit(message):
            return False
        if not self.push:
            print("  [git] push skipped (push=False)")
            return True
        r = self._run_git("push")
        if r.returncode != 0:
            print(f"  [git WARN] git push failed: {r.stderr[:200]}")
            return False
        print("  [git] pushed → remote")
        return True

    def _tag_release(self, score: float) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d")
        tag = f"{_TAG_PREFIX}-{ts}-score{int(score)}"
        r = self._run_git("tag", tag)
        if r.returncode == 0:
            if self.push:
                self._run_git("push", "origin", tag)
            print(f"  [git] tagged: {tag}")
        else:
            print(f"  [git WARN] tagging failed: {r.stderr[:200]}")

    def _run_git(self, *args: str) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                ["git", *args],
                capture_output=True, text=True,
                cwd=str(self.project), timeout=60, check=False,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            class _Fake:  # noqa: E306
                returncode = 1
                stdout = ""
                stderr = str(exc)
            return _Fake()  # type: ignore[return-value]
