import sys
import tempfile
import ast
import subprocess
import json
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 -m harness.toolchains.radon_mi_ast_stripped <root_dir>")
        sys.exit(1)
        
    root_dir = Path(sys.argv[1]).resolve()
    
    # 1. Use radon raw to find exactly which files radon considers (respecting ignores/excludes)
    try:
        raw_out = subprocess.check_output(["radon", "raw", str(root_dir), "-j"], text=True)
        raw_data = json.loads(raw_out)
    except Exception as e:
        print(f"Error running radon raw: {e}", file=sys.stderr)
        sys.exit(1)
        
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        has_files = False
        for filepath_str, data in raw_data.items():
            if "error" in data:
                continue
            original_path = Path(filepath_str)
            if not original_path.is_absolute():
                original_path = root_dir / original_path
            
            try:
                source = original_path.read_text(encoding="utf-8")
                tree = ast.parse(source)
                
                # Gather line ranges to blank out
                ranges_to_blank = []
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef, ast.Module)):
                        if ast.get_docstring(node) is not None:
                            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
                                expr = node.body[0]
                                if hasattr(expr, "lineno") and hasattr(expr, "end_lineno"):
                                    ranges_to_blank.append((expr.lineno, expr.end_lineno))
                
                # Blank out the lines. Replacing with empty string removes docstrings from
                # radon's multi count, preventing the sin() penalty bug on heavy docstring files,
                # while preserving all legitimate '#' comments for the normal comment bonus.
                lines = source.splitlines()
                for start, end in ranges_to_blank:
                    for i in range(start - 1, end):
                        if 0 <= i < len(lines):
                            lines[i] = ""
                
                stripped = "\n".join(lines)
                
                rel_path = original_path.relative_to(root_dir)
                target_path = tmp_root / rel_path
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(stripped, encoding="utf-8")
                has_files = True
            except Exception as exc:
                print(f"[WARN] radon_mi_ast_stripped: could not parse/strip "
                      f"{original_path}, excluded from MI calculation: {exc}", file=sys.stderr)
                
        if not has_files:
            print("{}")
            sys.exit(0)
            
        result = subprocess.run(["radon", "mi", str(tmp_root), "-j"], capture_output=True, text=True)
        try:
            mi_data = json.loads(result.stdout)
            rewritten_data = {}
            for tmp_path_str, val in mi_data.items():
                if "error" in val:
                    continue
                tmp_path = Path(tmp_path_str)
                try:
                    rel_path = tmp_path.relative_to(tmp_root)
                    rewritten_data[str(root_dir / rel_path)] = val
                except ValueError:
                    # In case radon outputs something unexpected
                    rewritten_data[tmp_path_str] = val
            print(json.dumps(rewritten_data))
        except Exception:
            # Fallback if radon mi didn't output JSON
            print(result.stdout)
        sys.exit(result.returncode)

if __name__ == "__main__":
    main()
