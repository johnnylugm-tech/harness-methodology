"""
Regression test for HIGH bug in git_strategy._tag_release (line 909):

  `tag = f"{_TAG_PREFIX}-{ts}-score{int(score)}"` calls
  `int(float('inf'))` (raises OverflowError) or
  `int(float('nan'))` (raises ValueError). Both propagate uncaught,
  aborting the entire Gate 4 push pipeline. Fix: validate score is
  finite before using it in the tag name, raise a clear ValueError.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from harness.git_strategy import GitStrategy


@pytest.fixture
def gs(tmp_path: Path) -> GitStrategy:
    return GitStrategy(project=tmp_path, enabled=True, push=False)


class TestTagReleaseScoreValidation:
    def test_nan_score_raises_value_error(self, gs: GitStrategy):
        """NaN score must raise ValueError (clear contract) instead
        of letting the bare int(float('nan')) ValueError bubble up
        with a confusing message."""
        with pytest.raises(ValueError, match="[Ss]core"):
            gs._tag_release(score=float("nan"))

    def test_inf_score_raises_value_error(self, gs: GitStrategy):
        with pytest.raises(ValueError, match="[Ss]core"):
            gs._tag_release(score=float("inf"))
        with pytest.raises(ValueError, match="[Ss]core"):
            gs._tag_release(score=float("-inf"))

    def test_valid_score_proceeds_without_raising(
        self, gs: GitStrategy,
    ):
        """Sanity guard: a finite score must NOT raise. (Note: the
        actual git command will fail because tmp_path isn't a git
        repo — but the validation must succeed and pass through to
        the _run_git call.)"""
        # Use a real-ish score; _run_git returns failure but we
        # should not see a validation error.
        gs._tag_release(score=85.0)  # should not raise ValueError
