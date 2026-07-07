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
        ASTDependencyScanner("foo.py")
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

    def test_nested_method_allowed(self):
        """Allowed method inside a class: only that method may change."""
        from core.auto_fix.guardrails import ast_mutation_guard
        pre = textwrap.dedent("""\
            class Engine:
                def target(self):
                    return 1

                def helper(self):
                    return 2
        """)
        post = textwrap.dedent("""\
            class Engine:
                def target(self):
                    return 42

                def helper(self):
                    return 2
        """)
        assert ast_mutation_guard(Path("x.py"), pre, post, "target") is True

    def test_nested_method_oob_blocks(self):
        """Sibling method changed while only 'target' is allowed → must be blocked."""
        from core.auto_fix.guardrails import ast_mutation_guard
        pre = textwrap.dedent("""\
            class Engine:
                def target(self):
                    return 1

                def helper(self):
                    return 2
        """)
        post = textwrap.dedent("""\
            class Engine:
                def target(self):
                    return 42

                def helper(self):
                    return 999
        """)
        assert ast_mutation_guard(Path("x.py"), pre, post, "target") is False

    def test_nested_method_class_metadata_blocks(self):
        """Modifying class bases/name while a nested method is allowed must be blocked."""
        from core.auto_fix.guardrails import ast_mutation_guard
        pre = textwrap.dedent("""\
            class Engine(Base):
                def target(self):
                    return 1
        """)
        post = textwrap.dedent("""\
            class Engine(HackedBase):
                def target(self):
                    return 42
        """)
        assert ast_mutation_guard(Path("x.py"), pre, post, "target") is False

    def test_nested_method_class_decorator_blocks(self):
        """Adding a class decorator while a nested method is allowed must be blocked."""
        from core.auto_fix.guardrails import ast_mutation_guard
        pre = textwrap.dedent("""\
            class Engine:
                def target(self):
                    return 1
        """)
        post = textwrap.dedent("""\
            @malicious
            class Engine:
                def target(self):
                    return 42
        """)
        assert ast_mutation_guard(Path("x.py"), pre, post, "target") is False


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

# (TestDynamicActivation / TestCriticDebateScoring / TestSteeringLoopWithDebate
#  removed with the steering/ package in 減法 T4 — the env-gated subsystem
#  was dormant by default and never enabled in any E2E run.)
