#!/usr/bin/env python3
"""Verify every guard test in tests/REGRESSION_GUARDS.yaml still exists.

Each registry entry pins a test that guards a previously fixed bug. This
script collects the referenced test files with pytest and fails (exit 1) if
any registered node id no longer exists — deleting a guard test without
updating the registry in the same commit is exactly the failure mode that
let the sqlite-swallow fix (ff98cc7) regress undetected.

Fail-closed by design: an unreadable registry, an empty registry, or a test
file that no longer collects (import error) all exit 1.

Usage:
    python scripts/verify_regression_guards.py \
        [--registry tests/REGRESSION_GUARDS.yaml] [--repo-root .]
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path,
                        default=Path("tests/REGRESSION_GUARDS.yaml"))
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    entries = load_registry(args.registry)

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
