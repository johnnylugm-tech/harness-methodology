# conftest.py — pytest configuration for harness-methodology self-tests
import sys
from pathlib import Path

# Ensure repo root is on sys.path so `from harness.xxx import` works
sys.path.insert(0, str(Path(__file__).parent))
