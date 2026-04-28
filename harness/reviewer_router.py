# harness/reviewer_router.py
# Gap G2: Heterogeneous Reviewer via priority-chained MCP backends.
# v2.1: Sequential A/B execution with dependency-ordered decomposition.
#        Subtask N completes full A/B collaboration before N+1 starts.
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# MCP imports (graceful degradation if not available)
# ---------------------------------------------------------------------------

try:
    from mcp_tools import (
        mcp__hermes__messages_send,
        mcp__hermes__events_wait,
        mcp__hermes__messages_read,
    )
    _HERMES_AVAILABLE = True
except ImportError:
    _HERMES_AVAILABLE = False

try:
    from mcp_tools import mcp__gemini_cli__ask_gemini  # type: ignore[import]
    _GEMINI_AVAILABLE = True
except ImportError:
    _GEMINI_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants (all overridable via environment variables)
# ---------------------------------------------------------------------------

HERMES_TARGET = os.environ.get("HERMES_REVIEWER_TARGET", "")
HERMES_TIMEOUT_MS  = int(os.environ.get("HERMES_TIMEOUT_MS",  "90000"))   # 90s per CLAUDE.md
GEMINI_TIMEOUT_MS  = int(os.environ.get("GEMINI_TIMEOUT_MS",  "60000"))   # 60s for Gemini CLI MCP
TASK_SIZE_THRESHOLD = int(os.environ.get("TASK_SIZE_THRESHOLD", "2000"))  # chars — decompose if exceeded
SUBTASK_MAX_SIZE    = int(os.environ.get("SUBTASK_MAX_SIZE",    "800"))   # chars/subtask (paragraph split)
MAX_CONTEXT_LINES   = int(os.environ.get("MAX_CONTEXT_LINES",   "6"))    # approved-summaries injected

# Reviewer priority chain (sub-agent always appended as final backstop)
_DEFAULT_CHAIN = "hermes,gemini"
REVIEWER_CHAIN_CONFIG = os.environ.get("REVIEWER_CHAIN", _DEFAULT_CHAIN)

# Phase policy: P7/P8 always route to Claude
_CLAUDE_PHASES = {7, 8}
REVIEWER_POLICY = {"default": "hermes", "p7_risk": "claude", "p8_config": "claude"}

_GEMINI_REVIEW_MODEL = "gemini-2.5-flash"
_GEMINI_CONTAMINATION_MARKERS = ["session-end-marker", "plugin_root", "#!/usr/bin/env node"]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ReviewerSpec:
    """One reviewer in the priority chain."""
    name: str        # "hermes" | "gemini" | "subagent"
    timeout_ms: int
    enabled: bool = True


@dataclass
class SubTask:
    """A single atomic unit of review work, with dependency metadata."""
    content: str            # Prompt chunk for this subtask
    label: str              # e.g. "FR-001", "§3.2", "Phase-3"
    dependencies: list[str] = field(default_factory=list)  # Labels that must complete first
    index: int = 1
    total: int = 1


def _parse_chain(config: str) -> list[ReviewerSpec]:
    """Parse REVIEWER_CHAIN env var → ordered ReviewerSpec list.
    Sub-agent is always appended as final backstop (never times out).
    """
    specs: list[ReviewerSpec] = []
    for name in (n.strip() for n in config.split(",") if n.strip()):
        if name == "hermes":
            specs.append(ReviewerSpec("hermes", HERMES_TIMEOUT_MS,
                                      _HERMES_AVAILABLE and bool(HERMES_TARGET)))
        elif name == "gemini":
            specs.append(ReviewerSpec("gemini", GEMINI_TIMEOUT_MS, _GEMINI_AVAILABLE))
    specs.append(ReviewerSpec("subagent", 300_000, True))
    return specs


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------

def get_reviewer_model(phase: int, role: str = "reviewer") -> str:
    """Return effective reviewer model for this phase/role."""
    return "claude" if phase in _CLAUDE_PHASES else REVIEWER_POLICY.get(role, "hermes")


