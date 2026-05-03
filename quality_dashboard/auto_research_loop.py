#!/usr/bin/env python3
"""
AutoResearch Loop - Automated Quality Improvement System
Based on Karpathy AutoResearch concept: iterative automatic optimization

Flow:
1. Evaluate current score
2. Identify lowest-scoring dimension
3. Agent generates improvement plan
4. Execute changes
5. Re-evaluate
6. If improved -> keep; otherwise -> revert
7. Repeat until target reached or max iterations hit
"""

import subprocess
import sys
import re
from dataclasses import dataclass
from typing import List, Dict, Optional
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from dashboard import QualityDashboard

# ============================================================================
# IMPROVEMENT STRATEGIES
# ============================================================================

@dataclass
class ImprovementAction:
    dimension: str
    file_path: str
    original_code: str
    new_code: str
    expected_improvement: float
    executed: bool = False
    reverted: bool = False
    improvement_achieved: Optional[float] = None

class ImprovementStrategy:
    """Base class for improvement strategies"""

    def __init__(self, dashboard: QualityDashboard):
        self.dashboard = dashboard
        self.actions: List[ImprovementAction] = []

    def analyze(self) -> List[ImprovementAction]:
        """Analyze and generate improvement actions"""
        raise NotImplementedError

    def execute(self, action: ImprovementAction) -> bool:
        """Execute an improvement action"""
        raise NotImplementedError

    def validate(self, action: ImprovementAction) -> float:
        """Validate improvement effect; returns actual score gain"""
        raise NotImplementedError

    def revert(self, action: ImprovementAction) -> bool:
        """Revert changes"""
        raise NotImplementedError


class CoverageImprovement(ImprovementStrategy):
    """D3: Test Coverage improvement strategy"""

    TARGET_COVERAGE = 70  # target coverage %

    def analyze(self) -> List[ImprovementAction]:
        actions = []
        project_path = self.dashboard.project_path

        # find all Python files
        py_files = list(project_path.rglob("*.py"))

        # find existing test files
        test_files = list((project_path / "tests").rglob("test_*.py")) if (project_path / "tests").exists() else []
        tested_modules = set()

        for test_file in test_files:
            content = test_file.read_text()
            # extract tested module names
            imports = re.findall(r'from\s+(\S+)\s+import', content)
            for imp in imports:
                if '.' in imp:
                    tested_modules.add(imp.split('.')[0])

        # find untested modules
        for py_file in py_files:
            if 'test' in py_file.name or py_file.name.startswith('__'):
                continue

            rel_path = py_file.relative_to(project_path)

            # check if this file is tested
            module_name = str(rel_path).replace('/', '.').replace('.py', '')
            if module_name in tested_modules:
                continue

            # read code, extract functions
            try:
                content = py_file.read_text()
                functions = re.findall(r'def\s+(\w+)\s*\([^)]*\)\s*(?:->\s*\w+\s*)?:', content)

                if functions and len(functions) > 0:
                    # generate test file name
                    test_name = f"test_{rel_path.stem}.py"
                    test_path = project_path / "tests" / test_name

                    # generated test template
                    test_code = self._generate_test_template(py_file, functions, rel_path)

                    actions.append(ImprovementAction(
                        dimension="D3_Coverage",
                        file_path=str(test_path),
                        original_code="",  # new file, no original
                        new_code=test_code,
                        expected_improvement=10.0  # expected improvement per test file: 10%
                    ))
            except Exception:
                continue

        return actions[:3]  # generate at most 3 test files

    def _generate_test_template(self, py_file: Path, functions: List[str], rel_path) -> str:
        module_name = str(rel_path).replace('/', '.').replace('.py', '')
        class_name = ''.join([w.capitalize() for w in rel_path.stem.split('_')]) + 'Tests'

        imports = ["import pytest", "import sys", f"sys.path.insert(0, '{py_file.parent.parent}')"]

        try:
            content = py_file.read_text()
            # extract imports
            existing_imports = re.findall(r'^import\s+\S+|^from\s+\S+\s+import\s+[^\n]+', content, re.MULTILINE)
            for imp in existing_imports[:5]:
                imports.append(imp)
            module_import = f"from {module_name} import *"
            if module_import not in imports:
                imports.append(module_import)
        except Exception:
            pass

        test_methods = []
        for func in functions:
            if not func.startswith('_'):
                test_methods.append(f"""
    def test_{func}(self):
        \"\"\"Test {func}\"\"\"
        pass""")

        return f'''"""Auto-generated tests for {rel_path}"""

{chr(10).join(imports)}


class {class_name}:
{chr(10).join(test_methods)}
'''

    def execute(self, action: ImprovementAction) -> bool:
        try:
            test_path = Path(action.file_path)
            test_path.parent.mkdir(parents=True, exist_ok=True)
            test_path.write_text(action.new_code)
            action.executed = True
            return True
        except Exception as e:
            print(f"Execute failed: {e}")
            return False

    def validate(self, action: ImprovementAction) -> float:
        """Validate test coverage improvement"""
        # re-run evaluation
        result = self.dashboard.run_evaluation()
        coverage_dim = result.dimensions.get("D3_Coverage")
        if coverage_dim:
            return coverage_dim.score
        return 0.0

    def revert(self, action: ImprovementAction) -> bool:
        try:
            if Path(action.file_path).exists():
                Path(action.file_path).unlink()
            action.reverted = True
            return True
        except Exception:
            return False


