"""Feature #8 Gap Detector — SPEC.md Parser.

Parses SPEC.md files and detects gaps vs code implementation.
"""

from gap_detector.parser import (
    SpecParser,
    ParsedSpec,
    FeatureItem,
    SpecMetadata,
    ParseStats,
    ParseError,
    SpecParseError,
)

from gap_detector.scanner import (
    CodeScanner,
    ScannedCode,
    CodeItem,
    CodeFile,
    ScanStats,
    ScanError,
    ScanErrorRecord,
)

from gap_detector.detector import (
    GapDetector,
    Gap,
    GapSummary,
    Match,
)

from gap_detector.reporter import (
    GapReporter,
    ReportPaths,
    GapReportJSON,
)

__all__ = [
    "SpecParser", "ParsedSpec", "FeatureItem", "SpecMetadata",
    "ParseStats", "ParseError", "SpecParseError",
    "CodeScanner", "ScannedCode", "CodeItem", "CodeFile",
    "ScanStats", "ScanError", "ScanErrorRecord",
    "GapDetector", "Gap", "GapSummary", "Match",
    "GapReporter", "ReportPaths", "GapReportJSON",
]
