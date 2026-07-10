"""Tests for deferred-fix closure enforcement (Stage 5).

advance-phase hard-blocks (exit 17) when deferred_fixes.md has unresolved
"- [ ]" items, closing the quality-loop gap where deferred debt was created but
never enforced.
"""

from cli.phase_cmds import _check_deferred_fixes_resolved


def _write_deferred(tmp_path, body: str):
    m = tmp_path / ".methodology"
    m.mkdir(parents=True, exist_ok=True)
    (m / "deferred_fixes.md").write_text(body, encoding="utf-8")


def test_no_deferred_file_passes(tmp_path):
    (tmp_path / ".methodology").mkdir()
    assert _check_deferred_fixes_resolved(tmp_path) == 0


def test_legacy_freetext_without_checkboxes_passes(tmp_path):
    _write_deferred(tmp_path, "# Deferred Fixes — Gate 2 (P3)\n\nmutation_testing deferred to P4.\n")
    assert _check_deferred_fixes_resolved(tmp_path) == 0


def test_open_checkbox_blocks(tmp_path):
    _write_deferred(
        tmp_path,
        "# Deferred Fixes\n\n- [ ] mutation_testing remediation (P4)\n- [x] resolved one\n",
    )
    assert _check_deferred_fixes_resolved(tmp_path) == 17


def test_all_resolved_passes(tmp_path):
    _write_deferred(tmp_path, "# Deferred Fixes\n\n- [x] item 1\n- [x] item 2\n")
    assert _check_deferred_fixes_resolved(tmp_path) == 0
