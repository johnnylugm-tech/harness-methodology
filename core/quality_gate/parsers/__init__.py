"""
quality_gate.parsers — Markdown document parsers.
"""
from .spec_tracking_parser import SpecTrackingParser
from .spec_assertion_parser import SpecAssertionParser

__all__ = ["SpecTrackingParser", "SpecAssertionParser"]
