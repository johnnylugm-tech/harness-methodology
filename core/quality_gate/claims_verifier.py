"""
claims_verifier.py — Verifies agent claims in sessions_spawn.log.

Minimal implementation referenced by IntegratedStagePassGenerator.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict


@dataclass
class ClaimsVerifyResult:
    passed: bool
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


class ClaimsVerifier:
    """Verifies authenticity of A/B session claims."""

    def __init__(self, project_root: str) -> None:
        self.project_root = Path(project_root)

    def verify_sessions_spawn_log(self) -> ClaimsVerifyResult:
        """
        Verify sessions_spawn.log contains at least two distinct roles.

        Returns:
            ClaimsVerifyResult with passed=True if A/B roles present.
        """
        log_path = self.project_root / "sessions_spawn.log"
        if not log_path.exists():
            return ClaimsVerifyResult(
                passed=False, message="sessions_spawn.log not found"
            )
        try:
            content = log_path.read_text(encoding="utf-8").strip()
            roles: set = set()
            try:
                data = json.loads(content)
                entries = (
                    data.get("sessions", data) if isinstance(data, dict) else data
                )
                if isinstance(entries, list):
                    roles = {e.get("role", "") for e in entries}
                else:
                    roles = {data.get("role", "")}
            except json.JSONDecodeError:
                for line in content.splitlines():
                    try:
                        roles.add(json.loads(line).get("role", ""))
                    except json.JSONDecodeError:
                        pass
            has_ab = len(roles) >= 2
            return ClaimsVerifyResult(
                passed=has_ab,
                message="A/B roles confirmed" if has_ab else "Single-role or empty log",
                details={"roles": list(roles)},
            )
        except Exception as exc:
            return ClaimsVerifyResult(passed=False, message=f"Error: {exc}")
