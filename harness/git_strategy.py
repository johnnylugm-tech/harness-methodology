"""
harness/git_strategy.py — Gate-aligned Git commit + push strategy.

10-Push Handover-Aware Strategy
────────────────────────────────
  PUSH ①  — P1 exit: SRS/SAD draft complete
  PUSH ②  — P2 exit: quality_manifest.json created (human checkpoint)
  PUSH ③  — P3 mid: FR Gate 1 PASS ≥ 50 % of total FRs
  PUSH ④  — P3 pre-Gate2: all FRs done, ready for Gate 2
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

import json as _json
import math as _math
import os
import re as _re
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
        # Validate fr_id: must match the FR-NN pattern (NN = digits).
        # Without this check, an embedded newline would split the
        # commit subject/body, breaking the message[:72] display
        # invariant; a semicolon or backtick would break downstream
        # tools that eval the subject.
        if not isinstance(fr_id, str) or not _re.fullmatch(r"FR-\d+", fr_id):
            raise ValueError(
                f"fr_id must match the FR-NN pattern; got {fr_id!r}"
            )
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
        Commit + push at P1 exit (SRS + P1 deliverables complete).  PUSH ①

        Args:
            fr_ids:     Functional requirement IDs captured in the SRS.
                        If empty, auto-detected from SRS.md.
            background: Optional project context for HANDOVER.md.
            notes:      Extra notes appended after DEFAULT_NOTES.
        """
        if not self.enabled:
            return True
        # Auto-detect FR IDs from SRS.md when caller omits --fr-ids
        if not fr_ids:
            fr_ids = self._auto_fr_ids()
            if fr_ids:
                print(f"  [git] auto-detected {len(fr_ids)} FR(s) from SRS.md")
        fr_list = self._fr_summary(fr_ids)

        # Enrich HANDOVER.md with actual project state
        ab = self._ab_session_summary()
        gaps = self._gap_register_summary()
        committed = self._recently_committed_files()
        deliverables = self._deliverable_files(1)

        p1_deliverable_count = sum(1 for d in deliverables if "✅" in d)
        status_parts = [
            f"{len(fr_ids)} FR(s) defined in SRS [{fr_list}]. "
            f"{p1_deliverable_count}/4 deliverables present, Agent-B APPROVED.",
        ]
        if ab:
            status_parts.append(f"\n**A/B Session Results:**\n{ab}")
        if gaps:
            status_parts.append(f"\n**Review Gaps (carry-forward to P2+):**\n{gaps}")
        if committed:
            file_md = "\n".join(f"  - `{f}`" for f in committed)
            status_parts.append(f"\n**Recently Committed Files:**\n{file_md}")

        if self._next_phase_plan_exists(1):
            p2_step = "Open `.methodology/phase2_plan.md` and follow from the top"
        else:
            p2_step = (
                "Generate Phase 2 plan: "
                "`python3 harness_cli.py plan-phase --phase 2 --project .`"
            )

        self._write_handover(
            checkpoint_id=self._cp("P1-exit"),
            phase=1,
            background=background or "P1 Spec & Discovery: SRS + 4 deliverables complete.",
            status="\n".join(status_parts),
            steps=[
                p2_step,
                "Follow SKILL.md §0.1 for P2 entry",
                "Review carry-forward gaps before starting P2 (SPEC_TRACKING.md gap register)",
            ],
            notes=notes,
            extra={
                "fr_count": str(len(fr_ids)),
            },
            plan_override=".methodology/phase2_plan.md" if self._next_phase_plan_exists(1) else None,
            deliverables=deliverables,
        )
        msg = f"phase1(review-complete): SRS + P1 deliverables; {len(fr_ids)} FR(s) [{fr_list}]"
        return self._commit_and_push(msg, skip_hooks=True)

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
        if not fr_ids:
            fr_ids = self._auto_fr_ids()
            if fr_ids:
                print(f"  [git] auto-detected {len(fr_ids)} FR(s) from SRS.md")
        fr_list = self._fr_summary(fr_ids)

        ab = self._ab_session_summary()
        committed = self._recently_committed_files()
        deliverables = self._deliverable_files(2)

        p2_deliverable_count = sum(1 for d in deliverables if "✅" in d)
        status_parts = [
            f"{len(fr_ids)} FR(s) in quality manifest [{fr_list}]. "
            f"{p2_deliverable_count}/3 P2 deliverables present, Agent-B APPROVED.",
        ]
        if ab:
            status_parts.append(f"\n**A/B Session Results:**\n{ab}")
        if committed:
            file_md = "\n".join(f"  - `{f}`" for f in committed)
            status_parts.append(f"\n**Recently Committed Files:**\n{file_md}")

        if self._next_phase_plan_exists(2):
            p3_step = "Open `.methodology/phase3_plan.md` and follow from the top"
        else:
            p3_step = (
                "Generate Phase 3 plan: "
                "`python3 harness_cli.py plan-phase --phase 3 --project .`"
            )

        self._write_handover(
            checkpoint_id=self._cp("P2-exit"),
            phase=2,
            background=background or "P2 Architecture & Design: SAD.md + ADR.md + quality_manifest.json complete.",
            status="\n".join(status_parts),
            steps=[
                p3_step,
                "Implement each FR with TDD (Gate 1 target per FR ≥75)",
                "Push P3-mid checkpoint at ≥50 % FR Gate 1 PASS",
                "Push P3-pre-gate2 checkpoint when all FRs done",
            ],
            notes=notes,
            extra={"fr_count": str(len(fr_ids))},
            plan_override=".methodology/phase3_plan.md" if self._next_phase_plan_exists(2) else None,
            deliverables=deliverables,
        )
        msg = f"phase2(review-complete): SAD + ADR + quality manifest complete [fr_ids={fr_list}]"
        return self._commit_and_push(msg, skip_hooks=True)

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
        completed_set = set(fr_ids)
        all_ids = self._manifest_fr_ids() or self._auto_fr_ids()
        remaining = [f for f in all_ids if f not in completed_set]
        if remaining:
            remaining_str = ", ".join(remaining)
        elif all_ids:
            remaining_str = "(all FRs Gate 1 PASS — ready for P3-pre-gate2)"
        else:
            remaining_str = "(manifest not found — run `python3 harness_cli.py manifest` first)"

        ab = self._ab_session_summary()
        committed = self._recently_committed_files()

        status_parts = [
            f"{fr_done}/{fr_total} FRs Gate 1 PASS [{fr_list}]. "
            f"TDD cycles complete for passing FRs.",
        ]
        if ab:
            status_parts.append(f"\n**A/B Session Results:**\n{ab}")
        if committed:
            file_md = "\n".join(f"  - `{f}`" for f in committed)
            status_parts.append(f"\n**Recently Committed Files:**\n{file_md}")

        self._write_handover(
            checkpoint_id=self._cp("P3-mid"),
            phase=3,
            background=background or f"P3 Implementation in progress (≥50% milestone). {fr_done}/{fr_total} FRs done.",
            status="\n".join(status_parts),
            steps=[
                f"Complete remaining {fr_total - fr_done} FR(s): {remaining_str}",
                "Ensure each FR has passing unit tests (TDD)",
                "When all FRs done → `push-milestone --type p3-pre-gate2`",
            ],
            notes=notes,
            extra={
                "fr_done": str(fr_done),
                "fr_total": str(fr_total),
                "remaining_frs": remaining_str,
            },
            resume_phase=3,
        )
        msg = (
            f"feat(P3-mid): {fr_done}/{fr_total} FR(s) Gate1 PASS "
            f"[{fr_list}]"
        )
        return self._commit_and_push(msg)

    # ── Push ④ — P3 pre-Gate2 ────────────────────────────────────────────────

    def commit_and_push_p3_pre_gate2(
        self,
        fr_ids: list[str],
        background: str = "",
        notes: list[str] | None = None,
    ) -> bool:
        """
        Commit + push when all FRs done but Gate 2 has not yet run.  PUSH ④

        This is the last stable snapshot before the phase-exit gate evaluation
        modifies files.

        Args:
            fr_ids:     All FR IDs (Gate 1 PASS).
            background: Optional project context.
            notes:      Extra notes.
        """
        if not self.enabled:
            return True
        fr_list = self._fr_summary(fr_ids)

        ab = self._ab_session_summary()
        committed = self._recently_committed_files()

        status_parts = [
            f"All {len(fr_ids)} FR(s) Gate 1 PASS [{fr_list}]. "
            "Gate 2 evaluation not yet started.",
        ]
        if ab:
            status_parts.append(f"\n**A/B Session Results:**\n{ab}")
        if committed:
            file_md = "\n".join(f"  - `{f}`" for f in committed)
            status_parts.append(f"\n**Recently Committed Files:**\n{file_md}")

        self._write_handover(
            checkpoint_id=self._cp("P3-pre-gate2"),
            phase=3,
            background=background or "P3 Implementation complete. Gate 2 not yet executed.",
            status="\n".join(status_parts),
            steps=[
                "Run Gate 2 evaluation (target score ≥ 75)",
                "Fix any failures during evaluation",
                "On Gate 2 PASS → `finalize-gate --gate 2` handles push + HANDOVER",
            ],
            notes=notes,
            extra={
                "fr_count": str(len(fr_ids)),
            },
            resume_phase=3,
        )
        msg = f"feat(P3-pre-gate2): all {len(fr_ids)} FR(s) Gate1 PASS; ready for Gate 2"
        return self._commit_and_push(msg)

    # ── Push ⑤ — P3 post-Gate2 (P3 formal exit, v2.9.1 B.2) ────────────────

    def commit_and_push_p3_post_gate2(
        self,
        fr_ids: list[str],
        background: str = "",
        notes: list[str] | None = None,
    ) -> bool:
        """
        Commit + push when Gate 2 has PASSed and all FR Gate 1s are PASS.  PUSH ⑤

        This is the FORMAL P3 exit milestone. Pre-flight is enforced in
        `cmd_push_milestone` via `_validate_p3_post_gate2_precondition`
        (gate2_result.json composite ≥ 75 + per-FR Gate 1 sentinels present),
        so by the time this method runs the conditions are satisfied.

        Closes the e2e finding where the orchestrator called its commit
        "P3-exit" without verifying any gate — orchestrators can now invoke
        this milestone type instead of writing label-only commits.

        Args:
            fr_ids:     All FR IDs (Gate 1 PASS).
            background: Optional project context.
            notes:      Extra notes.
        """
        if not self.enabled:
            return True
        fr_list = self._fr_summary(fr_ids)

        ab = self._ab_session_summary()
        committed = self._recently_committed_files()

        status_parts = [
            f"Gate 2 PASS + all {len(fr_ids)} FR(s) Gate 1 PASS [{fr_list}]. "
            "Phase 3 formally complete. P4 (verification + adversarial) ready.",
        ]
        if ab:
            status_parts.append(f"\n**A/B Session Results:**\n{ab}")
        if committed:
            file_md = "\n".join(f"  - `{f}`" for f in committed)
            status_parts.append(f"\n**Recently Committed Files:**\n{file_md}")

        self._write_handover(
            checkpoint_id=self._cp("P3-post-gate2"),
            phase=3,
            background=background or "P3 Implementation complete. Gate 2 PASS. Ready for P4.",
            status="\n".join(status_parts),
            steps=[
                "advance-phase --completed 3  (transitions to P4)",
                "Spawn Phase 4 orchestrator (verification + adversarial bug hunt)",
                "Gate 3 at P4 exit (target composite ≥ 80)",
            ],
            notes=notes,
            extra={
                "fr_count": str(len(fr_ids)),
            },
            resume_phase=4,
        )
        msg = f"feat(P3-post-gate2): Gate 2 PASS + all {len(fr_ids)} FR(s) Gate1 PASS; P3 exit"
        return self._commit_and_push(msg)

    # ── Push ③④ (P4 variant) — P4 mid + pre-Gate3 milestones ────────────────

    def commit_and_push_p4_mid(
        self,
        fr_done: int,
        fr_total: int,
        fr_ids: list[str],
        background: str = "",
        notes: list[str] | None = None,
    ) -> bool:
        """Commit + push at P4 mid-point (FR Gate 1 re-eval PASS ≥ 50%)."""
        if not self.enabled:
            return True
        if not fr_ids:
            fr_ids = self._manifest_fr_ids() or self._auto_fr_ids()
        fr_list = self._fr_summary(fr_ids)
        completed_set = set(fr_ids)
        all_ids = self._manifest_fr_ids() or self._auto_fr_ids()
        remaining = [f for f in all_ids if f not in completed_set]
        if remaining:
            remaining_str = ", ".join(remaining)
        elif all_ids:
            remaining_str = "(all FRs Gate 1 PASS — ready for P4-pre-gate3)"
        else:
            remaining_str = "(manifest not found — run `python3 harness_cli.py manifest` first)"

        ab = self._ab_session_summary()
        committed = self._recently_committed_files()

        status_parts = [
            f"{fr_done}/{fr_total} FRs Gate 1 PASS [{fr_list}]. "
            f"Test cycles complete for passing FRs.",
        ]
        if ab:
            status_parts.append(f"\n**A/B Session Results:**\n{ab}")
        if committed:
            file_md = "\n".join(f"  - `{f}`" for f in committed)
            status_parts.append(f"\n**Recently Committed Files:**\n{file_md}")

        self._write_handover(
            checkpoint_id=self._cp("P4-mid"),
            phase=4,
            background=background or f"P4 Testing in progress (≥50% milestone). {fr_done}/{fr_total} FRs done.",
            status="\n".join(status_parts),
            steps=[
                f"Complete remaining {fr_total - fr_done} FR(s): {remaining_str}",
                "Ensure each FR has ≥80% branch coverage",
                "When all FRs done → `push-milestone --type p4-pre-gate3`",
            ],
            notes=notes,
            extra={
                "fr_done": str(fr_done),
                "fr_total": str(fr_total),
            },
            resume_phase=4,
        )
        msg = f"feat(P4-mid): {fr_done}/{fr_total} FRs Gate1 re-eval PASS"
        return self._commit_and_push(msg)

    def commit_and_push_p4_pre_gate3(
        self,
        fr_ids: list[str],
        background: str = "",
        notes: list[str] | None = None,
    ) -> bool:
        """Commit + push when all P4 FRs done but Gate 3 has not yet run."""
        if not self.enabled:
            return True
        if not fr_ids:
            fr_ids = self._manifest_fr_ids() or self._auto_fr_ids()
        fr_list = self._fr_summary(fr_ids)

        ab = self._ab_session_summary()
        committed = self._recently_committed_files()

        status_parts = [
            f"All {len(fr_ids)} FR(s) Gate 1 re-eval PASS [{fr_list}]. "
            "Gate 3 (14 dims) not yet started.",
        ]
        if ab:
            status_parts.append(f"\n**A/B Session Results:**\n{ab}")
        if committed:
            file_md = "\n".join(f"  - `{f}`" for f in committed)
            status_parts.append(f"\n**Recently Committed Files:**\n{file_md}")

        self._write_handover(
            checkpoint_id=self._cp("P4-pre-gate3"),
            phase=4,
            background=background or "P4 Testing complete. Gate 3 not yet executed.",
            status="\n".join(status_parts),
            steps=[
                "Run Gate 3 evaluation (14 dims, target score ≥ 80)",
                "Fix any failures during evaluation",
                "On Gate 3 PASS → `finalize-gate --gate 3` handles push + HANDOVER",
            ],
            notes=notes,
            extra={
                "fr_count": str(len(fr_ids)),
            },
            resume_phase=4,
        )
        msg = f"feat(P4-pre-gate3): all {len(fr_ids)} FR(s) Gate1 re-eval PASS; ready for Gate 3"
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
            background=background or f"Gate {gate_num} PASS — quality cycle complete.",
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
            # P8 is the terminal phase — resume_phase=8 prevents HandoverGenerator
            # from computing _target = phase+1 = 9 and embedding phase9_plan.md refs.
            resume_phase=8,
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

    # ── Project-state auto-detection ─────────────────────────────────────────

    def _manifest_fr_ids(self) -> list[str]:
        """Read FR IDs from quality_manifest.json (authoritative source from P2 exit).

        Validates that every element is a string matching the
        FR-NN pattern. A malformed manifest (non-string elements,
        injection attempts) raises ValueError so the operator is
        forced to fix the manifest rather than silently producing
        broken commit messages.
        """
        manifest = self.project / ".methodology" / "quality_manifest.json"
        if not manifest.exists():
            return []
        try:
            raw_ids = _json.loads(manifest.read_text(encoding="utf-8")).get("fr_ids", [])
        except Exception:  # pylint: disable=broad-exception-caught
            return []
        if not isinstance(raw_ids, list):
            raise ValueError(
                f"quality_manifest.json fr_ids must be a list; got "
                f"{type(raw_ids).__name__}"
            )
        for fid in raw_ids:
            if not isinstance(fid, str) or not _re.fullmatch(r"FR-\d+", fid):
                raise ValueError(
                    f"quality_manifest.json fr_ids must all match the "
                    f"FR-NN pattern; got invalid entry: {fid!r}"
                )
        return raw_ids

    def _auto_fr_ids(self) -> list[str]:
        """Parse SRS.md (repo root or docs/) for FR IDs.

        Only recognises headings of the exact form ``### FR-XX:`` (three ``#``,
        numeric suffix, colon terminator).  Non-standard heading levels or
        missing colons are silently skipped.
        """
        for srs_path in (
            self.project / "01-requirements" / "SRS.md",
        ):
            if not srs_path.exists():
                continue
            try:
                text = srs_path.read_text(encoding="utf-8", errors="replace")
                return sorted(set(_re.findall(r"###\s+(FR-\d+)\s*:", text)))
            except Exception:  # pylint: disable=broad-exception-caught
                return []
        return []

    def _recently_committed_files(self, n: int = 20) -> list[str]:
        """Return files changed in the last *n* commits (phase artifacts).

        Uses ``git log --name-only`` instead of ``git status`` so it captures
        deliverables that were committed earlier in the session (e.g. SRS.md
        committed during A/B review), not just files that are currently dirty.
        """
        r = self._run_git("log", f"--max-count={n}", "--name-only", "--pretty=format:")
        seen: dict[str, None] = {}  # insertion-ordered deduplication
        for line in r.stdout.splitlines():
            f = line.strip()
            if f:
                seen[f] = None
        return list(seen.keys())[:20]

    def _ab_session_summary(self) -> str:
        """Read sessions_spawn.log → markdown bullet list of A/B results."""
        log_path = self.project / ".methodology" / "sessions_spawn.log"
        if not log_path.exists():
            return ""
        lines: list[str] = []
        seen: set[tuple] = set()
        try:
            for raw in log_path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    e = _json.loads(raw)
                except ValueError:
                    continue
                sub = e.get("sub_task") or e.get("fr_id", "?")
                role = e.get("role", "?")
                verdict = e.get("review_status") or e.get("status", "?")
                rnd = e.get("round", 1)
                key = (sub, role, rnd)
                if key in seen:
                    continue
                seen.add(key)
                round_str = f" r{rnd}" if rnd > 1 else ""
                lines.append(f"  - {sub} / {role}{round_str}: **{verdict}**")
        except Exception:  # pylint: disable=broad-exception-caught
            return ""
        return "\n".join(lines)

    def _gap_register_summary(self) -> str:
        """Parse Review Gap Register table in SPEC_TRACKING.md.

        Returns a compact markdown table with Gap ID, Area, Disposition, and
        Target Phase so the next session knows exactly what carry-forward work
        remains and which gaps are deferred vs. in-scope.

        Table column order assumed: Gap ID | Source | Area | Disposition | Target Phase
        """
        for path in (
            self.project / "01-requirements" / "SPEC_TRACKING.md",
        ):
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:  # pylint: disable=broad-exception-caught
                return ""

            _GAP_RE = _re.compile(r"^((?:[A-Z]+-)*GAP-\d+)$")
            rows: list[dict[str, str]] = []
            for line in text.splitlines():
                parts = [p.strip() for p in line.split("|")]
                parts = [p for p in parts if p]  # drop empty leading/trailing
                if len(parts) < 2:
                    continue
                if not _GAP_RE.match(parts[0]):
                    continue  # header or non-gap row
                # Column layout: Gap ID | Source | Area | Disposition | Target Phase
                # Accept 2-5 columns; shorter rows leave trailing fields empty.
                rows.append({
                    "id":          parts[0],
                    # 3+ cols: parts[1]=Source, parts[2]=Area; 2 cols: parts[1]=Area
                    "area":        parts[2] if len(parts) > 2 else parts[1],
                    "disposition": parts[3] if len(parts) > 3 else "",
                    "target":      parts[4] if len(parts) > 4 else "",
                })

            if not rows:
                return ""

            medium = [r for r in rows if "M-GAP-" in r["id"] or "TM-GAP-" in r["id"]]
            total = len(rows)

            table_rows = []
            for r in rows:
                flag = " ⚠️" if r in medium else ""
                disp = r["disposition"]
                if len(disp) > 55:
                    disp = disp[:52] + "…"
                table_rows.append(
                    f"| `{r['id']}`{flag} | {r['area']} | {disp} | {r['target']} |"
                )

            header = (
                f"  {total} gap(s)"
                + (f" — ⚠️ {len(medium)} medium-priority" if medium else "")
                + "\n\n"
                "| Gap ID | Area | Disposition | Target |\n"
                "|--------|------|-------------|--------|\n"
                + "\n".join(table_rows)
            )
            return header
        return ""

    # Map of known phase deliverable filenames (P1 and P2 are well-defined by
    # the methodology; other phases have project-specific outputs).
    _DELIVERABLE_NAMES: dict[int, list[str]] = {
        1: ["01-requirements/SRS.md", "01-requirements/SPEC_TRACKING.md",
            "01-requirements/TRACEABILITY_MATRIX.md"],
        2: ["02-architecture/SAD.md"],
    }

    def _deliverable_files(self, phase: int) -> list[str]:
        """Return formatted deliverable lines for ``HandoverGenerator``.

        Each line is a markdown-ready string like `` `SRS.md` ✅ (312L) `` or
        `` ~~`SAD.md`~~ ❌ missing ``.  Only P1/P2 are enumerated; other phases
        return an empty list.
        """
        names = self._DELIVERABLE_NAMES.get(phase, [])
        items: list[str] = []
        for name in names:
            p = self.project / name
            if p.exists():
                try:
                    lines = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
                    items.append(f"`{name}` ✅ ({lines}L)")
                except Exception:  # pylint: disable=broad-exception-caught
                    items.append(f"`{name}` ✅")
            else:
                items.append(f"~~`{name}`~~ ❌ missing")
        return items

    def _next_phase_plan_exists(self, current_phase: int) -> bool:
        """Return True if the next-phase plan file already exists on disk."""
        return (
            self.project / ".methodology" / f"phase{current_phase + 1}_plan.md"
        ).exists()

    # ── Handover writer ───────────────────────────────────────────────────────

    def _write_handover(
        self,
        checkpoint_id: str,
        phase: int,
        background: str,
        status: str,
        steps: list[str],
        notes: list[str] | None,
        extra: dict[str, str] | None = None,
        plan_override: str | None = None,
        deliverables: list[str] | None = None,
        resume_phase: int | None = None,
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
                plan_override=plan_override,
                deliverables=deliverables,
                resume_phase=resume_phase,
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

    def _commit(self, message: str, skip_hooks: bool = False) -> bool:
        """Stage all changes and commit. Returns True on success or nothing-to-commit.

        Args:
            skip_hooks: If True, uses --no-verify to bypass prepare-commit-msg hook.
                        Use only when caller has its own gate enforcement (push-checkpoint).
        """
        if not self._has_changes():
            print("  [git] nothing to commit — skip")
            return True
        r1 = self._run_git("add", "-A")
        if r1.returncode != 0:
            print(f"  [git WARN] git add failed: {r1.stderr[:200]}")
            return False
        cmd = ["commit", "-m", message]
        if skip_hooks:
            cmd.insert(1, "--no-verify")
        r2 = self._run_git(*cmd)
        if r2.returncode != 0:
            print(f"  [git WARN] git commit failed: {r2.stderr[:200]}")
            return False
        sha = self._run_git("rev-parse", "--short", "HEAD").stdout.strip()
        print(f"  [git] committed {sha}: {message[:72]}")
        return True

    def _commit_and_push(self, message: str, skip_hooks: bool = False) -> bool:
        if not self._commit(message, skip_hooks=skip_hooks):
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
        # Score validation: `int(float('inf'))` raises OverflowError
        # and `int(float('nan'))` raises ValueError, both uncaught,
        # aborting the entire Gate 4 push pipeline. Validate the
        # score is finite before using it in the tag name.
        try:
            _score_val = float(score)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"score for tag must be a real number; got "
                f"{type(score).__name__}: {score!r}"
            ) from exc
        if not _math.isfinite(_score_val):
            raise ValueError(
                f"score for tag must be a finite number; got {_score_val}"
            )
        ts = datetime.now(timezone.utc).strftime("%Y%m%d")
        tag = f"{_TAG_PREFIX}-{ts}-score{int(_score_val)}"
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
