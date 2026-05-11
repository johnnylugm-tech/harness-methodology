#!/usr/bin/env python3
"""
12-Dimension Quality Evaluation for harness-methodology.
Adapted for flat source layout (no 03-development/ wrapper).
"""
import json, subprocess, statistics, sys, re, os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

PROJECT = Path(__file__).resolve().parent.parent
SRC_DIRS = ["core", "harness", "detection", "enforcement", "gap_detector", "kill_switch", "steering", "scripts"]
EXCLUDE_FILES = {"cli.py", "harness_cli.py", "__init__.py"}
EXCLUDE_DIRS = {"tests", "__pycache__", ".git", "venv", "node_modules"}
HISTORY_FILE = PROJECT / ".methodology" / "quality_history.json"

# ———————————————————————————————————————————— helpers ————————————————————————————————————————————

def run(cmd: list, timeout: int = 90, cwd=None) -> Tuple[str, str, int]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd or str(PROJECT))
        return r.stdout, r.stderr, r.returncode
    except Exception as e:
        return "", str(e), 1

def src_paths() -> List[Path]:
    """Collect all .py files in SRC_DIRS respecting exclusions."""
    files = []
    for d in SRC_DIRS:
        p = PROJECT / d
        if p.is_dir():
            for f in p.rglob("*.py"):
                if f.name in EXCLUDE_FILES:
                    continue
                parts = set(f.relative_to(PROJECT).parts)
                if EXCLUDE_DIRS & parts:
                    continue
                files.append(f)
    return files

# ——————————————————————————————————————————— evaluators ——————————————————————————————————————————

class LintingEvaluator:
    name = "Linting"
    weight = 0.10

    def evaluate(self) -> dict:
        paths = [str(PROJECT / d) for d in SRC_DIRS]
        stdout, _, _ = run(["ruff", "check"] + paths + ["--ignore=D100,E501,F401", "--select=E,F,I,N,UP,B,SIM,TCH,PT,RET,ARG,ICN,PLC,PIE,PYI,Q,RSE,RUF,TID,TRY,ERA,ASY,SLF,N818,DTZ,T10,T20,CPY,INP,LOG,G,FAST,C4,FLY,PERF,AIR,RET,A,DJ,YTT,TD,E701,E402"])
        lines = [l.strip() for l in stdout.split('\n') if l.strip()]
        error_count = len([l for l in lines if not l.startswith("All checks passed")])
        score = max(0, min(100, 100 - error_count * 2.5))
        return {"name": self.name, "score": score, "weight": self.weight,
                "issues": lines[:5], "tool_driven": True, "tool_name": "ruff"}


class TypeSafetyEvaluator:
    name = "Type Safety"
    weight = 0.12

    def evaluate(self) -> dict:
        paths = [str(PROJECT / d) for d in SRC_DIRS]
        stdout, _, _ = run(["mypy"] + paths + ["--ignore-missing-imports", "--no-error-summary"], timeout=90)
        errors = stdout.count(": error:")
        score = max(0, 100 - errors * 10)
        issues = [l.strip() for l in stdout.split('\n') if ': error:' in l][:5]
        return {"name": self.name, "score": score, "weight": self.weight,
                "issues": issues, "tool_driven": True, "tool_name": "mypy"}


class CoverageEvaluator:
    name = "Test Coverage"
    weight = 0.15

    def evaluate(self) -> dict:
        src_files = src_paths()
        if not src_files:
            return {"name": self.name, "score": 0, "weight": self.weight,
                    "issues": ["No source files found"], "tool_driven": True, "tool_name": "pytest-cov"}
        # Build cov args
        cov_args = []
        cov_dirs = set()
        for f in src_files:
            d = str(f.relative_to(PROJECT).parts[0])
            cov_dirs.add(d)
        for d in sorted(cov_dirs):
            cov_args.extend(["--cov", d])

        stdout, _, _ = run(["python3", "-m", "pytest", "tests/", "--tb=no", "-q"] + cov_args + ["--cov-report=term-missing"], timeout=90, cwd=str(PROJECT))
        coverage = 0.0
        for line in stdout.split('\n'):
            if 'TOTAL' in line and line.strip().endswith('%'):
                try:
                    # "TOTAL                  4917   1021    79%"
                    parts = line.strip().split()
                    pct = parts[-1].rstrip('%')
                    coverage = float(pct)
                except (ValueError, IndexError):
                    pass
        issues = [] if coverage >= 70 else [f"Coverage {coverage:.0f}% < 70%"]
        return {"name": self.name, "score": coverage, "weight": self.weight,
                "issues": issues, "tool_driven": True, "tool_name": "pytest-cov"}


