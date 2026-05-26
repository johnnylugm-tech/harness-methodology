"""Coverage source resolution utilities (shared between FrameworkEnforcer and PhaseTruthVerifier)."""
import configparser
from pathlib import Path


def read_coveragerc_source(project_root: Path) -> str:
    """Return coverage source path from .coveragerc [run] source, defaulting to '.'.

    Using ``--cov=.`` overrides .coveragerc and includes helper/script files
    that inflate or deflate the reported coverage number.  Reading the project's
    own config respects intentional source scoping (e.g. ``source = 03-development/src``).
    """
    coveragerc = project_root / ".coveragerc"
    if coveragerc.exists():
        try:
            parser = configparser.ConfigParser()
            parser.read(coveragerc)
            src = parser.get("run", "source", fallback=".").strip()
            if src:
                return src
        except Exception:
            pass
    return "."
