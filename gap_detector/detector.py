"""Gap Detection Module.

Detects gaps between SPEC.md specifications and actual code implementation.
Types: MISSING, INCOMPLETE, ORPHANED.
"""

from dataclasses import dataclass
from typing import Optional

from gap_detector.parser import ParsedSpec, FeatureItem
from gap_detector.scanner import ScannedCode, CodeItem


def _levenshtein_distance(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row: list[int] = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            current_row.append(min(previous_row[j + 1] + 1, current_row[j] + 1, previous_row[j] + (c1 != c2)))
        previous_row = current_row
    return previous_row[-1]


@dataclass
class Match:
    """Match component."""
    spec_item: FeatureItem
    code_item: Optional[CodeItem]
    match_type: str
    similarity: float


@dataclass
class Gap:
    """Gap component."""
    gap_type: str
    spec_item: Optional[str] = None
    code_item: Optional[str] = None
    spec_location: Optional[str] = None
    code_location: Optional[str] = None
    severity: str = "minor"
    reason: str = ""
    recommended_action: str = ""
    downstream_missing: bool = False


@dataclass
class GapSummary:
    """GapSummary component."""
    total_gaps: int = 0
    missing: int = 0
    incomplete: int = 0
    orphaned: int = 0
    critical: int = 0
    major: int = 0
    minor: int = 0


class GapDetector:
    """Detector for gaps between specification and implementation."""

    def __init__(self, spec: ParsedSpec, code: ScannedCode, similarity_threshold: float = 0.6) -> None:
        """Initialize instance."""
        self.spec = spec
        self.code = code
        self.similarity_threshold = similarity_threshold
        self._gaps: list = []
        self._matches: list = []

    def detect(self):
        """Detect."""
        self._gaps: list = []
        self._matches: list = []
        try:
            self._matches = self._match_spec_to_code()
        except Exception:
            return []
        for fn in [self._detect_missing, self._detect_incomplete, self._detect_orphaned]:
            try:
                self._gaps.extend(fn())
            except Exception:  # nosec B110
                pass
        try:
            self._mark_downstream_effects()
        except Exception:  # nosec B110
            pass
        return self._gaps

    def get_summary(self) -> GapSummary:
        """Get summary."""
        if not self._gaps:
            self.detect()
        s = GapSummary(total_gaps=len(self._gaps))
        for g in self._gaps:
            if g.gap_type == "MISSING":
                s.missing += 1
            elif g.gap_type == "INCOMPLETE":
                s.incomplete += 1
            elif g.gap_type == "ORPHANED":
                s.orphaned += 1
            if g.severity == "critical":
                s.critical += 1
            elif g.severity == "major":
                s.major += 1
            elif g.severity == "minor":
                s.minor += 1
        return s

    def _match_spec_to_code(self):
        code_items = [item for m in self.code.modules for item in m.items]
        return [self._find_best_match(si, code_items) for si in self.spec.feature_items]

    def _normalize_name(self, name: str) -> str:
        return name.lower().replace("_", "").replace("-", "").replace(" ", "")

    def _compute_similarity(self, a: str, b: str) -> float:
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        d = _levenshtein_distance(a, b)
        return 1.0 - (d / max(len(a), len(b)))

    def _find_best_match(self, spec_item, code_items):
        sn = self._normalize_name(spec_item.name)
        best_match, best_sim, best_type = None, 0.0, "none"
        for ci in code_items:
            if not ci.is_public:
                continue
            cn = self._normalize_name(ci.name)
            sim = self._compute_similarity(sn, cn)
            if sim == 1.0 and sn == cn:
                return Match(spec_item=spec_item, code_item=ci, match_type="exact", similarity=1.0)
            if sim > best_sim:
                best_sim, best_match = sim, ci
                best_type = "fuzzy" if sim >= self.similarity_threshold else "none"
        if best_type == "fuzzy":
            return Match(spec_item=spec_item, code_item=best_match, match_type="fuzzy", similarity=best_sim)
        return Match(spec_item=spec_item, code_item=None, match_type="none", similarity=0.0)

    def _detect_missing(self):
        return [Gap(
            gap_type="MISSING", spec_item=m.spec_item.name,
            spec_location=f"SPEC.md:Line {m.spec_item.line_number}",
            severity="critical" if m.spec_item.priority == "P0" else "major",
            reason=f"Feature '{m.spec_item.name}' specified but not implemented",
            recommended_action=f"Implement {m.spec_item.name} per SPEC.md"
        ) for m in self._matches if m.code_item is None]

    def _detect_incomplete(self):
        return [Gap(
            gap_type="INCOMPLETE", spec_item=m.spec_item.name, code_item=m.code_item.name,
            spec_location=f"SPEC.md:Line {m.spec_item.line_number}",
            code_location=f"{m.code_item.file_path}:Line {m.code_item.line_number}",
            severity="minor",
            reason=f"'{m.code_item.name}' lacks documentation (docstring)",
            recommended_action=f"Add docstring to {m.code_item.name}"
        ) for m in self._matches if m.code_item is not None and not m.code_item.docstring]

    def _detect_orphaned(self):
        matched_ids = {m.code_item.id for m in self._matches if m.code_item is not None}
        code_items = [item for mod in self.code.modules for item in mod.items]
        return [Gap(
            gap_type="ORPHANED", code_item=ci.name,
            code_location=f"{ci.file_path}:Line {ci.line_number}",
            severity="minor",
            reason=f"'{ci.name}' has no corresponding specification in SPEC.md",
            recommended_action=f"Add spec for {ci.name} or remove if unnecessary"
        ) for ci in code_items if ci.is_public and not ci.name.startswith("_") and ci.id not in matched_ids]

    def _mark_downstream_effects(self):
        missing_names = {g.spec_item for g in self._gaps if g.gap_type == "MISSING"}
        for g in self._gaps:
            if g.gap_type == "MISSING":
                si = next((s for s in self.spec.feature_items
                           if self._normalize_name(s.name) == self._normalize_name(g.spec_item or "")), None)
                if si:
                    for dep_id in si.depends_on:
                        dep = next((s for s in self.spec.feature_items if s.id == dep_id), None)
                        if dep and dep.name in missing_names:
                            g.downstream_missing = True
                            g.reason += " (downstream: depends on missing feature)"