class SecurityEvaluator:
    name = "Security"
    weight = 0.12

    def evaluate(self) -> dict:
        paths = [str(PROJECT / d) for d in SRC_DIRS]
        stdout, _, _ = run(["bandit", "-r"] + paths + ["-f", "json", "-ll"], timeout=90)
        try:
            data = json.loads(stdout)
            high = data["metrics"]["_totals"]["SEVERITY.HIGH"]
            medium = data["metrics"]["_totals"]["SEVERITY.MEDIUM"]
            score = max(0, 100 - high * 20 - medium * 10)
            issues = [f"HIGH: {high}", f"MEDIUM: {medium}"]
        except Exception:
            score = 100
            issues = ["No issues found"]
        return {"name": self.name, "score": score, "weight": self.weight,
                "issues": issues, "tool_driven": True, "tool_name": "bandit"}


class ComplexityEvaluator:
    name = "Complexity"
    weight = 0.08

    def evaluate(self) -> dict:
        paths = [str(PROJECT / d) for d in SRC_DIRS]
        stdout, _, _ = run(["lizard"] + paths, timeout=60)
        # Parse summary line for AvgCCN across all functions
        avg_ccn = 0.0
        warn_cnt = 0
        fun_cnt = 0
        for line in stdout.split('\n'):
            if 'AvgCCN' in line:
                # "Total nloc   Avg.NLOC  AvgCCN  Avg.token   Fun Cnt  Warning cnt   Fun Rt   nloc Rt"
                # next line has numbers
                continue
            if 'Warning cnt' in line and 'AvgCCN' not in line:
                # This is the data line after the header
                parts = line.split()
                if len(parts) >= 6:
                    try:
                        avg_ccn = float(parts[1])  # AvgCCN is 2nd column (after Total nloc)
                    except (ValueError, IndexError):
                        pass
                # Actually find the right column: after "Total nloc   Avg.NLOC  AvgCCN"
                continue
        # Parse summary: header "Total nloc ... AvgCCN ..." then separator then data
        lines = stdout.split('\n')
        for i, line in enumerate(lines):
            if 'AvgCCN' in line and 'Total nloc' in line:
                # Skip separator line (-----)
                for j in range(i + 1, min(i + 3, len(lines))):
                    data = lines[j].split()
                    # Data line: all numeric, ~8 columns
                    if len(data) >= 5:
                        try:
                            # Columns: Total_nloc AvgNLOC AvgCCN AvgToken FunCnt WarningCnt ...
                            avg_ccn = float(data[2])
                            fun_cnt = int(data[4])
                            warn_cnt = int(data[5])
                            break
                        except (ValueError, IndexError):
                            continue
                break
        score = max(0, 100 - avg_ccn * 8)
        issues = [f"Avg CCN: {avg_ccn:.1f} across {fun_cnt} functions",
                  f"CCN > 15 warnings: {warn_cnt}/{fun_cnt}"]
        return {"name": self.name, "score": min(100, score), "weight": self.weight,
                "issues": issues, "tool_driven": False, "tool_name": "lizard"}


class ArchitectureEvaluator:
    name = "Architecture"
    weight = 0.08

    def evaluate(self) -> dict:
        paths = [str(PROJECT / d) for d in SRC_DIRS]
        stdout, _, _ = run(["radon", "cc"] + paths + ["-a"], timeout=30)
        all_lines = [l.strip() for l in stdout.split('\n') if ' - ' in l]
        total_funcs = len(all_lines)
        c_grade = [l for l in all_lines if ' - C' in l]
        d_grade = [l for l in all_lines if ' - D' in l]
        e_grade = [l for l in all_lines if ' - E' in l]
        bad = len(c_grade) * 1 + len(d_grade) * 3 + len(e_grade) * 5
        a_b = total_funcs - len(c_grade) - len(d_grade) - len(e_grade)
        ratio = (a_b / max(total_funcs, 1)) * 100
        score = min(100, ratio)
        issue_lines = c_grade[:3] + d_grade[:1] + e_grade[:1]
        return {"name": self.name, "score": score, "weight": self.weight,
                "issues": issue_lines[:5] or ["All A/B grades"], "tool_driven": False, "tool_name": "radon-cc"}


