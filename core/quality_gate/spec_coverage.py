"""D4 spec-coverage: TEST_SPEC.md → test-function traceability (unified v2.6).

Moved verbatim from harness_cli.py (方案六 final step) so that
core.quality_gate.spec_tracking_checker no longer imports the CLI layer —
core must never depend on cli/harness_cli. harness_cli re-exports these
names for its own callers and for existing monkeypatch targets.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from core.utils.project_layout import ProjectLayout
from core.quality_gate.parsers import SRS_SUBSECTION_PREFIX

def _get_test_directories(project: Path) -> list[Path]:
    """Return all valid test directories (resolving symlinks and canonical layout)."""
    dirs = []
    
    # 1. Project root tests/
    tests_root = project / "tests"
    if tests_root.is_dir() and not tests_root.is_symlink():
        dirs.append(tests_root)
        
    if tests_root.is_symlink():
        try:
            real_tests = tests_root.resolve()
            if real_tests.is_dir() and real_tests not in dirs:
                dirs.append(real_tests)
        except ValueError:
            pass
            
    # 2. Canonical harness layout
    canonical_tests = ProjectLayout(project).phase3_development_dir / "tests"
    if canonical_tests.is_dir():
        # Avoid duplicate if symlink already resolved to canonical
        if canonical_tests.resolve() not in [d.resolve() for d in dirs]:
            dirs.append(canonical_tests)
            
    return dirs


def _scan_test_functions(test_dir: Path, language: str = "python") -> set[str]:
    """Scan test files for harness-convention test names.

    python: `def test_*` function definitions.
    js/ts:  it('test_*') / test("test_*") TITLES — the harness naming
            convention (templates/TEST_SPEC.md) that keeps D4 spec-coverage
            and P1 Naming Authority matching language-independent.
    """
    from core.utils.lang_patterns import JS_TEST_TITLE_PATTERN, iter_test_files

    fns: set[str] = set()
    if not test_dir.is_dir():
        return fns
    if language == "python":
        files = sorted(test_dir.rglob("*.py"))
    else:
        files = list(iter_test_files(test_dir, language))
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if language == "python":
            for line in text.splitlines():
                m2 = re.match(r"^\s*(?:async\s+)?def\s+(test_\w+)\s*\(", line)
                if m2:
                    fns.add(m2.group(1))
        else:
            fns.update(JS_TEST_TITLE_PATTERN.findall(text))
    return fns


def _flatten_test_names(inventory: dict | None) -> set[str]:
    """Flatten TEST_INVENTORY.yaml fr_tests + cross_cutting into a set of function names."""
    names: set[str] = set()
    if not inventory:
        return names
    for fr_key in ("fr_tests", "cross_cutting"):
        section = inventory.get(fr_key, {})
        if isinstance(section, list):
            names.update(section)
        elif isinstance(section, dict):
            for layers in section.values():
                if isinstance(layers, list):
                    names.update(layers)
                elif isinstance(layers, dict):
                    for items in layers.values():
                        if isinstance(items, list):
                            names.update(items)
    return names


def _header_columns(header: str) -> dict:
    """Which column holds `Test Function`, `Type` and `Derivation`.

    Round 73 站1. The header row was already being read — searching it for
    "Test Function" is what decides a table has started — and then the name
    was taken from `cols[1]` no matter which column that header named.

    `templates/TEST_SPEC.md` writes `| # | Test Function | Type | Derivation |`,
    so the assumption held for the shape this framework ships. It does not hold
    for the shape this framework's own rules produce: `cli/checks/specs.py`'s
    NFR Layering Hard Rule requires every unit/static NFR test to live in a
    "Deferred to Downstream Phases" section, and every project on this machine
    writes that table as `| # | NFR | Test Function | Layer | Title |`. `cols[1]`
    is then the NFR id, and the row was dropped in silence — 34 of taskq-new's
    115 declarations, 36 of taskq-super's, 8 of taskq-advance's.

    `Type` and `Derivation` move with it. Read from `cols[2]`/`cols[3]`, they
    were one column off on the five-column FR table the projects actually write
    (`| # | Test Function | Inputs | Type | Derivation |`) — `type` carried the
    inputs, and `_record_spec_undelivered` copies both into the ledger.

    Returns `{}` when no column names a test function: a sub-assertion table
    (`| rule_id | predicate | phase |`) names none, and a header-driven index
    that fired on any table would put predicates into the denominator.
    """
    cols = [c.strip() for c in header.split("|")[1:-1]]
    out: dict = {}
    for i, col in enumerate(cols):
        for key, pattern in (("fn", r"Test Function"),
                             ("type", r"^Type$"),
                             ("derivation", r"^Derivation$")):
            if key not in out and re.search(pattern, col, re.IGNORECASE):
                out[key] = i
    return out if "fn" in out else {}


def _row_test_fn(cols: list, header_index: "int | None") -> str:
    """The test function named by one table row, or "".

    The header locates `Type` and `Derivation`, which have no decidable
    content, and it arbitrates ties here. It does NOT locate the name on its
    own, and the reason is measured: taskq-renew's deferred-NFR table writes
    the header `| # | NFR | Test Function | Layer | Title |` above rows shaped
    `| 9 | \\`test_nfr02_bandit…\\` | NFR-02 | static | … |` — the header and its
    own rows disagree about column order. A first draft of this station trusted
    the header alone and dropped all 36 of that project's declarations; the
    monotonicity guard beside this function is what caught it.

    So the name is found by what a cell IS, not where it sits: exactly one cell
    in a declaration row is an identifier beginning `test_`. Zero means the row
    declares no test (a separator, a totals line, a sub-assertion). More than
    one is genuine ambiguity — a Title column repeating the name — and there
    the header decides, because with two candidates the position is the only
    thing left that can.
    """
    candidates = []
    for i, cell in enumerate(cols):
        name = re.sub(r"\[.*\]$", "", cell.strip("`").strip())
        if name.startswith("test_") and len(name) > 6 and " " not in name:
            candidates.append((i, name))
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0][1]
    for i, name in candidates:
        if i == header_index:
            return name
    return candidates[0][1]


def _parse_test_spec(spec_path: Path) -> list[dict]:
    """Parse TEST_SPEC.md and return all named test cases.

    Handles the markdown table format produced by the derive_test_cases.md skill:
      | # | Test Function | Type | Derivation |
      |---|---|---|---|
      | 1 | `test_frXX_...` | happy_path | Q1 |

    and the five-column NFR shape the NFR Layering Hard Rule produces:
      | # | NFR | Test Function | Layer | Title |

    Which column holds what is decided by `_header_columns` from the header
    row, per table — resolving it once for the file would read a four-column
    table following a five-column one out of the wrong column.

    Returns a list of dicts with keys: test_fn, type, derivation, fr_id.
    Backtick-wrapped function names (e.g. `test_foo`) are unwrapped automatically.
    """
    results: list[dict] = []
    if not spec_path.exists():
        return results

    text = spec_path.read_text(encoding="utf-8")
    current_fr: str = ""
    in_table = False
    header_skipped = False
    columns: dict = {}

    for line in text.splitlines():
        stripped = line.strip()

        # Detect FR section headers: ## FR-XX: ... or ### FR-XX: ...
        # Accept both H2 and H3 levels so docs and concrete specs can use either.
        # SRS_SUBSECTION_PREFIX tolerates TOC-numbered subsections like
        # `### 2.1 FR-01` (see spec_alignment.py for the same bug class).
        fr_match = re.match(r"^#{2,3}\s+" + SRS_SUBSECTION_PREFIX + r"(FR-\d+)[:\s]", stripped)
        if fr_match:
            current_fr = fr_match.group(1)
            in_table = False
            header_skipped = False
            continue

        # Detect any H2/H3 section that is NOT an FR header — prevents last FR
        # bleeding into the next section. Tags items under a normalised slug so
        # they're traceable but won't be confused with real FR-IDs (which follow
        # the FR-\d+ pattern).
        if re.match(r"^#{2,3}\s+\S", stripped) and not re.match(r"^#{2,3}\s+" + SRS_SUBSECTION_PREFIX + r"(FR-\d+)[:\s]", stripped):
            h_text = re.sub(r"^#{2,3}\s+", "", stripped).strip()
            current_fr = re.sub(r"\W+", "_", h_text.lower()).rstrip("_")[:30]
            in_table = False
            header_skipped = False
            continue

        # Horizontal rule — close current table without changing FR context
        if re.match(r"^---+$", stripped) or re.match(r"^\*\*\*+$", stripped):
            in_table = False
            continue

        # Detect table header row (| # | Test Function | ...)
        if "|" in stripped and re.search(r"Test Function", stripped, re.IGNORECASE):
            columns = _header_columns(stripped)
            in_table = bool(columns)
            header_skipped = False
            continue

        # Skip the separator row (|---|---|...)
        if in_table and re.match(r"^\|[-| ]+\|$", stripped):
            header_skipped = True
            continue

        # Parse data rows
        if in_table and header_skipped and stripped.startswith("|") and stripped.endswith("|"):
            cols = [c.strip() for c in stripped.split("|")[1:-1]]

            def _col(key: str) -> str:
                i = columns.get(key)
                return cols[i] if i is not None and i < len(cols) else ""

            raw_fn = _row_test_fn(cols, columns.get("fn"))
            if raw_fn:
                results.append({
                    "test_fn": raw_fn,
                    "type": _col("type"),
                    "derivation": _col("derivation"),
                    "fr_id": current_fr,
                })
            continue

        # A blank line or non-table line ends the table
        if in_table and not stripped.startswith("|"):
            if stripped:
                in_table = False

    return results


def spec_coverage_report(
    project: Path,
    *,
    fr_id: "str | None" = None,
    _items: "list[dict] | None" = None,
) -> dict:
    """Which declared tests exist, which do not, and the ratio between them.

    Round 42 站2. `_run_spec_coverage_check` has always known exactly which
    declared tests are absent — it prints them — and has always returned only
    `(exit_code, pct)`. The list was a local; stdout was the only place it
    went.

    Measured on taskq-renew with this module's own checker: 81/89 = 91.011...%
    against Gate 4's threshold of 90.0, over by one point. That ratio is not
    merely close to the `traceability` score in its committed
    gate4_result.json — it is that number to the last digit
    (91.01123595505618). So the count reached the verdict, the artifact and
    the quality report, and the eight names behind it reached a terminal:
    three `nfr_pattern`, four `fault_injection`, one static scan, every one of
    them a p95 budget or a survives-SIGKILL case.

    Returns ``{declared, implemented, covered, missing, pct}`` where `covered`
    and `missing` are the parsed TEST_SPEC rows, each keeping its `type`,
    `derivation` and `fr_id` — without those a name cannot say which
    requirement lost its evidence.

    `pct` is 100.0 for an empty declaration set. That is the same vacuous-pass
    answer `_run_spec_coverage_check` gives, and it is that function, not this
    one, that decides whether an empty set is legitimate (it blocks when FRs
    are defined). This function reports; it does not adjudicate.

    `_items` is for `_run_spec_coverage_check`, which has already parsed and
    filtered the rows and must not parse them a second time — two parses is
    two chances to disagree about the denominator.
    """
    from core.utils.lang_patterns import project_language

    items = _items
    if items is None:
        spec_path = ProjectLayout(project).test_spec_path
        items = _parse_test_spec(spec_path) if spec_path.exists() else []
        if fr_id:
            items = [i for i in items if i["fr_id"] == fr_id]

    # Bug #130 fix (2026-06-27): the canonical harness layout puts tests at
    # `03-development/tests/`, not `<project>/tests/`. _scan_test_functions
    # reads only the directory it's pointed at, so without scanning both
    # paths D4 spec-coverage reports 0% on the canonical layout. Combine
    # both scans (dedup via set union) so projects with either layout
    # produce the correct coverage percentage.
    _lang = project_language(project)
    actual_fns: set = set()
    for test_dir in _get_test_directories(project):
        actual_fns |= _scan_test_functions(test_dir, _lang)

    covered = [i for i in items if i["test_fn"] in actual_fns]
    missing = [i for i in items if i["test_fn"] not in actual_fns]
    return {
        "declared": len(items),
        "implemented": len(covered),
        "covered": covered,
        "missing": missing,
        "pct": (len(covered) / len(items) * 100) if items else 100.0,
    }


def _run_spec_coverage_check(
    project: Path,
    threshold: float = 80.0,
    *,
    fr_id: str | None = None,
    verbose: bool = True,
) -> tuple[int, float]:
    """Check TEST_SPEC.md items against actual test function implementations.

    This is the UNIFIED D4 check (v2.6). TEST_SPEC.md is the single source of
    truth for all test traceability. For each test case declared in TEST_SPEC.md
    (P2 deliverable), verify that a matching test function exists in tests/.
    Replaces the prior two-check model (I-1 TEST_INVENTORY.yaml forward +
    I-5 TEST_SPEC.md backward).

    Args:
        project: Project root directory.
        threshold: Minimum percentage of spec items that must be implemented.
        fr_id: If set, check only items for that FR (e.g. "FR-03").
        verbose: Print detailed results.

    Returns:
        (exit_code, coverage_pct). 0 = pass, 1 = below threshold.
        If TEST_SPEC.md is absent, returns (0, 100.0) — non-blocking.
    """
    spec_path = ProjectLayout(project).test_spec_path
    if not spec_path.exists():
        sad_path = ProjectLayout(project).sad_path
        sad_has_frs = False
        if sad_path.exists():
            sad_text = sad_path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"\bFR-\d+\b", sad_text):
                sad_has_frs = True

        if sad_has_frs:
            if verbose:
                print("[spec-coverage] ERROR: TEST_SPEC.md not found at 02-architecture/TEST_SPEC.md but SAD.md has FRs.")
            return (1, 0.0)
            
        if verbose:
            print("[spec-coverage] TEST_SPEC.md not found and SAD.md has no FRs — skipping.")
        return (0, 100.0)

    items = _parse_test_spec(spec_path)
    if fr_id:
        items = [i for i in items if i["fr_id"] == fr_id]

    if not items:
        # v2.9 B.3 fix: vacuous pass was masking wrong-shape TEST_SPEC.md
        # (e.g. prose strategy doc instead of derive_test_cases.md table).
        # Check whether FRs are actually defined — if yes, 0 cases is a real
        # failure (orchestrator skipped derive_test_cases.md skill), not a
        # vacuous pass.
        fr_defined = False
        # Authoritative source: SAD.md FR table (P2 deliverable).
        # Fallback: SPEC_TRACKING.md (P1 deliverable) — if SAD doesn't exist
        # but SPEC_TRACKING does, FRs are still declared.
        for probe_rel in (
            "02-architecture/SAD.md",
            "01-requirements/SPEC_TRACKING.md",
        ):
            probe_path = project / probe_rel
            if probe_path.exists():
                try:
                    _probe_text = probe_path.read_text(encoding="utf-8", errors="replace")
                    if re.search(r"\bFR-\d+\b", _probe_text):
                        fr_defined = True
                        break
                except OSError:
                    pass
        scope = f" for {fr_id}" if fr_id else ""
        if fr_defined:
            if verbose:
                print(
                    f"[spec-coverage] BLOCKED{scope}: TEST_SPEC.md has 0 parseable "
                    f"test cases but FRs are defined. The file is likely the wrong "
                    f"shape (prose strategy doc instead of derive_test_cases.md "
                    f"table). Re-run the derive_test_cases.md skill in Phase 2."
                )
            return (1, 0.0)
        if verbose:
            print(f"[spec-coverage] No test cases found in TEST_SPEC.md{scope} and no FRs defined — vacuous pass.")
        return (0, 100.0)

    # v2.6.1: Enforce P1 Naming Authority to prevent LLM hallucinations
    inventory_path = project / "TEST_INVENTORY.yaml"
    if inventory_path.exists() and not fr_id:
        try:
            import yaml
            inventory = yaml.safe_load(inventory_path.read_text())
        except ImportError:
            inventory = _parse_inventory_fallback(inventory_path.read_text())
            
        all_required = set(_flatten_test_names(inventory))
        spec_fns = {i["test_fn"] for i in items}
        missing_in_spec = all_required - spec_fns
        if missing_in_spec:
            if verbose:
                print(f"\n[BLOCKED] P1 Naming Authority Violation: {len(missing_in_spec)} test(s) from TEST_INVENTORY.yaml missing in TEST_SPEC.md.")
                for m in sorted(missing_in_spec)[:10]:
                    print(f"  - {m}")
                print("  Agent A may have hallucinated names. Re-run derive_test_cases.md.")
            return (1, 0.0)

    report = spec_coverage_report(project, fr_id=fr_id, _items=items)
    covered, missing, pct = report["covered"], report["missing"], report["pct"]

    if verbose:
        scope = f" [{fr_id}]" if fr_id else ""
        print(f"[spec-coverage]{scope} {len(covered)}/{len(items)} ({pct:.1f}%)")
        if missing:
            print(f"  Missing ({len(missing)}):")
            for item in missing[:20]:
                print(f"    - {item['test_fn']}  (type={item['type']}, deriv={item['derivation']})")
            if len(missing) > 20:
                print(f"    ... and {len(missing) - 20} more")

    if pct < threshold:
        if verbose:
            print(f"\n[BLOCKED] spec-coverage {pct:.1f}% < {threshold}% threshold")
        return (1, pct)
    return (0, pct)


def _parse_inventory_fallback(text: str) -> dict:
    """Minimal YAML-free parser for flat test name lists."""
    result: dict = {"fr_tests": {}, "cross_cutting": {}}
    current_section = "fr_tests"
    current_sub = "unit"
    for line in text.splitlines():
        line_s = line.strip()
        if line_s.startswith("cross_cutting"):
            current_section = "cross_cutting"
        elif line and line[0] == " " and (_m := re.match(r"^(\w+):\s*$", line_s)):
            # Indented YAML key (sub-section like unit:, integration:, security:).
            # Must check original `line` for indentation (line_s is stripped).
            # Must NOT catch list items like "      - test_name" which also have
            # leading spaces but do not match r"^(\w+):\s*$".
            current_sub = _m.group(1)
        elif line_s.startswith("- "):
            name = line_s[2:].strip()
            result.setdefault(current_section, {}).setdefault(current_sub, []).append(name)
    return result


# --- test-path resolution (moved verbatim from harness_cli.py, S4e) ---

def _collect_shared_test_files(project: Path, base: str,
                                existing: list[str]) -> None:
    """Append git-tracked conftest.py and helpers/**/*.py under *base*."""
    import subprocess as _sp
    try:
        r = _sp.run(
            ["git", "ls-files", f"{base}/conftest.py", f"{base}/helpers/"],
            capture_output=True, text=True, cwd=str(project),
        )
        for line in r.stdout.splitlines():
            if line.endswith(".py") and line not in existing:
                existing.append(line)
    except Exception as exc:
        print(f"[WARN] spec_coverage: git ls-files for {base}/conftest.py+helpers/ "
              f"failed, shared test files may be undercounted: {exc}", file=sys.stderr)


def _git_test_patterns(project: Path, num: str, num_raw: str) -> list[str]:
    """Return git-tracked test file path patterns, resolving symlinks.

    Bug #130 fix (2026-06-27): canonical harness layout places tests at
    ``03-development/tests/``. Without explicit patterns for it, `git log`
    returns empty and D1-RED blocks. We scan all valid test directories
    returned by `_get_test_directories`.
    """
    patterns = []
    # Always include 'tests/' by default to preserve historical behavior
    test_dirs_rel = ["tests"]

    for d in _get_test_directories(project):
        try:
            d_rel = str(d.resolve().relative_to(project.resolve()))
            if d_rel not in test_dirs_rel:
                test_dirs_rel.append(d_rel)
        except ValueError:
            continue

    for d_rel in test_dirs_rel:
        patterns.extend([
            f"{d_rel}/test_fr{num}.py",
            f"{d_rel}/test_fr{num_raw}.py",
        ])
        _collect_shared_test_files(project, d_rel, patterns)

    return patterns
