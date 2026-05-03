"""
harness/git_strategy.py — Gate-aligned Git commit + push strategy.

10-Push Handover-Aware Strategy
────────────────────────────────
  PUSH ①  — P1 exit: SRS/SAD draft complete
  PUSH ②  — P2 exit: quality_manifest.json created (human checkpoint)
  PUSH ③  — P3 mid: FR Gate 1 PASS ≥ 50 % of total FRs
  PUSH ④  — P3 pre-SSI: all FRs done, SSI not yet executed
  PUSH ⑤  — Gate 2 PASS (P3 exit, score ≥75): all FRs implemented
  PUSH ⑥  — Gate 3 PASS (P4 exit, score ≥80): full test suite
  PUSH ⑦  — P5 BASELINE.md (lightweight; auto-committed when present)
  PUSH ⑧  — Gate 4 APPROVE (P6 full, score ≥85) + git tag gate4-YYYYMMDD-scoreXX
  PUSH ⑨  — P7 exit: risk register complete
  PUSH ⑩  — P8 exit: config records complete

Each push writes HANDOVER.md at the project root before committing.
HANDOVER.md tells the next Claude session what to do and prompts /compact.

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
import subprocess  # nosec B404
from datetime import datetime, timezone
from pathlib import Path

from harness.handover_generator import HandoverGenerator
from harness.fr_progress_tracker import FRProgressTracker

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

        Also persists progress to ``.methodology/fr_progress.json`` so a new
        session can resume P3 without re-running completed FRs.

        Returns True if commit succeeded or if there was nothing to commit.
        """
        if not self.enabled:
            return True
        try:
            FRProgressTracker(self.project, phase=phase).record_gate1_pass(
                fr_id, score=score, phase=phase
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"  [git WARN] FRProgressTracker update failed: {exc}")
        msg = f"feat({fr_id}): Gate1 PASS — score={score:.1f} [phase={phase}]"
        return self._commit(msg)

    # ── Push ① — P1 exit ────────────────────────────────────────────────────

    def commit_and_push_p1(
        self,
        fr_ids: list[str],
        background: str = "",
        notes: list[str] | None = None,
    ) -> bool:
        """
        Commit + push at P1 exit (SRS/SAD draft complete).  PUSH ①

        Args:
            fr_ids:     Functional requirement IDs captured in the SRS.
            background: Optional project context for HANDOVER.md.
            notes:      Extra notes appended after DEFAULT_NOTES.
        """
        if not self.enabled:
            return True
        fr_list = self._fr_summary(fr_ids)
        self._write_handover(
            checkpoint_id=self._cp("P1-exit"),
            phase=1,
            background=background or "P1 Spec & Discovery: SRS and SAD draft complete.",
            status=f"{len(fr_ids)} FR(s) defined in SRS [{fr_list}].",
            steps=[
                "Proceed to P2: Architecture & Design",
                "Generate quality_manifest.json from SRS",
                "Confirm FR traceability matrix",
            ],
            notes=notes,
        )
        msg = f"docs(P1): SRS + SAD draft; {len(fr_ids)} FR(s) [{fr_list}]"
        return self._commit_and_push(msg)

    # ── Push ② — P2 exit ────────────────────────────────────────────────────

    def commit_and_push_p2(
        self,
        fr_ids: list[str],
        background: str = "",
        notes: list[str] | None = None,
    ) -> bool:
        """
        Commit + push at P2 exit (quality_manifest.json generated).  PUSH ②
        """
        if not self.enabled:
            return True
        fr_list = self._fr_summary(fr_ids)
        self._write_handover(
            checkpoint_id=self._cp("P2-exit"),
            phase=2,
            background=background or "P2 Architecture & Design: quality_manifest.json generated.",
            status=f"{len(fr_ids)} FR(s) defined in quality manifest [{fr_list}].",
            steps=[
                "Proceed to P3: Implementation",
                "Implement each FR with TDD (Gate 1 target per FR)",
                "Push P3-mid checkpoint at ≥50 % FR Gate 1 PASS",
                "Push P3-pre-ssi checkpoint when all FRs done",
            ],
            notes=notes,
        )
        msg = f"docs(P2): finalize SRS + SAD; generate quality manifest [fr_ids={fr_list}]"
        return self._commit_and_push(msg)

    # ── Push ③ — P3 mid ─────────────────────────────────────────────────────

    def commit_and_push_p3_mid(
        self,
        fr_done: int,
        fr_total: int,
        fr_ids: list[str],
        background: str = "",
        notes: list[str] | None = None,
    ) -> bool:
        """
        Commit + push at P3 mid-point (FR Gate 1 PASS ≥ 50 %).  PUSH ③

        Args:
            fr_done:  Number of FRs that have Gate 1 PASS so far.
            fr_total: Total number of FRs in the project.
            fr_ids:   List of FR IDs with Gate 1 PASS.
            background: Optional project context.
            notes:    Extra notes.
        """
        if not self.enabled:
            return True
        fr_list = self._fr_summary(fr_ids)
        self._write_handover(
            checkpoint_id=self._cp("P3-mid"),
            phase=3,
            background=background or "P3 Implementation in progress (≥50 % milestone).",
            status=(
                f"{fr_done}/{fr_total} FRs Gate 1 PASS [{fr_list}]. "
                f"TDD cycles complete for passing FRs."
            ),
            steps=[
                f"Complete remaining {fr_total - fr_done} FR(s)",
                "Ensure each FR has passing unit tests (TDD)",
                "When all FRs done → call commit_and_push_p3_pre_ssi()",
            ],
            notes=notes,
            extra={"fr_done": str(fr_done), "fr_total": str(fr_total)},
        )
        msg = (
            f"feat(P3-mid): {fr_done}/{fr_total} FR(s) Gate1 PASS "
            f"[{fr_list}]"
        )
        return self._commit_and_push(msg)

    # ── Push ④ — P3 pre-SSI ─────────────────────────────────────────────────

    def commit_and_push_p3_pre_ssi(
        self,
        fr_ids: list[str],
        background: str = "",
        notes: list[str] | None = None,
    ) -> bool:
        """
        Commit + push when all FRs done but SSI has not yet run.  PUSH ④

        This is the last stable snapshot before SSI modifies files.

        Args:
            fr_ids:     All FR IDs (Gate 1 PASS).
            background: Optional project context.
            notes:      Extra notes.
        """
        if not self.enabled:
            return True
        fr_list = self._fr_summary(fr_ids)
        self._write_handover(
            checkpoint_id=self._cp("P3-pre-ssi"),
            phase=3,
            background=background or "P3 Implementation complete. SSI not yet executed.",
            status=(
                f"All {len(fr_ids)} FR(s) Gate 1 PASS [{fr_list}]. "
                "SSI 3-round quality cycle not yet started."
            ),
            steps=[
                "Run SSI 3 rounds (Gate 2 target score ≥ 75)",
                "Fix any failures between SSI rounds",
                "On Gate 2 PASS → call commit_and_push_gate(gate_num=2, ...)",
            ],
            notes=notes,
            extra={"fr_count": str(len(fr_ids))},
        )
        msg = f"feat(P3-pre-ssi): all {len(fr_ids)} FR(s) Gate1 PASS; ready for SSI"
        return self._commit_and_push(msg)

    # ── Push ⑤⑥⑧ — Gate 2/3/4 PASS ────────────────────────────────────────

    def commit_and_push_gate(
        self,
        gate_num: int,
        phase: int,
        score: float,
        n_frs: int = 0,
        background: str = "",
        notes: list[str] | None = None,
    ) -> bool:
        """
        Commit + push after Gate 2/3/4 PASS. Tags HEAD at Gate 4.  PUSH ⑤⑥⑧

        Args:
            gate_num:   2, 3, or 4.
            phase:      Current pipeline phase (used only for Gate 4 label fallback).
            score:      Composite gate score.
            n_frs:      FR count (Gate 2 suffix only).
            background: Optional project context.
            notes:      Extra notes.
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

        cp_map = {2: "P3-gate2", 3: "P4-gate3", 4: "P6-gate4"}
        next_steps_map = {
            2: [
                "Proceed to P4: Testing",
                "Build full test suite (Gate 3 target ≥ 80)",
                "On Gate 3 PASS → call commit_and_push_gate(gate_num=3, ...)",
            ],
            3: [
                "Proceed to P5: Review Baseline",
                "Generate BASELINE.md",
                "On BASELINE.md ready → call commit_and_push_p5_baseline()",
            ],
            4: [
                "Proceed to P7: Risk Register",
                "Document all known risks",
                "On P7 done → call commit_and_push_p7()",
                "On P8 done → call commit_and_push_p8()",
            ],
        }

        self._write_handover(
            checkpoint_id=self._cp(cp_map.get(gate_num, f"P{phase}-gate{gate_num}")),
            phase={2: 3, 3: 4, 4: 6}.get(gate_num, phase),
            background=background or f"Gate {gate_num} PASS — SSI quality cycle complete.",
            status=f"Gate {gate_num} PASS: score={score:.1f}. {suffix}".strip(),
            steps=next_steps_map.get(gate_num, ["Review gate results and proceed."]),
            notes=notes,
            extra={"gate": str(gate_num), "score": f"{score:.1f}"},
        )

        msg = f"{label}({phase_label}): Gate{gate_num} PASS score={score:.1f} {suffix}".rstrip()
        ok = self._commit_and_push(msg)
        if ok and gate_num == 4:
            self._tag_release(score)
        return ok

    # ── Push ⑦ — P5 BASELINE.md ─────────────────────────────────────────────

    def commit_and_push_p5_baseline(
        self,
        background: str = "",
        notes: list[str] | None = None,
    ) -> bool:
        """
        Commit + push at P5 BASELINE.md generation.  PUSH ⑦
        """
        if not self.enabled:
            return True
        self._write_handover(
            checkpoint_id=self._cp("P5-baseline"),
            phase=5,
            background=background or "P5 Review Baseline: BASELINE.md generated.",
            status="BASELINE.md committed. P5 Review Baseline complete.",
            steps=[
                "Proceed to P6: Full Review / Gate 4",
                "Run full Gate 4 review (target ≥ 85)",
                "On Gate 4 APPROVE → call commit_and_push_gate(gate_num=4, ...)",
            ],
            notes=notes,
        )
        msg = "docs(P5): BASELINE.md — review baseline checkpoint"
        return self._commit_and_push(msg)

    # ── Push ⑨ — P7 exit ────────────────────────────────────────────────────

    def commit_and_push_p7(
        self,
        background: str = "",
        notes: list[str] | None = None,
    ) -> bool:
        """
        Commit + push at P7 completion (risk register done).  PUSH ⑨
        """
        if not self.enabled:
            return True
        self._write_handover(
            checkpoint_id=self._cp("P7-exit"),
            phase=7,
            background=background or "P7 Risk Register: all risks documented.",
            status="P7 Risk Register complete. Risk log committed.",
            steps=[
                "Proceed to P8: Config & Records",
                "Finalize all configuration records",
                "On P8 done → call commit_and_push_p8()",
            ],
            notes=notes,
        )
        msg = "docs(P7): risk register complete"
        return self._commit_and_push(msg)

    # ── Push ⑩ — P8 exit ────────────────────────────────────────────────────

    def commit_and_push_p8(
        self,
        background: str = "",
        notes: list[str] | None = None,
    ) -> bool:
        """
        Commit + push at P8 completion (config records done).  PUSH ⑩
        """
        if not self.enabled:
            return True
        self._write_handover(
            checkpoint_id=self._cp("P8-exit"),
            phase=8,
            background=background or "P8 Config & Records: pipeline fully complete.",
            status="P8 Config & Records complete. All 8 phases done.",
            steps=[
                "Pipeline complete — all phases P1–P8 finished",
                "Review final HANDOVER.md and git tag for Gate 4",
                "Archive session via /compact",
            ],
            notes=notes,
        )
        msg = "docs(P8): config records — pipeline complete"
        return self._commit_and_push(msg)

    # ── Deprecated: kept for backward compatibility ──────────────────────────

    def commit_and_push_final(self, phases: list[int]) -> bool:
        """
        Deprecated: use ``commit_and_push_p7`` + ``commit_and_push_p8`` instead.

        Kept for backward compatibility; routes to p7 or p8 based on last phase.
        """
        if not self.enabled:
            return True
        last_phase = max(phases) if phases else 8
        if last_phase == 7:
            return self.commit_and_push_p7()
        return self.commit_and_push_p8()

    # ── Private helpers ──────────────────────────────────────────────────────

    def _write_handover(
        self,
        checkpoint_id: str,
        phase: int,
        background: str,
        status: str,
        steps: list[str],
        notes: list[str] | None,
        extra: dict[str, str] | None = None,
    ) -> None:
        """Write HANDOVER.md to project root. Never raises."""
        try:
            HandoverGenerator(self.project).write(
                checkpoint_id=checkpoint_id,
                phase=phase,
                task_background=background,
                current_status=status,
                next_steps=steps,
                notes=notes,
                extra=extra,
            )
            print(f"  [git] HANDOVER.md written: {checkpoint_id}")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"  [git WARN] HandoverGenerator failed: {exc}")

    def _cp(self, label: str) -> str:
        """Build a checkpoint_id with today's date suffix."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"{label}-{ts}"

    @staticmethod
    def _fr_summary(fr_ids: list[str]) -> str:
        """Return a compact FR list string (max 5 shown)."""
        fr_list = ",".join(fr_ids[:5])
        if len(fr_ids) > 5:
            fr_list += f",…+{len(fr_ids) - 5}"
        return fr_list

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
            return subprocess.run(  # nosec B603 B607
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
