"""Python Code Scanner Module.

Scans Python source code using AST to extract classes, functions, methods.
"""

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class ScanError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


@dataclass
class ScanErrorRecord:
    file_path: str
    error_type: str
    details: str


@dataclass
class CodeItem:
    id: str
    kind: str
    name: str
    module: str
    file_path: str
    line_number: int
    docstring: str = ""
    params: list = field(default_factory=list)
    is_public: bool = True
    decorators: list = field(default_factory=list)


@dataclass
class CodeFile:
    module_name: str
    file_path: str
    items: list = field(default_factory=list)
    line_count: int = 0


@dataclass
class ScanStats:
    total_files: int = 0
    scanned_files: int = 0
    skipped_files: int = 0
    total_items: int = 0
    scan_coverage_rate: float = 1.0
    errors: list = field(default_factory=list)


@dataclass
class ScannedCode:
    modules: list
    scan_stats: ScanStats = field(default_factory=ScanStats)


class _ASTVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str, module_name: str):
        self.file_path = file_path
        self.module_name = module_name
        self.items = []
        self.in_class = False
        self.current_class = ""

    def visit_ClassDef(self, node):
        self.in_class = True
        self.current_class = node.name
        docstring = ast.get_docstring(node) or ""
        self.items.append(CodeItem(
            id=f"{self.module_name}.{node.name}", kind="class", name=node.name,
            module=self.module_name, file_path=self.file_path, line_number=node.lineno,
            docstring=docstring.split("\n")[0].strip() if docstring else "",
            is_public=not node.name.startswith("_")
        ))
        self.generic_visit(node)
        self.in_class = False
        self.current_class = ""

    def visit_FunctionDef(self, node):
        self._visit_fn(node)

    def visit_AsyncFunctionDef(self, node):
        self._visit_fn(node)

    def _visit_fn(self, node):
        docstring = ast.get_docstring(node) or ""
        params = [arg.arg for arg in node.args.args]
        kind = "method" if self.in_class else "function"
        item_id = (f"{self.module_name}.{self.current_class}.{node.name}"
                   if self.in_class else f"{self.module_name}.{node.name}")
        self.items.append(CodeItem(
            id=item_id, kind=kind, name=node.name,
            module=self.module_name, file_path=self.file_path, line_number=node.lineno,
            docstring=docstring.split("\n")[0].strip() if docstring else "",
            params=params, is_public=not node.name.startswith("_")
        ))
        self.generic_visit(node)


class CodeScanner:
    """Scanner for Python implement/ directories."""

    def __init__(self, implement_dir) -> None:
        self.implement_dir = str(implement_dir)
        self._errors = []
        if not Path(self.implement_dir).exists():
            raise ScanError("E_FILE_NOT_FOUND", f"Directory not found: {self.implement_dir}")

    def scan(self) -> ScannedCode:
        self._errors = []
        modules = []
        files = self._discover_files()
        total = len(files)
        scanned = 0
        skipped = 0
        total_items = 0

        for fp in files:
            try:
                code_file = self._scan_file(fp)
                if code_file:
                    modules.append(code_file)
                    scanned += 1
                    total_items += len(code_file.items)
            except Exception as e:
                skipped += 1
                self._errors.append(ScanErrorRecord(str(fp), type(e).__name__, str(e)))

        return ScannedCode(
            modules=modules,
            scan_stats=ScanStats(
                total_files=total, scanned_files=scanned, skipped_files=skipped,
                total_items=total_items,
                scan_coverage_rate=(scanned / total) if total > 0 else 1.0,
                errors=self._errors.copy()
            )
        )

    def _discover_files(self):
        files = []
        try:
            for root, dirs, filenames in os.walk(self.implement_dir):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for fn in filenames:
                    if fn.endswith(".py") and not fn.startswith("test_") and fn != "conftest.py":
                        files.append(Path(root) / fn)
        except Exception:
            return []
        return sorted(files)

    def _scan_file(self, file_path: Path) -> Optional[CodeFile]:
        module_name = self._get_module_name(file_path)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return None
        try:
            tree = ast.parse(content, filename=str(file_path))
        except SyntaxError as e:
            raise ScanError("E_SCAN_FAILED", f"Syntax error in {file_path}: {e}")
        visitor = _ASTVisitor(str(file_path), module_name)
        visitor.visit(tree)
        return CodeFile(
            module_name=module_name, file_path=str(file_path),
            items=visitor.items, line_count=len(content.split("\n"))
        )

    def _get_module_name(self, file_path: Path) -> str:
        try:
            rel = file_path.relative_to(Path(self.implement_dir))
        except ValueError:
            rel = file_path
        parts = list(rel.parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        elif parts[-1].endswith(".py"):
            parts[-1] = parts[-1][:-3]
        return ".".join(parts) if parts else "root"
