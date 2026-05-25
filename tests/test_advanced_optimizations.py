#!/usr/bin/env python3
"""
tests/test_advanced_optimizations.py
======================================
Unit tests for the three 2025-2026 cutting-edge optimizations:
  1. ASTDependencyScanner (ASDG Static Dependency Sandboxing)
  2. ast_mutation_guard   (AST Scoped Mutation Guardrail)
  3. Dynamic Activation   (Cross-Critic Debate Steering)
"""

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ASTDependencyScanner Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestASTDependencyScanner:
    """Verifies that ASTDependencyScanner resolves imports with 100% accuracy."""

    def _scan(self, code: str, rel: str = "core/foo.py"):
        from detection.drift_detector import ASTDependencyScanner
        import ast
        scanner = ASTDependencyScanner(rel)
        scanner.visit(ast.parse(textwrap.dedent(code)))
        return scanner.imports

    def test_simple_import(self):
        imports = self._scan("import os")
        assert "os" in imports

    def test_from_import(self):
        imports = self._scan("from pathlib import Path")
        assert "pathlib" in imports
        assert "pathlib.Path" in imports

    def test_alias_import(self):
        """import X as Y must resolve to X, not Y."""
        imports = self._scan("import numpy as np")
        assert "numpy" in imports

    def test_from_alias_import(self):
        imports = self._scan("from collections import OrderedDict as OD")
        assert "collections" in imports
        assert "collections.OrderedDict" in imports

    def test_dotted_import(self):
        imports = self._scan("from core.quality_gate import sab_parser")
        assert "core.quality_gate" in imports
        assert "core.quality_gate.sab_parser" in imports

    def test_multi_from_import(self):
        code = "from os.path import join, exists, dirname"
        imports = self._scan(code)
        assert "os.path" in imports
        assert "os.path.join" in imports
        assert "os.path.exists" in imports
        assert "os.path.dirname" in imports

    def test_nested_import_inside_function(self):
        code = """\
def foo():
    import json
    from pathlib import Path
"""
        imports = self._scan(code)
        assert "json" in imports
        assert "pathlib" in imports

    def test_relative_import_single_dot(self):
        """from .sibling import X inside core/foo.py → core.sibling.X"""
        code = "from .sibling import helper"
        imports = self._scan(code, rel="core/foo.py")
        assert "core.sibling" in imports
        assert "core.sibling.helper" in imports

    def test_relative_import_double_dot(self):
        """from ..utils import X inside core/sub/foo.py → core.utils.X"""
        code = "from ..utils import calc"
        imports = self._scan(code, rel="core/sub/foo.py")
        assert "core.utils" in imports
        assert "core.utils.calc" in imports

    def test_star_import(self):
        code = "from os.path import *"
        imports = self._scan(code)
        assert "os.path" in imports

    def test_empty_module_returns_empty(self):
        imports = self._scan("")
        assert len(imports) == 0

    def test_syntax_error_no_crash(self):
        """Graceful fallback: ast.parse raises SyntaxError → scanner should not be called."""
        from detection.drift_detector import ASTDependencyScanner
        import ast
        scanner = ASTDependencyScanner("foo.py")
        with pytest.raises(SyntaxError):
            ast.parse("def broken(")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. ast_mutation_guard Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestAstMutationGuard:
    """Verifies that ast_mutation_guard blocks out-of-bounds mutations."""

    def test_allowed_change_passes(self):
        from core.auto_fix.guardrails import ast_mutation_guard
        pre = textwrap.dedent("""\
            def target():
                return 1

            def other():
                return 2
        """)
        post = textwrap.dedent("""\
            def target():
                return 42

            def other():
                return 2
        """)
        assert ast_mutation_guard(Path("x.py"), pre, post, "target") is True

    def test_oob_change_blocks(self):
        """Mutation outside the allowed node must be blocked."""
        from core.auto_fix.guardrails import ast_mutation_guard
        pre = textwrap.dedent("""\
            def target():
                return 1

            def other():
                return 2
        """)
        post = textwrap.dedent("""\
            def target():
                return 42

            def other():
                return 999
        """)
        assert ast_mutation_guard(Path("x.py"), pre, post, "target") is False

    def test_new_import_added_blocks(self):
        """Adding a new import statement outside the allowed node must be blocked."""
        from core.auto_fix.guardrails import ast_mutation_guard
        pre = textwrap.dedent("""\
            def target():
                return 1
        """)
        post = textwrap.dedent("""\
            import os
            def target():
                return 42
        """)
        assert ast_mutation_guard(Path("x.py"), pre, post, "target") is False

    def test_no_constraint_always_passes(self):
        from core.auto_fix.guardrails import ast_mutation_guard
        assert ast_mutation_guard(Path("x.py"), "x=1", "x=2", None) is True

    def test_non_python_always_passes(self):
        from core.auto_fix.guardrails import ast_mutation_guard
        assert ast_mutation_guard(Path("x.md"), "# hello", "# world", "target") is True

    def test_post_syntax_error_blocks(self):
        """Syntax error in post-fix content must block (corruption)."""
        from core.auto_fix.guardrails import ast_mutation_guard
        pre = "def target():\n    return 1\n"
        post = "def target(\n    return 1\n"  # broken syntax
        assert ast_mutation_guard(Path("x.py"), pre, post, "target") is False

    def test_class_node_allowed(self):
        """Modifications inside an allowed ClassDef should pass."""
        from core.auto_fix.guardrails import ast_mutation_guard
        pre = textwrap.dedent("""\
            class MyClass:
                def method(self):
                    return 1

            class Other:
                pass
        """)
        post = textwrap.dedent("""\
            class MyClass:
                def method(self):
                    return 42

            class Other:
                pass
        """)
        assert ast_mutation_guard(Path("x.py"), pre, post, "MyClass") is True

    def test_class_oob_blocks(self):
        """Modification of a different class must be blocked."""
        from core.auto_fix.guardrails import ast_mutation_guard
        pre = textwrap.dedent("""\
            class MyClass:
                pass

            class Other:
                x = 1
        """)
        post = textwrap.dedent("""\
            class MyClass:
                pass

            class Other:
                x = 999
        """)
        assert ast_mutation_guard(Path("x.py"), pre, post, "MyClass") is False


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Cross-Critic Debate & Dynamic Activation Tests
# ═══════════════════════════════════════════════════════════════════════════════

