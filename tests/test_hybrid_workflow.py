"""Unit tests for core/hybrid_workflow.py — HR-04 mode enforcement."""
from core.hybrid_workflow import (
    HybridWorkflow, WorkflowMode, ChangeType, ChangeAnalysis,
)


# ── analyze_change ────────────────────────────────────────────────────────

class TestAnalyzeChange:
    def test_small_change_below_threshold(self):
        """<10 lines changed, no security/new-feature keywords → SMALL."""
        wf = HybridWorkflow(mode=WorkflowMode.HYBRID)
        diff = "+x\n+y\n+z\n"
        analysis = wf.analyze_change(diff)
        assert analysis.type == ChangeType.SMALL
        assert analysis.reason.startswith("change <")

    def test_large_change_above_threshold(self):
        """>30 lines changed → LARGE."""
        wf = HybridWorkflow(mode=WorkflowMode.HYBRID)
        diff = "\n".join(f"+line{i}" for i in range(31))
        analysis = wf.analyze_change(diff)
        assert analysis.type == ChangeType.LARGE

    def test_security_keyword_forces_large(self):
        """'password' in diff forces LARGE regardless of line count."""
        wf = HybridWorkflow(mode=WorkflowMode.HYBRID)
        diff = "+password = 'abc'\n"
        analysis = wf.analyze_change(diff)
        assert analysis.type == ChangeType.LARGE
        assert analysis.is_security_related

    def test_auth_keyword_forces_large(self):
        wf = HybridWorkflow(mode=WorkflowMode.HYBRID)
        diff = "+def authenticate():\n"
        analysis = wf.analyze_change(diff)
        assert analysis.type == ChangeType.LARGE

    def test_new_feature_forces_large(self):
        """'def new_' in diff forces LARGE."""
        wf = HybridWorkflow(mode=WorkflowMode.HYBRID)
        diff = "+def new_login():\n"
        analysis = wf.analyze_change(diff)
        assert analysis.type == ChangeType.LARGE

    def test_medium_change_defaults_to_small(self):
        """Between small_threshold and large_threshold → SMALL."""
        wf = HybridWorkflow(mode=WorkflowMode.HYBRID,
                            small_change_threshold=10,
                            large_change_threshold=30)
        diff = "\n".join(f"+line{i}" for i in range(15))
        analysis = wf.analyze_change(diff)
        assert analysis.type == ChangeType.SMALL

    def test_removed_lines_counted(self):
        wf = HybridWorkflow(mode=WorkflowMode.HYBRID)
        diff = "\n".join(f"-line{i}" for i in range(25))
        analysis = wf.analyze_change(diff)
        assert analysis.lines_changed == 25

    def test_fields_on_result(self):
        wf = HybridWorkflow()
        analysis = wf.analyze_change("+x\n+y\n")
        assert analysis.lines_changed == 2
        assert not analysis.is_new_feature
        assert not analysis.is_security_related
        assert isinstance(analysis.files_affected, int)

    def test_empty_diff(self):
        """Empty diff → 0 lines changed, SMALL."""
        wf = HybridWorkflow()
        analysis = wf.analyze_change("")
        assert analysis.lines_changed == 0
        assert analysis.type == ChangeType.SMALL


# ── should_review ─────────────────────────────────────────────────────────

class TestShouldReview:
    def test_off_mode_never_reviews(self):
        wf = HybridWorkflow(mode=WorkflowMode.OFF)
        analysis = ChangeAnalysis(type=ChangeType.LARGE, lines_changed=100,
                                  files_affected=5, is_security_related=True,
                                  is_new_feature=True, reason="big")
        assert not wf.should_review(analysis)
        assert wf.stats["auto_approved"] == 1

    def test_on_mode_always_reviews(self):
        wf = HybridWorkflow(mode=WorkflowMode.ON)
        analysis = ChangeAnalysis(type=ChangeType.SMALL, lines_changed=1,
                                  files_affected=1, is_security_related=False,
                                  is_new_feature=False, reason="tiny")
        assert wf.should_review(analysis)
        assert wf.stats["review_required"] == 1

    def test_hybrid_mode_reviews_large(self):
        wf = HybridWorkflow(mode=WorkflowMode.HYBRID)
        analysis = ChangeAnalysis(type=ChangeType.LARGE, lines_changed=50,
                                  files_affected=3, is_security_related=False,
                                  is_new_feature=False, reason="big")
        assert wf.should_review(analysis)

    def test_hybrid_mode_auto_approves_small(self):
        wf = HybridWorkflow(mode=WorkflowMode.HYBRID)
        analysis = ChangeAnalysis(type=ChangeType.SMALL, lines_changed=3,
                                  files_affected=1, is_security_related=False,
                                  is_new_feature=False, reason="tiny")
        assert not wf.should_review(analysis)


# ── execute ───────────────────────────────────────────────────────────────

def test_execute_auto_approved_calls_code_func():
    wf = HybridWorkflow(mode=WorkflowMode.HYBRID)
    called = []

    def my_func():
        called.append(True)
        return "done"

    result = wf.execute("+x\n+y\n", my_func)
    assert result["status"] == "auto_approved"
    assert called  # code_func was invoked


def test_execute_needs_review_does_not_call_code_func():
    wf = HybridWorkflow(mode=WorkflowMode.ON)
    called = []

    def my_func():
        called.append(True)

    result = wf.execute("+x\n", my_func)
    assert result["status"] == "needs_review"
    assert not called  # code_func was NOT invoked


# ── get_stats ─────────────────────────────────────────────────────────────

def test_get_stats_empty():
    wf = HybridWorkflow()
    assert wf.get_stats()["auto_approve_rate"] == "N/A"


def test_get_stats_counts_correctly():
    wf = HybridWorkflow(mode=WorkflowMode.HYBRID)
    # 3 small → auto, 1 large → review
    for _ in range(3):
        wf.should_review(ChangeAnalysis(
            type=ChangeType.SMALL, lines_changed=1,
            files_affected=1, is_security_related=False,
            is_new_feature=False, reason="tiny"))
    wf.should_review(ChangeAnalysis(
        type=ChangeType.LARGE, lines_changed=100,
        files_affected=5, is_security_related=True,
        is_new_feature=True, reason="big"))
    stats = wf.get_stats()
    assert stats["total_tasks"] == 4
    assert stats["auto_approved"] == 3
    assert stats["review_required"] == 1
    assert stats["auto_approve_rate"] == "75.0%"
    assert stats["review_rate"] == "25.0%"


# ── threshold boundaries ──────────────────────────────────────────────────

def test_exactly_at_small_threshold_pass():
    """Exactly at small_threshold lines → SMALL (not < small, medium default)."""
    wf = HybridWorkflow(mode=WorkflowMode.HYBRID,
                        small_change_threshold=10, large_change_threshold=30)
    diff = "\n".join(f"+line{i}" for i in range(10))
    analysis = wf.analyze_change(diff)
    # 10 is NOT < 10, so falls into medium → SMALL
    assert analysis.type == ChangeType.SMALL


def test_exactly_at_large_threshold():
    """31 lines > large_threshold(30) → LARGE."""
    wf = HybridWorkflow(mode=WorkflowMode.HYBRID,
                        small_change_threshold=10, large_change_threshold=30)
    diff = "\n".join(f"+line{i}" for i in range(31))
    analysis = wf.analyze_change(diff)
    assert analysis.type == ChangeType.LARGE
