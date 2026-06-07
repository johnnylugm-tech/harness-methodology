from pathlib import Path
from core.utils.project_layout import ProjectLayout

def test_project_layout_basic_paths(tmp_path):
    layout = ProjectLayout(tmp_path)
    assert layout.root == tmp_path.resolve()
    assert layout.phase1_requirements_dir.name == "01-requirements"
    assert layout.srs_path.name == "SRS.md"
    assert layout.srs_path.parent == layout.phase1_requirements_dir
    assert layout.methodology_dir.name == ".methodology"

def test_project_layout_active_test_dir(tmp_path):
    layout = ProjectLayout(tmp_path)
    # By default, falls back to root/tests
    assert layout.active_test_dir == tmp_path / "tests"

    # Create 03-development/tests
    dev_dir = tmp_path / "03-development"
    dev_dir.mkdir()
    (dev_dir / "tests").mkdir()
    
    # Should now resolve to 03-development/tests
    assert layout.active_test_dir == dev_dir / "tests"

def test_project_layout_relative_str(tmp_path):
    layout = ProjectLayout(tmp_path)
    target = tmp_path / "tests" / "test_foo.py"
    assert layout.get_relative_str(target) == "tests/test_foo.py"
    
    # Test path outside project (should return absolute path string)
    outside = Path("/tmp/outside.txt")
    assert layout.get_relative_str(outside) == str(outside)
