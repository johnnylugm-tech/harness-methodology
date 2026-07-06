"""cr_manager.py — Phase 9 Change Request ticket lifecycle (ASPICE SUP.9/SUP.10).

A CR (Change Request) is the unit of work inside Phase 9 "Maintenance":
  - type="bug"  → SUP.9 Problem Resolution Management (CR-BUG)
  - type="feat" → SUP.10 Change Request Management     (CR-FEAT)

Tickets live at `.methodology/change_requests/CR-NN.json` (machine state,
single source of truth); the human-readable index is
`09-maintenance/MAINTENANCE_LOG.md`, appended on closure.

State machine (forward-only; any non-terminal state may go to REJECTED):

    OPEN → ANALYZED → APPROVED → IN_PROGRESS → VERIFIED → CLOSED
      └────────┴──────────┴────────────┴───────────┴──→ REJECTED

Transition validation is fail-closed and mirrors the bug_hunt_report
anti-fabrication contract:
  - → ANALYZED : bug needs root_cause; feat needs affected_frs + impact_analysis
  - → APPROVED : bug needs repro_test (file must EXIST on disk);
                 feat needs approval{approved_by, justification}
  - → CLOSED   : only from VERIFIED, needs resolution.fix_commit;
                 bug additionally re-verifies repro_test still exists
  - → REJECTED : needs rejected_reason

The deeper harness-integration closure checks (Gate 1 scores in
quality_manifest, trace attestation, drift) belong to `cr-close` in
harness_cli.py — this module owns only the ticket-intrinsic invariants.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.atomic_io import StateTransaction, atomic_write_json, atomic_write_text
from core.canonical_form import canonical_form
from core.utils.project_layout import ProjectLayout

_STATES = ("OPEN", "ANALYZED", "APPROVED", "IN_PROGRESS", "VERIFIED", "CLOSED", "REJECTED")
_ORDER = {s: i for i, s in enumerate(("OPEN", "ANALYZED", "APPROVED", "IN_PROGRESS", "VERIFIED", "CLOSED"))}
_TERMINAL = ("CLOSED", "REJECTED")
_TYPES = ("bug", "feat")


class CRValidationError(ValueError):
    """A CR field/transition violates the ticket contract (fail-closed)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CRManager:
    """Create/load/transition Phase 9 Change Request tickets."""

    def __init__(self, project_root: Path | str):
        self.project = Path(project_root).resolve()
        self.layout = ProjectLayout(self.project)
        self.cr_dir = self.layout.change_requests_dir

    # ── storage ───────────────────────────────────────────────────────────

    def _path(self, cr_id: str) -> Path:
        return self.cr_dir / f"{cr_id}.json"

    def load(self, cr_id: str) -> dict[str, Any]:
        cr_id = canonical_form(cr_id)
        p = self._path(cr_id)
        if not p.exists():
            raise CRValidationError(f"{cr_id} not found at {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise CRValidationError(f"{cr_id} is corrupt (not a JSON object)")
        return data

    def save(self, cr: dict[str, Any]) -> Path:
        cr["updated_at"] = _now()
        p = self._path(cr["id"])
        atomic_write_json(p, cr)
        return p

    def list_all(self) -> list[dict[str, Any]]:
        if not self.cr_dir.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for p in sorted(self.cr_dir.glob("CR-*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("id"):
                    out.append(data)
            except (json.JSONDecodeError, OSError):
                # Surface corrupt tickets instead of silently hiding them.
                out.append({"id": p.stem, "status": "CORRUPT", "title": f"(unreadable: {p.name})"})
        return out

    def _next_id(self) -> str:
        max_n = 0
        if self.cr_dir.is_dir():
            for p in self.cr_dir.glob("CR-*.json"):
                try:
                    n = int(p.stem.split("-", 1)[1])
                    max_n = max(max_n, n)
                except (IndexError, ValueError):
                    continue
        return f"CR-{max_n + 1:02d}"

    # ── lifecycle ─────────────────────────────────────────────────────────

    def create(
        self,
        cr_type: str,
        title: str,
        description: str = "",
        severity: Optional[str] = None,
    ) -> dict[str, Any]:
        if cr_type not in _TYPES:
            raise CRValidationError(f"type must be one of {_TYPES}, got {cr_type!r}")
        if not title or len(title.strip()) < 3:
            raise CRValidationError("title must be at least 3 characters")
        cr: dict[str, Any] = {
            "id": self._next_id(),
            "type": cr_type,
            "title": title.strip(),
            "description": description,
            "status": "OPEN",
            "affected_frs": [],
            "created_at": _now(),
        }
        if severity:
            cr["severity"] = severity
        self.save(cr)
        return cr

    def validate_transition(self, cr: dict[str, Any], new_status: str) -> list[str]:
        """Return the list of unmet requirements for `cr` → `new_status`.

        Empty list = transition allowed. Fail-closed: unknown states and
        backward moves are violations, not silent no-ops.
        """
        problems: list[str] = []
        cur = str(cr.get("status", ""))
        if new_status not in _STATES:
            return [f"unknown status {new_status!r} (valid: {', '.join(_STATES)})"]
        if cur in _TERMINAL:
            return [f"{cr.get('id')} is {cur} — terminal, no further transitions"]

        if new_status == "REJECTED":
            if not str(cr.get("rejected_reason", "")).strip():
                problems.append("REJECTED requires rejected_reason")
            return problems

        if cur not in _ORDER or new_status not in _ORDER:
            return [f"invalid transition {cur} → {new_status}"]
        if _ORDER[new_status] != _ORDER[cur] + 1:
            problems.append(
                f"non-sequential transition {cur} → {new_status} "
                f"(next allowed: {list(_ORDER)[_ORDER[cur] + 1]})"
            )
            return problems

        cr_type = cr.get("type")
        if new_status == "ANALYZED":
            if cr_type == "bug" and not str(cr.get("root_cause", "")).strip():
                problems.append("CR-BUG → ANALYZED requires root_cause (SUP.9 analysis)")
            if cr_type == "feat":
                if not cr.get("affected_frs"):
                    problems.append("CR-FEAT → ANALYZED requires affected_frs (new/changed FR IDs)")
                if not isinstance(cr.get("impact_analysis"), dict) or not cr["impact_analysis"]:
                    problems.append("CR-FEAT → ANALYZED requires impact_analysis (SUP.10 analysis)")
        elif new_status == "APPROVED":
            if cr_type == "bug":
                repro = str(cr.get("repro_test", "")).strip()
                if not repro:
                    problems.append("CR-BUG → APPROVED requires repro_test (failing test path)")
                elif not (self.project / repro).is_file():
                    problems.append(f"repro_test '{repro}' does not exist in the project (anti-fabrication)")
            if cr_type == "feat":
                approval = cr.get("approval")
                if (not isinstance(approval, dict)
                        or not str(approval.get("approved_by", "")).strip()
                        or not str(approval.get("justification", "")).strip()):
                    problems.append(
                        "CR-FEAT → APPROVED requires approval{approved_by, justification} "
                        "(SUP.10 approval decision)")
        elif new_status == "CLOSED":
            problems.extend(self.closure_problems(cr))
        return problems

    def _apply_transition(self, cr: dict[str, Any], new_status: str) -> dict[str, Any]:
        """Validate + mutate in memory; no IO (see close() for staged writes)."""
        problems = self.validate_transition(cr, new_status)
        if problems:
            raise CRValidationError(
                f"{cr['id']}: cannot transition {cr.get('status')} → {new_status}:\n  - "
                + "\n  - ".join(problems)
            )
        cr["status"] = new_status
        if new_status in _TERMINAL:
            cr["closed_at"] = _now()
        return cr

    def transition(self, cr_id: str, new_status: str) -> dict[str, Any]:
        cr = self._apply_transition(self.load(cr_id), new_status)
        self.save(cr)
        return cr

    def close(self, cr_id: str) -> tuple[dict[str, Any], Path]:
        """CLOSED transition + MAINTENANCE_LOG append as one StateTransaction.

        A crash between the two writes previously left a CLOSED ticket
        missing from the human-readable index. The log row is staged first,
        the authoritative CR json last, so a partial commit never shows a
        closed ticket without its log entry.
        """
        cr = self._apply_transition(self.load(cr_id), "CLOSED")
        cr["updated_at"] = _now()
        log_path, log_content = self._render_maintenance_log(cr)
        with StateTransaction(self.project) as txn:
            txn.stage_text(log_path, log_content)
            txn.stage_json(self._path(cr["id"]), cr)
            txn.commit()
        return cr, log_path

    def update_fields(self, cr_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Merge field updates (no status change — use transition for that)."""
        cr = self.load(cr_id)
        if cr.get("status") in _TERMINAL:
            raise CRValidationError(f"{cr['id']} is {cr['status']} — terminal, fields frozen")
        if "status" in fields:
            raise CRValidationError("use transition() / --status for status changes")
        if "id" in fields and fields["id"] != cr["id"]:
            raise CRValidationError("id is immutable")
        if "affected_frs" in fields:
            fields["affected_frs"] = [canonical_form(f) for f in fields["affected_frs"]]
        cr.update(fields)
        self.save(cr)
        return cr

    # ── closure contract (ticket-intrinsic part) ──────────────────────────

    def closure_problems(self, cr: dict[str, Any]) -> list[str]:
        """Ticket-intrinsic closure requirements (evidence present & real).

        The harness-integration half (Gate 1 scores, attestation, drift)
        is enforced by `cr-close` in harness_cli.py on top of this.
        """
        problems: list[str] = []
        resolution = cr.get("resolution")
        if not isinstance(resolution, dict) or not str(resolution.get("fix_commit", "")).strip():
            problems.append("CLOSED requires resolution.fix_commit (anti-fabrication)")
        if cr.get("type") == "bug":
            repro = str(cr.get("repro_test", "")).strip()
            if not repro:
                problems.append("CR-BUG CLOSED requires repro_test")
            elif not (self.project / repro).is_file():
                problems.append(f"repro_test '{repro}' does not exist in the project")
        if not cr.get("affected_frs"):
            problems.append("CLOSED requires affected_frs (which FRs did this CR touch?)")
        return problems

    # ── maintenance log ───────────────────────────────────────────────────

    def _render_maintenance_log(self, cr: dict[str, Any]) -> tuple[Path, str]:
        """Full post-append log content for *cr*; no IO (staged by close())."""
        log_path = self.layout.maintenance_log_path
        if log_path.exists():
            content = log_path.read_text(encoding="utf-8")
        else:
            content = (
                "# MAINTENANCE_LOG — Phase 9 Change Request Index\n\n"
                "> ASPICE SUP.9 (problem resolution) / SUP.10 (change request management).\n"
                "> Machine state: `.methodology/change_requests/CR-NN.json` — this file is the human-readable index.\n\n"
                "| CR | Type | Title | Status | FRs | Fix commit | Closed |\n"
                "|----|------|-------|--------|-----|------------|--------|\n"
            )
        resolution = cr.get("resolution") or {}
        row = (
            f"| {cr['id']} | CR-{'BUG' if cr.get('type') == 'bug' else 'FEAT'} "
            f"| {cr.get('title', '')} | {cr.get('status', '')} "
            f"| {', '.join(cr.get('affected_frs', []))} "
            f"| {resolution.get('fix_commit', '—')} "
            f"| {cr.get('closed_at', '—')} |\n"
        )
        if not content.endswith("\n"):
            content += "\n"
        return log_path, content + row

    def append_maintenance_log(self, cr: dict[str, Any]) -> Path:
        """Append the closed/rejected CR to 09-maintenance/MAINTENANCE_LOG.md."""
        log_path, content = self._render_maintenance_log(cr)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(log_path, content)
        return log_path
