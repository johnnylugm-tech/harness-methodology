"""shared_owner_test_files: for a module fr_module_traceability lists under
more than one FR (a shared file like a CLI dispatch module each FR owns a
slice of), FR-scoped Gate 1 coverage must run every co-owning FR's test file,
not just the current FR's own one — see the function's docstring and
cov_utils.py's module docstring for the FR-02 Phase-5 Gate-1 incident this
fixes: measuring a shared file's coverage with one owning FR's test suite
charges it for every OTHER owning FR's untested-by-this-suite lines, and that
charge regresses every time any sibling FR adds code to the shared file.
"""
from core.quality_gate.cov_utils import shared_owner_test_files


def test_no_traceability_returns_empty():
    assert shared_owner_test_files("FR-02", {}, "03-development/tests") == []


def test_fr_not_in_traceability_returns_empty():
    manifest = {"fr_module_traceability": {"FR-01": "taskq.models.task"}}
    assert shared_owner_test_files("FR-02", manifest, "03-development/tests") == []


def test_unshared_module_returns_empty():
    """FR owns a module no other FR claims — the common case."""
    manifest = {
        "fr_module_traceability": {
            "FR-02": "taskq_plus.service.executor",
            "FR-04": "taskq_plus.service.cache",
        }
    }
    assert shared_owner_test_files("FR-02", manifest, "03-development/tests") == []


def test_shared_module_returns_sibling_test_files():
    """FR-02/05/07/08 all list taskq_plus.cli.commands — the real taskq-renew
    shape (Phase-5 Gate-1 incident)."""
    manifest = {
        "fr_module_traceability": {
            "FR-02": ["taskq_plus.service.executor", "taskq_plus.cli.commands"],
            "FR-05": ["taskq_plus.cli.main", "taskq_plus.cli.commands"],
            "FR-07": ["taskq_plus.service.plugins", "taskq_plus.cli.commands"],
            "FR-08": ["taskq_plus.observability.audit", "taskq_plus.cli.commands"],
        }
    }
    result = shared_owner_test_files("FR-02", manifest, "03-development/tests")
    assert result == [
        "03-development/tests/test_fr05.py",
        "03-development/tests/test_fr07.py",
        "03-development/tests/test_fr08.py",
    ]


def test_shared_module_is_symmetric():
    """FR-05's own check must also pick up FR-02, FR-07, FR-08 as siblings —
    ownership sharing is not one-directional."""
    manifest = {
        "fr_module_traceability": {
            "FR-02": "taskq_plus.cli.commands",
            "FR-05": ["taskq_plus.cli.main", "taskq_plus.cli.commands"],
            "FR-07": "taskq_plus.cli.commands",
        }
    }
    result = shared_owner_test_files("FR-05", manifest, "03-development/tests")
    assert result == [
        "03-development/tests/test_fr02.py",
        "03-development/tests/test_fr07.py",
    ]


def test_string_trace_form_handled():
    """fr_module_traceability values may be a bare str, not always a list."""
    manifest = {
        "fr_module_traceability": {
            "FR-02": "taskq_plus.cli.commands",
            "FR-05": "taskq_plus.cli.commands",
        }
    }
    assert shared_owner_test_files("FR-02", manifest, "tests") == ["tests/test_fr05.py"]


def test_malformed_other_entry_skipped_not_crashed():
    """A malformed sibling entry (neither str nor list) must not crash the
    scan — it's simply not counted as sharing."""
    manifest = {
        "fr_module_traceability": {
            "FR-02": "taskq_plus.cli.commands",
            "FR-05": 42,
        }
    }
    assert shared_owner_test_files("FR-02", manifest, "tests") == []


def test_malformed_own_entry_returns_empty():
    manifest = {"fr_module_traceability": {"FR-02": 42}}
    assert shared_owner_test_files("FR-02", manifest, "tests") == []
