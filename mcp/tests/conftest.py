"""Shared pytest fixtures + the loud banner for fake-only tests.

Tests are split into two families:

  * Tier 1-3 (default): assert SHAPES only. They must keep passing after
    we swap fake_analyzer for the real analyzer. Don't write value
    assertions here.

  * Tier 4 (`fake_only` marker): pin specific behavior of the fake
    analyzer. Will FAIL after the analyzer swap. That is expected. The
    banner below makes sure no one is surprised.
"""

import sys
from pathlib import Path

import pytest

# Add mcp/ to sys.path so `import contract`, `import fake_analyzer` work
# without packaging the project. Hackathon-grade — replace with a real
# package install if this lives past the demo.
_MCP_DIR = Path(__file__).resolve().parents[1]
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))


@pytest.fixture
def analyzer():
    """The Analyzer under test. Swap to the real one by changing this fixture."""
    from real_analyzer import get_analyzer
    return get_analyzer()


@pytest.fixture
def snapshot(analyzer):
    return analyzer.analyze("/fake/repo")


_BANNER_PRINTED = False


def pytest_collection_modifyitems(config, items):
    """Print a loud banner if any fake_only tests are about to run."""
    global _BANNER_PRINTED
    has_fake_only = any("fake_only" in item.keywords for item in items)
    if has_fake_only and not _BANNER_PRINTED:
        _BANNER_PRINTED = True
        sys.stderr.write(
            "\n"
            "============================================================\n"
            "NOTE: Running fake-analyzer-only tests (marker: fake_only).\n"
            "These pin behavior of the STUB analyzer and WILL fail after\n"
            "we swap to the real analyzer. That is expected -- delete the\n"
            "tests at that point, do NOT 'fix' them.\n"
            "See mcp/tests/test_fake_analyzer_only.py for details.\n"
            "============================================================\n\n"
        )
