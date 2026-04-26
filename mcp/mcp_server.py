"""Kowalski-Kevin MCP server.

Exposes tools that let an AI coding agent query architectural metrics
about a Python repo before/while making changes. Built against the
Analyzer protocol in contract.py; today backed by fake_analyzer, later
swapped for the real one with a one-line import change.

CRITICAL: This process speaks JSON-RPC over stdout. Anything that prints
to stdout corrupts the protocol stream. All logging goes to stderr.
Never use print() in this module or anything it imports at runtime.
"""

import logging
import os
import re
import subprocess
import sys
import traceback
from functools import wraps
from pathlib import Path

import networkx as nx
from fastmcp import FastMCP

from real_analyzer import get_analyzer
from visualization.utils import (
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
    format_refactor_assistance,
    format_suggest_refactor,
    viz_html_path_from_generate_stdout,
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


def _metrics_to_dict(nm) -> dict:
    return {
        "ca": nm.ca,
        "ce": nm.ce,
        "instability": nm.instability,
        "impact": nm.impact,
        "susceptibility": nm.susceptibility,
        "raw_impact": nm.raw_impact,
        "raw_susceptibility": nm.raw_susceptibility,
    }


def _display_node_id(nid: str, metadata: dict | None) -> str:
    if not metadata:
        return nid
    meta = metadata.get(nid) or {}
    label, path = meta.get("label"), meta.get("file_path")
    if label and path is not None and "line" in meta:
        return f"{label} (`{path}`:{meta['line']})"
    return nid


def _refactor_level_block(
    graph: nx.DiGraph,
    metrics: dict,
    *,
    metadata: dict | None = None,
    top_sus: int = 5,
    top_imp: int = 5,
    neighbor_cap: int = 3,
) -> dict:
    """Package/file/function slice for ``format_refactor_assistance``."""
    if graph.number_of_nodes() == 0 or not metrics:
        return {
            "node_count": 0,
            "edge_count": 0,
            "high_susceptibility_detail": [],
            "high_impact_detail": [],
        }

    nodes = list(graph.nodes())
    by_susc = sorted(nodes, key=lambda n: metrics[n].raw_susceptibility, reverse=True)[:top_sus]
    by_imp = sorted(nodes, key=lambda n: metrics[n].raw_impact, reverse=True)[:top_imp]

    sus_detail = []
    for nid in by_susc:
        preds = sorted(
            list(graph.predecessors(nid)),
            key=lambda p: metrics[p].raw_impact,
            reverse=True,
        )[:neighbor_cap]
        sus_detail.append(
            {
                "id": _display_node_id(nid, metadata),
                "metrics": _metrics_to_dict(metrics[nid]),
                "high_impact_dependents": [
                    {"id": _display_node_id(p, metadata), **_metrics_to_dict(metrics[p])}
                    for p in preds
                ],
            }
        )

    imp_detail = []
    for nid in by_imp:
        succs = sorted(
            list(graph.successors(nid)),
            key=lambda s: metrics[s].raw_susceptibility,
            reverse=True,
        )[:neighbor_cap]
        imp_detail.append(
            {
                "id": _display_node_id(nid, metadata),
                "metrics": _metrics_to_dict(metrics[nid]),
                "high_susceptibility_dependencies": [
                    {"id": _display_node_id(s, metadata), **_metrics_to_dict(metrics[s])}
                    for s in succs
                ],
            }
        )

    return {
        "node_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
        "high_susceptibility_detail": sus_detail,
        "high_impact_detail": imp_detail,
    }


def _build_graph_metrics_bundle(source_root: Path) -> dict:
    """Same graphs/metrics as ``generate_graph`` / the interactive visualization."""
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

    return {
        "source_root": source_root,
        "edges": edges,
        "all_modules": all_modules,
        "file_graph": file_graph,
        "file_edge_types": file_edge_types,
        "file_metrics": file_metrics,
        "file_cycle_nodes": file_cycle_nodes,
        "file_cycle_edges": file_cycle_edges,
        "package_graph": package_graph,
        "package_metrics": package_metrics,
        "package_cycle_nodes": package_cycle_nodes,
        "package_cycle_edges": package_cycle_edges,
        "function_graph": function_graph,
        "function_metadata": function_metadata,
        "function_metrics": function_metrics,
        "function_cycle_nodes": function_cycle_nodes,
        "function_cycle_edges": function_cycle_edges,
    }


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


_REPO_ROOT = Path(__file__).parent.parent


def _parse_viz_stdout(stdout: str, output_path: str) -> dict:
    """Parse visualization CLI stdout into the result dict for format_generate_graph."""
    result: dict = {"output_path": output_path}

    m = re.search(r"File-level:\s*(\d+)\s*nodes,\s*(\d+)\s*edges", stdout)
    result["file_nodes"] = int(m.group(1)) if m else 0
    result["file_edges"] = int(m.group(2)) if m else 0

    m = re.search(r"Package-level:\s*(\d+)\s*nodes,\s*(\d+)\s*edges", stdout)
    result["package_nodes"] = int(m.group(1)) if m else 0
    result["package_edges"] = int(m.group(2)) if m else 0

    m = re.search(r"Function-level:\s*(\d+)\s*nodes,\s*(\d+)\s*edges", stdout)
    result["function_nodes"] = int(m.group(1)) if m else 0
    result["function_edges"] = int(m.group(2)) if m else 0

    m = re.search(r"Cycles \(file\):\s*(\d+)\s*nodes", stdout)
    result["file_cycle_count"] = int(m.group(1)) if m else 0

    m = re.search(r"High impact.*?:\s*(\d+)", stdout)
    result["high_impact_count"] = int(m.group(1)) if m else 0

    m = re.search(r"High susceptibility.*?:\s*(\d+)", stdout)
    result["high_susceptibility_count"] = int(m.group(1)) if m else 0

    return result


@mcp.tool()
@_safe_tool
def refactor_assistance(path: str) -> str:
    """Ca/Ce-focused refactor brief using the same graphs as the visualizer.

    Returns Markdown with **package**, **file/module**, and **function** levels.
    Each layer lists Ca, Ce, instability, impact, and susceptibility (normalized
    and raw), plus targeted neighbor context: for high-susceptibility nodes, the
    heaviest **impact** dependents (predecessors); for high-impact nodes, the
    most **susceptible** dependencies (successors). Use this to plan decoupling:
    shrink high-impact fan-in onto susceptible hubs, and trim high-susceptibility
    fan-out from impact-heavy modules.

    Args:
        path: Absolute filesystem path to the repo root. Required.
    """
    repo = _resolve_path(path)
    log.info("refactor_assistance: %s", repo)
    bundle = _build_graph_metrics_bundle(Path(repo))
    payload = {
        "root": repo,
        "levels": {
            "package": _refactor_level_block(
                bundle["package_graph"],
                bundle["package_metrics"],
            ),
            "file": _refactor_level_block(
                bundle["file_graph"],
                bundle["file_metrics"],
            ),
            "function": _refactor_level_block(
                bundle["function_graph"],
                bundle["function_metrics"],
                metadata=bundle["function_metadata"],
            ),
        },
    }
    return format_refactor_assistance(payload)


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
        output: Optional output path for the HTML file. Defaults to the same
            path the CLI uses: visualization/output/<stem>.html under the
            Kowalski repo, where <stem> is derived from the analyzed path.
    """
    repo = _resolve_path(path)
    log.info("generate_graph: %s", repo)

    if output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, "-m", "visualization.generate_graph",
            "--path", repo,
            "--output", str(output_path),
            "--no-browser",
        ]
    else:
        output_path = None
        cmd = [
            sys.executable, "-m", "visualization.generate_graph",
            "--path", repo,
            "--no-browser",
        ]
    proc = subprocess.run(
        cmd, cwd=str(_REPO_ROOT),
        capture_output=True, text=True, timeout=120,
    )

    if proc.returncode != 0:
        raise RuntimeError(
            f"Visualization failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )

    if output_path is None:
        output_path = viz_html_path_from_generate_stdout(proc.stdout, Path(_REPO_ROOT))
    else:
        output_path = output_path.resolve()

    result = _parse_viz_stdout(proc.stdout, str(output_path))
    return format_generate_graph(result)


if __name__ == "__main__":
    log.info("Kowalski-Kevin MCP server starting (stdio transport)")
    mcp.run()
