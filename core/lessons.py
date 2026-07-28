"""Cross-run failure memory (Direction C).

Distils each Gate BLOCK / confirmed finding into a retrievable *lesson* so the
next FR or project does not repeat it — the "stably improving" layer
(ReasoningBank / AWM / A-MEM, without fine-tuning). Lessons are markdown +
frontmatter files under ``.methodology/lessons/`` (human-inspectable, mirroring
the auto-memory format).

The recall path is what makes auto-injection safe: it is relevance-GATED (a
lesson unrelated to the querying FR/dimension is never surfaced) and CAPPED, so
injecting lessons into a phase prompt cannot balloon context. record is
idempotent — the same failure is stored once.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

__all__ = [
    "Lesson",
    "lessons_dir",
    "record_lesson",
    "record_gate_block",
    "load_lessons",
    "recall_lessons",
    "format_lessons_block",
]

DEFAULT_RECALL_LIMIT = 5
_FRONT = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
_FIELD = re.compile(r"^(\w+):\s*(.*)$")


@dataclass
class Lesson:
    failure_mode: str          # one line: what went wrong
    fix: str = ""              # one line: how it was resolved / what to do next time
    source: str = "manual"     # gate-block | bug-hunt | manual
    phase: int | None = None
    dimension: str | None = None   # e.g. "mutation_testing", "security"
    fr_ids: list[str] = field(default_factory=list)
    created_at: str = ""       # ISO date

    def key(self) -> str:
        raw = f"{self.source}|{self.dimension or ''}|{self.failure_mode.strip()}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def lessons_dir(project: Path) -> Path:
    return Path(project) / ".methodology" / "lessons"


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _serialise(lesson: Lesson) -> str:
    frs = ", ".join(lesson.fr_ids)
    return (
        "---\n"
        f"key: {lesson.key()}\n"
        f"source: {lesson.source}\n"
        f"phase: {'' if lesson.phase is None else lesson.phase}\n"
        f"dimension: {lesson.dimension or ''}\n"
        f"fr_ids: {frs}\n"
        f"created_at: {lesson.created_at or _today()}\n"
        "---\n\n"
        f"**Failure:** {lesson.failure_mode.strip()}\n"
        f"**Fix:** {lesson.fix.strip()}\n"
    )


def _parse(text: str) -> Lesson | None:
    m = _FRONT.match(text)
    if not m:
        return None
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        fm = _FIELD.match(line)
        if fm:
            meta[fm.group(1)] = fm.group(2).strip()
    body = m.group(2)
    fail = re.search(r"\*\*Failure:\*\*\s*(.*)", body)
    fix = re.search(r"\*\*Fix:\*\*\s*(.*)", body)
    phase_raw = meta.get("phase", "")
    fr_ids = [f.strip() for f in meta.get("fr_ids", "").split(",") if f.strip()]
    return Lesson(
        failure_mode=(fail.group(1).strip() if fail else ""),
        fix=(fix.group(1).strip() if fix else ""),
        source=meta.get("source", "manual"),
        phase=int(phase_raw) if phase_raw.isdigit() else None,
        dimension=meta.get("dimension") or None,
        fr_ids=fr_ids,
        created_at=meta.get("created_at", ""),
    )


def record_lesson(project: Path, lesson: Lesson) -> Path:
    """Persist a lesson (idempotent by key). Returns its file path."""
    if not lesson.created_at:
        lesson.created_at = _today()
    d = lessons_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{lesson.key()}.md"
    if not path.exists():
        path.write_text(_serialise(lesson), encoding="utf-8")
    return path


def record_gate_block(
    project: Path, *, gate_num: int, phase: int | None, fr_id: str | None, result,
    details: dict | None = None,
) -> list[Path]:
    """Distil a blocked gate into lessons — one per reason the gate blocked.

    `result` is a harness_bridge.GateResult (duck-typed here to avoid importing
    the bridge into core). Capture is best-effort: the caller wraps it so a
    lesson-store hiccup can never break the gate flow.

    Round 24 站1: reasons come from core.quality_gate.block_reason, the same
    SSOT cli/gate_cmds.py's diagnostic uses. This function previously carried
    its own copy of the "dimension below threshold" filter and never saw
    `details`, so every anti-fabrication block distilled into the same
    contentless lesson pair — "Gate N blocked: composite 0" / "Resolve the
    findings above". A lesson whose fix restates its own failure teaches the
    next run nothing.
    """
    from core.quality_gate.block_reason import derive_block_reasons

    frs = [fr_id] if fr_id else []
    paths: list[Path] = []
    for reason in derive_block_reasons(gate_num, result, details):
        dimension = reason.items[0] if reason.kind == "dimension_below_threshold" else None
        paths.append(record_lesson(project, Lesson(
            failure_mode=f"Gate {gate_num} blocked [{reason.kind}]: {reason.headline}",
            fix=reason.remediation,
            source="gate-block", phase=phase, dimension=dimension, fr_ids=frs)))
    return paths


def load_lessons(project: Path) -> list[Lesson]:
    d = lessons_dir(project)
    if not d.is_dir():
        return []
    out: list[Lesson] = []
    for f in sorted(d.glob("*.md")):
        try:
            parsed = _parse(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        if parsed is not None:
            out.append(parsed)
    return out


def _relevance(lesson: Lesson, fr_ids: set[str], dimension: str | None) -> int:
    score = 0
    if dimension and lesson.dimension == dimension:
        score += 2
        
    overlap = len(fr_ids & set(lesson.fr_ids))
    score += overlap
    
    # Global lessons (no specific FRs) get a base relevance of 1 so they aren't dropped
    if not lesson.fr_ids:
        score += 1
        
    return score


def recall_lessons(
    project: Path,
    *,
    fr_ids: list[str] | None = None,
    dimension: str | None = None,
    limit: int = DEFAULT_RECALL_LIMIT,
) -> list[Lesson]:
    """Return up to `limit` lessons relevant to (fr_ids, dimension), most
    relevant first. Relevance-GATED: a lesson with zero relevance is never
    returned (the anti-pollution guarantee for prompt injection)."""
    want_frs = set(fr_ids or [])
    scored = [
        (_relevance(le, want_frs, dimension), le.created_at, le)
        for le in load_lessons(project)
    ]
    scored = [t for t in scored if t[0] > 0]
    # sort by score desc, then recency (created_at) desc
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [le for _s, _c, le in scored[: max(0, limit)]]


def format_lessons_block(lessons: list[Lesson]) -> str:
    """Render lessons as a compact prompt-injectable markdown block ("" if none)."""
    if not lessons:
        return ""
    lines = ["### Known failure modes from past runs (avoid repeating these)"]
    for le in lessons:
        tag = f"[{le.dimension}] " if le.dimension else ""
        frs = f" ({', '.join(le.fr_ids)})" if le.fr_ids else ""
        fix = f" → {le.fix}" if le.fix else ""
        lines.append(f"- {tag}{le.failure_mode}{fix}{frs}")
    return "\n".join(lines)
