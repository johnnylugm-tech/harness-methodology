"""
quality_gate.parsers — Markdown document parsers.

Extracted from ab_enforcer and spec_tracking_checker to break the
high-coupling identified by crg-003 (CRG architecture analysis).
"""
from .development_log_parser import DevelopmentLogParser
from .spec_tracking_parser import SpecTrackingParser

__all__ = ["DevelopmentLogParser", "SpecTrackingParser"]
