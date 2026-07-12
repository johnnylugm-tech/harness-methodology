"""Coverage source resolution utilities (shared between FrameworkEnforcer and PhaseTruthVerifier)."""
import configparser
import warnings
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


def _fr_source_files_from_imports(
    project: Path, test_file: str, src_dir: str
) -> list[str]:
    """Return source files under src_dir that are imported by test_file.

    Parses the test file with ast and matches imported module paths against
    .py files under src_dir.  Returns relative-to-project paths, e.g.
    ["03-development/src/omnibot/adapters/telegram_adapter.py"].

    Returns [] when the test file is absent, unparseable, or no matches are
    found — callers should fall back to the full src_dir in that case.
    """
    import ast as _ast

    test_path = project / test_file
    if not test_path.exists():
        return []
    try:
        tree = _ast.parse(test_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    # Collect every dotted name that appears in an import statement.
    imported: set[str] = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, _ast.ImportFrom):
            if node.module:
                imported.add(node.module)
                # "from pkg import name" also covers pkg.name
                for alias in node.names:
                    imported.add(f"{node.module}.{alias.name}")

    src_path = project / src_dir
    if not src_path.exists():
        return []

    matched: list[str] = []
    for py_file in sorted(src_path.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        # Convert file path to dotted module name relative to src_dir root.
        try:
            rel_parts = py_file.relative_to(src_path).with_suffix("").parts
        except ValueError:
            continue
        module_dot = ".".join(rel_parts)
        # Match if any imported name equals or is a sub-path of the module.
        for imp in imported:
            if imp == module_dot or imp.startswith(module_dot + "."):
                matched.append(str(py_file.relative_to(project)))
                break

    # Layer 2 (auto): follow __init__.py re-exports for imported packages.
    # When a test does `from omnibot.queries import ODD_QUERIES`, the AST sees
    # the package `omnibot.queries` but not the submodule `odd_queries.py` that
    # __init__.py re-exports.  One level of __init__.py expansion catches this.
    _seen_dirs: set[str] = set()
    for imp in list(imported):
        pkg_candidate = src_path / Path(*imp.split("."))
        if not pkg_candidate.is_dir():
            continue
        pkg_key = str(pkg_candidate)
        if pkg_key in _seen_dirs:
            continue
        _seen_dirs.add(pkg_key)
        init_file = pkg_candidate / "__init__.py"
        if not init_file.exists():
            continue
        try:
            init_tree = _ast.parse(init_file.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        for node in _ast.walk(init_tree):
            if not isinstance(node, _ast.ImportFrom):
                continue
            mod = node.module or ""
            # Resolve relative import: "from .sub import X" inside pkg/__init__.py
            rel_mod = mod.lstrip(".")
            for seg in (rel_mod, *(f"{alias.name}" for alias in node.names)):
                candidate = pkg_candidate / f"{seg}.py"
                if candidate.exists():
                    rel = str(candidate.relative_to(project))
                    if rel not in matched:
                        matched.append(rel)

    return matched


def resolve_fr_scoped_src_files(
    project: str,
    fr_id: str,
    test_file: str,
    src_dir: str,
    manifest_data: dict,
) -> list[str]:
    """Resolve the source files/globs owned by fr_id, for coverage scoping.

    Priority 1: fr_module_traceability[fr_id] (dotted module path) resolved
    under src_dir, with package-dir glob fallback for re-export shims /
    directory-only packages (both trace-exists and trace-missing cases).
    Priority 2: AST-import-based detection from test_file, via
    _fr_source_files_from_imports(), when traceability is absent or the
    traced path could not be resolved.
    Priority 3: manifest_data["fr_scope_overrides"][fr_id] merged in.

    Returns [] when nothing could be determined — callers should fall back
    to the full src_dir in that case (this function does not decide the
    fallback command shape; that's caller-specific presentation).

    This avoids the import-based scope problem where a TDD test imports
    helpers from other FRs' modules, inflating the scope and diluting
    coverage. Example: test_fr04.py imports
    taskq.{cli, store, executor, config, models, cache} as helpers, but
    fr_module_traceability["FR-04"] = "taskq.cache" says FR-04 owns cache
    only — measuring all 6 modules reports ~17% per FR instead of 100%.
    """
    # Accepts str or list[str]; malformed entries (".", "..", empty,
    # path-traversal, non-string) emit a warning and fall back to imports
    # rather than crashing the audit with ValueError from Path.with_suffix.
    src_files: list[str] = []
    fr_trace = manifest_data.get("fr_module_traceability", {}).get(fr_id)
    trace_entries: list[str] = []
    if isinstance(fr_trace, str):
        trace_entries = [fr_trace]
    elif isinstance(fr_trace, list):
        non_str = [t for t in fr_trace if not isinstance(t, str)]
        trace_entries = [t for t in fr_trace if isinstance(t, str)]
        if non_str:
            warnings.warn(
                f"fr_module_traceability[{fr_id}] contains non-string entries; "
                f"non-string entries ignored",
                stacklevel=3,
            )
    elif fr_trace is not None:
        warnings.warn(
            f"fr_module_traceability[{fr_id}] is {type(fr_trace).__name__}, "
            f"expected str or list[str]; falling back to import-based detection",
            stacklevel=3,
        )

    for trace in trace_entries:
        parts = trace.replace("\\", "/").split("/")
        if not trace or any(p in (".", "..") for p in parts):
            warnings.warn(
                f"fr_module_traceability[{fr_id}]={trace!r} is malformed "
                f"(empty or contains '.' / '..' path segment); skipped",
                stacklevel=3,
            )
            continue
        try:
            owned_path = (
                Path(project) / src_dir
                / Path(trace.replace(".", "/")).with_suffix(".py")
            )
        except ValueError as exc:
            warnings.warn(
                f"fr_module_traceability[{fr_id}]={trace!r} produced invalid "
                f"path ({exc}); skipped",
                stacklevel=3,
            )
            continue
        if owned_path.exists():
            # Fix III: when owned_path is a thin re-export shim (≤ 5 lines
            # after stripping comments) and a package directory with the same
            # stem exists next to it, coverage --include must match the WHOLE
            # package (e.g. executor/**/*.py), not just the shim file.
            # Without this, FR-02 executor shows 0% coverage because the real
            # code lives in executor/runner.py.
            pkg_dir = owned_path.with_suffix("")
            if pkg_dir.is_dir() and (pkg_dir / "__init__.py").exists():
                # Use recursive glob to cover the package directory
                pkg_glob = str(owned_path.relative_to(project).with_suffix("") / "**" / "*.py")
                src_files.append(pkg_glob)
            else:
                src_files.append(str(owned_path.relative_to(project)))
        else:
            # Fix III extension: .py file doesn't exist at all (e.g. executor.py
            # was never created, but executor/__init__.py + executor/runner.py
            # exist as an untracked package). Use recursive glob to match the
            # whole package directory.
            pkg_dir = owned_path.with_suffix("")
            if pkg_dir.is_dir() and (pkg_dir / "__init__.py").exists():
                pkg_glob = str(owned_path.relative_to(project).with_suffix("") / "**" / "*.py")
                src_files.append(pkg_glob)

    # Priority 2: detect FR-specific source files by parsing the test file's
    # imports. Used when fr_module_traceability is absent or the owned path
    # does not exist on disk.
    if not src_files:
        src_files = _fr_source_files_from_imports(Path(project), test_file, src_dir)

    # Issue 4: manual fr_scope_overrides — merges declared files into scope.
    # Use when __init__.py transitive re-exports can't be auto-detected.
    # Add to quality_manifest.json: {"fr_scope_overrides": {"FR-16": ["path/to/file.py"]}}
    scope_override = manifest_data.get("fr_scope_overrides", {}).get(fr_id, [])
    if scope_override:
        src_files = list(dict.fromkeys(src_files + scope_override))

    return src_files