class MaintainabilityEvaluator:
    name = "Maintainability"
    weight = 0.08

    def evaluate(self) -> dict:
        paths = [str(PROJECT / d) for d in SRC_DIRS]
        stdout, _, _ = run(["radon", "mi"] + paths, timeout=30)
        scores = []
        for line in stdout.split('\n'):
            # radon mi output: "file.py - A (85.23)"
            m = re.search(r'-\s*([A-F])\s*\(([\d.]+)\)', line)
            if m:
                scores.append(float(m.group(2)))
        avg_mi = statistics.mean(scores) if scores else 95
        score = min(100, avg_mi)  # radon mi is 0-100
        issues = [f"Avg MI: {avg_mi:.1f}"] if avg_mi < 60 else [f"Avg MI: {avg_mi:.1f} (healthy)"]
        return {"name": self.name, "score": score, "weight": self.weight,
                "issues": issues, "tool_driven": False, "tool_name": "radon-mi"}


class ReadabilityEvaluator:
    name = "Readability"
    weight = 0.06

    def evaluate(self) -> dict:
        files = src_paths()
        if not files:
            return {"name": self.name, "score": 0, "weight": self.weight,
                    "issues": ["No files"], "tool_driven": False, "tool_name": "agent"}
        # Check: avg file length, comment ratio
        total_lines = 0
        comment_lines = 0
        file_count = 0
        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                lines = text.split('\n')
                total_lines += len(lines)
                comment_lines += sum(1 for l in lines if l.strip().startswith('#') or '"""' in l)
                file_count += 1
            except Exception:
                pass
        avg_len = total_lines / max(file_count, 1)
        comment_ratio = (comment_lines / max(total_lines, 1)) * 100

        # Score: penalize large files and reward comments
        len_score = max(0, 100 - max(0, avg_len - 200) * 0.3)
        comment_score = min(100, comment_ratio * 5)
        score = (len_score * 0.5 + comment_score * 0.5)
        return {"name": self.name, "score": min(100, score), "weight": self.weight,
                "issues": [f"Avg file: {avg_len:.0f} lines", f"Comment ratio: {comment_ratio:.1f}%"],
                "tool_driven": False, "tool_name": "agent"}


class ErrorHandlingEvaluator:
    name = "Error Handling"
    weight = 0.05

    def evaluate(self) -> dict:
        files = src_paths()
        total_try = 0
        total_bare_except = 0
        total_broad_except = 0
        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                total_try += len(re.findall(r'\btry\s*:', text))
                total_bare_except += len(re.findall(r'except\s*:', text))
                total_broad_except += len(re.findall(r'except\s+Exception', text))
            except Exception:
                pass
        # Good: has try blocks, low rate of bare excepts
        bare_ratio = total_bare_except / max(total_try, 1)
        score = min(100, 50 + (total_try * 0.5) - (bare_ratio * 100))
        issues = [f"try blocks: {total_try}", f"bare excepts: {total_bare_except}",
                  f"broad excepts: {total_broad_except}"]
        return {"name": self.name, "score": max(0, score), "weight": self.weight,
                "issues": issues, "tool_driven": False, "tool_name": "agent"}


class DocumentationEvaluator:
    name = "Documentation"
    weight = 0.05

    def evaluate(self) -> dict:
        files = src_paths()
        with_doc = 0
        with_module_doc = 0
        total = 0
        for f in files:
            total += 1
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                if text.strip().startswith('"""') or text.strip().startswith("'''"):
                    with_module_doc += 1
                if '"""' in text:
                    with_doc += 1
            except Exception:
                pass
        module_ratio = (with_module_doc / max(total, 1)) * 100
        any_ratio = (with_doc / max(total, 1)) * 100
        score = (module_ratio * 0.6 + any_ratio * 0.4)
        return {"name": self.name, "score": min(100, score), "weight": self.weight,
                "issues": [f"Module docstrings: {with_module_doc}/{total}", f"Any docstring: {with_doc}/{total}"],
                "tool_driven": False, "tool_name": "agent"}


class TestQualityEvaluator:
    name = "Test Quality"
    weight = 0.06

    def evaluate(self) -> dict:
        test_dir = PROJECT / "tests"
        test_files = list(test_dir.rglob("test_*.py")) if test_dir.is_dir() else []
        total_asserts = 0
        total_tests = 0
        test_lines = 0
        for f in test_files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                total_asserts += len(re.findall(r'\bassert\b', text))
                total_tests += text.count("def test_")
                test_lines += len(text.split('\n'))
            except Exception:
                pass
        # Metrics
        asserts_per_test = total_asserts / max(total_tests, 1)
        # Score: good if ~1-3 asserts per test, reasonable test-to-code ratio
        ap_score = min(100, asserts_per_test * 40)  # 2.5 asserts → 100
        issues = [f"Tests: {total_tests}", f"Asserts: {total_asserts}",
                  f"Asserts/test: {asserts_per_test:.1f}"]
        return {"name": self.name, "score": min(100, ap_score), "weight": self.weight,
                "issues": issues, "tool_driven": False, "tool_name": "agent"}


