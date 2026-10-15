"""Global pytest fixtures and environment setup."""
import sys
from pathlib import Path

# Prevent site-packages packages (e.g. anaconda ultralytics) from shadowing the local tests directory
_TESTS_DIR = str(Path(__file__).resolve().parent)
if "tests" in sys.modules:
    _tests_mod = sys.modules["tests"]
    if hasattr(_tests_mod, "__path__") and _TESTS_DIR not in _tests_mod.__path__:
        _tests_mod.__path__.insert(0, _TESTS_DIR)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