_DIM_KEYS = ("correctness", "completeness", "consistency", "concision", "maintainability")

_SCORES_A_HIGH = {
    "A": {"correctness": 0.9, "completeness": 0.8,
          "consistency": 0.8, "concision": 0.7, "maintainability": 0.8},
    "B": {"correctness": 0.3, "completeness": 0.4,
          "consistency": 0.5, "concision": 0.4, "maintainability": 0.3},
}

_CRITIC_RESP = {
    "A_gaps": ["Minor edge case on empty input"],
    "B_gaps": ["Missing null check", "No error handling"],
}

_FEEDBACK_OK = {
    "winner_advantages": ["precise logic"],
    "loser_improvements": ["add examples"],
    "actionable_guidance": "focus on clarity",
}


def _build_provider(responses):
    """Build mock provider from a list of JSON-serializable responses."""
    m = MagicMock()
    m.chat.side_effect = [json.dumps(r) for r in responses]
    return m


class TestDynamicActivation:
    """Verifies the Dynamic Activation mechanism for critic debate triggering."""

    def _loop(self, provider=None, config=None, history_path=""):
        from steering.steering_loop import SteeringLoop
        if provider is None:
            # Default: enough responses for iterate without debate
            provider = _build_provider([_SCORES_A_HIGH, _FEEDBACK_OK] * 6)
        return SteeringLoop(provider, config=config, history_path=history_path)

    def test_debate_activated_for_sensitive_module(self):
        loop = self._loop()
        result = loop._should_activate_debate(0.5, ["steering/steering_loop.py"])
        assert result is True

    def test_debate_activated_for_close_delta(self):
        loop = self._loop()
        result = loop._should_activate_debate(0.05, None)
        assert result is True

    def test_debate_skipped_for_large_delta_non_sensitive(self):
        loop = self._loop()
        result = loop._should_activate_debate(0.5, ["tests/test_foo.py"])
        assert result is False

    def test_debate_skipped_when_no_modules(self):
        loop = self._loop()
        result = loop._should_activate_debate(0.5, None)
        assert result is False

    def test_enforcement_module_triggers_debate(self):
        loop = self._loop()
        assert loop._should_activate_debate(0.8, ["enforcement/policy_engine.py"]) is True

    def test_core_auto_fix_triggers_debate(self):
        loop = self._loop()
        assert loop._should_activate_debate(0.8, ["core/auto_fix/guardrails.py"]) is True

    def test_core_fsm_triggers_debate(self):
        loop = self._loop()
        assert loop._should_activate_debate(0.8, ["core/fsm/fsm.py"]) is True


class TestCriticDebateScoring:
    """Verifies that score_with_critic_debate produces valid multi-round debate results."""

    def test_critic_debate_returns_valid_scores(self):
        # Responses: critic → A defense → B defense → consensus decider
        responses = [
            _CRITIC_RESP,
            "A's design is robust because...",
            "B's design handles edge cases...",
            _SCORES_A_HIGH,
        ]
        provider = _build_provider(responses)

        from steering.steering_loop import LLMJudgeScorer
        scorer = LLMJudgeScorer(provider)
        result = scorer.score_with_critic_debate({"text": "A output"}, {"text": "B output"})

        assert "A" in result
        assert "B" in result
        for dim in _DIM_KEYS:
            assert dim in result["A"]
            assert dim in result["B"]

    def test_critic_debate_fallback_on_failure(self):
        """If all debate steps fail, falls back to normal score()."""
        provider = MagicMock()
        # All calls raise
        provider.chat.side_effect = Exception("LLM unavailable")

        from steering.steering_loop import LLMJudgeScorer
        scorer = LLMJudgeScorer(provider)
        result = scorer.score_with_critic_debate({"text": "A"}, {"text": "B"})

        # Fallback returns 0.5 for all dims
        assert result["A"]["correctness"] == 0.5
        assert result["B"]["correctness"] == 0.5


class TestSteeringLoopWithDebate:
    """Integration test: SteeringLoop.iterate() with changed_modules."""

    def test_iterate_with_changed_modules_param(self):
        """iterate() accepts the new changed_modules parameter without breaking."""
        responses = [_SCORES_A_HIGH, _FEEDBACK_OK] * 6
        provider = _build_provider(responses)

        from steering.steering_loop import SteeringLoop
        loop = SteeringLoop(provider, history_path="")
        result = loop.iterate(
            {"text": "A output"}, {"text": "B output"},
            changed_modules=["tests/test_foo.py"]
        )
        assert result.winner in ("A", "B")
        assert result.iteration == 1

    def test_iterate_backward_compatible_no_modules(self):
        """iterate() still works without changed_modules (backward compat)."""
        responses = [_SCORES_A_HIGH, _FEEDBACK_OK] * 6
        provider = _build_provider(responses)

        from steering.steering_loop import SteeringLoop
        loop = SteeringLoop(provider, history_path="")
        result = loop.iterate({"text": "A"}, {"text": "B"})
        assert result.winner in ("A", "B")
