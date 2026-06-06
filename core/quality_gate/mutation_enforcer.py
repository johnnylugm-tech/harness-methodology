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
        r = subprocess.run(
            ["mutmut", "run", "--paths-to-mutate=03-development/src"],
            cwd=str(project), capture_output=True, text=True,
        )
        
        if r.returncode != 0:
            return False, f"mutmut run crashed (return code {r.returncode}).\n\nSTDOUT:\n{r.stdout.strip()}\n\nSTDERR:\n{r.stderr.strip()}"

        res = subprocess.run(
            ["mutmut", "results"], cwd=str(project), capture_output=True, text=True,
        )
        
        out = res.stdout.strip()
        if out:
            # Try to parse the exact number for a better error message, but always block!
            m = re.search(r"Survived[^(]*\((\d+)\)", out)
            survivors = m.group(1) if m else "Unknown"
            return False, (
                f"Mutation testing failed: {survivors} surviving mutant(s) found.\n\n"
                f"{out}"
            )
            
        return True, ""
    except Exception as e:
        return False, f"Error running mutmut: {e}"
