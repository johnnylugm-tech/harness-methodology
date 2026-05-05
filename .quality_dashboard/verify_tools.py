#!/usr/bin/env python3
"""
Verify tool availability for Harness Quality Framework.

Checks:
1. Core tools (always required)
2. Extended dimension tools (optional)
3. CRG (optional but recommended)

Usage:
  python3 scripts/verify_tools.py              # Check all
  python3 scripts/verify_tools.py --core       # Only core
  python3 scripts/verify_tools.py --extended   # Only extended
  python3 scripts/verify_tools.py --crg        # Only CRG
  python3 scripts/verify_tools.py --install-guide  # Print install commands
"""

import subprocess
import sys
import json

CORE_TOOLS = {
    "python3": ("python3 --version", "Python 3.8+"),
    "pip3": ("pip3 --version", "pip 20+"),
    "node": ("node --version", "Node.js 14+"),
    "npm": ("npm --version", "npm 6+"),
    "git": ("git --version", "git 2.0+"),
    "eslint": ("eslint --version", "JavaScript linting"),
    "pytest": ("pytest --version", "Python testing"),
    "coverage": ("coverage --version", "Python coverage"),
}

EXTENDED_TOOLS = {
    # HIGH priority
    "mutmut": ("mutmut --version", "pip3 install mutmut", "Python mutation testing"),
    "stryker": (
        "stryker --version",
        "npm install -g @stryker-mutator/core",
        "JS mutation testing",
    ),
    # MEDIUM priority
    "hypothesis": (
        "python3 -c 'import hypothesis; print(hypothesis.__version__)'",
        "pip3 install hypothesis",
        "Property testing",
    ),
    "fast-check": (
        "npm list -g fast-check",
        "npm install -g fast-check",
        "JS property testing",
    ),
    "atheris": (
        "python3 -c 'import atheris'",
        "pip3 install atheris",
        "Python fuzzing",
    ),
    "pa11y": ("pa11y --version", "npm install -g pa11y", "Accessibility testing"),
    # LOW priority
    "scancode": (
        "scancode --version",
        "pip3 install scancode-toolkit",
        "License scanning",
    ),
    "syft": ("syft --version", "brew install syft", "SBOM generation"),
    "grype": ("grype --version", "brew install grype", "Vulnerability scanning"),
    "cosign": (
        "cosign version",
        "brew install sigstore/sigstore/cosign",
        "Code signing",
    ),
}

CRG_TOOLS = {
    "code-review-graph": (
        "code-review-graph status",
        "pipx install code-review-graph",
        "Architecture analysis",
    ),
}


