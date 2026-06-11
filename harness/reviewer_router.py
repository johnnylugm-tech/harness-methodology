# harness/reviewer_router.py
# Gap G2: Claude sub-agent reviewer — single backend, no MCP dependencies.
# v3.0: Simplified from priority-chained Hermes→Gemini→sub-agent to Claude-only.
#        Stateless sub-agent provides per-task isolation identical to the old
#        sub-agent backstop; setup requires only the claude CLI (no env vars).
from __future__ import annotations

import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (all overridable via environment variables)
# ---------------------------------------------------------------------------

def _parse_int_env(key: str, default: int) -> int:
    """Return int value of env var *key*, or *default* on missing/non-numeric."""
    raw = os.environ.get(key, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


TASK_SIZE_THRESHOLD = _parse_int_env("TASK_SIZE_THRESHOLD", 2000)    # chars — decompose if exceeded
SUBTASK_MAX_SIZE    = _parse_int_env("SUBTASK_MAX_SIZE",    800)     # chars/subtask (paragraph split)
MAX_CONTEXT_LINES   = _parse_int_env("MAX_CONTEXT_LINES",  6)        # approved-summaries injected


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SubTask:
    """A single atomic unit of review work, with dependency metadata."""
    content: str            # Prompt chunk for this subtask
    label: str              # e.g. "FR-001", "§3.2", "Phase-3"
    dependencies: list[str] = field(default_factory=list)  # Labels that must complete first
    index: int = 1
    total: int = 1


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------

def get_reviewer_model(phase: int, role: str = "reviewer") -> str:
    """Return effective reviewer model for this phase/role.

    All phases use Claude sub-agent as the sole reviewer backend.
    No MCP dependencies required — only the claude CLI.
    """
    return "claude"


# ---------------------------------------------------------------------------
# ReviewerRouter
# ---------------------------------------------------------------------------

class ReviewerRouter:
    """
    Routes review requests to Claude sub-agent with structured task decomposition.

    Execution model:
      1. Decompose prompt into dependency-ordered SubTask list.
      2. For each SubTask IN ORDER (respecting dependencies):
         a. Inject context from previously approved subtasks.
         b. Dispatch to Claude sub-agent (stateless, isolated session).
         c. On APPROVE: accumulate summary → proceed to next SubTask.
         d. On REJECT:  stop immediately, return REJECT with audit trail.
      3. Merge all APPROVE results into final response.

    Backend: Claude sub-agent (all phases, no MCP dependencies required).
    No environment variables needed — only the claude CLI must be installed.
    """

    def __init__(self, project_path: "Path | None" = None):
        """Initialise the router. No MCP backend configuration needed."""
        self.project_path = Path(project_path) if project_path else None

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    def review(
        self,
        role: str,
        prompt: str,
        phase: int,
        fr_id: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """
        Send review request through the priority chain.

        If the prompt is large/complex, auto-decomposes into SubTasks ordered
        by dependency analysis. Each SubTask completes a full A/B review cycle
        before the next begins. Any REJECT halts the chain immediately.

        Returns dict with:
          review_status, confidence, violations, summary
          _reviewer_used, _degraded, _degradation, _degradation_note
          _subtask_count (if multi-subtask), _stopped_at (if REJECT mid-chain)
        """
        subtasks = self._decompose_with_deps(prompt, role, phase)

        if len(subtasks) == 1:
            return self._try_chain(role, subtasks[0].content, phase, fr_id, timeout_ms)

        return self._execute_parallel_waves(role, subtasks, phase, fr_id, timeout_ms)

    # ------------------------------------------------------------------
    # Chain execution (per individual subtask)
    # ------------------------------------------------------------------

    def _try_chain(
        self,
        role: str,
        prompt: str,
        phase: int,
        fr_id: str | None,
        timeout_ms: int | None,
        task_idx: int = 1,
        task_total: int = 1,
        cancel_event: threading.Event | None = None,
    ) -> dict:
        """Dispatch review to Claude sub-agent (sole backend)."""
        if cancel_event and cancel_event.is_set():
            return {
                "review_status": "CANCELLED",
                "violations": [],
                "summary": "[CANCELLED] Sibling reviewer returned REJECT. Skipped subagent.",
                "_reviewer_used": "subagent",
            }
        # Use the caller-supplied timeout; fall back to 300 s.
        task_timeout_s = int(timeout_ms / 1000) if timeout_ms is not None else 300
        result = self._try_subagent(role, prompt, phase, fr_id, task_timeout_s=task_timeout_s)
        result["_reviewer_used"] = "subagent"
        return result

    # ------------------------------------------------------------------
    # Backend: Claude sub-agent
    # ------------------------------------------------------------------

    def _try_subagent(self, role: str, prompt: str, phase: int, fr_id: str | None,
                      task_timeout_s: int = 300) -> dict:
        """Dispatch review to a stateless Claude sub-agent.

        On backend failure (timeout, MCP error, ImportError, OOM) the gate
        must fail CLOSED: a crashed reviewer cannot vouch for quality, so we
        return REJECT and let the merge layer short-circuit. The
        ``_emergency_fallback`` flag is preserved for forensics so operators
        can distinguish "reviewer crashed" from a real review REJECT.
        """
        try:
            from core.agent_spawner import AgentSpawner  # lazy import — avoids circular dep
            spawner = AgentSpawner(project_path=self.project_path)
            result = spawner.spawn(
                role=role, prompt=prompt,
                context={},
                model="claude", phase=phase, fr_id=fr_id,
                task_timeout=task_timeout_s,
            )
            return result if isinstance(result, dict) else {"output": str(result), "status": "complete"}
        except Exception as exc:
            _log.exception(
                "Reviewer sub-agent crashed (role=%s, phase=%s, fr_id=%s); "
                "failing gate closed with REJECT.",
                role, phase, fr_id,
            )
            return {
                "review_status": "REJECT",
                "confidence": 0.0,
                "violations": ["subagent_crashed"],
                "summary": (
                    f"[REVIEW BLOCKED] Sub-agent failed: {exc}. "
                    "Gate failed-closed; manual review required."
                ),
                "_emergency_fallback": True,
            }

    # ------------------------------------------------------------------
    # Task decomposition — structured + dependency-aware
    # ------------------------------------------------------------------

    def _execute_parallel_waves(
        self,
        role: str,
        subtasks: list[SubTask],
        phase: int,
        fr_id: str | None,
        timeout_ms: int | None,
    ) -> dict:
        """
        Execute subtasks in dependency-ordered waves using ThreadPoolExecutor.

        Subtasks with no pending dependencies form a wave and run concurrently.
        Each wave waits for completion before the next wave begins.
        Any REJECT short-circuits: not-yet-started futures are cancelled, in-progress
        futures are abandoned (shutdown wait=False) so the caller returns immediately
        without blocking on slow sibling reviewers.
        """
        label_to_subtask = {s.label: s for s in subtasks}
        approved_context: list[str] = []
        lock = threading.Lock()

        # Compute remaining in-degree for each subtask
        pending_deps: dict[str, set[str]] = {
            s.label: set(d for d in s.dependencies if d in label_to_subtask)
            for s in subtasks
        }

        all_results: list[dict] = []
        remaining = list(subtasks)

        while remaining:
            # Collect subtasks whose dependencies are all satisfied
            wave = [s for s in remaining if not pending_deps[s.label]]
            if not wave:
                # Dependency cycle guard: break by taking the first remaining
                wave = [remaining[0]]

            remaining = [s for s in remaining if s not in wave]

            cancel_event = threading.Event()
            _rejected = False
            executor = ThreadPoolExecutor(max_workers=len(wave))
            try:
                futures = {
                    executor.submit(
                        self._try_chain,
                        role,
                        self._enrich_with_context(s, approved_context),
                        phase,
                        fr_id,
                        timeout_ms,
                        s.index,
                        s.total,
                        cancel_event,
                    ): s
                    for s in wave
                }

                for future in as_completed(futures):
                    subtask = futures[future]
                    result = future.result()
                    all_results.append(result)

                    if result.get("review_status") == "REJECT":
                        cancel_event.set()
                        _rejected = True
                        # cancel_futures=True drops not-yet-started futures.
                        # wait=False abandons in-progress ones so the caller returns
                        # immediately without blocking on slow sibling reviewers.
                        # The finally block skips wait=True on the reject path.
                        executor.shutdown(wait=False, cancel_futures=True)
                        result["_stopped_at"] = subtask.label
                        result["_completed_subtasks"] = len(all_results)
                        result["_total_subtasks"] = subtasks[-1].total if subtasks else 1
                        return self._merge_results(all_results)

                    summary = result.get("summary", "")
                    if summary:
                        with lock:
                            approved_context.append(f"✅ [{subtask.label}] {summary}")
            finally:
                # On the APPROVE path: wait=True joins threads cleanly.
                # On the REJECT path: shutdown(wait=False) was already called above;
                # skip the blocking join so the fast-exit intent is preserved.
                if not _rejected:
                    executor.shutdown(wait=True)

            # Update pending_deps for the next wave
            for s in remaining:
                pending_deps[s.label] -= {w.label for w in wave}

        return self._merge_results(all_results)

    def _decompose_with_deps(self, prompt: str, role: str, phase: int = 0) -> list[SubTask]:
        """
        Decompose large prompts into dependency-ordered SubTask list.

        Detection pipeline (in priority order):
          1. Phase blocks (Phase N / PX patterns) — SRS.md style
          2. FR-XXX blocks — FR-level granularity
          3. §X.Y section headers — SAD.md / chapter style
          4. Paragraph split — fallback, no dependency analysis

        After extraction, dependency edges are built from cross-references
        (label A's content mentions label B → A depends on B).
        Kahn's topological sort produces execution order with no cycles.

        P1/P2: always returns a single SubTask (holistic deliverable review).
        Returns [single SubTask] unchanged if decomposition not needed.
        """
        # P1/P2: whole-deliverable review — never decompose into FR subtasks
        if phase in {1, 2}:
            return [SubTask(content=prompt, label="full_deliverable", index=1, total=1)]

        if len(prompt) <= TASK_SIZE_THRESHOLD:
            return [SubTask(content=prompt, label="full_task", index=1, total=1)]

        # Try each structured extraction strategy in order
        sections = (
            self._extract_phase_sections(prompt)
            or self._extract_fr_sections(prompt)
            or self._extract_heading_sections(prompt)
        )

        if not sections or len(sections) < 2:
            # Fallback: paragraph split, no dependency analysis
            return self._paragraph_subtasks(prompt, role)

        # Build dependency graph + topological sort
        dep_graph = self._build_dep_graph(sections)
        ordered_labels = self._topological_sort(dep_graph, list(sections.keys()))
        total = len(ordered_labels)

        return [
            SubTask(
                content=f"[Subtask {i + 1}/{total}: {label} | {role}]\n{sections[label]}",
                label=label,
                dependencies=dep_graph.get(label, []),
                index=i + 1,
                total=total,
            )
            for i, label in enumerate(ordered_labels)
        ]

    # -- Section extractors --

    def _extract_phase_sections(self, prompt: str) -> dict[str, str]:
        """Extract Phase N / PX blocks (SRS.md / SOP style).
        Returns {} if fewer than 2 phase sections found.
        """
        # Match "## Phase 3", "# Phase 1 — ...", "P3:", "Phase-3" etc.
        pattern = re.compile(
            r'(?=^#{1,3}\s+(?:Phase\s+\d+|P\d+\b)|\bPhase\s*[:\-–]\s*\d+)',
            re.MULTILINE | re.IGNORECASE,
        )
        matches = list(pattern.finditer(prompt))
        if len(matches) < 2:
            return {}
        return self._split_at_matches(prompt, matches, label_fn=lambda m, txt: self._extract_label(txt, "Phase"))

    def _extract_fr_sections(self, prompt: str) -> dict[str, str]:
        """Extract FR-XXX blocks. Returns {} if fewer than 2 FR sections found."""
        matches = list(re.finditer(r'(?=\bFR-(\d+)\b)', prompt))
        if len(matches) < 2:
            return {}
        return self._split_at_matches(
            prompt, matches,
            label_fn=lambda m, txt: f"FR-{m.group(1).zfill(3)}",
        )

    def _extract_heading_sections(self, prompt: str) -> dict[str, str]:
        """Extract §X.Y or numbered heading sections (SAD.md style).
        Returns {} if fewer than 2 sections found.
        """
        pattern = re.compile(
            r'(?=^#{1,4}\s+(?:§[\d.]+|\d+\.\d+))',
            re.MULTILINE,
        )
        matches = list(pattern.finditer(prompt))
        if len(matches) < 2:
            return {}
        return self._split_at_matches(
            prompt, matches,
            label_fn=lambda m, txt: re.sub(r'^#+\s+', '', txt.split('\n')[0]).strip()[:40],
        )

    def _split_at_matches(
        self,
        text: str,
        matches: list,
        label_fn,
    ) -> dict[str, str]:
        """Generic helper: split text at match positions, label each chunk via label_fn."""
        sections: dict[str, str] = {}
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            chunk = text[start:end].strip()
            if not chunk:
                continue
            label = label_fn(m, chunk)
            # Deduplicate labels by appending index if needed
            if label in sections:
                label = f"{label}_{i}"
            sections[label] = chunk
        return sections

    def _extract_label(self, text: str, prefix: str) -> str:
        """Extract a short label from the first line of a section."""
        first_line = text.split('\n')[0]
        m = re.search(rf'{prefix}\s*[\-–:]?\s*(\d+)', first_line, re.IGNORECASE)
        return f"{prefix}-{m.group(1)}" if m else first_line[:30].strip()

    def _paragraph_subtasks(self, prompt: str, role: str) -> list[SubTask]:
        """Fallback: split at paragraph boundaries, no dependency analysis."""
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0
        for para in prompt.split("\n\n"):
            if current_len + len(para) > SUBTASK_MAX_SIZE and current:
                chunks.append("\n\n".join(current))
                current = [para]
                current_len = len(para)
            else:
                current.append(para)
                current_len += len(para)
        if current:
            chunks.append("\n\n".join(current))
        total = len(chunks)
        return [
            SubTask(
                content=f"[Subtask {i + 1}/{total} | {role}]\n{chunk}",
                label=f"part_{i + 1}",
                dependencies=[f"part_{i}"] if i > 0 else [],
                index=i + 1,
                total=total,
            )
            for i, chunk in enumerate(chunks)
        ]

    # -- Dependency analysis --

    def _build_dep_graph(self, sections: dict[str, str]) -> dict[str, list[str]]:
        """
        Build dependency edges by scanning cross-references.

        For each section, check whether its content mentions any other section
        label. If label B appears in label A's content → A depends on B
        (B must be reviewed and approved before A).

        Special case: phase ordering — Phase N implicitly depends on Phase N-1.
        """
        labels = list(sections.keys())
        deps: dict[str, list[str]] = {label: [] for label in labels}

        # Cross-reference scan
        for label, content in sections.items():
            for other in labels:
                if other == label:
                    continue
                # Match the label as a whole word in content
                if re.search(rf'\b{re.escape(other)}\b', content):
                    if other not in deps[label]:
                        deps[label].append(other)

        # Implicit phase ordering: Phase-N depends on Phase-(N-1)
        def _phase_num(label: str) -> int:
            m = re.search(r'\d+', label)
            return int(m.group()) if m else 0

        phase_labels = sorted(
            [label for label in labels if re.match(r'^Phase-\d+$', label, re.IGNORECASE)],
            key=_phase_num,
        )
        for i in range(1, len(phase_labels)):
            prev = phase_labels[i - 1]
            curr = phase_labels[i]
            if prev not in deps[curr]:
                deps[curr].append(prev)

        return deps

    def _topological_sort(self, deps: dict[str, list[str]], labels: list[str]) -> list[str]:
        """
        Kahn's algorithm: labels with no dependencies first.
        Cycles (if any) are broken by appending remaining nodes in original order.
        """
        in_degree: dict[str, int] = {label: 0 for label in labels}
        for label, dep_list in deps.items():
            for dep in dep_list:
                if dep in in_degree:
                    in_degree[label] += 1

        queue = sorted(label for label in labels if in_degree[label] == 0)
        result: list[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            # Reduce in-degree for nodes that depend on this one
            for label in labels:
                if node in deps.get(label, []) and label not in result:
                    in_degree[label] -= 1
                    if in_degree[label] == 0:
                        queue.append(label)
                        queue.sort()

        # Append any remaining (cycle nodes) in original order
        remaining = [label for label in labels if label not in result]
        result.extend(remaining)
        return result

    # -- Context injection --

    def _enrich_with_context(self, subtask: SubTask, approved_context: list[str]) -> str:
        """
        Inject summaries of previously approved subtasks into the current subtask prompt.

        Limits to MAX_CONTEXT_LINES (6) most recent approved summaries to keep
        prompt size bounded. Context is clearly delimited so the reviewer can
        understand what has already been approved.
        """
        if not approved_context:
            return subtask.content

        context_entries = approved_context[-MAX_CONTEXT_LINES:]
        context_block = "\n".join(context_entries)
        dep_note = (
            f"Dependencies: {', '.join(subtask.dependencies)}" if subtask.dependencies else ""
        )
        header = f"[Previously approved ({len(context_entries)} of {len(approved_context)} shown)]\n{context_block}"
        if dep_note:
            header = f"{header}\n{dep_note}"

        return f"{header}\n\n[Current task: {subtask.label}]\n{subtask.content}"

    # ------------------------------------------------------------------
    # Result merging
    # ------------------------------------------------------------------

    def _merge_results(self, results: list[dict]) -> dict:
        """
        Merge multi-subtask results conservatively.

        - Any REJECT → overall REJECT (short-circuit already handled in review()).
        - Confidence = min across all subtasks.
        - Violations = union.
        - Summary = pipe-separated.
        """
        if not results:
            return {"review_status": "REJECT", "confidence": 0.0,
                    "violations": ["no_subtask_results"], "summary": ""}
        if len(results) == 1:
            return results[0]

        all_violations: list[str] = []
        all_summaries: list[str] = []
        min_confidence = 1.0
        any_degraded = False
        degradation_notes: list[str] = []
        reviewer_used = "unknown"

        for r in results:
            if r.get("review_status") == "REJECT":
                return {**r, "_merged": True, "_subtask_count": len(results)}
            all_violations.extend(r.get("violations", []))
            if r.get("summary"):
                all_summaries.append(r["summary"])
            min_confidence = min(min_confidence, r.get("confidence", 1.0))
            if r.get("_degraded"):
                any_degraded = True
                if r.get("_degradation_note"):
                    degradation_notes.append(r["_degradation_note"])
            reviewer_used = r.get("_reviewer_used", reviewer_used)

        return {
            "review_status": "APPROVE",
            "confidence": min_confidence,
            "violations": all_violations,
            "summary": " | ".join(all_summaries),
            "_merged": True,
            "_subtask_count": len(results),
            "_reviewer_used": reviewer_used,
            "_degraded": any_degraded,
            "_degradation_note": "; ".join(degradation_notes) if degradation_notes else None,
        }

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        role: str,
        prompt: str,
        phase: int,
        fr_id: str | None = None,
        task_idx: int = 1,
        task_total: int = 1,
    ) -> str:
        fr_tag   = f" | FR {fr_id}" if fr_id else ""
        task_tag = f" | Task {task_idx}/{task_total}" if task_total > 1 else ""
        header   = f"[Harness Reviewer | Phase {phase}{fr_tag}{task_tag}]\nRole: {role}\n\n"
        footer   = (
            '\n\nOutput JSON: {"review_status": "APPROVE|REJECT", '
            '"confidence": 0-1, "violations": [], "summary": ""}'
        )
        return header + prompt + footer

    def _parse_response(self, raw: str) -> dict:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return {
            "review_status": "REJECT",
            "confidence": 0.0,
            "violations": ["parse_error"],
            "summary": raw[:200],
        }