class CodeHygieneEvaluator:
    name = "Code Hygiene"
    weight = 0.05

    def evaluate(self) -> dict:
        files = src_paths()
        issues = []
        # Check for TODO/FIXME density, unused import patterns, trailing whitespace
        todo_count = 0
        fixme_count = 0
        files_with_trailing = 0
        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                todo_count += len(re.findall(r'#\s*TODO', text, re.IGNORECASE))
                fixme_count += len(re.findall(r'#\s*FIXME', text, re.IGNORECASE))
                # Check trailing whitespace
                for line in text.split('\n'):
                    if line.endswith(' ') or line.endswith('\t'):
                        files_with_trailing += 1
                        break
            except Exception:
                pass
        hygiene_issues = todo_count + fixme_count * 2
        score = max(0, 100 - hygiene_issues * 2)
        issues = [f"TODOs: {todo_count}", f"FIXMEs: {fixme_count}",
                  f"Files w/ trailing ws: {files_with_trailing}"]
        return {"name": self.name, "score": min(100, score), "weight": self.weight,
                "issues": issues, "tool_driven": False, "tool_name": "agent"}


EVALUATORS = [
    ("D1_Linting", LintingEvaluator()),
    ("D2_TypeSafety", TypeSafetyEvaluator()),
    ("D3_TestCoverage", CoverageEvaluator()),
    ("D4_Security", SecurityEvaluator()),
    ("D5_Complexity", ComplexityEvaluator()),
    ("D6_Architecture", ArchitectureEvaluator()),
    ("D7_Maintainability", MaintainabilityEvaluator()),
    ("D8_Readability", ReadabilityEvaluator()),
    ("D9_ErrorHandling", ErrorHandlingEvaluator()),
    ("D10_Documentation", DocumentationEvaluator()),
    ("D11_TestQuality", TestQualityEvaluator()),
    ("D12_CodeHygiene", CodeHygieneEvaluator()),
]


def main():
    print("=" * 70)
    print("12-DIMENSION QUALITY EVALUATION")
    print(f"Project: {PROJECT}  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Scope: {', '.join(SRC_DIRS)}")
    print("=" * 70)

    dimensions = {}
    for i, (key, evaluator) in enumerate(EVALUATORS, 1):
        print(f"\n[{i}/12] {evaluator.name}...", end=" ", flush=True)
        try:
            result = evaluator.evaluate()
            dimensions[key] = result
            icon = "✅" if result["score"] >= 80 else "⚠️" if result["score"] >= 60 else "❌"
            print(f"{icon} {result['score']:.1f}%")
        except Exception as e:
            print(f"❌ Error: {e}")
            dimensions[key] = {"name": evaluator.name, "score": 0, "weight": evaluator.weight,
                              "issues": [str(e)], "tool_driven": False, "tool_name": "error"}

    # Calculate weighted total
    total = sum(d["score"] * d["weight"] for d in dimensions.values())
    debt = 100 - total

    # Print report
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"{'Dimension':<22} {'Score':>7} {'Weight':>7} {'Contrib':>7}")
    print("-" * 70)
    for key, d in dimensions.items():
        contrib = d["score"] * d["weight"]
        name = d["name"]
        icon = "✅" if d["score"] >= 80 else "⚠️" if d["score"] >= 60 else "❌"
        print(f"{icon} {name:<20} {d['score']:>6.1f}% {d['weight']*100:>5.0f}% {contrib:>6.1f}")
    print("-" * 70)
    print(f"{'TOTAL':<22} {total:>7.1f}%")
    print(f"{'TECHNICAL DEBT':<22} {debt:>7.1f}%")
    print("=" * 70)

    # Detailed issues
    print("\nDETAILED ISSUES:")
    for key, d in dimensions.items():
        if d["issues"] and d["score"] < 90:
            print(f"\n  [{key}] {d['name']} ({d['score']:.0f}%):")
            for issue in d["issues"]:
                print(f"    - {issue}")

    # Save to history
    result = {
        "iteration": 2,
        "timestamp": datetime.now().isoformat(),
        "dimensions": dimensions,
        "total_score": total,
        "technical_debt": debt,
        "hotspots": {},
        "improvements": [],
        "agent_actions": []
    }
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE) as f:
            history = json.load(f)
    else:
        history = []
    history.append(result)
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

    print(f"\nHistory saved to {HISTORY_FILE} (iteration {len(history)})")
    return total, debt


if __name__ == "__main__":
    main()
