#!/usr/bin/env python3
"""Verify every guard test in tests/REGRESSION_GUARDS.yaml still exists.

Each registry entry pins a test that guards a previously fixed bug. This
script collects the referenced test files with pytest and fails (exit 1) if
any registered node id no longer exists — deleting a guard test without
updating the registry in the same commit is exactly the failure mode that
let the sqlite-swallow fix (ff98cc7) regress undetected.

Fail-closed by design: an unreadable registry, an empty registry, or a test
file that no longer collects (import error) all exit 1.

The registry was checked in ONE direction only until Round 33 站5: "is every
registered guard still present?". Nothing asked "did this fix bring its guard
with it?", so the registry grew only when someone remembered. Measured on
8637c6a..4bdc0fb — three bug-fix commits, twelve-plus new test functions
across three new files, registry count unmoved at 239. The preflight and
postflight registries both carry a completeness meta-test for exactly this;
this one did not.

`--added-tests` supplies the other direction. It takes the test files a push
is ADDING (the only place that signal exists) and reports any that no registry
entry mentions. It is deliberately not a tree scan: 6600+ tests exist, almost
none of them are guards, and demanding an entry for each would be a machine
that cries wolf.

Usage:
    python scripts/verify_regression_guards.py \
        [--registry tests/REGRESSION_GUARDS.yaml] [--repo-root .]
    python scripts/verify_regression_guards.py --added-tests tests/test_a.py ...
"""

import argparse
import subprocess
import sys
from pathlib import Path

import yaml


def load_registry(registry_path: Path) -> list[dict]:
    if not registry_path.is_file():
        print(f"[regression-guards] FAIL: registry not found: {registry_path}")
        raise SystemExit(1)
    try:
        entries = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print(f"[regression-guards] FAIL: registry is not valid YAML: {exc}")
        raise SystemExit(1)
    if not isinstance(entries, list) or not entries:
        # An emptied registry must not pass vacuously — emptying it is how a
        # guard deletion would dodge this check while looking like cleanup.
        print("[regression-guards] FAIL: registry is empty or not a list — "
              "refusing to pass vacuously")
        raise SystemExit(1)
    bad = [e for e in entries
           if not isinstance(e, dict) or "test" not in e or "bug" not in e]
    if bad:
        print(f"[regression-guards] FAIL: {len(bad)} entr(ies) missing "
              f"required 'test'/'bug' fields: {bad}")
        raise SystemExit(1)
    return entries


def collect_node_ids(files: list[str], repo_root: Path) -> set[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-o", "addopts=", "-p", "no:cacheprovider", *files],
        capture_output=True, text=True, cwd=repo_root, timeout=300,
    )
    collected = {
        line.strip() for line in result.stdout.splitlines()
        if "::" in line and not line.startswith(("=", "!", " "))
    }
    if result.returncode != 0:
        # Collection errors (e.g. an import-broken guard file) mean we cannot
        # prove the guards exist — fail closed and show pytest's diagnosis.
        print("[regression-guards] FAIL: pytest collection errored "
              f"(exit {result.returncode}):")
        print(result.stdout[-2000:])
        print(result.stderr[-2000:])
        raise SystemExit(1)
    return collected


def unregistered_test_files(entries: list[dict], added: list[str]) -> list[str]:
    """Which of *added* are test files that no registry entry mentions.

    Keys on `tests/test_*.py` specifically, not on every added path: a new
    conftest, fixture directory or helper module is not a guard, and blocking
    those would make the check noise. Order is preserved so the message lists
    files as the author sees them.
    """
    registered = {e["test"].split("::", 1)[0] for e in entries if e.get("test")}
    out: list[str] = []
    for path in added:
        norm = str(path).replace("\\", "/")
        name = norm.rsplit("/", 1)[-1]
        if not (norm.startswith("tests/") and name.startswith("test_")
                and name.endswith(".py")):
            continue
        if norm not in registered:
            out.append(norm)
    return out


def _report_unregistered(entries: list[dict], added: list[str]) -> int:
    missing = unregistered_test_files(entries, added)
    if not missing:
        print(f"[regression-guards] OK: {len(added)} added path(s), "
              "every new test file has a registry entry")
        return 0
    print("[regression-guards] FAIL: new test file(s) with no entry in "
          "tests/REGRESSION_GUARDS.yaml:")
    for path in missing:
        print(f"    {path}")
    print(
        "\n  A test that pins a fixed bug belongs in the registry, with the\n"
        "  bug it guards and the commit that fixed it — that is the only\n"
        "  record of WHY it may not be deleted.\n"
        "  If this file is not a guard (pure refactor, fixtures, a harness\n"
        "  for other tests), say so in the commit message with the marker\n"
        "  [no-guard] and re-run the push."
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path,
                        default=Path("tests/REGRESSION_GUARDS.yaml"))
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument(
        "--added-tests", nargs="*", default=None, metavar="PATH",
        help="repo-relative paths this push adds; reports any test file with "
             "no registry entry instead of running the presence check",
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    entries = load_registry(args.registry)

    if args.added_tests is not None:
        return _report_unregistered(entries, args.added_tests)

    files = sorted({e["test"].split("::", 1)[0] for e in entries})
    missing_files = [f for f in files if not (repo_root / f).is_file()]
    if missing_files:
        for entry in entries:
            if entry["test"].split("::", 1)[0] in missing_files:
                print(f"[regression-guards] MISSING FILE: {entry['test']}")
                print(f"    guards: {entry['bug']}")
    collected = collect_node_ids(
        [f for f in files if f not in missing_files], repo_root
    ) if len(missing_files) < len(files) else set()

    missing = []
    for entry in entries:
        node_id = entry["test"]
        if node_id.split("::", 1)[0] in missing_files:
            missing.append(entry)
            continue
        # Parametrized guards are registered by bare id; any [param] variant counts.
        if node_id not in collected and not any(
            c.startswith(node_id + "[") for c in collected
        ):
            missing.append(entry)
            print(f"[regression-guards] MISSING: {node_id}")
            print(f"    guards: {entry['bug']}")
            print(f"    fixed_in: {entry.get('fixed_in', '?')}")

    if missing:
        print(f"\n[regression-guards] FAIL: {len(missing)}/{len(entries)} "
              "guard test(s) missing. A guard may only be removed together "
              "with its registry entry, in the same reviewed commit.")
        return 1

    print(f"[regression-guards] OK: all {len(entries)} guard tests present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
