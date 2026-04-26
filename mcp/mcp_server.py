"""Kowalski-Kevin MCP server.

Exposes 5 tools that let an AI coding agent query architectural metrics
about a Python repo before/while making changes. Built against the
Analyzer protocol in contract.py; today backed by fake_analyzer, later
swapped for the real one with a one-line import change.
"""

from fastmcp import FastMCP

from fake_analyzer import get_analyzer
from formatters import (
    format_analyze_repo,
    format_check_change,
    format_metric_graph,
    format_module_health,
    format_suggest_refactor,
)


mcp = FastMCP("KowalskiKevin")
_analyzer = get_analyzer()


@mcp.tool()
def analyze_repo(path: str = ".") -> str:
    """Run a full architectural analysis of a Python repo.

    Returns a Markdown summary: module count, edge count, average instability,
    and the top modules by violation severity. Call this first to ground any
    refactoring or feature work.

    Args:
        path: Filesystem path to the repo root. Defaults to current directory.
    """
    snapshot = _analyzer.analyze(path)
    return format_analyze_repo(snapshot)


@mcp.tool()
def module_health(path: str, module: str) -> str:
    """Get a per-module health card.

    Returns Markdown with the module's Ca/Ce/instability/LCOM/CC, any rule
    violations with plain-English explanations, and which modules import or
    are imported by it.

    Args:
        path: Filesystem path to the repo root.
        module: Dotted module name, e.g. "handlers.user".
    """
    snapshot = _analyzer.analyze(path)
    return format_module_health(snapshot, module)


@mcp.tool()
def suggest_refactor(path: str, feature_description: str) -> str:
    """Given a feature you're about to implement, list decouplings to do FIRST.

    Identifies modules whose existing structural problems would be amplified by
    the planned change, ranked by severity. Each entry includes a rationale.

    Args:
        path: Filesystem path to the repo root.
        feature_description: Natural-language description of the feature being added.
    """
    snapshot = _analyzer.analyze(path)
    return format_suggest_refactor(snapshot, feature_description)


@mcp.tool()
def check_change(path: str, files: list[str]) -> str:
    """Re-analyze recently-modified files and report metric deltas.

    Use after making edits to confirm the change improved (or didn't worsen)
    the architecture. Returns before/after metrics and a green/yellow/red
    verdict.

    Args:
        path: Filesystem path to the repo root.
        files: Repo-relative paths of files just modified.
    """
    result = _analyzer.incremental_check(path, files)
    return format_check_change(result)


@mcp.tool()
def get_metric_graph(path: str = ".") -> str:
    """Return the metric graph as JSON (nodes + edges) for visualization.

    Same shape consumed by the upcoming graph viewer. Useful when an agent or
    UI wants raw data instead of formatted prose.

    Args:
        path: Filesystem path to the repo root. Defaults to current directory.
    """
    snapshot = _analyzer.analyze(path)
    return format_metric_graph(snapshot)


if __name__ == "__main__":
    mcp.run()
