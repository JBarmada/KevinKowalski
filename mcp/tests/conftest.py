"""Shared pytest fixtures.

Tests assert SHAPES only -- they don't pin specific metric values, so
they work against any Analyzer implementation (currently RealAnalyzer).
"""

import sys
from pathlib import Path

import pytest

# Add mcp/ to sys.path so `import contract`, `import real_analyzer`, etc.
# work without packaging the project.
_MCP_DIR = Path(__file__).resolve().parents[1]
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))


@pytest.fixture
def analyzer():
    """The Analyzer under test (RealAnalyzer)."""
    from real_analyzer import get_analyzer
    return get_analyzer()


@pytest.fixture
def snapshot(analyzer):
    return analyzer.analyze("/fake/repo")
