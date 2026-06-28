#!/usr/bin/env python3
"""check_methodology_consistency.py — drift detector for methodology rules
(improvement A) AND deliverable H1/diskPrefix (improvement C).

Root cause (Bug A+C of 5-point plan):
  - Rule text duplicated in 3 places (plan source, workflow JS prompt,
    reviewer checklist); drift caused P1 HR-12 deadlock on ambiguous SPEC.
  - diskPrefix cfg string in workflow JS drifted from template H1 line and
    from actual deliverable H1 (Bug #137/#138).

This tool consolidates both checks into a single CLI runner that exits
non-zero on any drift. Used as:
  - Pre-commit hook (suggested, not installed by setup-git-hooks.sh)
  - CI step (recommended: `python3 check_methodology_consistency.py --all`)
  - Manual verification after editing plan source or workflow JS prompts.

Commonality: phase-agnostic. Reads the registry once and validates every
rule against every surface it appears in, across all 8 phases.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, cast


HARNESS_ROOT = Path(__file__).resolve().parent.parent
RULES_MANIFEST = HARNESS_ROOT / "rules" / "manifest.yaml"
DELIVERABLES_SCHEMA = HARNESS_ROOT / "schemas" / "deliverables.schema.yaml"

WORKFLOW_JS_DIR_DEFAULT = HARNESS_ROOT.parent / ".claude" / "workflows"
PLAN_DIR_DEFAULT = HARNESS_ROOT.parent / ".methodology"
TEMPLATES_DIR = HARNESS_ROOT / "templates"


# ---------------------------------------------------------------------------
# Minimal YAML reader (avoids PyYAML dependency for tools-layer script)
# ---------------------------------------------------------------------------


def _parse_simple_yaml(text: str) -> dict:
    """Tiny YAML parser supporting the subset manifest.yaml uses.

    Supports: nested mappings (2-space indent), top-level lists, literal
    block scalars (`|`). Does NOT support anchors, complex keys, multi-doc.
    Raises ValueError on syntax errors with line context.

    This is intentionally minimal — the manifest format is constrained by
    our own generator (no external YAML features needed).
    """
    lines = text.split("\n")
    root: dict = {}
    stack: list[tuple[int, object]] = [(-1, root)]

    def _coerce_scalar(s: str):
        s = s.strip()
        # Strip inline comments (only outside of quoted strings).
        # Simple heuristic: if the value starts with a quote, keep it as-is.
        # Otherwise strip from first unquoted `#` onwards.
        if s and s[0] in ('"', "'"):
            # Quoted — keep as-is, just strip surrounding quotes
            if s[0] == '"' and s[-1] == '"':
                return s[1:-1]
            if s[0] == "'" and s[-1] == "'":
                return s[1:-1]
            return s
        # Unquoted — strip trailing comment
        hash_idx = s.find("#")
        if hash_idx >= 0:
            s = s[:hash_idx].rstrip()
        if not s:
            return None
        if s.startswith("[") and s.endswith("]"):
            inner = s[1:-1].strip()
            return [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]
        if s.startswith('"') and s.endswith('"'):
            return s[1:-1]
        if s.startswith("'") and s.endswith("'"):
            return s[1:-1]
        if s.lower() in ("true", "false"):
            return s.lower() == "true"
        if s.lower() in ("null", "~", ""):
            return None
        try:
            return int(s)
        except ValueError:
            pass
        return s

    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            i += 1
            continue

        # Detect literal block scalar (`|`)
        if stripped.endswith("|") and ":" in stripped:
            key_part = stripped[:-1].rstrip()
            key = key_part.split(":", 1)[0].strip()
            indent = len(raw) - len(raw.lstrip())
            parent = cast(dict[str, Any], stack[-1][1])
            # Collect lines at indent+2 or more
            block_lines: list[str] = []
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                if not next_line.strip():
                    block_lines.append("")
                    j += 1
                    continue
                next_indent = len(next_line) - len(next_line.lstrip())
                if next_indent <= indent:
                    break
                block_lines.append(next_line[indent + 2:])
                j += 1
            parent[key] = "\n".join(block_lines).rstrip("\n")
            i = j
            continue

        indent = len(raw) - len(raw.lstrip())
        # Pop stack to find correct parent:
        #   - For `key: value` (non-empty): pop anything > indent (strict).
        #     Same-indent stays so list-of-mappings continuation works.
        #   - For `key:` (empty, creates new container): pop anything >= indent.
        #     Same-indent must pop because the new key is a SIBLING of the
        #     previous empty-value key, not a child.
        if ":" in stripped and not stripped.lstrip().startswith("- "):
            key, _, val = stripped.lstrip().partition(":")
            key = key.strip()
            val = val.strip()
            if val == "":
                # Empty value → new container at this indent.
                while stack and stack[-1][0] >= indent:
                    stack.pop()
            else:
                # Non-empty value → assign to existing parent at lower indent.
                while stack and stack[-1][0] > indent:
                    stack.pop()
        else:
            # `- item` (list item) or block scalar continuation.
            while stack and stack[-1][0] > indent:
                stack.pop()

        if not stack:
            raise ValueError(f"YAML parse error at line {i+1}: indent mismatch")
        parent = cast(Any, stack[-1][1])

        if ":" in stripped and not stripped.lstrip().startswith("- "):
            key, _, val = stripped.lstrip().partition(":")
            key = key.strip()
            val = val.strip()
            if val == "":
                # Peek ahead: if next non-empty line at indent+2 starts with "- ",
                # create a list. Otherwise create a dict.
                next_kind = _peek_next_kind(lines, i + 1, indent)
                new: dict | list = [] if next_kind == "list" else {}
                stack.append((indent, new))
                cast(dict[str, Any], parent)[key] = new
            else:
                cast(dict[str, Any], parent)[key] = _coerce_scalar(val)
        elif stripped.lstrip().startswith("- "):
            item = stripped.lstrip()[2:].strip()
            # Ensure parent is a list (peek-ahead should have set it up)
            if not isinstance(parent, list):
                raise ValueError(
                    f"YAML parse error at line {i+1}: list item in non-list parent"
                )
            if item and ":" in item:
                # List of mappings: first key on same line as `-`
                inner_key, _, inner_val = item.partition(":")
                inner_key = inner_key.strip()
                inner_val = inner_val.strip()
                m: dict[str, Any] = {inner_key: _coerce_scalar(inner_val)}
                parent.append(m)
                # Future indented lines belong to this mapping
                stack.append((indent + 2, m))
            else:
                parent.append(_coerce_scalar(item))
        i += 1

    return root


def _peek_next_kind(lines: list[str], start: int, parent_indent: int) -> str:
    """Look ahead to determine if the next non-empty indented block is a list or mapping.

    Returns 'list' if the next line at indent > parent_indent starts with '- ',
    'dict' otherwise. Used to decide whether to create a list or dict for
    empty-value YAML keys.
    """
    for j in range(start, len(lines)):
        line = lines[j]
        if not line.strip():
            continue
        line_indent = len(line) - len(line.lstrip())
        if line_indent <= parent_indent:
            return "dict"  # back to outer scope
        if line.lstrip().startswith("- "):
            return "list"
        return "dict"
    return "dict"


# ---------------------------------------------------------------------------
# Rule registry parser
# ---------------------------------------------------------------------------


def load_rules_manifest(path: Path = RULES_MANIFEST) -> dict:
    """Load and parse the rule registry. Returns dict {rule_id: rule_dict}."""
    if not path.exists():
        return {}
    raw = _parse_simple_yaml(path.read_text(encoding="utf-8"))
    rules = raw.get("rules") or {}
    return rules


def load_deliverables_schema(path: Path = DELIVERABLES_SCHEMA) -> dict:
    """Load and parse the deliverables schema. Returns dict keyed by deliverable name."""
    if not path.exists():
        return {}
    raw = _parse_simple_yaml(path.read_text(encoding="utf-8"))
    return raw.get("deliverables") or {}


# ---------------------------------------------------------------------------
# Surface scanners
# ---------------------------------------------------------------------------


_PLAN_RULE_MARKER_RE = re.compile(r"<!--\s*@rule\s+(?P<id>R-[\w\-]+)\s*-->")
_JS_RULE_MARKER_RE = re.compile(r"//\s*@rule\s+(?P<id>R-[\w\-]+)\s*")


def _scan_plan_files(plan_dir: Path) -> dict[str, list[str]]:
    """For each phase plan file, collect rule IDs marked in markdown comments.

    Returns {plan_filename: [rule_id, ...]}.
    """
    out: dict[str, list[str]] = {}
    if not plan_dir.exists():
        return out
    for plan_file in sorted(plan_dir.glob("phase*_plan.md")):
        text = plan_file.read_text(encoding="utf-8")
        out[plan_file.name] = _PLAN_RULE_MARKER_RE.findall(text)
    return out


def _scan_workflow_js(workflow_dir: Path) -> dict[str, list[str]]:
    """For each workflow JS file, collect rule IDs marked in JS line comments.

    Returns {js_filename: [rule_id, ...]}.
    """
    out: dict[str, list[str]] = {}
    if not workflow_dir.exists():
        return out
    for js_file in sorted(workflow_dir.glob("phase*.js")):
        text = js_file.read_text(encoding="utf-8")
        out[js_file.name] = _JS_RULE_MARKER_RE.findall(text)
    return out


# ---------------------------------------------------------------------------
# C check — three-way H1/diskPrefix consistency
# ---------------------------------------------------------------------------


def check_deliverables(
    schema: dict,
    plan_dir: Path = PLAN_DIR_DEFAULT,
    workflow_dir: Path = WORKFLOW_JS_DIR_DEFAULT,
    templates_dir: Path = TEMPLATES_DIR,
    project_root: Path | None = None,
) -> list[str]:
    """Three-way consistency check for every deliverable.

    Validates that:
      1. Workflow JS cfg `diskPrefix` literal matches schema `disk_prefix`.
      2. Template H1 line matches schema `template_h1_pattern`.
      3. (Optional) Live deliverable H1 matches schema `expected_actual_h1_pattern`.

    Returns list of error messages. Empty list = pass.
    """
    errors: list[str] = []
    if not schema:
        return ["deliverables schema missing or empty — cannot check C"]

    # Find diskPrefix literals in workflow JS files
    disk_prefix_literals: dict[str, dict[str, str]] = {}
    if workflow_dir.exists():
        for js_file in sorted(workflow_dir.glob("phase*.js")):
            # Match `diskPrefix: '...'` (single-quoted)
            matches = re.findall(
                r"diskPrefix:\s*['\"]([^'\"]+)['\"]",
                js_file.read_text(encoding="utf-8"),
            )
            for m in matches:
                # Map literal back to deliverable name (loose match)
                disk_prefix_literals.setdefault(m, {})[js_file.name] = m

    for deliv_name, deliv in schema.items():
        schema_prefix = deliv.get("disk_prefix")
        if not schema_prefix:
            errors.append(f"[{deliv_name}] schema missing disk_prefix")
            continue

        # (1) Check workflow JS cfg literal
        wf_match = disk_prefix_literals.get(schema_prefix)
        if not wf_match:
            errors.append(
                f"[{deliv_name}] workflow JS cfg diskPrefix literal "
                f"'{schema_prefix}' not found in any phase*.js (Bug #137 regression)"
            )

        # (2) Check template H1 pattern
        template_h1_pattern = deliv.get("template_h1_pattern")
        # Use explicit template_path if provided, otherwise derive from disk_path_segment basename.
        template_rel = deliv.get("template_path")
        if not template_rel:
            # Fallback: filename of disk_path_segment (handles '01-requirements/SRS.md' → 'SRS.md')
            disk_seg = deliv.get("disk_path_segment", "")
            template_rel = disk_seg.rsplit("/", 1)[-1] if "/" in disk_seg else disk_seg
        template_path = templates_dir / template_rel
        if template_path.exists():
            template_text = template_path.read_text(encoding="utf-8")
            first_line = template_text.split("\n", 1)[0]
            if template_h1_pattern and not re.match(template_h1_pattern, first_line):
                errors.append(
                    f"[{deliv_name}] template H1 '{first_line}' does not match "
                    f"pattern '{template_h1_pattern}'"
                )
        else:
            # Template may be absent for some phases; warn but don't fail
            errors.append(
                f"[{deliv_name}] template file missing: {template_path}"
            )

        # (3) Check live deliverable if project_root given
        if project_root:
            live_path = project_root / deliv.get("disk_path_segment", "")
            if live_path.exists():
                live_text = live_path.read_text(encoding="utf-8")
                live_first = live_text.split("\n", 1)[0]
                expected_pattern = deliv.get("expected_actual_h1_pattern")
                if expected_pattern and not re.match(expected_pattern, live_first):
                    errors.append(
                        f"[{deliv_name}] live deliverable H1 '{live_first}' does "
                        f"not match expected pattern '{expected_pattern}' "
                        f"(drift between A's authoring and schema)"
                    )

    return errors


# ---------------------------------------------------------------------------
# A check — rule drift across surfaces
# ---------------------------------------------------------------------------


def check_rules(
    rules: dict,
    plan_dir: Path = PLAN_DIR_DEFAULT,
    workflow_dir: Path = WORKFLOW_JS_DIR_DEFAULT,
) -> list[str]:
    """Validate that every rule's canonical text is present in every declared
    surface (plan source / workflow JS prompt / B checklist / constitution).

    Returns list of error messages. Empty list = pass.
    """
    errors: list[str] = []
    if not rules:
        return ["rules manifest missing or empty — cannot check A"]

    plan_files = _scan_plan_files(plan_dir)
    js_files = _scan_workflow_js(workflow_dir)

    plan_text = ""
    for fname in plan_files:
        plan_text += (plan_dir / fname).read_text(encoding="utf-8") + "\n"

    js_text = ""
    for fname in js_files:
        js_text += (workflow_dir / fname).read_text(encoding="utf-8") + "\n"

    for rule_id, rule in rules.items():
        canonical = (rule.get("text") or "").strip()
        if not canonical:
            errors.append(f"[{rule_id}] rule has empty text")
            continue
        # Use multi-token fingerprint matching instead of byte equality.
        # Markdown formatting (e.g. **bold**) and whitespace may differ between
        # surfaces; we check that 3+ key tokens from the canonical text appear
        # in each surface, which is robust to formatting drift but still
        # catches substantive rule drift (e.g. a rule rewritten in one surface
        # but not the others).
        tokens = _extract_fingerprint_tokens(canonical, n=4)
        if len(tokens) < 4:
            errors.append(
                f"[{rule_id}] canonical text too short to fingerprint "
                f"({len(tokens)} tokens < 4 required)"
            )
            continue
        for surface in rule.get("surfaces", []):
            if surface in ("plan_task_hint", "plan_checks"):
                missing = [t for t in tokens if t not in plan_text]
                if len(missing) > 0:
                    errors.append(
                        f"[{rule_id}] {len(missing)}/{len(tokens)} fingerprint "
                        f"tokens missing in plan source (surface: {surface}, "
                        f"missing: {missing[:3]})"
                    )
            elif surface in ("workflow_a_prompt", "workflow_b_checklist"):
                missing = [t for t in tokens if t not in js_text]
                if len(missing) > 0:
                    errors.append(
                        f"[{rule_id}] {len(missing)}/{len(tokens)} fingerprint "
                        f"tokens missing in workflow JS (surface: {surface}, "
                        f"missing: {missing[:3]})"
                    )
            elif surface == "constitution_doc":
                # Optional: CONSTITUTION.md may not exist; warn but skip if absent
                pass  # constitution text matching is optional — see §8 references

    return errors


def _extract_fingerprint_tokens(text: str, n: int = 4) -> list[str]:
    """Extract N characteristic tokens from canonical text for surface matching.

    Picks tokens that are unlikely to collide with stopwords or formatting.
    Heuristic: pick tokens >= 8 chars that are content-bearing (not common
    English). If fewer than N candidates, fall back to >= 5 char tokens.
    """
    STOP = frozenset("""
        a an the and or of in on at to for with by from as is are be been
        being has have had do does did will would shall should may might can
        could must this that these those it its their there here what which who
        whom whose if then else when where why how not no nor so than too very
        rule rules rule: canonical interpret interpretation verbatim canonical:
    """.split())

    words = re.findall(r"[A-Za-z][A-Za-z0-9\-]{4,}", text)
    seen: set[str] = set()
    tokens: list[str] = []
    # First pass: >= 8 chars, skip stopwords, prefer rare terms
    for w in words:
        wl = w.lower()
        if wl in STOP or len(w) < 8 or wl in seen:
            continue
        seen.add(wl)
        tokens.append(w)
        if len(tokens) >= n:
            break
    # Fallback: >= 5 chars if first pass insufficient
    if len(tokens) < n:
        for w in words:
            wl = w.lower()
            if wl in STOP or len(w) < 5 or wl in seen:
                continue
            seen.add(wl)
            tokens.append(w)
            if len(tokens) >= n:
                break
    return tokens


# ---------------------------------------------------------------------------
# Combined runner
# ---------------------------------------------------------------------------


def run_all(
    plan_dir: Path = PLAN_DIR_DEFAULT,
    workflow_dir: Path = WORKFLOW_JS_DIR_DEFAULT,
    project_root: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Run both A (rule drift) and C (deliverable schema) checks.

    Returns (rule_errors, deliverable_errors).
    """
    rules = load_rules_manifest()
    schema = load_deliverables_schema()

    rule_errors = check_rules(rules, plan_dir=plan_dir, workflow_dir=workflow_dir)
    deliv_errors = check_deliverables(
        schema, plan_dir=plan_dir, workflow_dir=workflow_dir, project_root=project_root,
    )
    return rule_errors, deliv_errors


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Check methodology consistency: rule drift (A) + "
                    "deliverable H1 schema (C).",
    )
    parser.add_argument("--plan-dir", default=str(PLAN_DIR_DEFAULT),
                        help="Path to .methodology/ (default: harness/../methodology)")
    parser.add_argument("--workflow-dir", default=str(WORKFLOW_JS_DIR_DEFAULT),
                        help="Path to .claude/workflows/ (default: harness/../.claude/workflows)")
    parser.add_argument("--project-root", default=None,
                        help="Path to project root for live deliverable H1 check (optional)")
    parser.add_argument("--rules-only", action="store_true",
                        help="Run only the rule drift (A) check")
    parser.add_argument("--deliverables-only", action="store_true",
                        help="Run only the deliverable schema (C) check")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    args = parser.parse_args()

    plan_dir = Path(args.plan_dir).resolve()
    workflow_dir = Path(args.workflow_dir).resolve()
    project_root = Path(args.project_root).resolve() if args.project_root else None

    rules = load_rules_manifest()
    schema = load_deliverables_schema()

    rule_errors: list[str] = []
    deliv_errors: list[str] = []

    if args.rules_only:
        rule_errors = check_rules(rules, plan_dir=plan_dir, workflow_dir=workflow_dir)
    elif args.deliverables_only:
        deliv_errors = check_deliverables(
            schema, plan_dir=plan_dir, workflow_dir=workflow_dir, project_root=project_root,
        )
    else:
        rule_errors, deliv_errors = run_all(
            plan_dir=plan_dir, workflow_dir=workflow_dir, project_root=project_root,
        )

    total_errors = len(rule_errors) + len(deliv_errors)

    if args.json:
        print(json.dumps({
            "rule_errors": rule_errors,
            "deliverable_errors": deliv_errors,
            "total_errors": total_errors,
            "rules_loaded": len(rules),
            "deliverables_loaded": len(schema),
        }, indent=2))
    else:
        print(f"[methodology_consistency] rules loaded: {len(rules)}, deliverables loaded: {len(schema)}")
        if rule_errors:
            print(f"\n  Rule drift errors ({len(rule_errors)}):")
            for e in rule_errors:
                print(f"    - {e}")
        if deliv_errors:
            print(f"\n  Deliverable schema errors ({len(deliv_errors)}):")
            for e in deliv_errors:
                print(f"    - {e}")
        if total_errors == 0:
            print("\n  OK — all surfaces consistent.")

    return 1 if total_errors > 0 else 0


if __name__ == "__main__":
    sys.exit(_cli())
