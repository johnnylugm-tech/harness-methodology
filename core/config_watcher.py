#!/usr/bin/env python3
"""
Config Watcher — Symphony-inspired SKILL.md hot-reload via mtime polling.

Uses stdlib only (os.stat). No pip dependencies required.
Detects SKILL.md YAML frontmatter changes and calls registered callbacks.

Usage:
    from core.config_watcher import ConfigWatcher

    watcher = ConfigWatcher(Path("SKILL.md"))
    watcher.on_change(lambda old, new: print(f"Config changed: {old.version} -> {new.version}"))
    watcher.start()
    ...
    watcher.stop()
"""

from __future__ import annotations
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class ConfigSnapshot:
    """Parsed YAML frontmatter snapshot from SKILL.md."""
    mtime: float = 0.0
    version: str = ""
    constitution_version: str = ""
    gate_thresholds: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


class ConfigWatcher:
    """Watches SKILL.md for YAML frontmatter changes via mtime polling (stdlib only)."""

    def __init__(self, skill_path: Path, poll_interval: float = 5.0):
        self.skill_path = Path(skill_path)
        self.poll_interval = max(poll_interval, 1.0)
        self._callbacks: list[Callable[[ConfigSnapshot, ConfigSnapshot], None]] = []
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._current: ConfigSnapshot = self._parse_frontmatter()

    @property
    def current(self) -> ConfigSnapshot:
        with self._lock:
            return self._current

    def on_change(self, callback: Callable[[ConfigSnapshot, ConfigSnapshot], None]) -> None:
        """Register a callback(old_snapshot, new_snapshot) invoked on config change."""
        self._callbacks.append(callback)

    def start(self) -> None:
        """Start background polling thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="config-watcher")
        self._thread.start()
        logger.info("ConfigWatcher started for %s (poll=%ss)", self.skill_path, self.poll_interval)

    def stop(self) -> None:
        """Stop background polling thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2 * self.poll_interval)
            self._thread = None
        logger.info("ConfigWatcher stopped")

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                new_snapshot = self._parse_frontmatter()
                old_snapshot = self.current
                if new_snapshot.mtime != old_snapshot.mtime:
                    with self._lock:
                        self._current = new_snapshot
                    for cb in self._callbacks:
                        try:
                            cb(old_snapshot, new_snapshot)
                        except Exception:
                            logger.exception("ConfigWatcher callback failed")
            except Exception:
                logger.exception("ConfigWatcher poll error")
            self._stop_event.wait(self.poll_interval)

    def _parse_frontmatter(self) -> ConfigSnapshot:
        """Parse YAML frontmatter from SKILL.md. Returns ConfigSnapshot."""
        snapshot = ConfigSnapshot()
        try:
            stat = os.stat(self.skill_path)
            snapshot.mtime = stat.st_mtime
        except OSError:
            return snapshot

        try:
            content = self.skill_path.read_text(encoding="utf-8")
        except OSError:
            return snapshot

        if not content.startswith("---"):
            return snapshot

        lines = content.split("\n")
        try:
            end = lines.index("---", 1)
        except ValueError:
            return snapshot

        yaml_block = "\n".join(lines[1:end])
        try:
            import yaml  # type: ignore[import-untyped]
            parsed = yaml.safe_load(yaml_block)
            if isinstance(parsed, dict):
                snapshot.raw = parsed
                snapshot.version = str(parsed.get("version", ""))
                snapshot.constitution_version = str(parsed.get("constitution_version", ""))
                snapshot.gate_thresholds = parsed.get("gate_thresholds", {}) or {}
        except Exception:
            logger.debug("Failed to parse SKILL.md YAML frontmatter", exc_info=True)

        return snapshot
