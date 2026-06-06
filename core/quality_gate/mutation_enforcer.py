"""Mutation testing FSM enforcer."""
import re
import subprocess
import shutil
from pathlib import Path

def run_mutation_precheck(project: Path) -> tuple[bool, str]:
    """Run mutmut and enforce no surviving mutants.

    mutmut run exits 0 regardless of surviving mutants, so we always
    parse ``mutmut results`` output afterwards to detect survivors.
    """
    if not shutil.which("mutmut"):
        return True, ""

    src_dir = project / "03-development" / "src"
    if not src_dir.exists():
        return True, ""

    try:
        subprocess.run(
            ["mutmut", "run", "--paths-to-mutate=03-development/src"],
            cwd=str(project), capture_output=True, text=True,
        )
        res = subprocess.run(
            ["mutmut", "results"], cwd=str(project), capture_output=True, text=True,
        )
        # mutmut results prints "Survived 🙁 (N)" when N > 0 survivors exist
        m = re.search(r"Survived[^(]*\((\d+)\)", res.stdout)
        survivors = int(m.group(1)) if m else 0
        if survivors > 0:
            return False, (
                f"Mutation testing failed: {survivors} surviving mutant(s) found.\n\n"
                f"{res.stdout.strip()}"
            )
        return True, ""
    except Exception as e:
        return False, f"Error running mutmut: {e}"
