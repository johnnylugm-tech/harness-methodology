"""One statement of "which modules does this FR own".

Round 57 站0 / 站2. `core/quality_gate/cov_utils.resolve_fr_scoped_src_files`
has been the answer since before Round 18 — `cli/gate_cmds.py:747` and
`cli/fr_prompts/fix.py:52` both consume it, it handles the package-directory
fallback, AST-import detection, `fr_scope_overrides`, and warns on malformed
entries. Round 56 站6 wrote a second one (`gate1_evidence._fr_module_paths`,
53 lines) that does the dotted-name→path conversion and nothing else.

Station 0 measured the two across all seven corpus projects and every FR:
**the resolved file sets are identical, project for project** — zero
naive-only, zero ssot-only, zero unresolvable entries, and no project uses
`fr_scope_overrides`. So this is a latent defect, not a live wound, and the
round records it as such.

The divergence has a concrete shape, though, and this is it. When the SAB
claims a dotted module whose real code lives in a package of the same name —
`pkg.executor` implemented as `pkg/executor/runner.py` — the SSOT resolver
returns the whole package and the second resolver returns a path with no file
behind it. The FR's coverage then reads as unmeasurable (or, when a thin
re-export shim does exist, as a misleading 100% over three lines).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def package_style_project(tmp_path: Path) -> Path:
    """An FR whose SAB entry `pkg.executor` is a package, not a module."""
    src = tmp_path / "03-development" / "src" / "pkg" / "executor"
    src.mkdir(parents=True)
    (tmp_path / "03-development" / "tests").mkdir(parents=True)
    (src.parent / "__init__.py").write_text("", encoding="utf-8")
    (src / "__init__.py").write_text(
        "from pkg.executor.runner import run\n", encoding="utf-8"
    )
    (src / "runner.py").write_text(
        "def run(x):\n"
        "    y = x + 1\n"
        "    return y\n",
        encoding="utf-8",
    )

    methodology = tmp_path / ".methodology"
    methodology.mkdir()
    trace = {"FR-02": "pkg.executor"}
    (methodology / "quality_manifest.json").write_text(
        json.dumps({
            "fr_ids": ["FR-02"],
            "quality_targets": {"min_coverage": 80.0},
            "fr_module_traceability": trace,
        }),
        encoding="utf-8",
    )
    (methodology / "SAB.json").write_text(
        json.dumps({"sab": {"fr_module_traceability": trace}}),
        encoding="utf-8",
    )

    # A real `.coverage` data file with every statement of runner.py executed.
    import coverage

    data = coverage.CoverageData(basename=str(tmp_path / ".coverage"))
    data.add_lines({str(src / "runner.py"): [1, 2, 3]})
    data.write()
    return tmp_path


def test_a_package_style_fr_module_is_measured_not_lost(package_style_project):
    """`pkg.executor` → the package's files, so the FR has a number at all.

    The SSOT resolver's package fallback exists precisely for this shape
    (`cov_utils.resolve_fr_scoped_src_files`, "Fix III"). The second resolver
    produced `pkg/executor.py`, which is not on disk, so the per-FR scope
    contained no measured file and the FR came back unmeasurable — Round 32
    站4's "could not measure", manufactured by the framework rather than by
    the project.
    """
    from core.quality_gate.gate1_evidence import fr_coverage_from_last_run

    assert fr_coverage_from_last_run(package_style_project, "FR-02") == 100.0


def test_the_two_resolvers_agree_on_the_corpus_shape(tmp_path):
    """A plain `pkg.mod` entry resolves the same way through either door.

    The positive control for 站2's replacement: the ordinary shape — which is
    what all seven corpus projects use — must keep resolving to exactly the
    file it resolves to today.
    """
    from core.quality_gate.cov_utils import resolve_fr_scoped_src_files
    from core.quality_gate.gate1_evidence import fr_coverage_from_last_run

    src = tmp_path / "03-development" / "src" / "pkg"
    src.mkdir(parents=True)
    (tmp_path / "03-development" / "tests").mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "cache.py").write_text("def get(k):\n    return k\n", encoding="utf-8")

    methodology = tmp_path / ".methodology"
    methodology.mkdir()
    trace = {"FR-04": "pkg.cache"}
    manifest = {
        "fr_ids": ["FR-04"],
        "quality_targets": {"min_coverage": 80.0},
        "fr_module_traceability": trace,
    }
    (methodology / "quality_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    (methodology / "SAB.json").write_text(
        json.dumps({"sab": {"fr_module_traceability": trace}}), encoding="utf-8")

    assert resolve_fr_scoped_src_files(
        str(tmp_path), "FR-04", "", "03-development/src", manifest
    ) == ["03-development/src/pkg/cache.py"]

    import coverage

    data = coverage.CoverageData(basename=str(tmp_path / ".coverage"))
    data.add_lines({str(src / "cache.py"): [1]})
    data.write()

    # One of two statements executed.
    assert fr_coverage_from_last_run(tmp_path, "FR-04") == 50.0