class LintingImprovement(ImprovementStrategy):
    """D1: Linting improvement strategy"""

    def analyze(self) -> List[ImprovementAction]:
        actions = []

        # run ruff check to get errors
        result = subprocess.run(
            ["ruff", "check", str(self.dashboard.project_path), "--ignore=D100,E501,F401"],
            capture_output=True, text=True
        )

        # parse errors
        errors = []
        for line in result.stdout.split('\n'):
            if line.startswith('F'):
                parts = line.split(':')
                if len(parts) >= 4:
                    file_path = ':'.join(parts[:-2])
                    error_code = parts[-2]
                    errors.append((file_path, error_code))

        # only handle simple errors like F401 (unused import)
        for file_path, error_code in errors[:5]:
            if error_code == 'F401':
                actions.append(ImprovementAction(
                    dimension="D1_Linting",
                    file_path=file_path,
                    original_code=Path(file_path).read_text(),
                    new_code="",  # agent handles this
                    expected_improvement=2.0
                ))

        return actions

    def execute(self, action: ImprovementAction) -> bool:
        # use ruff --fix for auto-fix
        result = subprocess.run(
            ["ruff", "check", str(self.dashboard.project_path), "--fix", "--ignore=D100,E501"],
            capture_output=True, text=True
        )
        action.executed = True
        return result.returncode == 0

    def validate(self, action: ImprovementAction) -> float:
        result = self.dashboard.run_evaluation()
        linting_dim = result.dimensions.get("D1_Linting")
        return linting_dim.score if linting_dim else 0.0

    def revert(self, action: ImprovementAction) -> bool:
        try:
            Path(action.file_path).write_text(action.original_code)
            action.reverted = True
            return True
        except Exception:
            return False


class ErrorHandlingImprovement(ImprovementStrategy):
    """D8: Error Handling improvement strategy"""

    def analyze(self) -> List[ImprovementAction]:
        actions = []
        project_path = self.dashboard.project_path

        for py_file in project_path.rglob("*.py"):
            if 'test' in py_file.name:
                continue

            content = py_file.read_text()

            # find empty or incomplete try-except blocks
            pattern = r'try:\s*.*?\s*except\s+\S+:\s*pass'
            matches = re.finditer(pattern, content, re.DOTALL)

            for match in matches:
                empty_blocks = match.group()
                # suggest adding meaningful error handling
                new_code = content.replace(empty_blocks, '''try:
        pass
    except Exception as e:
        # TODO: Handle specific exception
        print(f"Error occurred: {e}")
        raise''')

                if new_code != content:
                    actions.append(ImprovementAction(
                        dimension="D8_ErrorHandling",
                        file_path=str(py_file),
                        original_code=content,
                        new_code=new_code,
                        expected_improvement=5.0
                    ))
                    break  # only one change per file

            if len(actions) >= 3:
                break

        return actions

    def execute(self, action: ImprovementAction) -> bool:
        try:
            Path(action.file_path).write_text(action.new_code)
            action.executed = True
            return True
        except Exception:
            return False

    def validate(self, action: ImprovementAction) -> float:
        result = self.dashboard.run_evaluation()
        eh_dim = result.dimensions.get("D8_ErrorHandling")
        return eh_dim.score if eh_dim else 0.0

    def revert(self, action: ImprovementAction) -> bool:
        try:
            Path(action.file_path).write_text(action.original_code)
            action.reverted = True
            return True
        except Exception:
            return False


# ============================================================================
# AUTO-RESEARCH LOOP
# ============================================================================