def check_command(cmd):
    """Return True if command exists and works."""
    try:
        result = subprocess.run(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def check_tools(tools_dict, category):
    """Check tool availability and return results."""
    results = {"category": category, "installed": 0, "missing": 0, "tools": {}}

    for tool, (check_cmd, *meta) in tools_dict.items():
        is_installed = check_command(check_cmd)
        install_cmd = meta[0] if len(meta) > 0 else None
        description = meta[1] if len(meta) > 1 else None

        results["tools"][tool] = {
            "installed": is_installed,
            "install_cmd": install_cmd,
            "description": description,
        }

        if is_installed:
            results["installed"] += 1
            status = "✓"
        else:
            results["missing"] += 1
            status = "✗"

        desc_str = f" ({description})" if description else ""
        print(f"  {status} {tool}{desc_str}")

    return results


def print_summary(results):
    """Print summary of tool verification."""
    print("\n" + "=" * 70)
    print("TOOL VERIFICATION SUMMARY")
    print("=" * 70)

    total_core = len(CORE_TOOLS)
    total_ext = len(EXTENDED_TOOLS)
    total_crg = len(CRG_TOOLS)

    print(f"\n✓ Core Tools:     {results['core']['installed']}/{total_core}")
    if results["core"]["missing"] > 0:
        print(f"  Missing: {results['core']['missing']} (required)")

    print(f"\n✓ Extended Tools: {results['extended']['installed']}/{total_ext}")
    if results["extended"]["missing"] > 0:
        print(f"  Missing: {results['extended']['missing']} (optional)")

    print(f"\n✓ CRG:            {results['crg']['installed']}/{total_crg}")
    if results["crg"]["missing"] > 0:
        print(f"  Missing: {results['crg']['missing']} (optional, recommended)")

    # Recommendations
    print("\n" + "-" * 70)
    print("NEXT STEPS")
    print("-" * 70)

    if results["core"]["missing"] > 0:
        print(f"\n❌ BLOCKING: {results['core']['missing']} core tools missing")
        print("   Install missing tools before running framework")
        for tool, info in results["core"]["tools"].items():
            if not info["installed"] and info["install_cmd"]:
                print(f"   → {info['install_cmd']}")
    else:
        print("\n✅ All core tools available")

    if results["extended"]["missing"] > 0:
        print(
            f"\n⚠️  {results['extended']['missing']} extended tools missing (optional)"
        )
        print("   Run: ./scripts/install_extended_tools.sh --high")
    else:
        print("\n✅ All extended tools available")

    if results["crg"]["missing"] > 0:
        print(
            "\n💡 CRG not installed (optional, recommended for -30-50% token savings)"
        )
        for tool, info in results["crg"]["tools"].items():
            if not info["installed"] and info["install_cmd"]:
                print(f"   → {info['install_cmd']}")
        print("   Then run: code-review-graph build --repo .")
    else:
        print("\n✅ CRG installed")

    print()


def print_install_guide(category=None):
    """Print installation commands organized by tool manager."""
    print("\nINSTALLATION GUIDE")

    tools_to_check = {}
    if category == "core":
        tools_to_check = CORE_TOOLS
    elif category == "extended":
        tools_to_check = EXTENDED_TOOLS
    elif category == "crg":
        tools_to_check = CRG_TOOLS
    else:
        tools_to_check = {**EXTENDED_TOOLS, **CRG_TOOLS}

    print("\n" + "=" * 70)
    print("INSTALLATION GUIDE")
    print("=" * 70)

    # Group by priority for extended
    priorities = {
        "HIGH (test quality foundation)": ["mutmut", "stryker"],
        "MEDIUM (edge cases + fuzzing)": [
            "hypothesis",
            "fast-check",
            "atheris",
            "pa11y",
        ],
        "LOW (governance + observability)": ["scancode", "syft", "grype", "cosign"],
        "CRG (architecture analysis)": ["code-review-graph"],
    }

    for priority, tool_list in priorities.items():
        print(f"\n{priority}")
        print("-" * 70)

        for tool in tool_list:
            if tool not in tools_to_check:
                continue

            info = tools_to_check[tool]
            install_cmd = info[1] if len(info) > 1 else None

            if install_cmd:
                print(f"  {tool}:")
                print(f"    {install_cmd}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Verify tool availability")
    parser.add_argument("--core", action="store_true", help="Check only core tools")
    parser.add_argument(
        "--extended", action="store_true", help="Check only extended tools"
    )
    parser.add_argument("--crg", action="store_true", help="Check only CRG")
    parser.add_argument(
        "--install-guide", action="store_true", help="Print installation commands"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    # Determine what to check
    check_all = not any([args.core, args.extended, args.crg, args.install_guide])

    if args.install_guide:
        if args.core:
            print("(Core tools come pre-installed on most systems)")
        elif args.extended:
            print_install_guide("extended")
        elif args.crg:
            print_install_guide("crg")
        else:
            print_install_guide()
        return 0

    results = {}

    if check_all or args.core:
        print("\n" + "=" * 70)
        print("CORE TOOLS (Required)")
        print("=" * 70)
        results["core"] = check_tools(CORE_TOOLS, "core")

    if check_all or args.extended:
        print("\n" + "=" * 70)
        print("EXTENDED TOOLS (Optional)")
        print("=" * 70)
        results["extended"] = check_tools(EXTENDED_TOOLS, "extended")

    if check_all or args.crg:
        print("\n" + "=" * 70)
        print("CODE REVIEW GRAPH (Optional, Recommended)")
        print("=" * 70)
        results["crg"] = check_tools(CRG_TOOLS, "crg")

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        if check_all:
            print_summary(results)

    # Exit with error if core tools missing
    if "core" in results and results["core"]["missing"] > 0:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
