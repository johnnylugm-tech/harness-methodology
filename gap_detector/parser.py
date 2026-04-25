"""SPEC.md Parser Module.

Parses SPEC.md files and extracts structured feature information including:
- Feature IDs (F1, F2, etc.)
- Feature names, descriptions, acceptance criteria
- Priority levels, dependencies
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class SpecParseError(Exception):
    """Exception raised when SPEC.md parsing fails."""
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


@dataclass
class ParseError:
    line_number: int
    error_type: str
    details: str


@dataclass
class FeatureItem:
    id: str
    name: str
    description: str = ""
    acceptance_criteria: list = field(default_factory=list)
    priority: str = "P2"
    depends_on: list = field(default_factory=list)
    line_number: int = 0
    raw_text: str = ""


@dataclass
class SpecMetadata:
    title: str = ""
    version: str = ""
    created_date: str = ""


@dataclass
class ParseStats:
    total_lines: int = 0
    parsed_features: int = 0
    parse_success_rate: float = 1.0
    errors: list = field(default_factory=list)


@dataclass
class ParsedSpec:
    feature_items: list
    metadata: SpecMetadata = field(default_factory=SpecMetadata)
    parse_stats: ParseStats = field(default_factory=ParseStats)


class SpecParser:
    """Parser for SPEC.md files."""

    FEATURE_BLOCK_PATTERN = re.compile(r"### F(\d+):\s+(.+)")
    METADATA_PATTERN = re.compile(r"^\*\*(\w+)：\*\*\s*(.+)$|^\*\*(\w+):\*\*\s*(.+)$")
    DEPENDS_PATTERN = re.compile(r"\*\*依賴[：:][*]*\s*(.+?)(?:\n|$)")
    CRITERIA_PATTERN = re.compile(r"\*\*驗收標準：\*\*\s*(.+?)(?:\n|$)")

    def __init__(self, spec_path) -> None:
        self.spec_path = str(spec_path)
        self._errors = []
        if not Path(self.spec_path).exists():
            raise SpecParseError("E_FILE_NOT_FOUND", f"SPEC.md file not found: {self.spec_path}")
        if not self.spec_path.endswith(".md"):
            raise SpecParseError("E_NOT_MARKDOWN", f"File is not a Markdown file: {self.spec_path}")

    def parse(self) -> ParsedSpec:
        self._errors = []
        content = self._load_file()
        lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        feature_items = self._parse_features(lines)
        metadata = self._parse_metadata(lines)
        total_lines = len(lines)
        parsed_features = len(feature_items)
        expected = parsed_features + len(self._errors)
        success_rate = min(1.0, parsed_features / max(1, expected)) if expected else 1.0
        return ParsedSpec(
            feature_items=feature_items,
            metadata=metadata,
            parse_stats=ParseStats(total_lines=total_lines, parsed_features=parsed_features,
                                   parse_success_rate=success_rate, errors=self._errors.copy())
        )

    def get_error_log(self):
        return self._errors.copy()

    def _load_file(self) -> str:
        try:
            with open(self.spec_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            raise SpecParseError("E_PARSE_FAILED", f"Failed to read SPEC.md: {e}")

    def _parse_features(self, lines):
        feature_items = []
        current_feature = None
        current_block_lines = []
        in_feature_block = False

        for line_num, line in enumerate(lines, start=1):
            line = line.strip()
            header_match = self.FEATURE_BLOCK_PATTERN.match(line)
            if header_match:
                if current_feature is not None:
                    current_feature.raw_text = "\n".join(current_block_lines)
                    feature_items.append(current_feature)
                current_feature = FeatureItem(
                    id=f"F{header_match.group(1)}",
                    name=header_match.group(2),
                    line_number=line_num
                )
                current_block_lines = [line]
                in_feature_block = True
                continue
            if in_feature_block and current_feature is not None:
                metadata_match = self.METADATA_PATTERN.match(line)
                if metadata_match:
                    key = metadata_match.group(1) or metadata_match.group(3)
                    value = metadata_match.group(2) or metadata_match.group(4)
                    if key == "描述":
                        current_feature.description = value
                    elif key == "優先權" and value.startswith("P"):
                        current_feature.priority = value
                criteria_match = self.CRITERIA_PATTERN.search(line)
                if criteria_match:
                    current_feature.acceptance_criteria = [
                        c.strip() for c in criteria_match.group(1).split(";")
                    ]
                depends_match = self.DEPENDS_PATTERN.search(line)
                if depends_match:
                    current_feature.depends_on = re.findall(r"F\d+", depends_match.group(1))
                current_block_lines.append(line)

        if current_feature is not None:
            current_feature.raw_text = "\n".join(current_block_lines)
            feature_items.append(current_feature)
        return feature_items

    def _parse_metadata(self, lines) -> SpecMetadata:
        metadata = SpecMetadata()
        for line in lines[:20]:
            line = line.strip()
            if line.startswith("# Feature #"):
                m = re.search(r"# Feature #(\d+):\s+(.+)", line)
                if m:
                    metadata.title = m.group(2).strip()
            elif "版本" in line:
                m = re.search(r"\*\*版本[：:]\*\*\s*(.+)", line)
                if m:
                    metadata.version = m.group(1).strip()
        return metadata
