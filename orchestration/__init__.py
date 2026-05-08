"""Orchestration — pipeline integration layer.

Bridges CLI commands to quality gate checks with feedback loops.
Provides retry-aware wrappers around constitution, enforcement, and gate runners.

Exports:
    run_constitution_check_with_feedback: constitution check with retry/fix loop.
    run_enforcement_check_with_feedback: enforcement check with retry/fix loop.
    run_policy_check_with_feedback: policy engine check with retry/fix loop.
"""

from __future__ import annotations

from pathlib import Path


def run_constitution_check_with_feedback(
    check_type: str,
    docs_path: str,
    current_phase: int = 1,
    *,
    max_retries: int = 3,
    auto_fix: bool = False,
) -> "ConstitutionResult":
    """Run constitution check with automatic retry and feedback.

    On failure, delegates auto-fix to AutoFixEngine.

    Args:
        check_type: Artifact type ("srs", "sad", "implementation", etc.).
        docs_path: Path to docs/ directory.
        current_phase: Current pipeline phase.
        max_retries: Maximum fix-retry cycles.
        auto_fix: Whether to attempt auto-fix on violations.

    Returns:
        ConstitutionResult with .score, .passed, .violations.
    """
    from core.quality_gate.constitution.runner import run_constitution_check, ConstitutionResult

    result = run_constitution_check(
        check_type=check_type,
        docs_path=docs_path,
        current_phase=current_phase,
        check_mode="preflight",
        strict=False,
    )

    if not auto_fix:
        return result

    retry = 0
    while not result.passed and retry < max_retries:
        retry += 1
        print(f"\n[orchestration] Constitution check retry {retry}/{max_retries} — "
              f"score={result.score:.0f}%")

        _attempt_auto_fix_with_engine(result, docs_path, current_phase, retry)

        result = run_constitution_check(
            check_type=check_type,
            docs_path=docs_path,
            current_phase=current_phase,
            check_mode="postflight",
            strict=False,
        )

    return result


def run_enforcement_check_with_feedback(
    project_root: str,
    phase: int = 1,
    *,
    max_retries: int = 3,
    auto_fix: bool = False,
) -> "EnforcementResult":
    """Run enforcement check with retry and auto-fix loop.

    Args:
        project_root: Project root path.
        phase: Current pipeline phase.
        max_retries: Maximum fix-retry cycles.
        auto_fix: Whether to attempt auto-fix on violations.

    Returns:
        EnforcementResult with .passed, .violations.
    """
    from enforcement.framework_enforcer import FrameworkEnforcer, EnforcementResult

    enforcer = FrameworkEnforcer(project_root, phase=phase)
    result = enforcer.run(level="BLOCK")

    if not auto_fix:
        return result

    retry = 0
    while not result.passed and retry < max_retries:
        retry += 1
        print(f"\n[orchestration] Enforcement check retry {retry}/{max_retries}")

        fix_ctx = result.to_fix_context()
        if _apply_fix(fix_ctx, project_root, phase, retry):
            result = enforcer.run(level="BLOCK")
        else:
            break

    return result


def run_policy_check_with_feedback(
    project_root: str = "",
    *,
    max_retries: int = 3,
    auto_fix: bool = False,
) -> "list":
    """Run policy engine check with retry and auto-fix loop.

    Args:
        project_root: Project root path.
        max_retries: Maximum fix-retry cycles.
        auto_fix: Whether to attempt auto-fix on violations.

    Returns:
        List of PolicyResult objects.
    """
    from enforcement.policy_engine import PolicyEngine, PolicyViolationException

    engine = PolicyEngine()

    try:
        results = engine.enforce_all()
        return results
    except PolicyViolationException as e:
        if not auto_fix:
            raise
        retry = 0
        while retry < max_retries:
            retry += 1
            print(f"\n[orchestration] Policy check retry {retry}/{max_retries}")
            fix_ctx = e.to_fix_context()
            if _apply_fix(fix_ctx, project_root, 1, retry):
                try:
                    results = engine.enforce_all()
                    return results
                except PolicyViolationException as e2:
                    e = e2
                    if retry >= max_retries:
                        raise
            else:
                break
        raise


# ── Internal helpers ─────────────────────────────────────────────────────────


def _attempt_auto_fix_with_engine(
    result,
    docs_path: str,
    current_phase: int,
    retry_count: int,
) -> None:
    """Delegate auto-fix to AutoFixEngine."""
    from core.auto_fix import AutoFixEngine, FixContext

    path = Path(docs_path)
    project_root = path.parent if path.name == "docs" else path

    engine = AutoFixEngine(project_root=project_root, phase=current_phase)
    context = FixContext(
        source="constitution/runner",
        problem_type="missing_artifact" if result.score == 0.0 else "low_constitution_score",
        severity="critical" if result.score < 80.0 else "high",
        phase=current_phase,
        project_root=project_root,
        details={
            "files": [str(project_root / v.get("file", "")) for v in result.violations if v.get("file")],
            "score": result.score,
            "dimensions": dict(result.dimensions),
        },
        retry_count=retry_count,
    )
    engine.fix(context)


def _apply_fix(fix_ctx: dict, project_root: str, phase: int, retry_count: int) -> bool:
    """Apply a fix using AutoFixEngine. Returns True if fix was attempted."""
    from core.auto_fix import AutoFixEngine, FixContext

    engine = AutoFixEngine(project_root=project_root, phase=phase)
    context = FixContext(
        source=fix_ctx.get("source", "framework_enforcer"),
        problem_type=fix_ctx.get("problem_type", "low_constitution_score"),
        severity=fix_ctx.get("severity", "high"),
        phase=phase,
        project_root=Path(project_root),
        details=fix_ctx,
        retry_count=retry_count,
    )
    result = engine.fix(context)
    return result.success


# Re-export for external consumers
from core.quality_gate.constitution.runner import ConstitutionResult  # noqa: E402, F401

__all__ = [
    "run_constitution_check_with_feedback",
    "run_enforcement_check_with_feedback",
    "run_policy_check_with_feedback",
    "ConstitutionResult",
]
