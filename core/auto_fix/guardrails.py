#!/usr/bin/env python3
"""
Safety guardrails for auto-fix operations.

Pre-fix:  impact check, diff preview, file write safety
Post-fix: drift check, score regression detection, invariant verification
"""

from __future__ import annotations

import ast
import copy
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def pre_fix_safety_check(
    project_root: Path,
    files_to_modify: List[Path],
    crg_bridge=None,
) -> Dict[str, Any]:
    """Check if it's safe to auto-fix.

    Returns:
        {"safe": bool, "risks": list, "message": str}
    """
    risks = []

    for fp in files_to_modify:
        if not isinstance(fp, Path):
            continue
        # Never modify files outside project
        try:
            fp.resolve().relative_to(project_root.resolve())
        except ValueError:
            risks.append(f"File outside project root: {fp}")
            continue

        # Never modify .git/ files
        if ".git" in fp.parts:
            risks.append(f"Git internal file: {fp}")
            continue

        # Never modify .methodology/state.json directly
        if fp.name == "state.json" and ".methodology" in str(fp):
            risks.append(f"State file modification: {fp}")

    # CRG impact check if available
    if crg_bridge and files_to_modify:
        try:
            impact = crg_bridge.check_pre_fix_safety(str(project_root))
            if not impact.get("safe", True):
                risks.append(f"CRG impact: {impact.get('message', 'unsafe')}")
        except Exception as exc:
            print(f"[WARN] auto_fix guardrails: CRG pre-fix safety check failed, "
                  f"proceeding without it: {exc}", file=sys.stderr)

    safe = len(risks) == 0
    message = "Safety check passed" if safe else f"Safety check failed: {'; '.join(risks)}"
    return {"safe": safe, "risks": risks, "message": message}


def post_fix_drift_check(
    project_root: Path,
    files_modified: List[Path],
    drift_threshold: float = 85.0,
) -> Dict[str, Any]:
    """Verify no structural drift introduced by auto-fix.

    Returns:
        {"drifted": bool, "score": float, "threshold": float, "message": str}
    """
    # For now, check that all modified files are still well-formed markdown or python
    issues = []
    for fp in files_modified:
        if not isinstance(fp, Path) or not fp.exists():
            continue
        content = fp.read_text(encoding="utf-8")
        if fp.suffix == ".md" and len(content.strip()) == 0:
            issues.append(f"Empty markdown file after fix: {fp}")
        if fp.suffix == ".py":
            try:
                compile(content, str(fp), "exec")
            except SyntaxError as e:
                issues.append(f"Syntax error in {fp}: {e}")

    drifted = len(issues) > 0
    score = 100.0 if not drifted else max(0.0, 100.0 - len(issues) * 10.0)
    return {
        "drifted": drifted,
        "score": score,
        "threshold": drift_threshold,
        "message": "; ".join(issues) if issues else "No drift detected",
    }


def regression_check(
    project_root: Path,
    pre_fix_metrics: Dict[str, float],
    post_fix_metrics: Dict[str, float],
    regression_threshold: float = 5.0,
) -> Dict[str, Any]:
    """Check that no metrics regressed more than threshold.

    Returns:
        {"regressed": bool, "regressions": list, "message": str}
    """
    regressions = []
    for metric, pre_val in pre_fix_metrics.items():
        post_val = post_fix_metrics.get(metric, pre_val)
        if post_val < pre_val - regression_threshold:
            regressions.append(f"{metric}: {pre_val:.1f} -> {post_val:.1f} (delta: {post_val - pre_val:.1f})")

    regressed = len(regressions) > 0
    message = "No regression detected" if not regressed else f"Regression detected: {'; '.join(regressions)}"
    return {"regressed": regressed, "regressions": regressions, "message": message}


def safety_diff_preview(files_to_modify: List[Path]) -> str:
    """Generate a diff preview of pending auto-fix changes."""
    lines = []
    for fp in files_to_modify:
        if not isinstance(fp, Path) or not fp.exists():
            lines.append(f"[NEW] {fp}")
            continue
        lines.append(f"[MODIFY] {fp} ({fp.stat().st_size} bytes)")
    return "\n".join(lines)


def verify_no_secrets_introduced(content: str) -> bool:
    """Scan content for newly introduced secrets."""
    secret_indicators = [
        "password = \"", "password = '",
        "secret_key = \"", "secret_key = '",
        "api_key = \"", "api_key = '",
        "token = \"", "token = '",
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN EC PRIVATE KEY-----",
    ]
    content_lower = content.lower()
    for indicator in secret_indicators:
        if indicator.lower() in content_lower:
            return False
    return True


def rollback_if_unsafe(
    project_root: Path,
    backup_map: Dict[Path, str],
) -> int:
    """Restore files from backup if post-fix drift/regression detected.

    Returns number of files rolled back.
    """
    rolled_back = 0
    for fp, original_content in backup_map.items():
        try:
            fp.write_text(original_content, encoding="utf-8")
            rolled_back += 1
        except Exception as exc:
            from core.degradation_ledger import record_degradation
            record_degradation(
                project_root, "auto_fix.guardrails.rollback_if_unsafe",
                f"could not restore {fp} to its pre-fix content — file is left "
                f"in its (unsafe) post-fix state",
                why=str(exc), owner="harness"
            )
    return rolled_back


def ast_mutation_guard(
    file_path: Path,
    pre_content: str,
    post_content: str,
    allowed_node_name: Optional[str] = None
) -> bool:
    """
    Compare AST trees before and after fix.
    Ensures no nodes outside the allowed_node_name are modified or added.

    Handles two scopes:
    - Top-level: allowed_node_name matches a top-level FunctionDef / ClassDef →
      that entire node is excluded from the invariant set.
    - Nested method: allowed_node_name matches a method inside a top-level ClassDef →
      only that specific method is excluded from the class body invariants; all
      other class members and top-level nodes must remain unchanged.
    """
    if not allowed_node_name:
        return True  # No dynamic constraint applied

    if file_path.suffix != ".py":
        return True  # Only Python files supported for AST analysis

    try:
        pre_tree = ast.parse(pre_content)
        post_tree = ast.parse(post_content)

        def get_invariants(tree: ast.Module, exclude_name: str) -> List[str]:
            invariants = []
            for node in tree.body:
                # Top-level node matches: exclude entirely.
                if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                    if node.name == exclude_name:
                        continue
                    # ClassDef whose body contains the allowed method as a direct child.
                    if isinstance(node, ast.ClassDef):
                        member_names = {
                            item.name for item in node.body
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        }
                        if exclude_name in member_names:
                            # We must dump the class (to catch changes to bases, decorators, name)
                            # but we strip out the allowed method from the body before dumping.
                            node_copy = copy.deepcopy(node)
                            node_copy.body = [
                                item for item in node_copy.body
                                if not (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                                        and item.name == exclude_name)
                            ]
                            invariants.append(ast.dump(node_copy))
                            continue  # skip default dump of the original ClassDef
                invariants.append(ast.dump(node))
            return invariants

        pre_inv = get_invariants(pre_tree, allowed_node_name)
        post_inv = get_invariants(post_tree, allowed_node_name)

        if pre_inv != post_inv:
            return False

    except Exception:
        # Any parsing failure (including SyntaxError) represents corruption.
        return False

    return True
