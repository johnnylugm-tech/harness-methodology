"""Tests for CodeScanner scope narrowing (Stage 4b).

Orphan/gap analysis must scan the code under test, not its dependencies or
tooling — otherwise third-party symbols dominate as ORPHANED noise (tts-new
produced 37,510 such entries before this).
"""

import sys
from pathlib import Path

SCANNER_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCANNER_DIR))

from gap_detector.scanner import CodeScanner  # noqa: E402


def _names(code):
    return {item.name for m in code.modules for item in m.items}


def test_excludes_hidden_and_tooling_dirs(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def public_fn():\n    pass\n", encoding="utf-8")
    for d in (".venv", ".methodology", ".code-review-graph", "node_modules", "harness"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "dep.py").write_text(
            f"def {d.strip('.').replace('-', '_')}_fn():\n    pass\n", encoding="utf-8"
        )

    names = _names(CodeScanner(str(tmp_path)).scan())

    assert "public_fn" in names                 # product code scanned
    assert "venv_fn" not in names                # .venv pruned
    assert "methodology_fn" not in names         # .methodology pruned
    assert "code_review_graph_fn" not in names   # .code-review-graph pruned
    assert "node_modules_fn" not in names        # node_modules pruned
    assert "harness_fn" not in names             # harness submodule pruned


def test_scans_nested_product_packages(tmp_path):
    pkg = tmp_path / "src" / "engines"
    pkg.mkdir(parents=True)
    (pkg / "synth.py").write_text("def synthesize():\n    pass\n", encoding="utf-8")
    assert "synthesize" in _names(CodeScanner(str(tmp_path)).scan())
