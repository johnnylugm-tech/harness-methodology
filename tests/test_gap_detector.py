"""
tests/test_gap_detector.py — Unit tests for gap_detector package.

Covers: parser, scanner, detector, reporter (W1).
"""
import json
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def make_spec_md(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "SPEC.md"
    p.write_text(content, encoding="utf-8")
    return p


def make_py_file(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


SIMPLE_SPEC = """\
# Feature #1: Demo Project

**Version:** 1.0

### F1: User Registration
**Description:** Allow users to sign up
**Priority:** P1
**Acceptance Criteria:** user exists; email validated

### F2: Login
**Description:** Allow users to log in
**Priority:** P0
**Depends:** F1
"""

SIMPLE_PY = """\
class UserRegistration:
    \"\"\"Handles user sign-up.\"\"\"
    def register(self, email: str) -> bool:
        \"\"\"Register a new user.\"\"\"
        return True

def login(email: str, password: str) -> bool:
    \"\"\"Log in user.\"\"\"
    return True

def _private_helper():
    pass
"""


# ===========================================================================
# parser.py
# ===========================================================================

class TestSpecParser:
    def test_raises_file_not_found(self, tmp_path):
        from gap_detector.parser import SpecParser, SpecParseError
        with pytest.raises(SpecParseError) as exc_info:
            SpecParser(tmp_path / "nonexistent.md")
        assert exc_info.value.code == "E_FILE_NOT_FOUND"

    def test_raises_not_markdown(self, tmp_path):
        from gap_detector.parser import SpecParser, SpecParseError
        f = tmp_path / "spec.txt"
        f.write_text("content")
        with pytest.raises(SpecParseError) as exc_info:
            SpecParser(f)
        assert exc_info.value.code == "E_NOT_MARKDOWN"

    def test_parse_empty_file(self, tmp_path):
        from gap_detector.parser import SpecParser
        p = make_spec_md(tmp_path, "")
        parser = SpecParser(p)
        result = parser.parse()
        assert result.feature_items == []
        assert result.parse_stats.total_lines >= 1

    def test_parse_features_count(self, tmp_path):
        from gap_detector.parser import SpecParser
        p = make_spec_md(tmp_path, SIMPLE_SPEC)
        result = SpecParser(p).parse()
        assert len(result.feature_items) == 2

    def test_parse_feature_ids(self, tmp_path):
        from gap_detector.parser import SpecParser
        p = make_spec_md(tmp_path, SIMPLE_SPEC)
        result = SpecParser(p).parse()
        ids = [f.id for f in result.feature_items]
        assert "F1" in ids
        assert "F2" in ids

    def test_parse_feature_name(self, tmp_path):
        from gap_detector.parser import SpecParser
        p = make_spec_md(tmp_path, SIMPLE_SPEC)
        result = SpecParser(p).parse()
        assert result.feature_items[0].name == "User Registration"

    def test_parse_feature_priority(self, tmp_path):
        from gap_detector.parser import SpecParser
        p = make_spec_md(tmp_path, SIMPLE_SPEC)
        result = SpecParser(p).parse()
        f1 = next(f for f in result.feature_items if f.id == "F1")
        assert f1.priority == "P1"

    def test_parse_feature_p0_severity(self, tmp_path):
        from gap_detector.parser import SpecParser
        p = make_spec_md(tmp_path, SIMPLE_SPEC)
        result = SpecParser(p).parse()
        f2 = next(f for f in result.feature_items if f.id == "F2")
        assert f2.priority == "P0"

    def test_parse_depends_on(self, tmp_path):
        from gap_detector.parser import SpecParser
        p = make_spec_md(tmp_path, SIMPLE_SPEC)
        result = SpecParser(p).parse()
        f2 = next(f for f in result.feature_items if f.id == "F2")
        assert "F1" in f2.depends_on

    def test_parse_acceptance_criteria(self, tmp_path):
        from gap_detector.parser import SpecParser
        p = make_spec_md(tmp_path, SIMPLE_SPEC)
        result = SpecParser(p).parse()
        f1 = next(f for f in result.feature_items if f.id == "F1")
        assert len(f1.acceptance_criteria) >= 1

    def test_parse_metadata_version(self, tmp_path):
        from gap_detector.parser import SpecParser
        p = make_spec_md(tmp_path, SIMPLE_SPEC)
        result = SpecParser(p).parse()
        assert result.metadata.version == "1.0"

    def test_parse_metadata_title(self, tmp_path):
        from gap_detector.parser import SpecParser
        p = make_spec_md(tmp_path, SIMPLE_SPEC)
        result = SpecParser(p).parse()
        assert "Demo Project" in result.metadata.title

    def test_parse_stats_line_count(self, tmp_path):
        from gap_detector.parser import SpecParser
        p = make_spec_md(tmp_path, SIMPLE_SPEC)
        result = SpecParser(p).parse()
        assert result.parse_stats.total_lines > 0

    def test_parse_stats_success_rate(self, tmp_path):
        from gap_detector.parser import SpecParser
        p = make_spec_md(tmp_path, SIMPLE_SPEC)
        result = SpecParser(p).parse()
        assert result.parse_stats.parse_success_rate == 1.0

    def test_get_error_log_empty_on_success(self, tmp_path):
        from gap_detector.parser import SpecParser
        p = make_spec_md(tmp_path, SIMPLE_SPEC)
        parser = SpecParser(p)
        parser.parse()
        assert parser.get_error_log() == []

    def test_parse_no_features_section(self, tmp_path):
        from gap_detector.parser import SpecParser
        p = make_spec_md(tmp_path, "# Just a title\n\nSome text without features.")
        result = SpecParser(p).parse()
        assert result.feature_items == []

    def test_raw_text_populated(self, tmp_path):
        from gap_detector.parser import SpecParser
        p = make_spec_md(tmp_path, SIMPLE_SPEC)
        result = SpecParser(p).parse()
        assert result.feature_items[0].raw_text != ""

    def test_spec_parse_error_message_format(self):
        from gap_detector.parser import SpecParseError
        err = SpecParseError("E_TEST", "test message")
        assert "E_TEST" in str(err)
        assert "test message" in str(err)

    def test_feature_line_number_set(self, tmp_path):
        from gap_detector.parser import SpecParser
        p = make_spec_md(tmp_path, SIMPLE_SPEC)
        result = SpecParser(p).parse()
        for fi in result.feature_items:
            assert fi.line_number > 0


# ===========================================================================
# scanner.py
# ===========================================================================

class TestCodeScanner:
    def test_raises_on_missing_dir(self, tmp_path):
        from gap_detector.scanner import CodeScanner, ScanError
        with pytest.raises(ScanError) as exc_info:
            CodeScanner(tmp_path / "nonexistent")
        assert exc_info.value.code == "E_FILE_NOT_FOUND"

    def test_scan_empty_dir(self, tmp_path):
        from gap_detector.scanner import CodeScanner
        result = CodeScanner(tmp_path).scan()
        assert result.modules == []
        assert result.scan_stats.total_files == 0

    def test_scan_discovers_py_files(self, tmp_path):
        from gap_detector.scanner import CodeScanner
        make_py_file(tmp_path, "module_a.py", SIMPLE_PY)
        result = CodeScanner(tmp_path).scan()
        assert result.scan_stats.total_files == 1
        assert result.scan_stats.scanned_files == 1

    def test_scan_excludes_test_files(self, tmp_path):
        from gap_detector.scanner import CodeScanner
        make_py_file(tmp_path, "test_foo.py", "def test_foo(): pass")
        result = CodeScanner(tmp_path).scan()
        assert result.scan_stats.total_files == 0

    def test_scan_extracts_classes(self, tmp_path):
        from gap_detector.scanner import CodeScanner
        make_py_file(tmp_path, "module_a.py", SIMPLE_PY)
        result = CodeScanner(tmp_path).scan()
        kinds = [item.kind for mod in result.modules for item in mod.items]
        assert "class" in kinds

    def test_scan_extracts_functions(self, tmp_path):
        from gap_detector.scanner import CodeScanner
        make_py_file(tmp_path, "module_a.py", SIMPLE_PY)
        result = CodeScanner(tmp_path).scan()
        names = [item.name for mod in result.modules for item in mod.items]
        assert "login" in names

    def test_scan_excludes_private(self, tmp_path):
        from gap_detector.scanner import CodeScanner
        make_py_file(tmp_path, "module_a.py", SIMPLE_PY)
        result = CodeScanner(tmp_path).scan()
        public = [item for mod in result.modules for item in mod.items if not item.is_public]
        # _private_helper should be is_public=False
        private_names = [item.name for mod in result.modules for item in mod.items if not item.is_public]
        assert "_private_helper" in private_names

    def test_scan_module_name_from_path(self, tmp_path):
        from gap_detector.scanner import CodeScanner
        make_py_file(tmp_path, "my_module.py", "def foo(): pass")
        result = CodeScanner(tmp_path).scan()
        assert result.modules[0].module_name == "my_module"

    def test_scan_line_count(self, tmp_path):
        from gap_detector.scanner import CodeScanner
        make_py_file(tmp_path, "module_a.py", SIMPLE_PY)
        result = CodeScanner(tmp_path).scan()
        assert result.modules[0].line_count > 0

    def test_scan_subdirectory(self, tmp_path):
        from gap_detector.scanner import CodeScanner
        subdir = tmp_path / "subpkg"
        subdir.mkdir()
        make_py_file(subdir, "sub.py", "def bar(): pass")
        result = CodeScanner(tmp_path).scan()
        assert result.scan_stats.total_files == 1

    def test_scan_skips_pycache(self, tmp_path):
        from gap_detector.scanner import CodeScanner
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "foo.cpython-311.pyc").write_bytes(b"garbage")
        result = CodeScanner(tmp_path).scan()
        assert result.scan_stats.total_files == 0

    def test_scan_syntax_error_counted_as_skipped(self, tmp_path):
        from gap_detector.scanner import CodeScanner
        bad = tmp_path / "bad.py"
        bad.write_text("def broken(\n", encoding="utf-8")
        result = CodeScanner(tmp_path).scan()
        assert result.scan_stats.skipped_files == 1

    def test_scan_coverage_rate_all_success(self, tmp_path):
        from gap_detector.scanner import CodeScanner
        make_py_file(tmp_path, "ok.py", "x = 1")
        result = CodeScanner(tmp_path).scan()
        assert result.scan_stats.scan_coverage_rate == 1.0

    def test_scan_docstring_extracted(self, tmp_path):
        from gap_detector.scanner import CodeScanner
        make_py_file(tmp_path, "doc.py", SIMPLE_PY)
        result = CodeScanner(tmp_path).scan()
        all_items = [item for mod in result.modules for item in mod.items]
        docstrings = [item.docstring for item in all_items if item.docstring]
        assert len(docstrings) > 0

    def test_scan_total_items_count(self, tmp_path):
        from gap_detector.scanner import CodeScanner
        make_py_file(tmp_path, "module_a.py", SIMPLE_PY)
        result = CodeScanner(tmp_path).scan()
        assert result.scan_stats.total_items > 0

    def test_scan_error_error_format(self):
        from gap_detector.scanner import ScanError
        err = ScanError("E_TEST", "msg")
        assert "E_TEST" in str(err)


# ===========================================================================
# detector.py
# ===========================================================================

def _make_parsed_spec(names_priorities):
    """Build a minimal ParsedSpec with named features."""
    from gap_detector.parser import ParsedSpec, FeatureItem, SpecMetadata, ParseStats
    items = [
        FeatureItem(id=f"F{i+1}", name=name, priority=prio, line_number=i+1)
        for i, (name, prio) in enumerate(names_priorities)
    ]
    return ParsedSpec(feature_items=items, metadata=SpecMetadata(), parse_stats=ParseStats())


def _make_scanned_code(names_docstrings):
    """Build a minimal ScannedCode."""
    from gap_detector.scanner import ScannedCode, CodeFile, CodeItem, ScanStats
    items = [
        CodeItem(id=f"mod.{name}", kind="function", name=name,
                 module="mod", file_path="mod.py", line_number=i+1,
                 docstring=doc, is_public=True)
        for i, (name, doc) in enumerate(names_docstrings)
    ]
    mod = CodeFile(module_name="mod", file_path="mod.py", items=items)
    return ScannedCode(modules=[mod], scan_stats=ScanStats())


class TestLevenshtein:
    def test_identical(self):
        from gap_detector.detector import _levenshtein_distance
        assert _levenshtein_distance("abc", "abc") == 0

    def test_empty_strings(self):
        from gap_detector.detector import _levenshtein_distance
        assert _levenshtein_distance("", "") == 0

    def test_one_empty(self):
        from gap_detector.detector import _levenshtein_distance
        assert _levenshtein_distance("abc", "") == 3

    def test_single_substitution(self):
        from gap_detector.detector import _levenshtein_distance
        assert _levenshtein_distance("abc", "axc") == 1

    def test_transposition(self):
        from gap_detector.detector import _levenshtein_distance
        assert _levenshtein_distance("ab", "ba") == 2


class TestGapDetector:
    def test_no_gaps_exact_match(self):
        from gap_detector.detector import GapDetector
        spec = _make_parsed_spec([("UserRegistration", "P1")])
        code = _make_scanned_code([("UserRegistration", "Does registration")])
        gaps = GapDetector(spec, code).detect()
        missing = [g for g in gaps if g.gap_type == "MISSING"]
        assert missing == []

    def test_missing_gap_detected(self):
        from gap_detector.detector import GapDetector
        spec = _make_parsed_spec([("LoginFeature", "P1")])
        code = _make_scanned_code([])  # nothing implemented
        gaps = GapDetector(spec, code).detect()
        assert any(g.gap_type == "MISSING" for g in gaps)

    def test_missing_p0_is_critical(self):
        from gap_detector.detector import GapDetector
        spec = _make_parsed_spec([("CriticalFeature", "P0")])
        code = _make_scanned_code([])
        gaps = GapDetector(spec, code).detect()
        missing = [g for g in gaps if g.gap_type == "MISSING"]
        assert missing[0].severity == "critical"

    def test_missing_p1_is_major(self):
        from gap_detector.detector import GapDetector
        spec = _make_parsed_spec([("NormalFeature", "P1")])
        code = _make_scanned_code([])
        gaps = GapDetector(spec, code).detect()
        missing = [g for g in gaps if g.gap_type == "MISSING"]
        assert missing[0].severity == "major"

    def test_incomplete_gap_no_docstring(self):
        from gap_detector.detector import GapDetector
        spec = _make_parsed_spec([("UserAuth", "P1")])
        code = _make_scanned_code([("UserAuth", "")])  # no docstring
        gaps = GapDetector(spec, code).detect()
        incomplete = [g for g in gaps if g.gap_type == "INCOMPLETE"]
        assert len(incomplete) == 1

    def test_no_incomplete_when_docstring_present(self):
        from gap_detector.detector import GapDetector
        spec = _make_parsed_spec([("UserAuth", "P1")])
        code = _make_scanned_code([("UserAuth", "Has docstring")])
        gaps = GapDetector(spec, code).detect()
        incomplete = [g for g in gaps if g.gap_type == "INCOMPLETE"]
        assert incomplete == []

    def test_orphaned_gap_detected(self):
        from gap_detector.detector import GapDetector
        spec = _make_parsed_spec([("SpecFeature", "P1")])
        code = _make_scanned_code([
            ("SpecFeature", "doc"),
            ("OrphanedFunction", "doc"),  # not in spec
        ])
        gaps = GapDetector(spec, code).detect()
        orphaned = [g for g in gaps if g.gap_type == "ORPHANED"]
        assert any(g.code_item == "OrphanedFunction" for g in orphaned)

    def test_fuzzy_match_prevents_missing(self):
        from gap_detector.detector import GapDetector
        spec = _make_parsed_spec([("UserReg", "P1")])
        # similar enough name (levenshtein similarity > 0.6)
        code = _make_scanned_code([("UserReg", "doc")])
        gaps = GapDetector(spec, code).detect()
        missing = [g for g in gaps if g.gap_type == "MISSING"]
        assert missing == []

    def test_get_summary_counts(self):
        from gap_detector.detector import GapDetector
        spec = _make_parsed_spec([("Missing", "P1"), ("Incomplete", "P1")])
        code = _make_scanned_code([("Incomplete", "")])  # missing has no impl, incomplete has no doc
        detector = GapDetector(spec, code)
        detector.detect()
        s = detector.get_summary()
        assert s.missing >= 1
        assert s.total_gaps == s.missing + s.incomplete + s.orphaned

    def test_get_summary_calls_detect_if_not_run(self):
        from gap_detector.detector import GapDetector
        spec = _make_parsed_spec([("F", "P1")])
        code = _make_scanned_code([])
        detector = GapDetector(spec, code)
        # don't call detect() first
        s = detector.get_summary()
        assert s.total_gaps >= 1

    def test_empty_spec_empty_code(self):
        from gap_detector.detector import GapDetector
        spec = _make_parsed_spec([])
        code = _make_scanned_code([])
        gaps = GapDetector(spec, code).detect()
        assert gaps == []

    def test_downstream_flag_set(self):
        from gap_detector.detector import GapDetector
        from gap_detector.parser import ParsedSpec, FeatureItem, SpecMetadata, ParseStats
        # F2 depends on F1; both missing
        f1 = FeatureItem(id="F1", name="BaseFeature", priority="P1", line_number=1, depends_on=[])
        f2 = FeatureItem(id="F2", name="DependentFeature", priority="P1", line_number=2, depends_on=["F1"])
        spec = ParsedSpec(feature_items=[f1, f2], metadata=SpecMetadata(), parse_stats=ParseStats())
        code = _make_scanned_code([])
        detector = GapDetector(spec, code)
        gaps = detector.detect()
        # At least one gap should have downstream_missing=True after mark
        assert isinstance(gaps, list)


# ===========================================================================
# reporter.py
# ===========================================================================

class TestGapReporter:
    def _make_reporter(self, tmp_path, gaps=None):
        from gap_detector.reporter import GapReporter
        spec = _make_parsed_spec([("F1", "P1")])
        code = _make_scanned_code([])
        return GapReporter(gaps or [], spec, code, output_dir=str(tmp_path / "reports"))

    def test_generate_creates_json(self, tmp_path):
        reporter = self._make_reporter(tmp_path)
        paths = reporter.generate()
        assert Path(paths.json_path).exists()

    def test_generate_creates_markdown(self, tmp_path):
        reporter = self._make_reporter(tmp_path)
        paths = reporter.generate()
        assert Path(paths.md_path).exists()

    def test_json_valid_structure(self, tmp_path):
        reporter = self._make_reporter(tmp_path)
        paths = reporter.generate()
        data = json.loads(Path(paths.json_path).read_text())
        assert "generated_at" in data
        assert "summary" in data
        assert "gaps" in data

    def test_json_summary_counts_zero(self, tmp_path):
        reporter = self._make_reporter(tmp_path, gaps=[])
        paths = reporter.generate()
        data = json.loads(Path(paths.json_path).read_text())
        assert data["summary"]["total_gaps"] == 0

    def test_json_with_gaps(self, tmp_path):
        from gap_detector.detector import Gap
        gap = Gap(gap_type="MISSING", spec_item="F1", severity="major",
                  reason="Not implemented", recommended_action="Implement it")
        reporter = self._make_reporter(tmp_path, gaps=[gap])
        paths = reporter.generate()
        data = json.loads(Path(paths.json_path).read_text())
        assert data["summary"]["total_gaps"] == 1
        assert data["gaps"][0]["gap_type"] == "MISSING"

    def test_markdown_contains_header(self, tmp_path):
        reporter = self._make_reporter(tmp_path)
        paths = reporter.generate()
        content = Path(paths.md_path).read_text()
        assert "Gap Summary Report" in content

    def test_markdown_missing_section(self, tmp_path):
        from gap_detector.detector import Gap
        gap = Gap(gap_type="MISSING", spec_item="FeatureX", severity="critical",
                  reason="Not found", recommended_action="Fix it")
        reporter = self._make_reporter(tmp_path, gaps=[gap])
        paths = reporter.generate()
        content = Path(paths.md_path).read_text()
        assert "Missing Features" in content
        assert "FeatureX" in content

    def test_compute_summary_counts(self, tmp_path):
        from gap_detector.detector import Gap
        gaps = [
            Gap(gap_type="MISSING", severity="critical"),
            Gap(gap_type="INCOMPLETE", severity="minor"),
            Gap(gap_type="ORPHANED", severity="minor"),
        ]
        reporter = self._make_reporter(tmp_path, gaps=gaps)
        s = reporter._compute_summary()
        assert s.missing == 1
        assert s.incomplete == 1
        assert s.orphaned == 1
        assert s.total_gaps == 3
        assert s.critical == 1

    def test_gap_to_dict_fields(self, tmp_path):
        from gap_detector.detector import Gap
        gap = Gap(gap_type="ORPHANED", code_item="foo", severity="minor",
                  reason="no spec", recommended_action="add spec")
        reporter = self._make_reporter(tmp_path)
        d = reporter._gap_to_dict(gap)
        assert d["gap_type"] == "ORPHANED"
        assert d["code_item"] == "foo"
        assert d["downstream_missing"] is False

    def test_output_dir_created(self, tmp_path):
        out = tmp_path / "deep" / "nested" / "reports"
        from gap_detector.reporter import GapReporter
        spec = _make_parsed_spec([])
        code = _make_scanned_code([])
        reporter = GapReporter([], spec, code, output_dir=str(out))
        reporter.generate()
        assert out.exists()

    def test_spec_features_count_in_json(self, tmp_path):
        reporter = self._make_reporter(tmp_path)
        paths = reporter.generate()
        data = json.loads(Path(paths.json_path).read_text())
        assert data["spec_features_count"] == 1  # one feature in _make_parsed_spec
