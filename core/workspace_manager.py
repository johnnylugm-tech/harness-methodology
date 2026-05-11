#!/usr/bin/env python3
"""
Workspace Manager — Symphony-inspired per-FR workspace isolation with path safety.

Three mandatory safety invariants (inspired by Symphony §9.5):
1. Agent operates only inside its assigned workspace directory
2. Workspace path MUST stay within workspace root (prefix check + symlink resolution)
3. Workspace key is sanitized — only [A-Za-z0-9._-] allowed

Usage:
    from core.workspace_manager import WorkspaceManager

    wm = WorkspaceManager(project_root, phase=3)
    ws = wm.create_workspace("FR-01")
    wm.validate_path(some_path, "FR-01")  # raises WorkspaceViolationError if unsafe
    wm.cleanup_workspace("FR-01")
"""

from __future__ import annotations
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class WorkspaceViolationError(Exception):
    """Raised when a path escapes its assigned workspace boundary."""


@dataclass
class WorkspacePath:
    """Resolved workspace path with metadata."""
    base: Path
    fr_id: str
    phase: int
    is_symlink: bool = False


class WorkspaceManager:
    """Manages per-FR isolated workspace directories under .methodology/workspaces/."""

    WORKSPACE_ROOT = ".methodology/workspaces"

    def __init__(self, project_root: Path, phase: int):
        self.project_root = Path(project_root).resolve()
        self.phase = phase
        self.root = self.project_root / self.WORKSPACE_ROOT / f"phase_{phase}"
        self._active: dict[str, WorkspacePath] = {}

    # ── workspace lifecycle ────────────────────────────────────────────────

    def create_workspace(self, fr_id: str) -> Path:
        """Create (or reuse) a workspace directory for fr_id. Returns the path."""
        safe_key = self._sanitize(fr_id)
        ws_path = self.root / safe_key
        created = not ws_path.exists()

        ws_path.mkdir(parents=True, exist_ok=True)
        if created:
            logger.info("Workspace created: %s (FR=%s, phase=%d)", ws_path, fr_id, self.phase)

        self._active[fr_id] = WorkspacePath(
            base=ws_path, fr_id=fr_id, phase=self.phase, is_symlink=ws_path.is_symlink(),
        )
        return ws_path

    def cleanup_workspace(self, fr_id: str) -> None:
        """Remove workspace directory for fr_id after gate pass."""
        safe_key = self._sanitize(fr_id)
        ws_path = self.root / safe_key
        if ws_path.exists():
            shutil.rmtree(ws_path, ignore_errors=True)
            logger.info("Workspace cleaned: %s", ws_path)
        self._active.pop(fr_id, None)

    def list_workspaces(self) -> list[Path]:
        """List all active FR workspace directories."""
        if not self.root.exists():
            return []
        return sorted(p for p in self.root.iterdir() if p.is_dir())

    # ── safety invariants ──────────────────────────────────────────────────

    def validate_path(self, path: Path, fr_id: str) -> bool:
        """Check path is within the FR workspace. Raises WorkspaceViolationError if not."""
        resolved = self.resolve_symlink_aware(path)
        workspace = self.root / self._sanitize(fr_id)
        if not self.is_within_workspace(resolved, fr_id):
            raise WorkspaceViolationError(
                f"Path {path} (resolved: {resolved}) escapes workspace {workspace}"
            )
        return True

    def is_within_workspace(self, path: Path, fr_id: str) -> bool:
        """Check if resolved path is within the FR workspace boundary."""
        resolved = Path(os.path.realpath(path))
        workspace = Path(os.path.realpath(self.root / self._sanitize(fr_id)))
        try:
            resolved.relative_to(workspace)
            return True
        except ValueError:
            return False

    def resolve_symlink_aware(self, path: Path) -> Path:
        """Resolve a path with symlink traversal. Detects symlink escape attacks."""
        resolved = Path(os.path.realpath(path))
        # Verify the resolved path is still under project_root (coarse check)
        try:
            resolved.relative_to(self.project_root)
        except ValueError:
            raise WorkspaceViolationError(
                f"Symlink escape detected: {path} resolves to {resolved} (outside project root)"
            )
        # Verify each segment individually for fine-grained escape
        current = Path("/")
        for segment in path.resolve().parts[1:]:  # skip root '/'
            current = current / segment
            if current.is_symlink():
                target = os.readlink(str(current))
                resolved_target = (current.parent / target).resolve()
                try:
                    resolved_target.relative_to(self.root)
                except ValueError:
                    raise WorkspaceViolationError(
                        f"Symlink segment '{segment}' in {path} points outside workspace root"
                    )
        return resolved

    def verify_no_cross_fr_access(self, current_fr: str, paths: list[Path]) -> list[Path]:
        """Return paths that cross into another FR's workspace."""
        violations = []
        current_ws = self.root / self._sanitize(current_fr)
        for p in paths:
            resolved = Path(os.path.realpath(p))
            try:
                resolved.relative_to(current_ws)
            except ValueError:
                # Not in current workspace — check if it's in another FR's workspace
                for other_fr in self._active:
                    if other_fr != current_fr:
                        other_ws = self.root / self._sanitize(other_fr)
                        try:
                            resolved.relative_to(other_ws)
                            violations.append(p)
                            break
                        except ValueError:
                            pass
        return violations

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _sanitize(identifier: str) -> str:
        """Sanitize identifier: only [A-Za-z0-9._-] allowed (Symphony §9.5 invariant 3)."""
        import re
        sanitized = re.sub(r"[^a-zA-Z0-9._-]", "_", identifier)
        return sanitized or "unnamed"