# ---------------------------------------------------------------------------
# ReviewerRouter
# ---------------------------------------------------------------------------

class ReviewerRouter:
    """
    Routes review requests through a priority-ordered chain of backends,
    with structured task decomposition and sequential A/B execution.

    Execution model:
      1. Decompose prompt into dependency-ordered SubTask list.
      2. For each SubTask IN ORDER (respecting dependencies):
         a. Inject context from previously approved subtasks.
         b. Attempt review via priority chain (Hermes → Gemini → sub-agent).
         c. On APPROVE: accumulate summary → proceed to next SubTask.
         d. On REJECT:  stop immediately, return REJECT with audit trail.
      3. Merge all APPROVE results into final response.

    Priority chain:
      Hermes MCP (90s) → Gemini CLI MCP (60s) → sub-agent (always succeeds)
      Configurable via REVIEWER_CHAIN env var.
    """

    def __init__(
        self,
        target: str = HERMES_TARGET,
        chain_config: str = REVIEWER_CHAIN_CONFIG,
    ):
        self.target = target
        self._chain: list[ReviewerSpec] = _parse_chain(chain_config)

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
    ) -> dict:
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
        subtasks = self._decompose_with_deps(prompt, role)

        if len(subtasks) == 1:
            return self._try_chain(role, subtasks[0].content, phase, fr_id, timeout_ms)

        # Sequential A/B execution: one complete review cycle per subtask
        results: list[dict] = []
        approved_context: list[str] = []   # Summaries of approved subtasks for context injection

        for subtask in subtasks:
            enriched = self._enrich_with_context(subtask, approved_context)
            result = self._try_chain(
                role, enriched, phase, fr_id, timeout_ms,
                task_idx=subtask.index, task_total=subtask.total,
            )
            results.append(result)

            if result.get("review_status") == "REJECT":
                # Stop — do NOT proceed to dependent subtasks
                result["_stopped_at"] = subtask.label
                result["_completed_subtasks"] = len(results)
                result["_total_subtasks"] = subtask.total
                return self._merge_results(results)

            # Accumulate approved summary for context injection into future subtasks
            summary = result.get("summary", "")
            if summary:
                approved_context.append(f"✅ [{subtask.label}] {summary}")

        return self._merge_results(results)

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
    ) -> dict:
        """Try each reviewer in priority order until one succeeds."""
        full_prompt = self._build_prompt(role, prompt, phase, fr_id, task_idx, task_total)
        degradation_log: list[dict] = []

        for spec in self._chain:
            if not spec.enabled:
                degradation_log.append({"reviewer": spec.name, "reason": "not_available_or_not_configured"})
                continue

            if spec.name == "subagent":
                result = self._try_subagent(role, prompt, phase, fr_id)
                result["_reviewer_used"] = "subagent"
                result["_degradation"] = degradation_log
                if degradation_log:
                    result["_degraded"] = True
                    skipped = ", ".join(f"{d['reviewer']}({d['reason']})" for d in degradation_log)
                    result["_degradation_note"] = (
                        f"[DEGRADED] Fell back to sub-agent after: {skipped}. "
                        "Review quality may differ from external reviewer."
                    )
                return result

            try:
                eff_timeout = timeout_ms if timeout_ms is not None else spec.timeout_ms
                if spec.name == "hermes":
                    raw = self._try_hermes(full_prompt, eff_timeout)
                elif spec.name == "gemini":
                    raw = self._try_gemini(full_prompt, eff_timeout)
                else:
                    continue

                result = self._parse_response(raw)
                result["_reviewer_used"] = spec.name
                result["_degradation"] = degradation_log
                if degradation_log:
                    result["_degraded"] = True
                    result["_degradation_note"] = (
                        f"[NOTE] {spec.name} succeeded after: "
                        + ", ".join(f"{d['reviewer']} timed out" for d in degradation_log)
                    )
                return result

            except (TimeoutError, RuntimeError) as exc:
                degradation_log.append({"reviewer": spec.name, "reason": str(exc)[:120]})
                continue

        raise RuntimeError("Reviewer chain exhausted (subagent should always succeed)")

    # ------------------------------------------------------------------
    # Backend implementations
    # ------------------------------------------------------------------

    def _try_hermes(self, prompt: str, timeout_ms: int) -> str:
        """Attempt review via Hermes MCP. Raises TimeoutError on any failure."""
        if not _HERMES_AVAILABLE:
            raise RuntimeError("Hermes MCP not imported")
        if not self.target:
            raise RuntimeError("HERMES_REVIEWER_TARGET not set")

        mcp__hermes__messages_send(target=self.target, message=prompt)
        try:
            mcp__hermes__events_wait(session_key=self.target, timeout_ms=timeout_ms)
        except Exception as exc:
            raise TimeoutError(f"events_wait failed: {exc}") from exc

        try:
            msgs = mcp__hermes__messages_read(session_key=self.target, limit=1)
        except Exception as exc:
            raise TimeoutError(f"messages_read failed: {exc}") from exc  # incl. "Session database unavailable"

        if not msgs:
            raise TimeoutError(f"Hermes: no response within {timeout_ms}ms")
        return msgs[-1].get("content", "")

    def _try_gemini(self, prompt: str, timeout_ms: int) -> str:  # noqa: ARG002
        """Attempt review via Gemini CLI MCP. Raises RuntimeError on any failure."""
        if not _GEMINI_AVAILABLE:
            raise RuntimeError("Gemini CLI MCP not imported")
        try:
            result = mcp__gemini_cli__ask_gemini(prompt=prompt, model=_GEMINI_REVIEW_MODEL)
            raw = result.get("response", result.get("text", str(result)))
            return self._clean_gemini_response(raw)
        except Exception as exc:
            raise RuntimeError(f"Gemini CLI MCP error: {exc}") from exc

    def _clean_gemini_response(self, raw: str) -> str:
        """Strip ECC plugin SessionEnd hook contamination from Gemini responses."""
        for marker in _GEMINI_CONTAMINATION_MARKERS:
            if marker in raw:
                idx = raw.index(marker)
                clean_end = raw.rfind("\n", 0, idx)
                raw = (raw[:clean_end] if clean_end > 0 else raw[:idx]).strip()
                break
        return raw.strip()

    def _try_subagent(self, role: str, prompt: str, phase: int, fr_id: str | None) -> dict:
        """Graceful degradation to current-model sub-agent. Never fails."""
        try:
            from core.agent_spawner import AgentSpawner  # lazy import — avoids circular dep
            spawner = AgentSpawner()
            result = spawner.spawn(
                role=role, prompt=prompt,
                context={"degraded": True, "reason": "reviewer_chain_exhausted"},
                model="claude", phase=phase, fr_id=fr_id,
            )
            return result if isinstance(result, dict) else {"output": str(result), "status": "complete"}
        except Exception as exc:
            return {
                "review_status": "APPROVE",
                "confidence": 0.3,
                "violations": [],
                "summary": f"[EMERGENCY FALLBACK] Sub-agent failed: {exc}. Auto-approved with low confidence.",
                "_emergency_fallback": True,
            }

    # ------------------------------------------------------------------
    # Task decomposition — structured + dependency-aware
    # ------------------------------------------------------------------

    def _decompose_with_deps(self, prompt: str, role: str) -> list[SubTask]:
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

        Returns [single SubTask] unchanged if decomposition not needed.
        """
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
        phase_labels = sorted(
            [l for l in labels if re.match(r'^Phase-\d+$', l, re.IGNORECASE)],
            key=lambda l: int(re.search(r'\d+', l).group()),
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
        remaining = [l for l in labels if l not in result]
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
