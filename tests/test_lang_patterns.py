"""Direct unit tests for core/utils/lang_patterns.py.

Locks the is_test_file / iter_source_files / project_language contracts —
the silent-failure hotspots when a new language is added.
"""

from __future__ import annotations

import json

from core.utils.lang_patterns import (
    DEFAULT_LANGUAGE,
    SKIP_DIRS,
    is_test_file,
    iter_source_files,
    project_language,
    source_extensions,
)


class TestIsTestFile:
    def test_python_test_prefix_only(self):
        assert is_test_file("tests/test_alpha.py", "python")
        assert is_test_file("test_alpha.py", "python")
        # Non-prefix is not a test file in Python convention.
        assert not is_test_file("alpha_test.py", "python")
        assert not is_test_file("test_alpha.txt", "python")

    def test_js_vitest_test_suffix(self):
        assert is_test_file("src/foo.test.ts", "javascript")
        assert is_test_file("src/foo.spec.ts", "typescript")
        assert is_test_file("test_foo.ts", "javascript")
        assert is_test_file("test_foo.js", "javascript")
        assert is_test_file("src/foo.test.tsx", "typescript")

    def test_js_extension_must_match_language(self):
        # .py file in a TS project: not a test by JS convention.
        assert not is_test_file("src/foo.py", "typescript")
        # .ts file in a JS project: still detected via TEST_FILE_PATTERN.
        assert is_test_file("src/foo.test.ts", "javascript")

    def test_unknown_language_falls_back_to_python(self):
        # Future languages without a registered extension set fall back to
        # python's (.py,) — .test.ts is therefore NOT a test. This is the
        # gate; if a new language needs JS-style tests, add to SOURCE_EXTENSIONS
        # first or `is_test_file` will silently return False.
        assert not is_test_file("src/foo.test.ts", "rust")
        # .py is a test only if starts with test_ under python fallback.
        assert is_test_file("test_foo.py", "rust")
        assert not is_test_file("foo.py", "rust")

    def test_test_file_pattern_case_insensitive(self):
        # TEST_FILE_PATTERN is re.IGNORECASE — uppercase .TEST. should match.
        assert is_test_file("src/foo.TEST.ts", "javascript")


class TestSourceExtensions:
    def test_javascript_and_typescript_share_extensions(self):
        # JS and TS both scan .js .jsx .ts .tsx .mjs .cjs (TS loosens JS).
        assert source_extensions("javascript") == source_extensions("typescript")

    def test_default_language_is_python(self):
        assert source_extensions(DEFAULT_LANGUAGE) == (".py",)
        # Unknown language returns the default (python) — explicit, not crash.
        assert source_extensions("rust") == source_extensions(DEFAULT_LANGUAGE)


class TestIterSourceFiles:
    def test_skips_node_modules_dist_build_coverage(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.ts").write_text("export const x = 1;",
                                                 encoding="utf-8")
        (tmp_path / "node_modules" / "lib").mkdir(parents=True)
        (tmp_path / "node_modules" / "lib" / "index.ts").write_text("// dep",
                                                                   encoding="utf-8")
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "app.js").write_text("// built", encoding="utf-8")
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "app.js").write_text("// built", encoding="utf-8")
        (tmp_path / "coverage").mkdir()
        (tmp_path / "coverage" / "summary.json").write_text("{}", encoding="utf-8")
        (tmp_path / ".next").mkdir()
        (tmp_path / ".next" / "page.js").write_text("// ssr", encoding="utf-8")
        (tmp_path / ".sessi-work").mkdir()
        (tmp_path / ".sessi-work" / "scratch.ts").write_text("// scratch",
                                                            encoding="utf-8")
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "state.json").write_text("{}",
                                                              encoding="utf-8")

        names = [p.name for p in iter_source_files(tmp_path, "typescript")]
        # Only the legitimate source file remains.
        assert names == ["app.ts"]


class TestProjectLanguage:
    def test_missing_state_file_defaults_to_python(self, tmp_path):
        assert project_language(tmp_path) == "python"

    def test_corrupt_json_defaults_to_python(self, tmp_path):
        meth = tmp_path / ".methodology"
        meth.mkdir()
        (meth / "state.json").write_text("{not json", encoding="utf-8")
        assert project_language(tmp_path) == "python"

    def test_non_dict_state_defaults_to_python(self, tmp_path):
        meth = tmp_path / ".methodology"
        meth.mkdir()
        (meth / "state.json").write_text(json.dumps([1, 2, 3]),
                                         encoding="utf-8")
        assert project_language(tmp_path) == "python"

    def test_empty_language_field_defaults_to_python(self, tmp_path):
        meth = tmp_path / ".methodology"
        meth.mkdir()
        (meth / "state.json").write_text(json.dumps({"language": ""}),
                                         encoding="utf-8")
        assert project_language(tmp_path) == "python"


class TestSkipDirs:
    def test_skip_dirs_is_immutable(self):
        # frozenset — no runtime mutation possible.
        assert isinstance(SKIP_DIRS, frozenset)
        # Required JS/TS build artifacts.
        for must_skip in ("node_modules", "dist", "build", "coverage", ".next",
                          ".sessi-work", ".methodology"):
            assert must_skip in SKIP_DIRS
