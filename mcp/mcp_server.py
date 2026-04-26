"""Kowalski-Kevin MCP server.

Exposes 5 tools that let an AI coding agent query architectural metrics
about a Python repo before/while making changes. Built against the
Analyzer protocol in contract.py; today backed by fake_analyzer, later
swapped for the real one with a one-line import change.

CRITICAL: This process speaks JSON-RPC over stdout. Anything that prints
to stdout corrupts the protocol stream. All logging goes to stderr.
Never use print() in this module or anything it imports at runtime.
"""

import logging
import os
import sys
import traceback
from functools import wraps
from pathlib import Path

import networkx as nx
from fastmcp import FastMCP

from real_analyzer import get_analyzer

sys.path.insert(0, str(Path(__file__).parent.parent / "visualization"))
from render import generate_interactive_graph
from utils import (
    aggregate_to_packages,
    build_function_graph,
    compute_metrics,
    find_cycle_info,
    parse_edges,
)
from formatters import (
    format_analyze_repo,
    format_check_change,
    format_generate_graph,
    format_module_health,
    format_suggest_refactor,
)


logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("kowalski-kevin")

mcp = FastMCP("KowalskiKevin")
_analyzer = get_analyzer()


def _safe_tool(fn):
    """Decorator: trap exceptions, log to stderr, return readable string to agent.

    A raised exception inside an MCP tool surfaces as an opaque protocol error
    on the agent side. Returning a string lets the agent reason about the
    failure (and, often, fix its own arguments and retry).
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            log.exception("tool %s failed", fn.__name__)
            return (
                f"Error in `{fn.__name__}`: {type(e).__name__}: {e}\n"
                "(Server-side traceback was logged to stderr.)"
            )
    return wrapper


def _resolve_path(path: str) -> str:
    """Normalize the path arg.

    `.` and empty strings are rejected: when the MCP server is launched by a
    host like Claude Code, os.getcwd() is the host's launch directory (often
    C:\\WINDOWS\\System32), not the user's repo. Forcing an explicit path
    eliminates a whole class of "I analyzed the wrong directory" bugs.
    """
    if not path or path in (".", "./"):
        raise ValueError(
            "path must be an absolute path to the repo to analyze. "
            "'.' is rejected because the MCP server's CWD is the host's "
            "launch directory (e.g. System32), not your project. "
            "Pass the absolute repo path explicitly."
        )
    return os.path.abspath(path)


@mcp.tool()
@_safe_tool
def analyze_repo(path: str) -> str:
    """Run a full architectural analysis of a Python repo.

    Returns a Markdown summary: module count, edge count, average instability,
    and the top modules by violation severity. Call this first to ground any
    refactoring or feature work.

    Args:
        path: Absolute filesystem path to the repo root. Required -- '.' and
            empty strings are rejected because the MCP server's CWD is the
            host's launch dir, not the user's project.
    """
    repo = _resolve_path(path)
    log.info("analyze_repo: %s", repo)
    snapshot = _analyzer.analyze(repo)
    return format_analyze_repo(snapshot)


@mcp.tool()
@_safe_tool
def module_health(path: str, module: str) -> str:
    """Get a per-module health card.

    Returns Markdown with the module's Ca/Ce/instability/LCOM/CC, any rule
    violations with plain-English explanations, and which modules import or
    are imported by it.

    Args:
        path: Filesystem path to the repo root.
        module: Dotted module name, e.g. "handlers.user".
    """
    repo = _resolve_path(path)
    log.info("module_health: %s in %s", module, repo)
    snapshot = _analyzer.analyze(repo)
    return format_module_health(snapshot, module)


@mcp.tool()
@_safe_tool
def suggest_refactor(path: str, feature_description: str) -> str:
    """Given a feature you're about to implement, list decouplings to do FIRST.

    Identifies modules whose existing structural problems would be amplified by
    the planned change, ranked by severity. Each entry includes a rationale.

    Args:
        path: Filesystem path to the repo root.
        feature_description: Natural-language description of the feature being added.
    """
    repo = _resolve_path(path)
    log.info("suggest_refactor: %s -- %r", repo, feature_description[:80])
    snapshot = _analyzer.analyze(repo)
    return format_suggest_refactor(snapshot, feature_description)


@mcp.tool()
@_safe_tool
def check_change(path: str, files: list[str]) -> str:
    """Re-analyze recently-modified files and report metric deltas.

    Use after making edits to confirm the change improved (or didn't worsen)
    the architecture. Returns before/after metrics and a green/yellow/red
    verdict.

    Args:
        path: Filesystem path to the repo root.
        files: Repo-relative paths of files just modified.
    """
    repo = _resolve_path(path)
    log.info("check_change: %s files=%s", repo, files)
    result = _analyzer.incremental_check(repo, files)
    return format_check_change(result)


@mcp.tool()
@_safe_tool
def generate_graph(path: str, output: str = "") -> str:
    """Generate an interactive HTML dependency graph for a Python repo.

    Creates a visual dependency graph with three views: package-level,
    file-level, and function-level. The graph shows impact, susceptibility,
    cycles, and allows interactive exploration.

    Args:
        path: Absolute filesystem path to the repo root. Required -- '.' and
            empty strings are rejected.
        output: Optional output path for the HTML file. Defaults to
            visualization/output/graph.html relative to the MCP server.
    """
    repo = _resolve_path(path)
    log.info("generate_graph: %s", repo)
    source_root = Path(repo)

    if output:
        output_path = Path(output)
    else:
        output_path = Path(__file__).parent.parent / "visualization" / "output" / "graph.html"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    edges, all_modules = parse_edges(source_root)

    file_graph = nx.DiGraph()
    file_edge_types: dict[tuple[str, str], bool] = {}
    for module in all_modules:
        file_graph.add_node(module)
    for edge in edges:
        file_graph.add_edge(edge.src, edge.dst)
        file_edge_types[(edge.src, edge.dst)] = edge.is_dynamic

    file_metrics = compute_metrics(file_graph)
    file_cycle_nodes, file_cycle_edges = find_cycle_info(file_graph)

    pkg_edges = aggregate_to_packages(edges)
    package_graph = nx.DiGraph()
    pkg_names = {m.split(".")[0] for m in all_modules}
    for pkg in pkg_names:
        package_graph.add_node(pkg)
    for edge in pkg_edges:
        package_graph.add_edge(edge.src, edge.dst)

    package_metrics = compute_metrics(package_graph)
    package_cycle_nodes, package_cycle_edges = find_cycle_info(package_graph)

    try:
        function_graph, function_metadata = build_function_graph(source_root)
    except Exception as e:
        log.warning("Function graph generation failed: %s", e)
        function_graph = nx.DiGraph()
        function_metadata = {}

    function_metrics = compute_metrics(function_graph)
    function_cycle_nodes, function_cycle_edges = find_cycle_info(function_graph)

    generate_interactive_graph(
        package_graph=package_graph,
        file_graph=file_graph,
        function_graph=function_graph,
        file_edge_types=file_edge_types,
        package_metrics=package_metrics,
        file_metrics=file_metrics,
        function_metrics=function_metrics,
        file_cycle_nodes=file_cycle_nodes,
        file_cycle_edges=file_cycle_edges,
        package_cycle_nodes=package_cycle_nodes,
        package_cycle_edges=package_cycle_edges,
        function_cycle_nodes=function_cycle_nodes,
        function_cycle_edges=function_cycle_edges,
        function_metadata=function_metadata,
        source_root=source_root,
        output_path=output_path,
        open_browser=False,
    )

    high_impact = [n for n, m in file_metrics.items() if m.impact > 0.7]
    high_suscept = [n for n, m in file_metrics.items() if m.susceptibility > 0.7]

    result = {
        "output_path": str(output_path.resolve()),
        "file_nodes": file_graph.number_of_nodes(),
        "file_edges": file_graph.number_of_edges(),
        "package_nodes": package_graph.number_of_nodes(),
        "package_edges": package_graph.number_of_edges(),
        "function_nodes": function_graph.number_of_nodes(),
        "function_edges": function_graph.number_of_edges(),
        "file_cycle_count": len(file_cycle_nodes),
        "high_impact_count": len(high_impact),
        "high_susceptibility_count": len(high_suscept),
    }

    return format_generate_graph(result)


if __name__ == "__main__":
    log.info("Kowalski-Kevin MCP server starting (stdio transport)")
    mcp.run()