class AutoResearchLoop:
    """
    AutoResearch Loop - Automated Quality Improvement Iterator

    Based on Karpathy AutoResearch concept:
    - Define the criterion for "better"
    - Agent automatically executes improvements
    - Iterate until target reached or max iterations hit
    """

    MAX_ITERATIONS = 10
    TARGET_SCORE = 85.0  # target total score

    def __init__(self, dashboard: QualityDashboard):
        self.dashboard = dashboard
        self.strategies = [
            CoverageImprovement(dashboard),
            LintingImprovement(dashboard),
            ErrorHandlingImprovement(dashboard),
        ]
        self.iteration_log: List[Dict] = []

    def run(self, max_iterations: int = 5) -> Dict:
        """Run the AutoResearch Loop"""
        print("\n" + "=" * 60)
        print("AutoResearch Loop Started")
        print("=" * 60)

        for iteration in range(1, max_iterations + 1):
            print(f"\n{'─' * 60}")
            print(f"Iteration {iteration}/{max_iterations}")
            print(f"{'─' * 60}")

            # Step 1: evaluate current score
            result = self.dashboard.run_evaluation()

            print(f"\nCurrent score: {result.total_score:.1f}%")
            print(f"Technical debt: {result.technical_debt:.1f}%")

            # check if target reached
            if result.total_score >= self.TARGET_SCORE:
                print(f"\nTarget score {self.TARGET_SCORE}% reached!")
                break

            # Step 2: identify lowest-scoring dimension
            low_dims = [(name, dim) for name, dim in result.dimensions.items() if dim.score < 70]
            low_dims.sort(key=lambda x: x[1].score)

            if not low_dims:
                print("All dimensions meet target, no improvement needed")
                break

            print("\nLowest scoring dimensions:")
            for name, dim in low_dims[:3]:
                print(f"   {dim.name}: {dim.score:.0f}% (target: >=70%)")

            # Step 3: select the matching strategy
            strategy = self._select_strategy(low_dims[0][0])
            if not strategy:
                print("No matching improvement strategy found")
                continue

            print(f"\nUsing strategy: {strategy.__class__.__name__}")

            # Step 4: analyze and generate actions
            actions = strategy.analyze()
            if not actions:
                print("No improvable items found in analysis phase")
                continue

            print(f"Found {len(actions)} potential improvements")

            # Step 5: execute actions
            improvements_made = []
            for action in actions[:2]:  # execute at most 2 actions per iteration
                print(f"\n   Executing: {action.file_path}")

                # save original score
                original_score = result.total_score

                # execute
                if strategy.execute(action):
                    # validate
                    new_score = strategy.validate(action)
                    action.improvement_achieved = new_score - original_score

                    if new_score > original_score:
                        print(f"   Improved: {original_score:.1f}% -> {new_score:.1f}% (+{new_score-original_score:.1f}%)")
                        improvements_made.append(action)
                    else:
                        print("   No improvement, reverting...")
                        strategy.revert(action)
                        # re-validate
                        strategy.validate(action)
                else:
                    print("   Execution failed")

            # Step 6: log iteration result
            self.iteration_log.append({
                "iteration": iteration,
                "timestamp": datetime.now().isoformat(),
                "total_score": result.total_score,
                "improvements": [a.__dict__ for a in improvements_made],
                "low_dimensions": [(n, d.score) for n, d in low_dims[:3]]
            })

            # stop if no improvement in 2 consecutive iterations
            if len(self.iteration_log) >= 2:
                last_improvement = self.iteration_log[-1]["improvements"]
                prev_improvement = self.iteration_log[-2]["improvements"]
                if not last_improvement and not prev_improvement:
                    print("\nNo improvement in 2 consecutive iterations, stopping")
                    break

        # final report
        return self._generate_final_report()

    def _select_strategy(self, dimension_name: str) -> Optional[ImprovementStrategy]:
        """Select the matching strategy by dimension name"""
        if "Coverage" in dimension_name:
            return CoverageImprovement(self.dashboard)
        elif "Linting" in dimension_name:
            return LintingImprovement(self.dashboard)
        elif "ErrorHandling" in dimension_name:
            return ErrorHandlingImprovement(self.dashboard)
        return None

    def _generate_final_report(self) -> Dict:
        """Generate final report"""
        history = self.dashboard.load_history()
        first = history[0] if history else None
        last = history[-1] if history else None

        report = {
            "total_iterations": len(self.iteration_log),
            "initial_score": first.total_score if first else 0,
            "final_score": last.total_score if last else 0,
            "improvement": (last.total_score - first.total_score) if first and last else 0,
            "target_reached": last.total_score >= self.TARGET_SCORE if last else False,
            "iterations": self.iteration_log
        }

        print("\n" + "=" * 60)
        print("AutoResearch Loop Final Report")
        print("=" * 60)
        print(f"Total iterations: {report['total_iterations']}")
        print(f"Initial score:    {report['initial_score']:.1f}%")
        print(f"Final score:      {report['final_score']:.1f}%")
        print(f"Improvement:      {'+' if report['improvement'] >= 0 else ''}{report['improvement']:.1f}%")
        print(f"Target reached:   {'Yes' if report['target_reached'] else 'No'}")

        return report


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AutoResearch Loop")
    parser.add_argument("--project", default="/path/to/project")
    parser.add_argument("--iterations", type=int, default=3, help="Maximum number of iterations")
    args = parser.parse_args()

    dashboard = QualityDashboard(args.project)
    loop = AutoResearchLoop(dashboard)
    report = loop.run(max_iterations=args.iterations)

    print("\n" + "=" * 60)
    print("HTML Dashboard")
    print("=" * 60)
    html_file = dashboard.generate_html_dashboard()
    print(html_file)
